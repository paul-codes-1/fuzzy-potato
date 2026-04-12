"""FastAPI routes for vote tracking and policy monitoring."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from api.auth import Tenant, require_tenant
from api.tracker import ALERT_TYPES, get_tracker

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateAlertRequest(BaseModel):
    name: str
    type: str
    config: dict

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        if len(v) > 200:
            raise ValueError("name must be under 200 characters")
        return v

    @field_validator("type")
    @classmethod
    def type_must_be_valid(cls, v: str) -> str:
        if v not in ALERT_TYPES:
            raise ValueError(f"type must be one of: {', '.join(sorted(ALERT_TYPES))}")
        return v


class UpdateAlertRequest(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Alert CRUD
# ---------------------------------------------------------------------------

@router.post("/api/v1/alerts")
def create_alert(request: CreateAlertRequest, tenant: Tenant = Depends(require_tenant)):
    tracker = get_tracker()
    alert = tracker.create_alert(
        tenant_id=tenant.id,
        name=request.name,
        alert_type=request.type,
        config=request.config,
    )
    return alert.to_dict()


@router.get("/api/v1/alerts")
def list_alerts(tenant: Tenant = Depends(require_tenant)):
    tracker = get_tracker()
    alerts = tracker.list_alerts(tenant.id)
    return {"alerts": [a.to_dict() for a in alerts]}


@router.put("/api/v1/alerts/{alert_id}")
def update_alert(alert_id: str, request: UpdateAlertRequest,
                 tenant: Tenant = Depends(require_tenant)):
    tracker = get_tracker()
    alert = tracker.update_alert(
        alert_id=alert_id,
        tenant_id=tenant.id,
        name=request.name,
        config=request.config,
        enabled=request.enabled,
    )
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert.to_dict()


@router.delete("/api/v1/alerts/{alert_id}")
def delete_alert(alert_id: str, tenant: Tenant = Depends(require_tenant)):
    tracker = get_tracker()
    if not tracker.delete_alert(alert_id, tenant.id):
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"deleted": alert_id}


@router.get("/api/v1/alerts/{alert_id}/matches")
def get_alert_matches(
    alert_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tenant: Tenant = Depends(require_tenant),
):
    tracker = get_tracker()
    alert = tracker.get_alert(alert_id, tenant.id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return tracker.get_matches(alert_id, tenant.id, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Vote search
# ---------------------------------------------------------------------------

@router.get("/api/v1/votes")
def search_votes(
    member: str = Query("", description="Filter by council member name"),
    date_after: str = Query("", description="Filter votes after this date (YYYY-MM-DD)"),
    date_before: str = Query("", description="Filter votes before this date (YYYY-MM-DD)"),
    outcome: str = Query("", description="Filter by vote outcome (passed/failed)"),
    keyword: str = Query("", description="Search vote descriptions"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tenant: Tenant = Depends(require_tenant),
):
    tracker = get_tracker()
    return tracker.search_votes(
        member=member, date_after=date_after, date_before=date_before,
        outcome=outcome, keyword=keyword, limit=limit, offset=offset,
    )


@router.get("/api/v1/votes/member/{name}")
def member_voting_record(name: str, tenant: Tenant = Depends(require_tenant)):
    tracker = get_tracker()
    return tracker.member_voting_record(name)


@router.get("/api/v1/votes/stats")
def vote_stats(tenant: Tenant = Depends(require_tenant)):
    tracker = get_tracker()
    return tracker.vote_stats()


# ---------------------------------------------------------------------------
# Financial search
# ---------------------------------------------------------------------------

@router.get("/api/v1/financial")
def search_financial(
    min_amount: Optional[float] = Query(None, description="Minimum dollar amount"),
    max_amount: Optional[float] = Query(None, description="Maximum dollar amount"),
    item_type: str = Query("", description="Filter by type (appropriation, contract, etc.)"),
    date_after: str = Query("", description="Filter after this date (YYYY-MM-DD)"),
    date_before: str = Query("", description="Filter before this date (YYYY-MM-DD)"),
    keyword: str = Query("", description="Search descriptions"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tenant: Tenant = Depends(require_tenant),
):
    tracker = get_tracker()
    min_cents = int(min_amount * 100) if min_amount is not None else None
    max_cents = int(max_amount * 100) if max_amount is not None else None
    return tracker.search_financial(
        min_amount=min_cents, max_amount=max_cents,
        item_type=item_type, date_after=date_after, date_before=date_before,
        keyword=keyword, limit=limit, offset=offset,
    )


@router.get("/api/v1/financial/summary")
def financial_summary(tenant: Tenant = Depends(require_tenant)):
    tracker = get_tracker()
    return tracker.financial_summary()
