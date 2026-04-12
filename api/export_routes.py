"""FastAPI routes for data exports and FOIA requests."""

import logging
import os
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from openai import OpenAI
from pydantic import BaseModel, field_validator

from api.auth import Tenant, require_tenant
from api.export import (
    ExportFormat,
    ExportStatus,
    get_export_manager,
)
from api.ingest import get_chroma_collection
from api.query import load_clip_metadata

logger = logging.getLogger(__name__)

router = APIRouter(tags=["export"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class StartExportRequest(BaseModel):
    format: str = "json"
    filters: Optional[dict] = None

    @field_validator("format")
    @classmethod
    def format_must_be_valid(cls, v: str) -> str:
        valid = {e.value for e in ExportFormat}
        if v not in valid:
            raise ValueError(f"Invalid format. Choose from: {', '.join(sorted(valid))}")
        return v


class FOIARequest(BaseModel):
    query: str

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be empty")
        if len(v) > 5000:
            raise ValueError("query must be under 5000 characters")
        return v


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _job_to_dict(job) -> dict:
    """Convert an ExportJob or FOIARequest dataclass to a JSON-safe dict."""
    return asdict(job)


# ---------------------------------------------------------------------------
# Export routes
# ---------------------------------------------------------------------------

@router.post("/api/v1/export")
def start_export(
    request: StartExportRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Start an async data export job. Poll GET /api/v1/export/{job_id} for status."""
    manager = get_export_manager()
    job = manager.start_export(
        tenant_id=tenant.id,
        fmt=request.format,
        filters=request.filters,
    )
    logger.info(
        "export_started",
        extra={"tenant_id": tenant.id, "job_id": job.job_id, "format": request.format},
    )
    return _job_to_dict(job)


@router.get("/api/v1/export/history")
def export_history(
    limit: int = Query(50, ge=1, le=200),
    tenant: Tenant = Depends(require_tenant),
):
    """List past export jobs for the authenticated tenant."""
    manager = get_export_manager()
    jobs = manager.list_exports(tenant.id, limit=limit)
    return {"exports": [_job_to_dict(j) for j in jobs]}


@router.get("/api/v1/export/{job_id}")
def get_export_status(
    job_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Check the status of an export job."""
    manager = get_export_manager()
    job = manager.get_export_status(job_id, tenant.id)
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found.")
    return _job_to_dict(job)


@router.get("/api/v1/export/{job_id}/download")
def download_export(
    job_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Download a completed export file (ZIP archive)."""
    manager = get_export_manager()
    job = manager.get_export_status(job_id, tenant.id)
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found.")

    if job.status != ExportStatus.COMPLETED.value:
        raise HTTPException(
            status_code=409,
            detail=f"Export is not ready. Current status: {job.status}",
        )

    file_path = manager.get_export_file_path(job_id, tenant.id)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Export file not found on disk.")

    filename = f"civiclens_export_{job_id[:8]}.zip"
    return FileResponse(
        path=file_path,
        media_type="application/zip",
        filename=filename,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# FOIA routes
# ---------------------------------------------------------------------------

@router.post("/api/v1/foia")
def submit_foia(
    request: FOIARequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Submit a FOIA search request. Uses RAG to find all relevant meeting segments."""
    output_dir = os.environ.get("MEETINGS_OUTPUT_DIR", "./meetings_output")

    manager = get_export_manager()
    collection = get_chroma_collection(output_dir)
    openai_client = OpenAI()
    clip_metadata = load_clip_metadata(output_dir)

    foia_req = manager.start_foia_request(
        tenant_id=tenant.id,
        query=request.query,
        collection=collection,
        openai_client=openai_client,
        clip_metadata=clip_metadata,
    )

    logger.info(
        "foia_submitted",
        extra={
            "tenant_id": tenant.id,
            "request_id": foia_req.request_id,
            "query_length": len(request.query),
        },
    )
    return _job_to_dict(foia_req)


@router.get("/api/v1/foia/{request_id}")
def get_foia_status(
    request_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Get the status and results of a FOIA search request."""
    manager = get_export_manager()
    foia_req = manager.get_foia_status(request_id, tenant.id)
    if not foia_req:
        raise HTTPException(status_code=404, detail="FOIA request not found.")
    return _job_to_dict(foia_req)
