"""FastAPI routes for audit log search, export, and stats."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse, JSONResponse

from api.audit import get_audit_logger
from api.auth import Tenant, require_admin, require_tenant

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Tenant-facing audit endpoints (scoped to own tenant)
# ---------------------------------------------------------------------------

@router.get("/api/v1/audit")
@router.get("/api/v1/audit/logs")
def audit_search(
    action: Optional[str] = Query(None, description="Filter by action type"),
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    resource_id: Optional[str] = Query(None, description="Filter by resource ID"),
    date_after: Optional[str] = Query(None, description="ISO date lower bound"),
    date_before: Optional[str] = Query(None, description="ISO date upper bound"),
    request_id: Optional[str] = Query(None, description="Filter by request ID"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    tenant: Tenant = Depends(require_tenant),
):
    """Search the audit log for the authenticated tenant (paginated)."""
    audit = get_audit_logger()
    result = audit.search(
        tenant_id=tenant.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        date_after=date_after,
        date_before=date_before,
        request_id=request_id,
        limit=limit,
        offset=offset,
    )
    if "events" in result and "logs" not in result:
        result["logs"] = result["events"]
    return result


@router.get("/api/v1/audit/export")
def audit_export(
    format: str = Query("csv", pattern=r"^(csv|json)$"),
    action: Optional[str] = Query(None),
    date_after: Optional[str] = Query(None),
    date_before: Optional[str] = Query(None),
    tenant: Tenant = Depends(require_tenant),
):
    """Export the audit log for the authenticated tenant as CSV or JSON."""
    audit = get_audit_logger()

    # Log the export itself
    audit.log(
        tenant_id=tenant.id,
        action="export.started",
        resource_type="export",
        details={"export_type": "audit_log", "format": format},
    )

    if format == "json":
        events = audit.export_json(
            tenant_id=tenant.id,
            action=action,
            date_after=date_after,
            date_before=date_before,
        )
        return JSONResponse(
            content={"events": events, "count": len(events)},
            headers={
                "Content-Disposition": f"attachment; filename=audit_{tenant.id}.json"
            },
        )

    csv_data = audit.export_csv(
        tenant_id=tenant.id,
        action=action,
        date_after=date_after,
        date_before=date_before,
    )
    return PlainTextResponse(
        content=csv_data,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=audit_{tenant.id}.csv"
        },
    )


@router.get("/api/v1/audit/stats")
def audit_stats(
    days: int = Query(30, ge=1, le=365),
    tenant: Tenant = Depends(require_tenant),
):
    """Summary statistics for the authenticated tenant's audit events."""
    audit = get_audit_logger()
    return audit.stats(tenant_id=tenant.id, days=days)


@router.get("/api/v1/audit/verify")
def audit_verify_integrity(
    tenant: Tenant = Depends(require_tenant),
):
    """Verify the integrity hash chain for the authenticated tenant's audit events."""
    audit = get_audit_logger()
    return audit.verify_integrity(tenant_id=tenant.id)


# ---------------------------------------------------------------------------
# Admin audit endpoints (cross-tenant)
# ---------------------------------------------------------------------------

@router.get("/api/v1/admin/audit")
def admin_audit_search(
    tenant_id: Optional[str] = Query(None, description="Filter by tenant ID"),
    action: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    resource_id: Optional[str] = Query(None),
    date_after: Optional[str] = Query(None),
    date_before: Optional[str] = Query(None),
    request_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _: bool = Depends(require_admin),
):
    """Cross-tenant audit log search (admin only)."""
    audit = get_audit_logger()
    return audit.search(
        tenant_id=tenant_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        date_after=date_after,
        date_before=date_before,
        request_id=request_id,
        limit=limit,
        offset=offset,
    )


@router.get("/api/v1/admin/audit/export")
def admin_audit_export(
    format: str = Query("csv", pattern=r"^(csv|json)$"),
    tenant_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    date_after: Optional[str] = Query(None),
    date_before: Optional[str] = Query(None),
    _: bool = Depends(require_admin),
):
    """Cross-tenant audit log export (admin only)."""
    audit = get_audit_logger()

    if format == "json":
        events = audit.export_json(
            tenant_id=tenant_id,
            action=action,
            date_after=date_after,
            date_before=date_before,
        )
        return JSONResponse(
            content={"events": events, "count": len(events)},
            headers={"Content-Disposition": "attachment; filename=audit_all.json"},
        )

    csv_data = audit.export_csv(
        tenant_id=tenant_id,
        action=action,
        date_after=date_after,
        date_before=date_before,
    )
    return PlainTextResponse(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_all.csv"},
    )


@router.get("/api/v1/admin/audit/stats")
def admin_audit_stats(
    tenant_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    _: bool = Depends(require_admin),
):
    """Cross-tenant audit stats (admin only)."""
    audit = get_audit_logger()
    return audit.stats(tenant_id=tenant_id, days=days)


@router.get("/api/v1/admin/audit/verify")
def admin_audit_verify_integrity(
    tenant_id: Optional[str] = Query(None),
    _: bool = Depends(require_admin),
):
    """Verify integrity of the audit hash chain (admin only)."""
    audit = get_audit_logger()
    return audit.verify_integrity(tenant_id=tenant_id)


@router.post("/api/v1/admin/audit/purge")
def admin_audit_purge(
    _: bool = Depends(require_admin),
):
    """Purge expired audit entries according to retention policies (admin only)."""
    audit = get_audit_logger()
    deleted = audit.purge_expired()
    return {"purged_rows": deleted}


@router.put("/api/v1/admin/audit/retention/{target_tenant_id}")
def admin_set_retention(
    target_tenant_id: str,
    days: int = Query(..., ge=1, le=2555, description="Retention period in days"),
    _: bool = Depends(require_admin),
):
    """Set the audit retention policy for a specific tenant (admin only)."""
    audit = get_audit_logger()
    audit.set_retention_policy(target_tenant_id, days)
    return {
        "tenant_id": target_tenant_id,
        "retention_days": days,
    }
