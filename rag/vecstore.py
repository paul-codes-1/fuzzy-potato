"""Vector-store abstraction for the RAG layer: ChromaDB or sqlite-vec.

Two backends behind one small API, selected by the ``VECTOR_BACKEND`` env
var (default ``"chroma"`` — prod is unaffected until cutover):

- ``chroma`` — the historical backend. Delegates to the existing
  ChromaDB collection (``rag.ingest.get_chroma_collection``), an in-RAM
  HNSW index persisted under ``lfucg_output/chroma_db/``. Its RSS tracks
  archive growth (the 2026-07 OOM freezes — see RAG_CAPACITY_PLAN.md).
- ``sqlite`` — disk-first sqlite-vec (vec0 virtual tables) in a single
  file at ``${LFUCG_OUTPUT_DIR}/vec.db``. Matches the search.db ops
  posture: a file, no daemon, ~flat RSS. Rebuilt from ``clips/`` via
  ``scripts/rebuild_vec_db.py``.

Embedding dimensions come from ``RAG_EMBED_DIMS`` (default 1536; the
sqlite rebuild uses 512 via text-embedding-3-small matryoshka
truncation). The sqlite backend stamps its dims into the DB and refuses
to open a DB built at different dims — flip the env and the DB together.

Distance semantics / the RAG_MAX_DISTANCE=0.75 gate
---------------------------------------------------
Both backends return **cosine distance = 1 − cosine similarity**. Chroma
(``hnsw:space=cosine``) and sqlite-vec (``distance_metric=cosine``)
produce numerically identical distances for identical vectors (verified
to float32 precision), so ``rag.query``'s ``RAG_MAX_DISTANCE`` gate —
calibrated 2026-06-11 on Chroma cosine distances at 1536 dims — carries
over unchanged for a same-dims backend swap. Chroma's HNSW is
approximate; vec0 KNN is exact brute-force, so recall is equal or
better on identical data.

⚠️ Re-embedding at reduced dims (``RAG_EMBED_DIMS=512``) is a different
story: truncated+renormalized matryoshka embeddings shift the whole
cosine-distance distribution upward (less angular resolution), for
relevant and irrelevant pairs alike. The 0.75 gate was NOT calibrated at
512 dims — after a 512-dim rebuild, re-run the grounding checks
(tests/test_query.py + the 2026-06-11 eval questions) and re-tune
``RAG_MAX_DISTANCE`` via env if the no-coverage rate spikes. The gate is
env-tunable without a code change; this calibration note must travel
with any future move of that constant.

Filter semantics
----------------
``query()`` accepts equality filters (``meeting_body``, plus any other
metadata key) and ``date_after`` / ``date_before`` (inclusive, ISO
YYYY-MM-DD strings). Dates behave per ``_date_in_range``: chunks with an
empty/missing date are EXCLUDED whenever any date bound is set.

- chroma: equality filters go into the Chroma ``where`` clause; dates
  are post-filtered in Python (this ChromaDB version rejects string
  $gte/$lte), so a date-filtered query can return FEWER than ``k`` hits.
  Callers that need ``k`` survivors over-fetch — ``rag.query`` keeps its
  legacy ×4 multiplier when dates are present.
- sqlite: dates and the indexed equality fields (``clip_id``,
  ``meeting_body``, ``source``) are applied in SQL inside the KNN, so
  the top-``k`` returned are all filter-valid (a strict-superset recall
  vs the chroma path). Non-indexed equality keys are post-filtered on
  the metadata JSON.
"""

from __future__ import annotations

import json
import logging
import os
import re
import struct
import threading
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

DEFAULT_EMBED_DIMS = 1536
VEC_DB_FILENAME = "vec.db"

# Canonical shape for date_after/date_before filter values. Malformed dates
# DIVERGE between backends (chroma's string post-filter drops everything;
# sqlite's _date_to_int coerces to 0 and would silently no-op the bound), so
# the request surfaces (rag/server.py models, rag/mcp_server.py tool impls)
# reject anything that doesn't match BEFORE a filter reaches a store.
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Metadata keys with their own filterable columns in the sqlite backend.
# Everything else lives only in the metadata JSON blob.
_INDEXED_KEYS = ("clip_id", "meeting_body", "source")


def embedding_dims() -> int:
    """Resolve RAG_EMBED_DIMS at CALL time (same .env-after-import pattern
    as rag.query._synthesis_model)."""
    return int(os.getenv("RAG_EMBED_DIMS", str(DEFAULT_EMBED_DIMS)))


def _date_in_range(date_str: str, date_after: str | None, date_before: str | None) -> bool:
    """Check if an ISO date string falls within an optional [date_after, date_before] range.

    Empty/missing dates are EXCLUDED when any range is set. String comparison is
    safe because dates are stored in ISO format (YYYY-MM-DD).
    """
    if not date_str:
        return date_after is None and date_before is None
    if date_after and date_str < date_after:
        return False
    if date_before and date_str > date_before:
        return False
    return True


def _date_to_int(date_str: str) -> int:
    """ISO YYYY-MM-DD → sortable integer YYYYMMDD (0 for empty/malformed).

    Stored in the vec0 metadata column so date-range filters run as integer
    comparisons inside the KNN (vec0 supports int range constraints; text
    range constraints are less portable across sqlite-vec versions).
    """
    if not date_str:
        return 0
    digits = str(date_str).replace("-", "")[:8]
    return int(digits) if digits.isdigit() and len(digits) == 8 else 0


def build_chroma_filter(filters: dict | None) -> dict | None:
    """Build a ChromaDB where clause from filter parameters.

    Note: date_after/date_before are NOT passed to ChromaDB (this version rejects
    string comparisons on $gte/$lte). They're applied post-retrieval in Python
    instead. Every other key is an exact-match equality condition.
    """
    if not filters:
        return None

    conditions = [
        {key: value}
        for key, value in filters.items()
        if key not in ("date_after", "date_before")
    ]

    if not conditions:
        return None

    if len(conditions) == 1:
        return conditions[0]

    return {"$and": conditions}


class VecStore:
    """Backend-agnostic chunk store. Hits are plain dicts:

    - ``query`` → ``{"id", "document", "metadata", "distance"}``
    - ``get_chunks`` → ``{"id", "document", "metadata"[, "embedding"]}``
    """

    def upsert_chunks(self, ids: Sequence[str], embeddings: Sequence[Sequence[float]],
                      documents: Sequence[str], metadatas: Sequence[dict]) -> None:
        raise NotImplementedError

    def delete_clip(self, clip_id: int) -> None:
        raise NotImplementedError

    def query(self, embedding: Sequence[float], k: int,
              filters: dict | None = None) -> list[dict]:
        raise NotImplementedError

    def get_chunks(self, filters: dict | None = None, limit: int | None = None,
                   include_embeddings: bool = False) -> list[dict]:
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        """Release any held resources (sqlite fd). Safe to call twice."""


class ChromaVecStore(VecStore):
    """Thin adapter over a ChromaDB collection — the historical behavior,
    byte-identical query semantics (equality where clause + Python-side
    date post-filter)."""

    def __init__(self, collection):
        self._collection = collection

    def upsert_chunks(self, ids, embeddings, documents, metadatas):
        self._collection.upsert(
            ids=list(ids),
            embeddings=[list(e) for e in embeddings],
            documents=list(documents),
            metadatas=list(metadatas),
        )

    def delete_clip(self, clip_id: int) -> None:
        self._collection.delete(where={"clip_id": int(clip_id)})

    def query(self, embedding, k, filters=None):
        filters = dict(filters or {})
        date_after = filters.pop("date_after", None)
        date_before = filters.pop("date_before", None)
        where = build_chroma_filter(filters)

        query_kwargs = {
            "query_embeddings": [list(embedding)],
            "n_results": max(int(k), 1),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            query_kwargs["where"] = where

        results = self._collection.query(**query_kwargs)

        hits = []
        if results["ids"] and results["ids"][0]:
            for id_, doc, meta, dist in zip(
                results["ids"][0], results["documents"][0],
                results["metadatas"][0], results["distances"][0]
            ):
                meta = meta or {}
                if (date_after or date_before) and not _date_in_range(
                    meta.get("date", ""), date_after, date_before
                ):
                    continue
                hits.append({"id": id_, "document": doc,
                             "metadata": meta, "distance": dist})
        return hits

    def get_chunks(self, filters=None, limit=None, include_embeddings=False):
        where = build_chroma_filter(filters)
        include = ["documents", "metadatas"]
        if include_embeddings:
            include.append("embeddings")
        get_kwargs = {"include": include}
        if where:
            get_kwargs["where"] = where
        if limit is not None:
            get_kwargs["limit"] = limit
        results = self._collection.get(**get_kwargs)

        ids = results.get("ids") or []
        documents = results.get("documents") or [None] * len(ids)
        metadatas = results.get("metadatas") or [None] * len(ids)
        embeddings = results.get("embeddings")
        chunks = []
        for i, id_ in enumerate(ids):
            chunk = {
                "id": id_,
                "document": documents[i],
                "metadata": metadatas[i] or {},
            }
            if include_embeddings:
                # ChromaDB returns numpy arrays — normalize to list[float]
                # so callers never trip over ambiguous array truthiness.
                chunk["embedding"] = [float(v) for v in embeddings[i]]
            chunks.append(chunk)
        return chunks

    def count(self) -> int:
        return self._collection.count()

    def stats(self) -> dict:
        total = self.count()
        # Unique clips require paginating metadatas (Chroma has no DISTINCT).
        unique_clip_ids = set()
        batch_size = 5000
        offset = 0
        while offset < total:
            results = self._collection.get(
                include=["metadatas"],
                limit=batch_size,
                offset=offset,
            )
            for meta in results["metadatas"]:
                unique_clip_ids.add((meta or {}).get("clip_id"))
            if len(results["metadatas"]) < batch_size:
                break
            offset += batch_size
        return {
            "backend": "chroma",
            "total_chunks": total,
            "unique_clips": len(unique_clip_ids),
        }


class SqliteVecStore(VecStore):
    """Disk-first backend on sqlite-vec (vec0 virtual table + a plain
    chunks table for documents/metadata). One file, no daemon, mmap'd
    reads — the search.db ops posture applied to the vector layer.

    Thread-safe under one lock (callers span the FastAPI threadpool and
    the MCP asyncio worker threads — same posture as rag.telemetry).
    """

    def __init__(self, db_path: str, dims: Optional[int] = None):
        import sqlite3

        import sqlite_vec

        self.db_path = db_path
        self._lock = threading.Lock()

        conn = sqlite3.connect(db_path, check_same_thread=False)
        if not hasattr(conn, "enable_load_extension"):
            conn.close()
            raise RuntimeError(
                "This Python's sqlite3 was built without loadable-extension "
                "support (needed for sqlite-vec). Use a CPython built with "
                "--enable-loadable-sqlite-extensions (the prod Ubuntu 3.11 "
                "and uv-managed builds have it)."
            )
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("PRAGMA journal_mode=WAL")
        # Cross-process writers exist (ledger drain / cron ingest against the
        # live vec.db while the server reads) — wait out short lock windows
        # instead of surfacing an immediate SQLITE_BUSY.
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn

        requested_dims = int(dims) if dims else embedding_dims()
        existing_dims = self._existing_dims()
        if existing_dims is None:
            self.dims = requested_dims
            self._create_schema()
        else:
            if dims and int(dims) != existing_dims:
                conn.close()
                raise ValueError(
                    f"{db_path} was built at {existing_dims} dims; "
                    f"requested {dims}. Rebuild via scripts/rebuild_vec_db.py."
                )
            self.dims = existing_dims

    # ---------- schema ----------

    def _existing_dims(self) -> Optional[int]:
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_meta'"
        ).fetchone()
        if row is None:
            return None
        dims_row = self._conn.execute(
            "SELECT value FROM vec_meta WHERE key='dims'"
        ).fetchone()
        return int(dims_row[0]) if dims_row else None

    def _create_schema(self) -> None:
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id     TEXT PRIMARY KEY,
                    clip_id      INTEGER NOT NULL,
                    source       TEXT NOT NULL DEFAULT '',
                    meeting_body TEXT NOT NULL DEFAULT '',
                    date         TEXT NOT NULL DEFAULT '',
                    document     TEXT NOT NULL DEFAULT '',
                    metadata     TEXT NOT NULL DEFAULT '{}'
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_clip ON chunks(clip_id)"
            )
            self._conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                    chunk_id     TEXT PRIMARY KEY,
                    embedding    float[{self.dims}] distance_metric=cosine,
                    clip_id      INTEGER,
                    meeting_body TEXT,
                    date_int     INTEGER,
                    source       TEXT
                )
            """)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS vec_meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO vec_meta(key, value) VALUES ('dims', ?)",
                (str(self.dims),),
            )

    # ---------- helpers ----------

    def _serialize(self, embedding: Sequence[float]) -> bytes:
        vec = [float(v) for v in embedding]
        if len(vec) != self.dims:
            raise ValueError(
                f"embedding has {len(vec)} dims; store {self.db_path} expects "
                f"{self.dims} (RAG_EMBED_DIMS mismatch?)"
            )
        return struct.pack(f"{len(vec)}f", *vec)

    @staticmethod
    def _deserialize(blob: bytes) -> list[float]:
        return list(struct.unpack(f"{len(blob) // 4}f", blob))

    @staticmethod
    def _split_filters(filters: dict | None) -> tuple[dict, dict, str | None, str | None]:
        """→ (indexed equality, extra equality, date_after, date_before)."""
        filters = dict(filters or {})
        date_after = filters.pop("date_after", None)
        date_before = filters.pop("date_before", None)
        indexed = {k: filters.pop(k) for k in list(filters) if k in _INDEXED_KEYS}
        return indexed, filters, date_after, date_before

    # ---------- VecStore API ----------

    def upsert_chunks(self, ids, embeddings, documents, metadatas):
        rows = list(zip(ids, embeddings, documents, metadatas))
        with self._lock, self._conn:
            for chunk_id, embedding, document, meta in rows:
                meta = meta or {}
                blob = self._serialize(embedding)
                # vec0 has no native upsert — delete-then-insert inside the
                # transaction gives the same semantics as Chroma's upsert.
                self._conn.execute(
                    "DELETE FROM chunks_vec WHERE chunk_id = ?", (chunk_id,))
                self._conn.execute(
                    "INSERT INTO chunks_vec(chunk_id, embedding, clip_id, "
                    "meeting_body, date_int, source) VALUES (?,?,?,?,?,?)",
                    (
                        chunk_id,
                        blob,
                        int(meta.get("clip_id", 0)),
                        str(meta.get("meeting_body") or ""),
                        _date_to_int(meta.get("date") or ""),
                        str(meta.get("source") or ""),
                    ),
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO chunks(chunk_id, clip_id, source, "
                    "meeting_body, date, document, metadata) VALUES (?,?,?,?,?,?,?)",
                    (
                        chunk_id,
                        int(meta.get("clip_id", 0)),
                        str(meta.get("source") or ""),
                        str(meta.get("meeting_body") or ""),
                        str(meta.get("date") or ""),
                        document or "",
                        json.dumps(meta),
                    ),
                )

    def delete_clip(self, clip_id: int) -> None:
        with self._lock, self._conn:
            ids = [row[0] for row in self._conn.execute(
                "SELECT chunk_id FROM chunks WHERE clip_id = ?", (int(clip_id),))]
            self._conn.executemany(
                "DELETE FROM chunks_vec WHERE chunk_id = ?", [(i,) for i in ids])
            self._conn.execute(
                "DELETE FROM chunks WHERE clip_id = ?", (int(clip_id),))

    def query(self, embedding, k, filters=None):
        indexed, extra, date_after, date_before = self._split_filters(filters)

        conds = ["embedding MATCH ?", "k = ?"]
        params: list = [self._serialize(embedding), max(int(k), 1)]
        if "clip_id" in indexed:
            conds.append("clip_id = ?")
            params.append(int(indexed["clip_id"]))
        if "meeting_body" in indexed:
            conds.append("meeting_body = ?")
            params.append(str(indexed["meeting_body"]))
        if "source" in indexed:
            conds.append("source = ?")
            params.append(str(indexed["source"]))
        if date_after:
            conds.append("date_int >= ?")
            params.append(_date_to_int(date_after))
        if date_before:
            conds.append("date_int <= ?")
            params.append(_date_to_int(date_before))
            if not date_after:
                # Empty dates (stored as 0) are excluded when any bound is
                # set — matches _date_in_range semantics.
                conds.append("date_int >= 1")

        sql = ("SELECT chunk_id, distance FROM chunks_vec WHERE "
               + " AND ".join(conds) + " ORDER BY distance")
        with self._lock:
            knn = self._conn.execute(sql, params).fetchall()
            if not knn:
                return []
            placeholders = ",".join("?" for _ in knn)
            doc_rows = self._conn.execute(
                f"SELECT chunk_id, document, metadata FROM chunks "
                f"WHERE chunk_id IN ({placeholders})",
                [row[0] for row in knn],
            ).fetchall()
        docs = {row[0]: (row[1], row[2]) for row in doc_rows}

        hits = []
        for chunk_id, distance in knn:
            document, meta_json = docs.get(chunk_id, ("", "{}"))
            meta = json.loads(meta_json)
            # Non-indexed equality keys post-filter on the metadata JSON
            # (nothing in the query path uses these today).
            if any(meta.get(key) != value for key, value in extra.items()):
                continue
            hits.append({"id": chunk_id, "document": document,
                         "metadata": meta, "distance": distance})
        return hits

    def get_chunks(self, filters=None, limit=None, include_embeddings=False):
        indexed, extra, date_after, date_before = self._split_filters(filters)
        if date_after or date_before:
            raise ValueError("get_chunks supports equality filters only")

        conds = []
        params: list = []
        for key, value in indexed.items():
            conds.append(f"{key} = ?")
            params.append(int(value) if key == "clip_id" else str(value))
        for key, value in extra.items():
            conds.append("json_extract(metadata, ?) = ?")
            params.extend([f"$.{key}", value])

        sql = "SELECT chunk_id, document, metadata FROM chunks"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            chunks = []
            for chunk_id, document, meta_json in rows:
                chunk = {"id": chunk_id, "document": document,
                         "metadata": json.loads(meta_json)}
                if include_embeddings:
                    blob_row = self._conn.execute(
                        "SELECT embedding FROM chunks_vec WHERE chunk_id = ?",
                        (chunk_id,),
                    ).fetchone()
                    chunk["embedding"] = (
                        self._deserialize(blob_row[0]) if blob_row else [])
                chunks.append(chunk)
        return chunks

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def stats(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            unique = self._conn.execute(
                "SELECT COUNT(DISTINCT clip_id) FROM chunks").fetchone()[0]
        try:
            db_bytes = os.path.getsize(self.db_path)
        except OSError:
            db_bytes = 0
        return {
            "backend": "sqlite",
            "total_chunks": total,
            "unique_clips": unique,
            "dims": self.dims,
            "db_path": self.db_path,
            "db_bytes": db_bytes,
        }

    def close(self) -> None:
        # Take the store lock so /admin/reload (every ~6h from cron) can't
        # close the connection under an in-flight query from another thread
        # (FastAPI threadpool / MCP anyio workers) — close waits for the
        # current statement to finish, and later calls on the stale store
        # fail cleanly instead of racing a teardown.
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover - already closed
                pass


def vec_db_path(output_dir: str) -> str:
    return os.path.join(output_dir, VEC_DB_FILENAME)


def get_vecstore(output_dir: str) -> VecStore:
    """Backend factory — reads VECTOR_BACKEND at call time (default chroma,
    so prod behavior is unchanged until the cutover flips the env)."""
    backend = os.getenv("VECTOR_BACKEND", "chroma").strip().lower()
    if backend == "chroma":
        from rag.ingest import get_chroma_collection  # lazy: avoids import cycle

        return ChromaVecStore(get_chroma_collection(output_dir))
    if backend == "sqlite":
        store = SqliteVecStore(vec_db_path(output_dir))
        # The store adopts whatever dims the on-disk vec.db was built at. If
        # that disagrees with RAG_EMBED_DIMS (what query-time embeds use), fail
        # LOUDLY here instead of letting the mismatch surface as an opaque 500
        # deep inside _serialize() on the first query. Flip VECTOR_BACKEND /
        # RAG_EMBED_DIMS together to match how vec.db was built.
        expected = embedding_dims()
        if store.dims != expected:
            store.close()
            raise ValueError(
                f"vec.db at {vec_db_path(output_dir)} was built at {store.dims} "
                f"dims but RAG_EMBED_DIMS={expected}. Rebuild via "
                f"scripts/rebuild_vec_db.py or fix the env (see this module's "
                f"docstring)."
            )
        return store
    raise ValueError(f"Unknown VECTOR_BACKEND {backend!r} (expected 'chroma' or 'sqlite')")


def as_vecstore(store_or_collection) -> VecStore:
    """Coerce a raw ChromaDB collection into the store API.

    Lets callers (and the many existing tests) that still hold a bare
    Chroma collection pass it straight into the rewired rag.* functions.
    """
    if isinstance(store_or_collection, VecStore):
        return store_or_collection
    return ChromaVecStore(store_or_collection)
