"""FastAPI routes for compliance and governance reporting.

Enterprise-only feature. Requires authenticated tenant with enterprise plan.
"""

import logging
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.auth import Tenant, require_tenant
from api.compliance import get_compliance_reporter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["compliance"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_enterprise(tenant: Tenant) -> Tenant:
    """Enforce enterprise-only access."""
    if tenant.plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail="Compliance reports are available on the enterprise plan only.",
        )
    return tenant


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/v1/compliance/{tenant_id}/access")
def compliance_access_report(
    tenant_id: str,
    start_date: str = Query(..., description="ISO date lower bound (e.g. 2026-01-01)"),
    end_date: str = Query(..., description="ISO date upper bound (e.g. 2026-04-09)"),
    tenant: Tenant = Depends(require_tenant),
):
    """Generate a user access report for the specified tenant.

    Shows who accessed what resources, when, and from which IP addresses.
    Enterprise plan only.
    """
    _require_enterprise(tenant)
    _verify_tenant_scope(tenant, tenant_id)

    reporter = get_compliance_reporter()
    report = reporter.generate_access_report(tenant_id, start_date, end_date)
    return asdict(report)


@router.get("/api/v1/compliance/{tenant_id}/retention")
def compliance_retention_report(
    tenant_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Generate a data retention report for the specified tenant.

    Shows what meeting data exists, its age, file sizes, and completeness.
    Enterprise plan only.
    """
    _require_enterprise(tenant)
    _verify_tenant_scope(tenant, tenant_id)

    reporter = get_compliance_reporter()
    report = reporter.generate_retention_report(tenant_id)
    return asdict(report)


@router.get("/api/v1/compliance/{tenant_id}/security")
def compliance_security_report(
    tenant_id: str,
    start_date: str = Query(..., description="ISO date lower bound"),
    end_date: str = Query(..., description="ISO date upper bound"),
    tenant: Tenant = Depends(require_tenant),
):
    """Generate a security incident report for the specified tenant.

    Identifies failed authentication attempts, rate limit violations,
    and suspicious IP addresses. Enterprise plan only.
    """
    _require_enterprise(tenant)
    _verify_tenant_scope(tenant, tenant_id)

    reporter = get_compliance_reporter()
    report = reporter.generate_security_report(tenant_id, start_date, end_date)
    return asdict(report)


@router.get("/api/v1/compliance/{tenant_id}/summary")
def compliance_summary(
    tenant_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Generate an overall compliance health summary for the specified tenant.

    Returns a 0-100 score across audit integrity, data retention, access control,
    security posture, and FOIA compliance. Includes actionable recommendations.
    Enterprise plan only.
    """
    _require_enterprise(tenant)
    _verify_tenant_scope(tenant, tenant_id)

    reporter = get_compliance_reporter()
    report = reporter.generate_compliance_summary(tenant_id)
    return asdict(report)


@router.get("/api/v1/compliance/{tenant_id}/export")
def compliance_export(
    tenant_id: str,
    report_type: str = Query(
        "summary",
        description="Report type: access, retention, security, foia, summary",
        pattern=r"^(access|retention|security|foia|summary)$",
    ),
    format: str = Query(
        "html",
        description="Export format: html or csv",
        pattern=r"^(html|csv)$",
    ),
    start_date: Optional[str] = Query(None, description="ISO date lower bound (required for access/security)"),
    end_date: Optional[str] = Query(None, description="ISO date upper bound (required for access/security)"),
    tenant: Tenant = Depends(require_tenant),
):
    """Download a full compliance report as HTML or CSV.

    Enterprise plan only.
    """
    _require_enterprise(tenant)
    _verify_tenant_scope(tenant, tenant_id)

    reporter = get_compliance_reporter()

    # Generate the requested report
    if report_type == "access":
        if not start_date or not end_date:
            raise HTTPException(
                status_code=400,
                detail="start_date and end_date are required for access reports.",
            )
        report = reporter.generate_access_report(tenant_id, start_date, end_date)
    elif report_type == "retention":
        report = reporter.generate_retention_report(tenant_id)
    elif report_type == "security":
        if not start_date or not end_date:
            raise HTTPException(
                status_code=400,
                detail="start_date and end_date are required for security reports.",
            )
        report = reporter.generate_security_report(tenant_id, start_date, end_date)
    elif report_type == "foia":
        report = reporter.generate_foia_summary(tenant_id)
    elif report_type == "summary":
        report = reporter.generate_compliance_summary(tenant_id)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown report type: {report_type}")

    content = reporter.export_report(report, fmt=format)

    if format == "html":
        media_type = "text/html"
        extension = "html"
    else:
        media_type = "text/csv"
        extension = "csv"

    filename = f"civiclens_compliance_{report_type}_{tenant_id}.{extension}"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Scope enforcement
# ---------------------------------------------------------------------------


def _verify_tenant_scope(tenant: Tenant, requested_tenant_id: str) -> None:
    """Ensure the authenticated tenant can only access its own compliance data.

    The tenant_id in the path must match the authenticated tenant's ID.
    """
    if tenant.id != requested_tenant_id:
        raise HTTPException(
            status_code=403,
            detail="You can only access compliance reports for your own tenant.",
        )
