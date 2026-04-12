"""Data export and FOIA compliance system for CivicLens SaaS.

Provides:
- Full and filtered data exports (ZIP archives with JSON/CSV)
- FOIA request handler using RAG to find relevant meeting segments
- Async export jobs with status polling
- Export history per tenant (SQLite)
"""

import csv
import io
import json
import logging
import os
import sqlite3
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


def _coerce_token_count(value) -> int | None:
    """Convert a usage field to int only when it is actually numeric."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _usage_value(usage, *names: str) -> int:
    """Read the first available integer token field from a usage object."""
    if usage is None:
        return 0
    for name in names:
        value = _coerce_token_count(getattr(usage, name, None))
        if value is not None:
            return value
    return 0


def _record_embedding_cost(
    tenant_id: str | None,
    response,
    *,
    operation: str,
    request_id: str | None = None,
) -> None:
    """Best-effort embedding cost tracking; never raises."""
    if not tenant_id or response is None:
        return

    from api.ingest import EMBEDDING_MODEL

    usage = getattr(response, "usage", None)
    tokens = _usage_value(usage, "total_tokens", "prompt_tokens", "input_tokens")
    if tokens <= 0:
        return

    try:
        from api.cost import get_cost_tracker

        get_cost_tracker().record_embedding(
            tenant_id=tenant_id,
            model=EMBEDDING_MODEL,
            tokens=tokens,
            request_id=request_id,
            operation=operation,
        )
    except Exception:
        logger.debug("cost tracking failed for export embedding call", exc_info=True)


def _record_llm_cost(
    tenant_id: str | None,
    usage,
    *,
    model: str,
    operation: str,
    request_id: str | None = None,
) -> None:
    """Best-effort completion cost tracking; never raises."""
    if not tenant_id or usage is None:
        return

    input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
    output_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
    if input_tokens <= 0 and output_tokens <= 0:
        return

    try:
        from api.cost import get_cost_tracker

        get_cost_tracker().record_llm_call(
            tenant_id=tenant_id,
            model=model,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=request_id,
            module="export",
        )
    except Exception:
        logger.debug("cost tracking failed for export llm call", exc_info=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CONCURRENT_EXPORTS = 4
FOIA_TOP_K = 30  # retrieve more chunks for comprehensive FOIA searches


# ---------------------------------------------------------------------------
# Enums and data models
# ---------------------------------------------------------------------------

class ExportFormat(str, Enum):
    JSON = "json"
    CSV = "csv"


class ExportStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FOIAStatus(str, Enum):
    PENDING = "pending"
    SEARCHING = "searching"
    COMPILING = "compiling"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ExportJob:
    job_id: str
    tenant_id: str
    status: str
    format: str
    filters: dict
    created_at: str
    completed_at: Optional[str] = None
    file_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    total_meetings: Optional[int] = None
    error: Optional[str] = None


@dataclass
class FOIARequest:
    request_id: str
    tenant_id: str
    query: str
    status: str
    created_at: str
    completed_at: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Export history store (SQLite)
# ---------------------------------------------------------------------------

_EXPORT_TABLES = """
CREATE TABLE IF NOT EXISTS export_jobs (
    job_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    format TEXT NOT NULL DEFAULT 'json',
    filters TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    file_path TEXT,
    file_size_bytes INTEGER,
    total_meetings INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS foia_requests (
    request_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    query TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    result TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_export_jobs_tenant ON export_jobs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_foia_requests_tenant ON foia_requests(tenant_id);
"""


class ExportStore:
    """SQLite store for export job and FOIA request history."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_EXPORT_TABLES)
        self._conn.commit()
        self._lock = threading.Lock()

    # -- Export jobs ----------------------------------------------------------

    def create_export_job(self, tenant_id: str, fmt: str, filters: dict) -> ExportJob:
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO export_jobs (job_id, tenant_id, status, format, filters, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, tenant_id, ExportStatus.PENDING.value, fmt, json.dumps(filters), now),
            )
            self._conn.commit()
        return ExportJob(
            job_id=job_id, tenant_id=tenant_id, status=ExportStatus.PENDING.value,
            format=fmt, filters=filters, created_at=now,
        )

    def update_export_job(self, job_id: str, **kwargs):
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(job_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE export_jobs SET {', '.join(sets)} WHERE job_id = ?", vals
            )
            self._conn.commit()

    def get_export_job(self, job_id: str, tenant_id: str) -> Optional[ExportJob]:
        row = self._conn.execute(
            "SELECT * FROM export_jobs WHERE job_id = ? AND tenant_id = ?",
            (job_id, tenant_id),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["filters"] = json.loads(d["filters"])
        return ExportJob(**d)

    def list_export_jobs(self, tenant_id: str, limit: int = 50) -> list[ExportJob]:
        rows = self._conn.execute(
            "SELECT * FROM export_jobs WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
            (tenant_id, limit),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["filters"] = json.loads(d["filters"])
            results.append(ExportJob(**d))
        return results

    # -- FOIA requests -------------------------------------------------------

    def create_foia_request(self, tenant_id: str, query: str) -> FOIARequest:
        request_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO foia_requests (request_id, tenant_id, query, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (request_id, tenant_id, query, FOIAStatus.PENDING.value, now),
            )
            self._conn.commit()
        return FOIARequest(
            request_id=request_id, tenant_id=tenant_id, query=query,
            status=FOIAStatus.PENDING.value, created_at=now,
        )

    def update_foia_request(self, request_id: str, **kwargs):
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k == "result" and isinstance(v, dict):
                v = json.dumps(v)
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(request_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE foia_requests SET {', '.join(sets)} WHERE request_id = ?", vals
            )
            self._conn.commit()

    def get_foia_request(self, request_id: str, tenant_id: str) -> Optional[FOIARequest]:
        row = self._conn.execute(
            "SELECT * FROM foia_requests WHERE request_id = ? AND tenant_id = ?",
            (request_id, tenant_id),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("result"):
            d["result"] = json.loads(d["result"])
        return FOIARequest(**d)

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# ExportManager
# ---------------------------------------------------------------------------

class ExportManager:
    """Handles data exports and FOIA requests for a tenant.

    Uses a thread pool for async export processing. Large exports run in
    background threads and clients poll for completion via job_id.
    """

    def __init__(self, output_dir: str, store: ExportStore):
        self._output_dir = output_dir
        self._store = store
        self._exports_dir = os.path.join(output_dir, "exports")
        os.makedirs(self._exports_dir, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_EXPORTS)

    @property
    def store(self) -> ExportStore:
        return self._store

    # -- Meeting data loading ------------------------------------------------

    def _load_all_meetings(self, tenant_id: str) -> list[dict]:
        """Load all meeting metadata from clips directory."""
        clips_dir = os.path.join(self._output_dir, "clips")
        if not os.path.isdir(clips_dir):
            return []

        meetings = []
        for name in sorted(os.listdir(clips_dir)):
            meta_path = os.path.join(clips_dir, name, "metadata.json")
            if not os.path.exists(meta_path):
                continue
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                # In multi-tenant mode, filter by tenant_id if stored in metadata
                # For now, all clips in the output_dir belong to the tenant
                meetings.append(meta)
            except (json.JSONDecodeError, KeyError):
                continue
        return meetings

    def _load_clip_files(self, clip_id: int) -> dict:
        """Load all text content for a clip (transcript, summary, facts, etc.)."""
        clip_dir = os.path.join(self._output_dir, "clips", str(clip_id))
        meta_path = os.path.join(clip_dir, "metadata.json")
        if not os.path.exists(meta_path):
            return {}

        with open(meta_path) as f:
            meta = json.load(f)

        result = {"metadata": meta}
        files = meta.get("files", {})

        # Load text files
        for key in ("summary_txt", "agenda_txt", "minutes_txt"):
            fname = files.get(key)
            if fname:
                fpath = os.path.join(clip_dir, fname)
                if os.path.exists(fpath):
                    with open(fpath) as f:
                        result[key] = f.read()

        # Load transcript
        transcript_file = files.get("transcript")
        if transcript_file:
            tpath = os.path.join(clip_dir, transcript_file)
            if os.path.exists(tpath):
                with open(tpath) as f:
                    result["transcript"] = f.read()

        # Load transcript segments
        segments_file = files.get("transcript_segments")
        if segments_file:
            spath = os.path.join(clip_dir, segments_file)
            if os.path.exists(spath):
                with open(spath) as f:
                    result["transcript_segments"] = json.load(f)

        # Load extracted facts
        facts_file = files.get("extracted_facts")
        if facts_file:
            fpath = os.path.join(clip_dir, facts_file)
            if os.path.exists(fpath):
                with open(fpath) as f:
                    result["extracted_facts"] = json.load(f)

        return result

    def _apply_filters(self, meetings: list[dict], filters: dict) -> list[dict]:
        """Filter meetings by date range, meeting body, keyword."""
        filtered = meetings

        if filters.get("date_after"):
            filtered = [m for m in filtered if m.get("date", "") >= filters["date_after"]]

        if filters.get("date_before"):
            filtered = [m for m in filtered if m.get("date", "") <= filters["date_before"]]

        if filters.get("meeting_body"):
            body = filters["meeting_body"].lower()
            filtered = [
                m for m in filtered
                if body in (m.get("meeting_body") or "").lower()
            ]

        if filters.get("keyword"):
            keyword = filters["keyword"].lower()
            filtered = [
                m for m in filtered
                if keyword in (m.get("title") or "").lower()
                or keyword in json.dumps(m.get("topics", [])).lower()
            ]

        return filtered

    # -- Export execution (runs in background thread) ------------------------

    def _execute_export(self, job: ExportJob):
        """Run the export in a background thread."""
        try:
            self._store.update_export_job(job.job_id, status=ExportStatus.RUNNING.value)

            meetings = self._load_all_meetings(job.tenant_id)
            meetings = self._apply_filters(meetings, job.filters)

            if job.format == ExportFormat.JSON.value:
                file_path = self._build_json_export(job.job_id, job.tenant_id, meetings)
            elif job.format == ExportFormat.CSV.value:
                file_path = self._build_csv_export(job.job_id, job.tenant_id, meetings)
            else:
                raise ValueError(f"Unsupported format: {job.format}")

            file_size = os.path.getsize(file_path)
            self._store.update_export_job(
                job.job_id,
                status=ExportStatus.COMPLETED.value,
                completed_at=datetime.now(timezone.utc).isoformat(),
                file_path=file_path,
                file_size_bytes=file_size,
                total_meetings=len(meetings),
            )
            logger.info("export_completed", extra={
                "job_id": job.job_id, "tenant_id": job.tenant_id,
                "meetings": len(meetings), "size_bytes": file_size,
            })

        except Exception as e:
            logger.error("export_failed", extra={
                "job_id": job.job_id, "error": str(e),
            }, exc_info=True)
            self._store.update_export_job(
                job.job_id,
                status=ExportStatus.FAILED.value,
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=str(e),
            )

    def _build_json_export(self, job_id: str, tenant_id: str, meetings: list[dict]) -> str:
        """Build a ZIP containing JSON files for each meeting plus a manifest."""
        zip_path = os.path.join(self._exports_dir, f"{job_id}.zip")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            manifest = {
                "export_id": job_id,
                "tenant_id": tenant_id,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "total_meetings": len(meetings),
                "format": "json",
                "meetings": [],
            }

            for meta in meetings:
                clip_id = meta.get("clip_id")
                clip_data = self._load_clip_files(clip_id)
                if not clip_data:
                    continue

                # Package all data for this meeting
                meeting_export = {
                    "metadata": clip_data.get("metadata", {}),
                    "summary": clip_data.get("summary_txt", ""),
                    "transcript": clip_data.get("transcript", ""),
                    "extracted_facts": clip_data.get("extracted_facts", {}),
                    "agenda": clip_data.get("agenda_txt", ""),
                    "minutes": clip_data.get("minutes_txt", ""),
                }

                prefix = f"meetings/{clip_id}"
                zf.writestr(
                    f"{prefix}/meeting.json",
                    json.dumps(meeting_export, indent=2, default=str),
                )

                # Include transcript segments separately for completeness
                if "transcript_segments" in clip_data:
                    zf.writestr(
                        f"{prefix}/transcript_segments.json",
                        json.dumps(clip_data["transcript_segments"], indent=2),
                    )

                manifest["meetings"].append({
                    "clip_id": clip_id,
                    "date": meta.get("date", ""),
                    "title": meta.get("title", ""),
                    "meeting_body": meta.get("meeting_body", ""),
                })

            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        return zip_path

    def _build_csv_export(self, job_id: str, tenant_id: str, meetings: list[dict]) -> str:
        """Build a ZIP containing CSV files for meetings, votes, financial items, etc."""
        zip_path = os.path.join(self._exports_dir, f"{job_id}.zip")

        meetings_rows = []
        votes_rows = []
        financial_rows = []
        public_comments_rows = []
        agenda_items_rows = []

        for meta in meetings:
            clip_id = meta.get("clip_id")
            clip_data = self._load_clip_files(clip_id)
            if not clip_data:
                continue

            meetings_rows.append({
                "clip_id": clip_id,
                "date": meta.get("date", ""),
                "title": meta.get("title", ""),
                "meeting_body": meta.get("meeting_body", ""),
                "topics": "; ".join(meta.get("topics", [])),
                "transcript_words": meta.get("transcript_words", ""),
            })

            facts = clip_data.get("extracted_facts", {})
            if not facts:
                continue

            for vote in facts.get("motions_and_votes", []):
                votes_rows.append({
                    "clip_id": clip_id,
                    "date": meta.get("date", ""),
                    "meeting_body": meta.get("meeting_body", ""),
                    "identifier": vote.get("identifier", ""),
                    "description": vote.get("description", ""),
                    "motion_by": vote.get("motion_by", ""),
                    "second_by": vote.get("second_by", ""),
                    "outcome": vote.get("outcome", ""),
                    "vote_type": vote.get("vote_type", ""),
                    "ayes": vote.get("ayes", ""),
                    "nays": vote.get("nays", ""),
                    "votes_for": "; ".join(vote.get("votes_for", [])),
                    "votes_against": "; ".join(vote.get("votes_against", [])),
                })

            for item in facts.get("financial_items", []):
                financial_rows.append({
                    "clip_id": clip_id,
                    "date": meta.get("date", ""),
                    "meeting_body": meta.get("meeting_body", ""),
                    "description": item.get("description", ""),
                    "amount": item.get("amount", ""),
                    "type": item.get("type", ""),
                    "identifier": item.get("identifier", ""),
                    "vendor_or_recipient": item.get("vendor_or_recipient", ""),
                })

            for comment in facts.get("public_comments", []):
                public_comments_rows.append({
                    "clip_id": clip_id,
                    "date": meta.get("date", ""),
                    "meeting_body": meta.get("meeting_body", ""),
                    "speaker": comment.get("speaker", ""),
                    "topic": comment.get("topic", ""),
                    "summary": comment.get("summary", ""),
                })

            for agenda_item in facts.get("agenda_items", []):
                agenda_items_rows.append({
                    "clip_id": clip_id,
                    "date": meta.get("date", ""),
                    "meeting_body": meta.get("meeting_body", ""),
                    "identifier": agenda_item.get("identifier", ""),
                    "title": agenda_item.get("title", ""),
                    "type": agenda_item.get("type", ""),
                    "summary": agenda_item.get("summary", ""),
                    "outcome": agenda_item.get("outcome", ""),
                })

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for filename, rows in [
                ("meetings.csv", meetings_rows),
                ("votes.csv", votes_rows),
                ("financial_items.csv", financial_rows),
                ("public_comments.csv", public_comments_rows),
                ("agenda_items.csv", agenda_items_rows),
            ]:
                if not rows:
                    continue
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
                zf.writestr(filename, buf.getvalue())

            # Add manifest
            manifest = {
                "export_id": job_id,
                "tenant_id": tenant_id,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "total_meetings": len(meetings_rows),
                "format": "csv",
                "files": [
                    name for name, rows in [
                        ("meetings.csv", meetings_rows),
                        ("votes.csv", votes_rows),
                        ("financial_items.csv", financial_rows),
                        ("public_comments.csv", public_comments_rows),
                        ("agenda_items.csv", agenda_items_rows),
                    ] if rows
                ],
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        return zip_path

    # -- Public API: start export --------------------------------------------

    def start_export(self, tenant_id: str, fmt: str = "json",
                     filters: Optional[dict] = None) -> ExportJob:
        """Start an async export job. Returns the job immediately; poll for completion."""
        filters = filters or {}
        job = self._store.create_export_job(tenant_id, fmt, filters)
        self._executor.submit(self._execute_export, job)
        return job

    def get_export_status(self, job_id: str, tenant_id: str) -> Optional[ExportJob]:
        """Get current status of an export job."""
        return self._store.get_export_job(job_id, tenant_id)

    def get_export_file_path(self, job_id: str, tenant_id: str) -> Optional[str]:
        """Get the file path for a completed export. Returns None if not ready."""
        job = self._store.get_export_job(job_id, tenant_id)
        if not job or job.status != ExportStatus.COMPLETED.value:
            return None
        return job.file_path

    def list_exports(self, tenant_id: str, limit: int = 50) -> list[ExportJob]:
        """List export history for a tenant."""
        return self._store.list_export_jobs(tenant_id, limit)

    # -- FOIA request handling -----------------------------------------------

    def start_foia_request(self, tenant_id: str, query: str,
                           collection, openai_client,
                           clip_metadata: dict) -> FOIARequest:
        """Start an async FOIA search. Uses RAG to find all relevant segments."""
        foia_req = self._store.create_foia_request(tenant_id, query)
        self._executor.submit(
            self._execute_foia, foia_req, collection, openai_client, clip_metadata,
        )
        return foia_req

    def _execute_foia(self, foia_req: FOIARequest, collection,
                      openai_client, clip_metadata: dict):
        """Run FOIA search in background thread."""
        try:
            self._store.update_foia_request(
                foia_req.request_id, status=FOIAStatus.SEARCHING.value,
            )

            # Import here to avoid circular imports
            from api.query import (
                build_chroma_filter, deduplicate_results, _fmt_timestamp,
            )
            from api.ingest import EMBEDDING_MODEL

            # 1. Embed the FOIA query
            q_response = openai_client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=[foia_req.query],
            )
            _record_embedding_cost(
                foia_req.tenant_id,
                q_response,
                operation="foia_query_embed",
                request_id=foia_req.request_id,
            )
            q_embedding = q_response.data[0].embedding

            # 2. Retrieve with high top_k for comprehensive results
            total_chunks = collection.count()
            if total_chunks == 0:
                self._store.update_foia_request(
                    foia_req.request_id,
                    status=FOIAStatus.COMPLETED.value,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    result={
                        "query": foia_req.query,
                        "findings": [],
                        "summary": "No meeting records are available in the archive.",
                        "total_relevant_meetings": 0,
                    },
                )
                return

            # Build filter scoped to tenant
            filters = {}
            if foia_req.tenant_id != "dev":
                filters["tenant_id"] = foia_req.tenant_id

            where_clause = build_chroma_filter(filters if filters else None)

            query_kwargs = {
                "query_embeddings": [q_embedding],
                "n_results": min(FOIA_TOP_K, total_chunks),
                "include": ["documents", "metadatas", "distances"],
            }
            if where_clause:
                query_kwargs["where"] = where_clause

            results = collection.query(**query_kwargs)

            # 3. Deduplicate with higher limits for FOIA thoroughness
            deduped = deduplicate_results(
                results, max_per_clip=6, max_per_source_per_clip=3,
            )

            self._store.update_foia_request(
                foia_req.request_id, status=FOIAStatus.COMPILING.value,
            )

            # 4. Group findings by meeting
            meetings_found = {}
            for doc, meta in zip(deduped["documents"], deduped["metadatas"]):
                clip_id = meta.get("clip_id")
                if clip_id not in meetings_found:
                    clip_meta = clip_metadata.get(clip_id, {})
                    meetings_found[clip_id] = {
                        "clip_id": clip_id,
                        "date": meta.get("date", ""),
                        "title": clip_meta.get("title", "Unknown Meeting"),
                        "meeting_body": meta.get("meeting_body", ""),
                        "relevant_excerpts": [],
                    }

                excerpt = {
                    "source": meta.get("source", ""),
                    "text": doc,
                }
                if "start_time" in meta:
                    excerpt["timestamp"] = _fmt_timestamp(meta["start_time"])
                meetings_found[clip_id]["relevant_excerpts"].append(excerpt)

            findings = sorted(
                meetings_found.values(),
                key=lambda m: m["date"],
                reverse=True,
            )

            # 5. Generate FOIA summary using LLM
            synthesis_chunks = []
            for doc, meta in zip(deduped["documents"], deduped["metadatas"]):
                clip_id = meta.get("clip_id")
                clip_meta = clip_metadata.get(clip_id, {})
                chunk_info = {
                    "text": doc,
                    "clip_id": clip_id,
                    "date": meta.get("date", ""),
                    "meeting_body": meta.get("meeting_body", ""),
                    "source": meta.get("source", ""),
                    "title": clip_meta.get("title", "Unknown Meeting"),
                }
                if "start_time" in meta:
                    chunk_info["start_time"] = meta["start_time"]
                synthesis_chunks.append(chunk_info)

            foia_prompt = (
                "You are compiling a response to a Freedom of Information Act (FOIA) request. "
                "Based on the provided meeting excerpts, write a comprehensive summary of ALL "
                "relevant information found. Organize by meeting date. Include specific details: "
                "vote counts, dollar amounts, names of speakers, ordinance/resolution numbers. "
                "Cite each meeting by date, body, and Clip ID. If information is incomplete, "
                "note what records are available and what may require additional search."
            )

            messages = [
                {"role": "system", "content": foia_prompt},
                {
                    "role": "user",
                    "content": (
                        f"FOIA Request: {foia_req.query}\n\n"
                        + "Meeting excerpts:\n\n"
                        + "\n\n".join(
                            f"--- {c['title']} | {c['date']} | {c['meeting_body']} | Clip {c['clip_id']} ---\n"
                            f"[Source: {c['source']}]\n{c['text']}"
                            for c in synthesis_chunks
                        )
                    ),
                },
            ]

            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
            )
            _record_llm_cost(
                foia_req.tenant_id,
                getattr(response, "usage", None),
                model="gpt-4o",
                operation="foia_synthesis",
                request_id=foia_req.request_id,
            )
            summary = response.choices[0].message.content

            # 6. Store completed result
            foia_result = {
                "query": foia_req.query,
                "summary": summary,
                "total_relevant_meetings": len(findings),
                "total_excerpts": sum(len(f["relevant_excerpts"]) for f in findings),
                "findings": findings,
                "searched_at": datetime.now(timezone.utc).isoformat(),
                "disclaimer": (
                    "This report was generated by AI-assisted search of the meeting archive. "
                    "It may not capture all relevant records. A manual review of the full "
                    "archive is recommended for legally binding FOIA responses."
                ),
            }

            self._store.update_foia_request(
                foia_req.request_id,
                status=FOIAStatus.COMPLETED.value,
                completed_at=datetime.now(timezone.utc).isoformat(),
                result=foia_result,
            )
            logger.info("foia_completed", extra={
                "request_id": foia_req.request_id,
                "tenant_id": foia_req.tenant_id,
                "meetings_found": len(findings),
            })

        except Exception as e:
            logger.error("foia_failed", extra={
                "request_id": foia_req.request_id, "error": str(e),
            }, exc_info=True)
            self._store.update_foia_request(
                foia_req.request_id,
                status=FOIAStatus.FAILED.value,
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=str(e),
            )

    def get_foia_status(self, request_id: str, tenant_id: str) -> Optional[FOIARequest]:
        """Get current status/result of a FOIA request."""
        return self._store.get_foia_request(request_id, tenant_id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_export_manager: Optional[ExportManager] = None
_export_store: Optional[ExportStore] = None


def init_export(output_dir: str) -> ExportManager:
    """Initialize the export system. Call once at startup."""
    global _export_manager, _export_store
    db_path = os.path.join(output_dir, "exports.db")
    _export_store = ExportStore(db_path)
    _export_manager = ExportManager(output_dir, _export_store)
    return _export_manager


def get_export_manager() -> ExportManager:
    if _export_manager is None:
        output_dir = os.environ.get("MEETINGS_OUTPUT_DIR", "./meetings_output")
        return init_export(output_dir)
    return _export_manager
