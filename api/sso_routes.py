"""FastAPI routes for SSO (SAML 2.0) authentication.

Provides browser-based SAML login/logout flows and admin endpoints for
configuring per-tenant SSO settings and managing users.

Coexists with API key auth: SSO users authenticate via JWT in Authorization
header (Bearer token), API consumers use X-API-Key.
"""

import logging
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from api.auth import Tenant, require_tenant
from api.sso import (
    SSOUser,
    UserRole,
    get_sso_manager,
    get_user_store,
    verify_session_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sso"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class SSOConfigRequest(BaseModel):
    idp_entity_id: str
    idp_sso_url: str
    idp_x509_cert: str
    sp_entity_id: str
    enabled: bool = False
    idp_slo_url: str = ""
    default_role: str = "viewer"
    allowed_domains: str = ""


class SSOConfigUpdateRequest(BaseModel):
    idp_entity_id: Optional[str] = None
    idp_sso_url: Optional[str] = None
    idp_x509_cert: Optional[str] = None
    sp_entity_id: Optional[str] = None
    enabled: Optional[bool] = None
    idp_slo_url: Optional[str] = None
    default_role: Optional[str] = None
    allowed_domains: Optional[str] = None


class UpdateRoleRequest(BaseModel):
    role: str


class UserResponse(BaseModel):
    id: str
    tenant_id: str
    email: str
    name: str
    role: str
    last_login: Optional[str]
    created_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_tenant_id(request: Request) -> str:
    """Extract tenant_id from subdomain or query parameter.

    Priority: ?tenant= query param > subdomain detection.
    Subdomain detection looks for patterns like:
      lexington.app.civiclens.ai -> lexington
      elkgrove.civiclens.ai -> elkgrove
    """
    # Explicit query parameter
    tenant = request.query_params.get("tenant")
    if tenant:
        return tenant

    # Subdomain detection
    host = request.headers.get("host", "")
    parts = host.split(".")
    # At least 3 parts (subdomain.domain.tld) and the first part is not "www" or "app"
    if len(parts) >= 3 and parts[0] not in ("www", "app", "api"):
        return parts[0]

    raise HTTPException(
        status_code=400,
        detail="Cannot determine tenant. Provide ?tenant= parameter or use tenant subdomain.",
    )


def _build_request_info(request: Request, post_data: Optional[dict] = None) -> dict:
    """Build request info dict for python3-saml from FastAPI Request."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    return {
        "https": scheme == "https",
        "http_host": host,
        "script_name": "",
        "get_data": dict(request.query_params),
        "post_data": post_data or {},
    }


def _user_to_response(user: SSOUser) -> dict:
    return {
        "id": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "last_login": user.last_login,
        "created_at": user.created_at,
    }


async def _require_sso_user(request: Request) -> dict:
    """Extract and verify JWT from Authorization header or cookie.

    Returns the decoded JWT payload dict. Use as a FastAPI dependency.
    """
    token = None

    # Check Authorization: Bearer header
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    # Fall back to cookie
    if not token:
        token = request.cookies.get("civiclens_session")

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide Authorization: Bearer <token> or login via SSO.",
        )

    try:
        payload = verify_session_token(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid or expired session token: {e}")

    return payload


async def _require_sso_admin(request: Request) -> dict:
    """Verify JWT and require admin role."""
    payload = await _require_sso_user(request)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    return payload


def _require_admin_role_for_browser_session(request: Request) -> None:
    """Require the SSO admin role when the caller authenticated via JWT.

    Tenant API keys are still treated as full-tenant credentials. This helper
    only constrains browser/session-based access where role information exists.
    """
    payload = getattr(request.state, "sso_user", None)
    if payload and payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")


# ---------------------------------------------------------------------------
# SAML flow endpoints
# ---------------------------------------------------------------------------

@router.get("/api/v1/sso/login")
async def sso_login(request: Request):
    """Initiate SAML login -- redirects user to their tenant's IdP."""
    tenant_id = _extract_tenant_id(request)
    request_info = _build_request_info(request)

    try:
        manager = get_sso_manager()
        redirect_url = manager.initiate_login(tenant_id, request_info)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/api/v1/sso/acs")
async def sso_acs(request: Request):
    """SAML Assertion Consumer Service -- processes IdP response.

    On successful SAML assertion, creates/updates user, generates JWT,
    and redirects to the frontend with the token.
    """
    form_data = await request.form()
    post_data = dict(form_data)

    # Tenant can come from RelayState or subdomain
    tenant_id = post_data.get("RelayState") or _extract_tenant_id(request)
    request_info = _build_request_info(request, post_data)

    try:
        manager = get_sso_manager()
        user, token = manager.process_acs(tenant_id, request_info)
    except ValueError as e:
        logger.warning("sso_acs_failed", extra={"tenant_id": tenant_id, "error": str(e)})
        raise HTTPException(status_code=401, detail=str(e))

    # Redirect to frontend with JWT
    # The frontend reads the token from the URL hash (never sent to server)
    frontend_url = request.query_params.get(
        "redirect_url",
        post_data.get("redirect_url", "/"),
    )

    response = RedirectResponse(url=f"{frontend_url}#token={token}", status_code=302)

    # Also set as httponly cookie for convenience
    response.set_cookie(
        key="civiclens_session",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=8 * 3600,
        path="/",
    )

    return response


@router.get("/api/v1/sso/metadata")
async def sso_metadata(request: Request):
    """Return SP metadata XML for IdP configuration."""
    tenant_id = _extract_tenant_id(request)
    request_info = _build_request_info(request)

    try:
        manager = get_sso_manager()
        metadata_xml = manager.get_sp_metadata(tenant_id, request_info)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Response(content=metadata_xml, media_type="application/xml")


@router.get("/api/v1/sso/logout")
async def sso_logout(request: Request):
    """Log out the current SSO session.

    If the IdP supports Single Logout (SLO), redirects to IdP for
    federated logout. Otherwise, just clears the local session.
    """
    tenant_id = _extract_tenant_id(request)
    request_info = _build_request_info(request)

    # Try to get user email from JWT for SLO
    name_id = None
    try:
        payload = await _require_sso_user(request)
        name_id = payload.get("email")
    except HTTPException:
        pass  # No active session -- still clear cookies

    # Attempt IdP-initiated SLO
    redirect_url = ""
    try:
        manager = get_sso_manager()
        redirect_url = manager.initiate_logout(tenant_id, request_info, name_id)
    except (ValueError, Exception):
        pass  # SLO not configured or failed -- just do local logout

    if redirect_url:
        response = RedirectResponse(url=redirect_url, status_code=302)
    else:
        response = RedirectResponse(url="/?logged_out=1", status_code=302)

    # Clear session cookie
    response.delete_cookie(
        key="civiclens_session",
        path="/",
        secure=True,
        httponly=True,
    )

    return response


# ---------------------------------------------------------------------------
# Current user endpoint
# ---------------------------------------------------------------------------

@router.get("/api/v1/auth/me")
async def get_current_user(request: Request):
    """Get the currently authenticated SSO user from their JWT."""
    payload = await _require_sso_user(request)

    return {
        "id": payload.get("sub"),
        "email": payload.get("email"),
        "name": payload.get("name"),
        "role": payload.get("role"),
        "tenant_id": payload.get("tenant_id"),
        "permissions": sorted(
            list(
                {
                    "admin": ["query", "export", "alerts", "settings", "manage_users", "sso_config"],
                    "analyst": ["query", "export", "alerts"],
                    "viewer": ["query"],
                }.get(payload.get("role", "viewer"), ["query"])
            )
        ),
    }


# ---------------------------------------------------------------------------
# Admin: SSO configuration
# ---------------------------------------------------------------------------

@router.put("/api/v1/admin/sso/config")
async def admin_set_sso_config(
    request: Request,
    request_body: SSOConfigRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Configure or update SSO settings for a tenant.

    Requires enterprise plan and either a tenant API key or SSO admin role.
    """
    _require_admin_role_for_browser_session(request)

    if tenant.plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail="SSO requires an Enterprise plan.",
        )

    # Validate default_role
    if request_body.default_role not in UserRole.__members__:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid default_role. Must be one of: {', '.join(UserRole.__members__)}",
        )

    # Validate URLs
    for field_name in ("idp_sso_url",):
        url = getattr(request_body, field_name)
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid URL for {field_name}: {url}",
            )

    manager = get_sso_manager()
    config = manager.config_store.upsert(
        tenant_id=tenant.id,
        data=request_body.model_dump(),
    )

    logger.info(
        "sso_config_updated",
        extra={"tenant_id": tenant.id, "enabled": config.enabled},
    )

    return {
        "tenant_id": config.tenant_id,
        "idp_entity_id": config.idp_entity_id,
        "idp_sso_url": config.idp_sso_url,
        "sp_entity_id": config.sp_entity_id,
        "enabled": config.enabled,
        "idp_slo_url": config.idp_slo_url,
        "default_role": config.default_role,
        "allowed_domains": config.allowed_domains,
        "updated_at": config.updated_at,
    }


@router.get("/api/v1/admin/sso/config")
async def admin_get_sso_config(
    request: Request,
    tenant: Tenant = Depends(require_tenant),
):
    """Get SSO configuration for a tenant."""
    _require_admin_role_for_browser_session(request)

    if tenant.plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail="SSO requires an Enterprise plan.",
        )

    manager = get_sso_manager()
    config = manager.get_config(tenant.id)

    if not config:
        return {"configured": False, "tenant_id": tenant.id}

    return {
        "configured": True,
        "tenant_id": config.tenant_id,
        "idp_entity_id": config.idp_entity_id,
        "idp_sso_url": config.idp_sso_url,
        "sp_entity_id": config.sp_entity_id,
        "enabled": config.enabled,
        "idp_slo_url": config.idp_slo_url,
        "default_role": config.default_role,
        "allowed_domains": config.allowed_domains,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
    }


# ---------------------------------------------------------------------------
# Admin: User management
# ---------------------------------------------------------------------------

@router.get("/api/v1/admin/users")
async def admin_list_users(
    request: Request,
    tenant: Tenant = Depends(require_tenant),
):
    """List all SSO users for the tenant."""
    _require_admin_role_for_browser_session(request)

    if tenant.plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail="SSO user management requires an Enterprise plan.",
        )

    store = get_user_store()
    users = store.list_by_tenant(tenant.id)

    return {
        "users": [_user_to_response(u) for u in users],
        "total": len(users),
    }


@router.patch("/api/v1/admin/users/{user_id}/role")
async def admin_update_user_role(
    request: Request,
    user_id: str,
    request_body: UpdateRoleRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Update an SSO user's role within the tenant."""
    _require_admin_role_for_browser_session(request)

    if tenant.plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail="SSO user management requires an Enterprise plan.",
        )

    store = get_user_store()

    # Verify the user belongs to this tenant
    user = store.get_by_id(user_id)
    if not user or user.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="User not found.")

    try:
        updated = store.update_role(user_id, request_body.role)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not updated:
        raise HTTPException(status_code=404, detail="User not found.")

    logger.info(
        "user_role_updated",
        extra={
            "tenant_id": tenant.id,
            "user_id": user_id,
            "new_role": request_body.role,
        },
    )

    return _user_to_response(updated)
