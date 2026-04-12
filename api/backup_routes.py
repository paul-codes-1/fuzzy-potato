"""FastAPI routes for data backup and restore."""

import logging
import os
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from api.auth import Tenant, require_tenant
from api.backup import BackupStatus, BackupType, get_backup_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["backup"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateBackupRequest(BaseModel):
    backup_type: str = "full"
    upload_to_s3: bool = False

    @field_validator("backup_type")
    @classmethod
    def backup_type_must_be_valid(cls, v: str) -> str:
        valid = {e.value for e in BackupType}
        if v not in valid:
            raise ValueError(f"Invalid backup_type. Choose from: {', '.join(sorted(valid))}")
        return v


class RestoreBackupRequest(BaseModel):
    backup_id: str
    dry_run: bool = False

    @field_validator("backup_id")
    @classmethod
    def backup_id_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("backup_id must not be empty")
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manifest_to_dict(manifest) -> dict:
    """Convert a BackupManifest dataclass to a JSON-safe dict."""
    return asdict(manifest)


def _result_to_dict(result) -> dict:
    """Convert a BackupResult or RestoreResult to a JSON-safe dict."""
    return asdict(result)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/api/v1/backup/{tenant_id}")
def create_backup(
    tenant_id: str,
    request: CreateBackupRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Create a backup for the specified tenant.

    Requires that the authenticated tenant matches the requested tenant_id
    or that the caller has admin privileges.
    """
    if tenant.id != tenant_id and tenant.plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail="You can only create backups for your own tenant.",
        )

    manager = get_backup_manager()
    result = manager.create_backup(
        tenant_id=tenant_id,
        backup_type=request.backup_type,
        upload_to_s3=request.upload_to_s3,
    )

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error or "Backup failed")

    logger.info("backup_created_via_api", extra={
        "tenant_id": tenant_id,
        "backup_id": result.backup_id,
        "backup_type": request.backup_type,
    })

    return _result_to_dict(result)


@router.get("/api/v1/backup/{tenant_id}")
def list_backups(
    tenant_id: str,
    limit: int = Query(50, ge=1, le=200),
    tenant: Tenant = Depends(require_tenant),
):
    """List backups for the specified tenant."""
    if tenant.id != tenant_id and tenant.plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail="You can only list backups for your own tenant.",
        )

    manager = get_backup_manager()
    manifests = manager.list_backups(tenant_id=tenant_id, limit=limit)

    return {
        "tenant_id": tenant_id,
        "backups": [_manifest_to_dict(m) for m in manifests],
        "total": len(manifests),
    }


@router.post("/api/v1/backup/{tenant_id}/restore")
def restore_backup(
    tenant_id: str,
    request: RestoreBackupRequest,
    tenant: Tenant = Depends(require_tenant),
):
    """Restore a tenant from a previously created backup.

    Use dry_run=true to preview what would be restored without writing files.
    """
    if tenant.id != tenant_id and tenant.plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail="You can only restore backups for your own tenant.",
        )

    manager = get_backup_manager()

    # Look up the backup manifest to find the archive path
    manifest = manager.get_backup(request.backup_id, tenant_id=tenant_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Backup not found.")

    if manifest.status != BackupStatus.COMPLETED.value:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot restore from backup with status: {manifest.status}",
        )

    # Determine restore source (local file or S3)
    if manifest.file_path and os.path.exists(manifest.file_path):
        result = manager.restore_backup(
            backup_path=manifest.file_path,
            tenant_id=tenant_id,
            dry_run=request.dry_run,
        )
    elif manifest.s3_key:
        result = manager.restore_from_s3(
            s3_key=manifest.s3_key,
            tenant_id=tenant_id,
            dry_run=request.dry_run,
        )
    else:
        raise HTTPException(
            status_code=404,
            detail="Backup archive not found on disk or S3.",
        )

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error or "Restore failed")

    logger.info("backup_restored_via_api", extra={
        "tenant_id": tenant_id,
        "backup_id": request.backup_id,
        "dry_run": request.dry_run,
        "files_restored": result.files_restored,
    })

    return _result_to_dict(result)


@router.delete("/api/v1/backup/{tenant_id}/{backup_id}")
def delete_backup(
    tenant_id: str,
    backup_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Delete a specific backup archive and its manifest record."""
    if tenant.id != tenant_id and tenant.plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail="You can only delete backups for your own tenant.",
        )

    manager = get_backup_manager()
    deleted = manager.delete_backup(backup_id, tenant_id=tenant_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Backup not found.")

    logger.info("backup_deleted_via_api", extra={
        "tenant_id": tenant_id,
        "backup_id": backup_id,
    })

    return {"deleted": True, "backup_id": backup_id}


@router.get("/api/v1/backup/{tenant_id}/stats")
def backup_stats(
    tenant_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Get backup statistics for the specified tenant."""
    if tenant.id != tenant_id and tenant.plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail="You can only view backup stats for your own tenant.",
        )

    manager = get_backup_manager()
    stats = manager.get_backup_stats(tenant_id)

    return stats


@router.post("/api/v1/backup/{tenant_id}/cleanup")
def cleanup_backups(
    tenant_id: str,
    keep: int = Query(5, ge=1, le=100),
    tenant: Tenant = Depends(require_tenant),
):
    """Remove old backups, keeping the most recent N completed ones."""
    if tenant.id != tenant_id and tenant.plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail="You can only manage backups for your own tenant.",
        )

    manager = get_backup_manager()
    deleted_count = manager.cleanup_old_backups(tenant_id, keep=keep)

    logger.info("backup_cleanup_via_api", extra={
        "tenant_id": tenant_id,
        "kept": keep,
        "deleted": deleted_count,
    })

    return {
        "tenant_id": tenant_id,
        "kept": keep,
        "deleted": deleted_count,
    }


@router.get("/api/v1/backup/{tenant_id}/{backup_id}/download")
def download_backup(
    tenant_id: str,
    backup_id: str,
    tenant: Tenant = Depends(require_tenant),
):
    """Download a completed backup archive (ZIP)."""
    if tenant.id != tenant_id and tenant.plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail="You can only download backups for your own tenant.",
        )

    manager = get_backup_manager()
    manifest = manager.get_backup(backup_id, tenant_id=tenant_id)

    if not manifest:
        raise HTTPException(status_code=404, detail="Backup not found.")

    if manifest.status != BackupStatus.COMPLETED.value:
        raise HTTPException(
            status_code=409,
            detail=f"Backup is not ready. Current status: {manifest.status}",
        )

    if not manifest.file_path or not os.path.exists(manifest.file_path):
        raise HTTPException(status_code=404, detail="Backup file not found on disk.")

    filename = os.path.basename(manifest.file_path)
    return FileResponse(
        path=manifest.file_path,
        media_type="application/zip",
        filename=filename,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
