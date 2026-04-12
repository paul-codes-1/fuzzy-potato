"""FastAPI routes for per-tenant branding configuration."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import Tenant, require_tenant
from api.branding import (
    get_branding_store,
    validate_branding_update,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["branding"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class BrandingUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    favicon_url: Optional[str] = None
    custom_css: Optional[str] = None
    welcome_message: Optional[str] = None
    footer_text: Optional[str] = None
    support_email: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/v1/branding")
def get_branding(tenant: Tenant = Depends(require_tenant)):
    """Get the tenant's branding config. Public -- frontend fetches on load."""
    store = get_branding_store()
    config = store.get(tenant.id)
    return config.to_public_dict()


@router.put("/api/v1/branding")
def update_branding(
    request: BrandingUpdateRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Update branding config. Requires Pro or Enterprise plan."""
    if tenant.plan not in ("pro", "enterprise"):
        raise HTTPException(
            status_code=403,
            detail="Branding customization requires a Pro or Enterprise plan.",
        )

    # Build dict of only the fields that were explicitly provided
    data = {k: v for k, v in request.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="No branding fields provided.")

    try:
        cleaned = validate_branding_update(data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    store = get_branding_store()
    config = store.upsert(tenant.id, cleaned)

    logger.info(
        "branding_updated",
        extra={"tenant_id": tenant.id, "fields": list(cleaned.keys())},
    )
    return config.to_public_dict()


@router.get("/api/v1/branding/preview")
def preview_branding(
    tenant: Tenant = Depends(require_tenant),
    display_name: Optional[str] = None,
    logo_url: Optional[str] = None,
    primary_color: Optional[str] = None,
    secondary_color: Optional[str] = None,
    accent_color: Optional[str] = None,
    favicon_url: Optional[str] = None,
    custom_css: Optional[str] = None,
    welcome_message: Optional[str] = None,
    footer_text: Optional[str] = None,
    support_email: Optional[str] = None,
):
    """Preview branding changes without saving. Merges query params with current config."""
    store = get_branding_store()
    current = store.get(tenant.id)
    preview = current.to_public_dict()

    overrides = {
        k: v
        for k, v in {
            "display_name": display_name,
            "logo_url": logo_url,
            "primary_color": primary_color,
            "secondary_color": secondary_color,
            "accent_color": accent_color,
            "favicon_url": favicon_url,
            "custom_css": custom_css,
            "welcome_message": welcome_message,
            "footer_text": footer_text,
            "support_email": support_email,
        }.items()
        if v is not None
    }

    if overrides:
        try:
            cleaned = validate_branding_update(overrides)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        preview.update(cleaned)

    return preview


@router.post("/api/v1/branding/reset")
def reset_branding(tenant: Tenant = Depends(require_tenant)):
    """Reset branding to CivicLens defaults."""
    if tenant.plan not in ("pro", "enterprise"):
        raise HTTPException(
            status_code=403,
            detail="Branding customization requires a Pro or Enterprise plan.",
        )

    store = get_branding_store()
    config = store.reset(tenant.id)

    logger.info("branding_reset", extra={"tenant_id": tenant.id})
    return config.to_public_dict()
