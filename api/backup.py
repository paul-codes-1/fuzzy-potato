"""Data backup and restore system for CivicLens multi-tenant SaaS.

Provides:
- Full and incremental tenant backups (meetings data, SQLite databases, config)
- Backup to local directory or S3 (boto3 optional)
- Restore from backup archive with dry-run support
- Backup rotation (keep last N per tenant)
- Backup scheduling metadata (SQLite)
"""

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# Optional S3 support
try:
    import boto3

    _HAS_BOTO3 = True
except ImportError:
    _HAS_BOTO3 = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_KEEP_BACKUPS = 5
BACKUP_DIR_NAME = "backups"

# SQLite databases to include in backups
SQLITE_DBS = [
    "tenants.db",
    "analytics.db",
    "audit.db",
    "scheduler.db",
    "sso.db",
    "exports.db",
]

# ---------------------------------------------------------------------------
# Enums and data models
# ---------------------------------------------------------------------------


class BackupType(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class BackupStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BackupManifest:
    backup_id: str
    tenant_id: str
    backup_type: str
    status: str
    created_at: str
    completed_at: Optional[str] = None
    file_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    file_count: int = 0
    checksum: Optional[str] = None
    s3_key: Optional[str] = None
    incremental_since: Optional[str] = None
    error: Optional[str] = None


@dataclass
class BackupResult:
    success: bool
    backup_id: str
    tenant_id: str
    backup_type: str
    file_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    file_count: int = 0
    duration_seconds: float = 0.0
    s3_key: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RestoreResult:
    success: bool
    backup_id: str
    tenant_id: str
    files_restored: int = 0
    databases_restored: int = 0
    dry_run: bool = False
    warnings: list = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Backup metadata store (SQLite)
# ---------------------------------------------------------------------------

_BACKUP_TABLES = """
CREATE TABLE IF NOT EXISTS backup_manifests (
    backup_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    backup_type TEXT NOT NULL DEFAULT 'full',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    file_path TEXT,
    file_size_bytes INTEGER,
    file_count INTEGER DEFAULT 0,
    checksum TEXT,
    s3_key TEXT,
    incremental_since TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_backup_tenant ON backup_manifests(tenant_id);
CREATE INDEX IF NOT EXISTS idx_backup_tenant_created ON backup_manifests(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_backup_status ON backup_manifests(status);
"""


class BackupStore:
    """SQLite store for backup manifest history."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_BACKUP_TABLES)
        self._conn.commit()
        self._lock = threading.Lock()

    def create_manifest(
        self,
        tenant_id: str,
        backup_type: str,
        incremental_since: Optional[str] = None,
    ) -> BackupManifest:
        backup_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO backup_manifests "
                "(backup_id, tenant_id, backup_type, status, created_at, incremental_since) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (backup_id, tenant_id, backup_type, BackupStatus.PENDING.value, now, incremental_since),
            )
            self._conn.commit()
        return BackupManifest(
            backup_id=backup_id,
            tenant_id=tenant_id,
            backup_type=backup_type,
            status=BackupStatus.PENDING.value,
            created_at=now,
            incremental_since=incremental_since,
        )

    def update_manifest(self, backup_id: str, **kwargs):
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(backup_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE backup_manifests SET {', '.join(sets)} WHERE backup_id = ?",
                vals,
            )
            self._conn.commit()

    def get_manifest(self, backup_id: str, tenant_id: Optional[str] = None) -> Optional[BackupManifest]:
        if tenant_id:
            row = self._conn.execute(
                "SELECT * FROM backup_manifests WHERE backup_id = ? AND tenant_id = ?",
                (backup_id, tenant_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM backup_manifests WHERE backup_id = ?",
                (backup_id,),
            ).fetchone()
        if not row:
            return None
        return BackupManifest(**dict(row))

    def list_manifests(
        self,
        tenant_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[BackupManifest]:
        if tenant_id:
            rows = self._conn.execute(
                "SELECT * FROM backup_manifests WHERE tenant_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM backup_manifests ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [BackupManifest(**dict(row)) for row in rows]

    def delete_manifest(self, backup_id: str, tenant_id: Optional[str] = None) -> bool:
        with self._lock:
            if tenant_id:
                cur = self._conn.execute(
                    "DELETE FROM backup_manifests WHERE backup_id = ? AND tenant_id = ?",
                    (backup_id, tenant_id),
                )
            else:
                cur = self._conn.execute(
                    "DELETE FROM backup_manifests WHERE backup_id = ?",
                    (backup_id,),
                )
            self._conn.commit()
            return cur.rowcount > 0

    def get_completed_backups(
        self,
        tenant_id: str,
        limit: int = 100,
    ) -> list[BackupManifest]:
        rows = self._conn.execute(
            "SELECT * FROM backup_manifests "
            "WHERE tenant_id = ? AND status = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (tenant_id, BackupStatus.COMPLETED.value, limit),
        ).fetchall()
        return [BackupManifest(**dict(row)) for row in rows]

    def get_last_completed_backup(self, tenant_id: str) -> Optional[BackupManifest]:
        row = self._conn.execute(
            "SELECT * FROM backup_manifests "
            "WHERE tenant_id = ? AND status = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (tenant_id, BackupStatus.COMPLETED.value),
        ).fetchone()
        if not row:
            return None
        return BackupManifest(**dict(row))

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# BackupManager
# ---------------------------------------------------------------------------


class BackupManager:
    """Handles backup creation, restoration, and rotation for tenants.

    Supports local directory storage and optional S3 upload.
    """

    def __init__(
        self,
        output_dir: str,
        store: BackupStore,
        s3_bucket: Optional[str] = None,
        s3_prefix: str = "backups",
    ):
        self._output_dir = output_dir
        self._store = store
        self._backups_dir = os.path.join(output_dir, BACKUP_DIR_NAME)
        os.makedirs(self._backups_dir, exist_ok=True)
        self._s3_bucket = s3_bucket
        self._s3_prefix = s3_prefix

    @property
    def store(self) -> BackupStore:
        return self._store

    # ------------------------------------------------------------------
    # File collection helpers
    # ------------------------------------------------------------------

    def _get_tenant_clips_dir(self, tenant_id: str) -> str:
        """Get the clips directory for a tenant."""
        return os.path.join(self._output_dir, "clips")

    def _collect_meeting_files(
        self,
        tenant_id: str,
        since: Optional[str] = None,
    ) -> list[tuple[str, str]]:
        """Collect meeting files for a tenant.

        Returns list of (absolute_path, archive_relative_path) tuples.
        For incremental backups, only files modified after `since` timestamp.
        """
        clips_dir = self._get_tenant_clips_dir(tenant_id)
        if not os.path.isdir(clips_dir):
            return []

        since_ts = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
                since_ts = since_dt.timestamp()
            except (ValueError, TypeError):
                since_ts = None

        files = []
        for clip_name in sorted(os.listdir(clips_dir)):
            clip_dir = os.path.join(clips_dir, clip_name)
            if not os.path.isdir(clip_dir):
                continue

            for fname in sorted(os.listdir(clip_dir)):
                fpath = os.path.join(clip_dir, fname)
                if not os.path.isfile(fpath):
                    continue

                # For incremental: skip files not modified since last backup
                if since_ts is not None:
                    mtime = os.path.getmtime(fpath)
                    if mtime <= since_ts:
                        continue

                archive_path = f"meetings/{clip_name}/{fname}"
                files.append((fpath, archive_path))

        return files

    def _collect_database_files(self) -> list[tuple[str, str]]:
        """Collect SQLite database files for backup.

        Returns list of (absolute_path, archive_relative_path) tuples.
        """
        files = []
        for db_name in SQLITE_DBS:
            db_path = os.path.join(self._output_dir, db_name)
            if os.path.exists(db_path):
                files.append((db_path, f"databases/{db_name}"))
                # Also grab WAL and SHM files if present
                for suffix in ("-wal", "-shm"):
                    wal_path = db_path + suffix
                    if os.path.exists(wal_path):
                        files.append((wal_path, f"databases/{db_name}{suffix}"))
        return files

    def _collect_config_files(self, tenant_id: str) -> list[tuple[str, str]]:
        """Collect config and state files for backup.

        Returns list of (absolute_path, archive_relative_path) tuples.
        """
        files = []
        config_files = [
            "state.json",
            "rag_state.json",
            "index.json",
            "available_clips.json",
        ]
        for fname in config_files:
            fpath = os.path.join(self._output_dir, fname)
            if os.path.exists(fpath):
                files.append((fpath, f"config/{fname}"))
        return files

    def _compute_file_checksum(self, file_path: str) -> str:
        """Compute SHA-256 checksum of a file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    # ------------------------------------------------------------------
    # S3 helpers
    # ------------------------------------------------------------------

    def _upload_to_s3(self, local_path: str, s3_key: str) -> str:
        """Upload a file to S3. Returns the full S3 key."""
        if not _HAS_BOTO3:
            raise RuntimeError("boto3 is required for S3 backup storage. Install with: pip install boto3")
        if not self._s3_bucket:
            raise RuntimeError("S3 bucket not configured for backup storage")

        s3_client = boto3.client("s3")
        full_key = f"{self._s3_prefix}/{s3_key}" if self._s3_prefix else s3_key
        s3_client.upload_file(local_path, self._s3_bucket, full_key)
        logger.info("backup_uploaded_to_s3", extra={
            "bucket": self._s3_bucket,
            "key": full_key,
            "size_bytes": os.path.getsize(local_path),
        })
        return full_key

    def _download_from_s3(self, s3_key: str, local_path: str) -> None:
        """Download a file from S3."""
        if not _HAS_BOTO3:
            raise RuntimeError("boto3 is required for S3 backup storage. Install with: pip install boto3")
        if not self._s3_bucket:
            raise RuntimeError("S3 bucket not configured for backup storage")

        s3_client = boto3.client("s3")
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        s3_client.download_file(self._s3_bucket, s3_key, local_path)
        logger.info("backup_downloaded_from_s3", extra={
            "bucket": self._s3_bucket,
            "key": s3_key,
        })

    # ------------------------------------------------------------------
    # Backup creation
    # ------------------------------------------------------------------

    def create_backup(
        self,
        tenant_id: str,
        backup_type: str = "full",
        output_dir: Optional[str] = None,
        upload_to_s3: bool = False,
    ) -> BackupResult:
        """Create a backup archive for a tenant.

        Parameters
        ----------
        tenant_id : str
            The tenant to back up.
        backup_type : str
            "full" or "incremental". Incremental only includes files
            modified since the last completed backup.
        output_dir : str, optional
            Override the default backup output directory.
        upload_to_s3 : bool
            If True, upload the archive to S3 after local creation.

        Returns
        -------
        BackupResult
            Result with backup metadata and status.
        """
        start_time = time.time()

        # For incremental, find the last completed backup timestamp
        incremental_since = None
        if backup_type == BackupType.INCREMENTAL.value:
            last_backup = self._store.get_last_completed_backup(tenant_id)
            if last_backup and last_backup.completed_at:
                incremental_since = last_backup.completed_at
            else:
                # No prior backup -- fall back to full
                logger.info(
                    "incremental_fallback_to_full",
                    extra={"tenant_id": tenant_id, "reason": "no prior backup found"},
                )
                backup_type = BackupType.FULL.value

        manifest = self._store.create_manifest(
            tenant_id=tenant_id,
            backup_type=backup_type,
            incremental_since=incremental_since,
        )

        try:
            self._store.update_manifest(manifest.backup_id, status=BackupStatus.RUNNING.value)

            # Collect files
            meeting_files = self._collect_meeting_files(tenant_id, since=incremental_since)
            db_files = self._collect_database_files()
            config_files = self._collect_config_files(tenant_id)

            all_files = meeting_files + db_files + config_files
            file_count = len(all_files)

            # Build ZIP archive
            dest_dir = output_dir or self._backups_dir
            os.makedirs(dest_dir, exist_ok=True)

            ts_slug = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            zip_name = f"{tenant_id}_{backup_type}_{ts_slug}_{manifest.backup_id[:8]}.zip"
            zip_path = os.path.join(dest_dir, zip_name)

            # Build the manifest JSON to include inside the archive
            archive_manifest = {
                "backup_id": manifest.backup_id,
                "tenant_id": tenant_id,
                "backup_type": backup_type,
                "created_at": manifest.created_at,
                "incremental_since": incremental_since,
                "file_count": file_count,
                "files": [rel_path for _, rel_path in all_files],
            }

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                # Add manifest first
                zf.writestr("manifest.json", json.dumps(archive_manifest, indent=2))

                # Add all collected files
                for abs_path, rel_path in all_files:
                    try:
                        zf.write(abs_path, rel_path)
                    except (OSError, PermissionError) as e:
                        logger.warning(
                            "backup_file_skipped",
                            extra={"file": abs_path, "error": str(e)},
                        )
                        file_count -= 1

            file_size = os.path.getsize(zip_path)
            checksum = self._compute_file_checksum(zip_path)

            # Optional S3 upload
            s3_key = None
            if upload_to_s3 and self._s3_bucket:
                s3_key = self._upload_to_s3(zip_path, f"{tenant_id}/{zip_name}")

            duration = time.time() - start_time

            self._store.update_manifest(
                manifest.backup_id,
                status=BackupStatus.COMPLETED.value,
                completed_at=datetime.now(timezone.utc).isoformat(),
                file_path=zip_path,
                file_size_bytes=file_size,
                file_count=file_count,
                checksum=checksum,
                s3_key=s3_key,
            )

            logger.info("backup_completed", extra={
                "backup_id": manifest.backup_id,
                "tenant_id": tenant_id,
                "backup_type": backup_type,
                "file_count": file_count,
                "size_bytes": file_size,
                "duration_seconds": round(duration, 2),
            })

            return BackupResult(
                success=True,
                backup_id=manifest.backup_id,
                tenant_id=tenant_id,
                backup_type=backup_type,
                file_path=zip_path,
                file_size_bytes=file_size,
                file_count=file_count,
                duration_seconds=round(duration, 2),
                s3_key=s3_key,
            )

        except Exception as e:
            duration = time.time() - start_time
            logger.error("backup_failed", extra={
                "backup_id": manifest.backup_id,
                "tenant_id": tenant_id,
                "error": str(e),
            }, exc_info=True)

            self._store.update_manifest(
                manifest.backup_id,
                status=BackupStatus.FAILED.value,
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=str(e),
            )

            return BackupResult(
                success=False,
                backup_id=manifest.backup_id,
                tenant_id=tenant_id,
                backup_type=backup_type,
                duration_seconds=round(duration, 2),
                error=str(e),
            )

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore_backup(
        self,
        backup_path: str,
        tenant_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> RestoreResult:
        """Restore a tenant's data from a backup archive.

        Parameters
        ----------
        backup_path : str
            Path to the backup ZIP file (local or downloaded from S3).
        tenant_id : str, optional
            Override the tenant_id from the backup manifest. If None, uses
            the tenant_id stored in the archive manifest.
        dry_run : bool
            If True, list what would be restored without writing files.

        Returns
        -------
        RestoreResult
            Result with counts and any warnings.
        """
        warnings: list[str] = []

        if not os.path.exists(backup_path):
            return RestoreResult(
                success=False,
                backup_id="",
                tenant_id=tenant_id or "",
                dry_run=dry_run,
                error=f"Backup file not found: {backup_path}",
            )

        try:
            with zipfile.ZipFile(backup_path, "r") as zf:
                # Read manifest
                try:
                    manifest_data = json.loads(zf.read("manifest.json"))
                except KeyError:
                    return RestoreResult(
                        success=False,
                        backup_id="",
                        tenant_id=tenant_id or "",
                        dry_run=dry_run,
                        error="Backup archive missing manifest.json",
                    )

                backup_id = manifest_data.get("backup_id", "unknown")
                archive_tenant_id = manifest_data.get("tenant_id", "")
                effective_tenant_id = tenant_id or archive_tenant_id

                if not effective_tenant_id:
                    return RestoreResult(
                        success=False,
                        backup_id=backup_id,
                        tenant_id="",
                        dry_run=dry_run,
                        error="No tenant_id specified and none found in backup manifest",
                    )

                if tenant_id and tenant_id != archive_tenant_id:
                    warnings.append(
                        f"Restoring backup from tenant '{archive_tenant_id}' "
                        f"into tenant '{tenant_id}'"
                    )

                files_restored = 0
                databases_restored = 0

                for entry in zf.namelist():
                    if entry == "manifest.json":
                        continue

                    if entry.startswith("meetings/"):
                        dest_path = os.path.join(
                            self._output_dir, "clips", entry[len("meetings/"):]
                        )
                    elif entry.startswith("databases/"):
                        dest_path = os.path.join(
                            self._output_dir, entry[len("databases/"):]
                        )
                    elif entry.startswith("config/"):
                        dest_path = os.path.join(
                            self._output_dir, entry[len("config/"):]
                        )
                    else:
                        warnings.append(f"Unknown archive entry skipped: {entry}")
                        continue

                    if dry_run:
                        if entry.startswith("databases/"):
                            databases_restored += 1
                        else:
                            files_restored += 1
                        continue

                    # Write the file
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with zf.open(entry) as src, open(dest_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)

                    if entry.startswith("databases/"):
                        databases_restored += 1
                    else:
                        files_restored += 1

            action = "would restore" if dry_run else "restored"
            logger.info(f"backup_{action}", extra={
                "backup_id": backup_id,
                "tenant_id": effective_tenant_id,
                "files_restored": files_restored,
                "databases_restored": databases_restored,
                "dry_run": dry_run,
            })

            return RestoreResult(
                success=True,
                backup_id=backup_id,
                tenant_id=effective_tenant_id,
                files_restored=files_restored,
                databases_restored=databases_restored,
                dry_run=dry_run,
                warnings=warnings,
            )

        except zipfile.BadZipFile:
            return RestoreResult(
                success=False,
                backup_id="",
                tenant_id=tenant_id or "",
                dry_run=dry_run,
                error="Invalid or corrupted ZIP archive",
            )
        except Exception as e:
            logger.error("restore_failed", extra={
                "backup_path": backup_path,
                "error": str(e),
            }, exc_info=True)
            return RestoreResult(
                success=False,
                backup_id="",
                tenant_id=tenant_id or "",
                dry_run=dry_run,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # Restore from S3
    # ------------------------------------------------------------------

    def restore_from_s3(
        self,
        s3_key: str,
        tenant_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> RestoreResult:
        """Download a backup from S3 and restore it.

        Parameters
        ----------
        s3_key : str
            The S3 object key for the backup archive.
        tenant_id : str, optional
            Override tenant_id for the restore.
        dry_run : bool
            If True, list what would be restored without writing.

        Returns
        -------
        RestoreResult
        """
        local_path = os.path.join(self._backups_dir, f"s3_restore_{uuid.uuid4().hex[:8]}.zip")
        try:
            self._download_from_s3(s3_key, local_path)
            result = self.restore_backup(local_path, tenant_id=tenant_id, dry_run=dry_run)
            return result
        finally:
            # Clean up the temporary download
            if os.path.exists(local_path):
                os.remove(local_path)

    # ------------------------------------------------------------------
    # Listing and stats
    # ------------------------------------------------------------------

    def list_backups(
        self,
        tenant_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[BackupManifest]:
        """List backup manifests, optionally filtered by tenant."""
        return self._store.list_manifests(tenant_id=tenant_id, limit=limit)

    def get_backup(self, backup_id: str, tenant_id: Optional[str] = None) -> Optional[BackupManifest]:
        """Get a single backup manifest."""
        return self._store.get_manifest(backup_id, tenant_id=tenant_id)

    def get_backup_stats(self, tenant_id: str) -> dict:
        """Get backup statistics for a tenant.

        Returns
        -------
        dict
            Keys: total_backups, completed_backups, total_size_bytes,
            last_backup_at, last_backup_id, oldest_backup_at.
        """
        all_backups = self._store.list_manifests(tenant_id=tenant_id, limit=1000)
        completed = [b for b in all_backups if b.status == BackupStatus.COMPLETED.value]

        total_size = sum(b.file_size_bytes or 0 for b in completed)
        last = completed[0] if completed else None
        oldest = completed[-1] if completed else None

        return {
            "tenant_id": tenant_id,
            "total_backups": len(all_backups),
            "completed_backups": len(completed),
            "failed_backups": sum(1 for b in all_backups if b.status == BackupStatus.FAILED.value),
            "total_size_bytes": total_size,
            "last_backup_at": last.completed_at if last else None,
            "last_backup_id": last.backup_id if last else None,
            "oldest_backup_at": oldest.created_at if oldest else None,
        }

    # ------------------------------------------------------------------
    # Cleanup / rotation
    # ------------------------------------------------------------------

    def cleanup_old_backups(self, tenant_id: str, keep: int = DEFAULT_KEEP_BACKUPS) -> int:
        """Remove old backups, keeping the most recent `keep` completed ones.

        Parameters
        ----------
        tenant_id : str
            Tenant whose backups to rotate.
        keep : int
            Number of most recent completed backups to retain.

        Returns
        -------
        int
            Number of backups deleted.
        """
        completed = self._store.get_completed_backups(tenant_id, limit=1000)

        if len(completed) <= keep:
            return 0

        to_remove = completed[keep:]
        deleted = 0

        for backup in to_remove:
            # Remove the archive file from disk
            if backup.file_path and os.path.exists(backup.file_path):
                try:
                    os.remove(backup.file_path)
                except OSError as e:
                    logger.warning("backup_file_delete_failed", extra={
                        "backup_id": backup.backup_id,
                        "file_path": backup.file_path,
                        "error": str(e),
                    })

            # Remove from S3 if applicable
            if backup.s3_key and _HAS_BOTO3 and self._s3_bucket:
                try:
                    s3_client = boto3.client("s3")
                    s3_client.delete_object(Bucket=self._s3_bucket, Key=backup.s3_key)
                except Exception as e:
                    logger.warning("backup_s3_delete_failed", extra={
                        "backup_id": backup.backup_id,
                        "s3_key": backup.s3_key,
                        "error": str(e),
                    })

            # Remove manifest record
            self._store.delete_manifest(backup.backup_id)
            deleted += 1

        logger.info("backup_cleanup_completed", extra={
            "tenant_id": tenant_id,
            "kept": keep,
            "deleted": deleted,
        })

        return deleted

    def delete_backup(self, backup_id: str, tenant_id: str) -> bool:
        """Delete a specific backup by ID.

        Returns True if the backup was found and deleted.
        """
        manifest = self._store.get_manifest(backup_id, tenant_id=tenant_id)
        if not manifest:
            return False

        # Remove file from disk
        if manifest.file_path and os.path.exists(manifest.file_path):
            try:
                os.remove(manifest.file_path)
            except OSError as e:
                logger.warning("backup_file_delete_failed", extra={
                    "backup_id": backup_id,
                    "error": str(e),
                })

        # Remove from S3
        if manifest.s3_key and _HAS_BOTO3 and self._s3_bucket:
            try:
                s3_client = boto3.client("s3")
                s3_client.delete_object(Bucket=self._s3_bucket, Key=manifest.s3_key)
            except Exception:
                pass

        return self._store.delete_manifest(backup_id, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_backup_manager: Optional[BackupManager] = None
_backup_store: Optional[BackupStore] = None


def init_backup(
    output_dir: str,
    s3_bucket: Optional[str] = None,
    s3_prefix: str = "backups",
) -> BackupManager:
    """Initialize the backup system. Call once at startup."""
    global _backup_manager, _backup_store
    db_path = os.path.join(output_dir, "backups.db")
    _backup_store = BackupStore(db_path)
    _backup_manager = BackupManager(
        output_dir=output_dir,
        store=_backup_store,
        s3_bucket=s3_bucket,
        s3_prefix=s3_prefix,
    )
    logger.info("backup_manager_initialized", extra={
        "output_dir": output_dir,
        "s3_bucket": s3_bucket or "(none)",
    })
    return _backup_manager


def get_backup_manager() -> BackupManager:
    """Get the global backup manager singleton."""
    if _backup_manager is None:
        raise RuntimeError("Backup system not initialized -- call init_backup() first")
    return _backup_manager
