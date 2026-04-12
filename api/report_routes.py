"""FastAPI routes for PDF-ready report generation.

All endpoints return HTML with print-optimized CSS. The HTML can be
rendered directly in a browser iframe, printed via window.print(),
or saved as PDF using the browser's built-in Save-as-PDF feature.
"""


from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from api.auth import Tenant, require_tenant
from api.branding import get_branding_store
from api.reports import get_report_generator

router = APIRouter()


def _get_branding(tenant_id: str) -> dict:
    """Load branding config for a tenant as a plain dict."""
    store = get_branding_store()
    config = store.get(tenant_id)
    return config.to_public_dict()


# ---------------------------------------------------------------------------
# 1. Meeting Summary Report
# ---------------------------------------------------------------------------

@router.get("/api/v1/reports/meeting/{clip_id}", response_class=HTMLResponse)
def meeting_report(
    clip_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Generate a printable meeting summary report for a single clip.

    Returns HTML with @media print CSS optimized for Save-as-PDF.
    """
    generator = get_report_generator()
    branding = _get_branding(tenant.id)
    html = generator.meeting_summary(clip_id, branding)
    return HTMLResponse(content=html, media_type="text/html")


# ---------------------------------------------------------------------------
# 2. Council Member Report Card
# ---------------------------------------------------------------------------

@router.get("/api/v1/reports/member/{name}", response_class=HTMLResponse)
def member_report(
    name: str,
    start: str = Query("", description="Start date (YYYY-MM-DD)"),
    end: str = Query("", description="End date (YYYY-MM-DD)"),
    tenant: Tenant = Depends(require_tenant),
):
    """Generate a council member voting record and activity report card.

    Returns HTML with attendance stats, voting record, and financial positions.
    """
    generator = get_report_generator()
    branding = _get_branding(tenant.id)
    html = generator.member_report_card(name, branding, start=start, end=end)
    return HTMLResponse(content=html, media_type="text/html")


# ---------------------------------------------------------------------------
# 3. Financial Summary Report
# ---------------------------------------------------------------------------

@router.get("/api/v1/reports/financial", response_class=HTMLResponse)
def financial_report(
    start: str = Query("", description="Start date (YYYY-MM-DD)"),
    end: str = Query("", description="End date (YYYY-MM-DD)"),
    tenant: Tenant = Depends(require_tenant),
):
    """Generate a financial summary report for a date range.

    Returns HTML with totals by category and full item listing.
    """
    generator = get_report_generator()
    branding = _get_branding(tenant.id)
    html = generator.financial_summary(branding, start=start, end=end)
    return HTMLResponse(content=html, media_type="text/html")


# ---------------------------------------------------------------------------
# 4. Policy Tracking Report
# ---------------------------------------------------------------------------

@router.get("/api/v1/reports/tracking", response_class=HTMLResponse)
def tracking_report(
    alert_ids: str = Query("", description="Comma-separated alert IDs"),
    tenant: Tenant = Depends(require_tenant),
):
    """Generate a policy tracking report for specified alerts.

    Returns HTML with alert match details, vote outcomes, and context.
    """
    if not alert_ids.strip():
        raise HTTPException(status_code=400, detail="alert_ids parameter is required")

    ids = [aid.strip() for aid in alert_ids.split(",") if aid.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="No valid alert IDs provided")

    generator = get_report_generator()
    branding = _get_branding(tenant.id)
    html = generator.policy_tracking(ids, branding)
    return HTMLResponse(content=html, media_type="text/html")


# ---------------------------------------------------------------------------
# 5. Period Digest Report
# ---------------------------------------------------------------------------

@router.get("/api/v1/reports/digest", response_class=HTMLResponse)
def digest_report(
    period: str = Query("monthly", description="Period type: monthly or quarterly"),
    month: str = Query("", description="Period start month (YYYY-MM)"),
    tenant: Tenant = Depends(require_tenant),
):
    """Generate a monthly or quarterly digest report.

    Returns HTML with meeting summaries, vote and financial aggregates,
    top topics, and meeting body breakdown.
    """
    if period not in ("monthly", "quarterly"):
        raise HTTPException(status_code=400, detail="period must be 'monthly' or 'quarterly'")

    if not month:
        raise HTTPException(status_code=400, detail="month parameter is required (YYYY-MM)")

    # Validate format
    parts = month.split("-")
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")
    try:
        int(parts[0])
        m = int(parts[1])
        if m < 1 or m > 12:
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=400, detail="month must be a valid YYYY-MM value")

    generator = get_report_generator()
    branding = _get_branding(tenant.id)
    html = generator.period_digest(period, month, branding)
    return HTMLResponse(content=html, media_type="text/html")
