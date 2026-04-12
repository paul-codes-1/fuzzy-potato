"""SQLite-backed query response cache for /api/v1/ask.

Caches single-turn RAG responses keyed on (tenant_id, normalized question,
filters). Primary goals:

1. Cut LLM spend for repeated / popular questions within a TTL window.
2. Cut p95 latency by skipping retrieval + synthesis for exact re-asks.

Safety invariants -- these are load-bearing:

* Tenant isolation: entries are never shared across tenants. Every read
  and write is scoped by tenant_id. A question that matches verbatim in
  tenant A must miss for tenant B.
* Invalidation on new data: every successful ingest for a tenant must
  call ``invalidate_tenant`` so stale meeting sets cannot surface bad
  citations. Cache failures must never break the query path.
* TTL: default 1 hour. Any entry past its expires_at is treated as a
  miss even before ``cleanup_expired`` runs.
* Multi-turn chat is NOT cached -- conversation history would make a
  naive exact-match scheme unsafe.

Semantic caching is implemented with pure-Python cosine similarity over
the question embedding the retriever already computes, so we don't add
numpy as a dependency.
"""

from __future__ import annotations

import array
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration (env-driven)
# ---------------------------------------------------------------------------

DEFAULT_TTL_SECONDS = 3600
DEFAULT_SEMANTIC_THRESHOLD = 0.95
# Only the most recent N candidate entries are scanned per tenant+filter
# bucket during semantic lookup. Keeps pathological tenants from hosing
# latency on a long cache.
SEMANTIC_SCAN_LIMIT = 50


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Normalization + hashing
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")
# Trailing punctuation we consider cosmetic for cache-key purposes.
_TRAILING_PUNCT = ".?!,;: \t\n\r\"'"


def normalize_question(question: str) -> str:
    """Normalize a question for exact-match cache keys.

    Rules (documented because they affect hit rate):

    1. Lowercase.
    2. Strip leading/trailing whitespace.
    3. Collapse internal whitespace to a single space.
    4. Strip trailing punctuation (. ? ! , ; : and quote characters).

    These rules DO NOT stem, lemmatize, or rewrite contractions. So
    ``"What's the budget?"`` and ``"what is the budget"`` will NOT match
    as the same key -- they will hit different exact-match entries but
    the semantic cache layer can bridge them if embeddings are similar.
    """
    if question is None:
        return ""
    text = str(question).strip().lower()
    text = _WHITESPACE_RE.sub(" ", text)
    text = text.rstrip(_TRAILING_PUNCT).strip()
    return text


def hash_filters(filters: Optional[dict]) -> str:
    """Stable SHA-256 of the filters dict, or empty string for no filters.

    Sort keys so ``{"a":1,"b":2}`` and ``{"b":2,"a":1}`` collide. Filter
    values are cast through json to handle nested dicts (e.g. ChromaDB
    $and clauses). Tenant id is NOT part of this hash -- callers must
    pass tenant_id separately, so the filters hash is safe to compare
    across tenants as a "same filter shape" signal.
    """
    if not filters:
        return hashlib.sha256(b"{}").hexdigest()
    # Drop tenant_id if present; it is scoped elsewhere.
    scoped = {k: v for k, v in filters.items() if k != "tenant_id"}
    blob = json.dumps(scoped, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def compute_cache_key(tenant_id: str, question: str, filters: Optional[dict]) -> str:
    """Compute the full cache key for a (tenant, question, filters) tuple.

    Tenant id is mixed into the key so an accidental cross-tenant query
    cannot resolve even if isolation elsewhere fails.
    """
    normalized = normalize_question(question)
    f_hash = hash_filters(filters)
    raw = f"{tenant_id}\x00{normalized}\x00{f_hash}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def question_hash(question: str) -> str:
    """SHA-256 of the normalized question (without tenant/filters).

    Stored for debugging / analytics, not used for key lookup.
    """
    return hashlib.sha256(normalize_question(question).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Embedding (de)serialization + similarity
# ---------------------------------------------------------------------------

def _encode_embedding(embedding: Optional[list[float]]) -> Optional[bytes]:
    if not embedding:
        return None
    arr = array.array("f", [float(x) for x in embedding])
    return arr.tobytes()


def _decode_embedding(blob: Optional[bytes]) -> Optional[list[float]]:
    if not blob:
        return None
    arr = array.array("f")
    arr.frombytes(blob)
    return list(arr)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity.

    Returns 0.0 on zero-length or mismatched vectors. Embeddings returned
    by OpenAI's text-embedding-3-small are already unit normalized, so
    this is effectively a dot product in practice -- but we normalize
    explicitly to be safe against non-unit callers.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS cache_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    question_hash TEXT NOT NULL,
    question_text TEXT NOT NULL,
    filters_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    embedding BLOB,
    UNIQUE(tenant_id, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_cache_tenant_key
    ON cache_entries(tenant_id, cache_key);
CREATE INDEX IF NOT EXISTS idx_cache_tenant_expires
    ON cache_entries(tenant_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_cache_tenant_filters
    ON cache_entries(tenant_id, filters_hash, expires_at);
CREATE INDEX IF NOT EXISTS idx_cache_expires
    ON cache_entries(expires_at);
"""


# ---------------------------------------------------------------------------
# QueryCache
# ---------------------------------------------------------------------------


class QueryCache:
    """SQLite-backed query response cache."""

    def __init__(
        self,
        db_path: str,
        enabled: bool = True,
        default_ttl: int = DEFAULT_TTL_SECONDS,
        semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    ):
        self._db_path = db_path
        self.enabled = enabled
        self.default_ttl = int(default_ttl)
        self.semantic_threshold = float(semantic_threshold)

        # Runtime counters (process-local)
        self._hits = 0
        self._misses = 0

        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_CREATE_TABLES)
        self._conn.commit()
        # SQLite allows check_same_thread=False but the connection object
        # itself is not safe for concurrent use across threads. Serialize
        # all access with a reentrant lock; reads are cheap so the lock
        # contention cost is negligible versus a segfault.
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_exact(
        self,
        tenant_id: str,
        question: str,
        filters: Optional[dict] = None,
    ) -> Optional[dict]:
        """Return a cached response for an exact (normalized) match, or None."""
        if not self.enabled or not tenant_id:
            return None

        try:
            with self._lock:
                cache_key = compute_cache_key(tenant_id, question, filters)
                now = time.time()
                row = self._conn.execute(
                    "SELECT id, response_json, expires_at, hit_count "
                    "FROM cache_entries "
                    "WHERE tenant_id = ? AND cache_key = ?",
                    (tenant_id, cache_key),
                ).fetchone()

                if row is None:
                    self._misses += 1
                    return None

                if row["expires_at"] <= now:
                    # Expired. Treat as miss and let caller re-populate.
                    self._misses += 1
                    return None

                try:
                    response = json.loads(row["response_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    logger.warning(
                        "query_cache.malformed_response_json",
                        extra={"tenant_id": tenant_id, "entry_id": row["id"]},
                    )
                    self._misses += 1
                    return None

                # Bump hit count (best-effort).
                try:
                    self._conn.execute(
                        "UPDATE cache_entries SET hit_count = hit_count + 1 WHERE id = ?",
                        (row["id"],),
                    )
                    self._conn.commit()
                except sqlite3.Error:
                    logger.debug("query_cache.hit_count_update_failed", exc_info=True)

                self._hits += 1
                return response
        except Exception:
            logger.warning("query_cache.get_exact_failed", exc_info=True)
            return None

    def get_semantic(
        self,
        tenant_id: str,
        question_embedding: Optional[list[float]],
        filters: Optional[dict] = None,
        threshold: Optional[float] = None,
    ) -> Optional[dict]:
        """Return the closest cached response above the similarity threshold.

        Scans at most SEMANTIC_SCAN_LIMIT recent entries with the same
        filters_hash for the tenant, computes cosine similarity against
        each stored embedding, and returns the best match above threshold.
        """
        if not self.enabled or not tenant_id or not question_embedding:
            return None

        t = float(threshold) if threshold is not None else self.semantic_threshold
        f_hash = hash_filters(filters)
        now = time.time()

        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT id, response_json, embedding, hit_count "
                    "FROM cache_entries "
                    "WHERE tenant_id = ? AND filters_hash = ? AND expires_at > ? "
                    "AND embedding IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT ?",
                    (tenant_id, f_hash, now, SEMANTIC_SCAN_LIMIT),
                ).fetchall()
        except sqlite3.Error:
            logger.warning("query_cache.get_semantic_failed", exc_info=True)
            return None

        best_row = None
        best_sim = -1.0
        for row in rows:
            stored = _decode_embedding(row["embedding"])
            if not stored:
                continue
            sim = cosine_similarity(question_embedding, stored)
            if sim > best_sim:
                best_sim = sim
                best_row = row

        if best_row is None or best_sim < t:
            self._misses += 1
            return None

        try:
            response = json.loads(best_row["response_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.warning(
                "query_cache.malformed_response_json",
                extra={"tenant_id": tenant_id, "entry_id": best_row["id"]},
            )
            self._misses += 1
            return None

        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE cache_entries SET hit_count = hit_count + 1 WHERE id = ?",
                    (best_row["id"],),
                )
                self._conn.commit()
        except sqlite3.Error:
            logger.debug("query_cache.hit_count_update_failed", exc_info=True)

        self._hits += 1
        # Annotate response with similarity for observability.
        if isinstance(response, dict):
            response.setdefault("_cache_similarity", round(best_sim, 4))
        return response

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def put(
        self,
        tenant_id: str,
        question: str,
        filters: Optional[dict],
        response: dict,
        embedding: Optional[list[float]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        """Upsert a cache entry. Returns True on success."""
        if not self.enabled or not tenant_id:
            return False
        if response is None:
            return False

        ttl = int(ttl_seconds) if ttl_seconds is not None else self.default_ttl
        now = time.time()
        expires = now + max(1, ttl)

        try:
            # Don't pollute the cached response with runtime-only markers.
            to_store = {k: v for k, v in response.items() if k != "cached"} if isinstance(response, dict) else response
            response_json = json.dumps(to_store, default=str)
        except Exception:
            # json.dumps can raise arbitrary exceptions through default=str
            # (if an object's __str__ explodes), so cast a wide net rather
            # than letting a cache write crash a query response path.
            logger.warning(
                "query_cache.response_serialize_failed",
                extra={"tenant_id": tenant_id},
                exc_info=True,
            )
            return False

        cache_key = compute_cache_key(tenant_id, question, filters)
        q_hash = question_hash(question)
        f_hash = hash_filters(filters)
        emb_blob = _encode_embedding(embedding)

        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO cache_entries "
                    "(tenant_id, cache_key, question_hash, question_text, filters_hash, "
                    "response_json, created_at, expires_at, hit_count, embedding) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?) "
                    "ON CONFLICT(tenant_id, cache_key) DO UPDATE SET "
                    "response_json = excluded.response_json, "
                    "expires_at = excluded.expires_at, "
                    "embedding = COALESCE(excluded.embedding, cache_entries.embedding), "
                    "hit_count = cache_entries.hit_count + 1",
                    (
                        tenant_id,
                        cache_key,
                        q_hash,
                        question,
                        f_hash,
                        response_json,
                        now,
                        expires,
                        emb_blob,
                    ),
                )
                self._conn.commit()
            return True
        except sqlite3.Error:
            logger.warning("query_cache.put_failed", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Invalidation + housekeeping
    # ------------------------------------------------------------------

    def invalidate_tenant(self, tenant_id: str) -> int:
        """Delete every cache entry for a tenant. Returns count removed."""
        if not tenant_id:
            return 0
        try:
            with self._lock:
                cursor = self._conn.execute(
                    "DELETE FROM cache_entries WHERE tenant_id = ?",
                    (tenant_id,),
                )
                self._conn.commit()
                return cursor.rowcount or 0
        except sqlite3.Error:
            logger.warning("query_cache.invalidate_tenant_failed", exc_info=True)
            return 0

    def invalidate_all(self) -> int:
        """Delete every cache entry. Returns count removed."""
        try:
            with self._lock:
                cursor = self._conn.execute("DELETE FROM cache_entries")
                self._conn.commit()
                return cursor.rowcount or 0
        except sqlite3.Error:
            logger.warning("query_cache.invalidate_all_failed", exc_info=True)
            return 0

    def cleanup_expired(self) -> int:
        """Delete entries past their expires_at. Returns count removed."""
        now = time.time()
        try:
            with self._lock:
                cursor = self._conn.execute(
                    "DELETE FROM cache_entries WHERE expires_at <= ?",
                    (now,),
                )
                self._conn.commit()
                return cursor.rowcount or 0
        except sqlite3.Error:
            logger.warning("query_cache.cleanup_expired_failed", exc_info=True)
            return 0

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Global cache stats."""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*) as entries, "
                    "COALESCE(SUM(LENGTH(response_json)), 0) as bytes_stored, "
                    "COALESCE(SUM(hit_count), 0) as total_hits "
                    "FROM cache_entries"
                ).fetchone()
        except sqlite3.Error:
            logger.warning("query_cache.stats_failed", exc_info=True)
            return {
                "enabled": self.enabled,
                "entries": 0,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": 0.0,
                "bytes_stored": 0,
            }

        total_reads = self._hits + self._misses
        hit_rate = (self._hits / total_reads) if total_reads > 0 else 0.0
        return {
            "enabled": self.enabled,
            "entries": int(row["entries"] or 0),
            "hits": self._hits,
            "misses": self._misses,
            "cumulative_hits_stored": int(row["total_hits"] or 0),
            "hit_rate": round(hit_rate, 4),
            "bytes_stored": int(row["bytes_stored"] or 0),
            "default_ttl_seconds": self.default_ttl,
            "semantic_threshold": self.semantic_threshold,
        }

    def tenant_stats(self, tenant_id: str) -> dict[str, Any]:
        """Per-tenant cache stats."""
        try:
            row = self._conn.execute(
                "SELECT COUNT(*) as entries, "
                "COALESCE(SUM(LENGTH(response_json)), 0) as bytes_stored, "
                "COALESCE(SUM(hit_count), 0) as total_hits, "
                "MIN(created_at) as oldest, MAX(created_at) as newest "
                "FROM cache_entries WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        except sqlite3.Error:
            logger.warning("query_cache.tenant_stats_failed", exc_info=True)
            return {"tenant_id": tenant_id, "entries": 0, "bytes_stored": 0, "cumulative_hits": 0}

        return {
            "tenant_id": tenant_id,
            "entries": int(row["entries"] or 0),
            "bytes_stored": int(row["bytes_stored"] or 0),
            "cumulative_hits": int(row["total_hits"] or 0),
            "oldest_entry": row["oldest"],
            "newest_entry": row["newest"],
        }

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass


# ---------------------------------------------------------------------------
# Module-level singleton (matches api/analytics.py pattern)
# ---------------------------------------------------------------------------

_query_cache: Optional[QueryCache] = None


def init_query_cache(output_dir: str, enabled: Optional[bool] = None) -> QueryCache:
    """Initialize the global query cache. Call once at startup.

    Honors the following environment variables:

    - ``QUERY_CACHE_ENABLED`` (default: true)
    - ``QUERY_CACHE_TTL`` (seconds; default 3600)
    - ``QUERY_CACHE_SEMANTIC_THRESHOLD`` (float 0..1; default 0.95)
    """
    global _query_cache
    if enabled is None:
        enabled = _env_bool("QUERY_CACHE_ENABLED", True)
    ttl = _env_int("QUERY_CACHE_TTL", DEFAULT_TTL_SECONDS)
    threshold = _env_float("QUERY_CACHE_SEMANTIC_THRESHOLD", DEFAULT_SEMANTIC_THRESHOLD)

    db_path = os.path.join(output_dir, "query_cache.db")
    _query_cache = QueryCache(
        db_path=db_path,
        enabled=enabled,
        default_ttl=ttl,
        semantic_threshold=threshold,
    )
    # Best-effort startup sweep of expired entries.
    try:
        _query_cache.cleanup_expired()
    except Exception:
        logger.debug("query_cache.startup_cleanup_failed", exc_info=True)
    return _query_cache


def get_query_cache() -> Optional[QueryCache]:
    """Return the global query cache singleton, or None if not initialized.

    Unlike analytics, this returns None rather than raising so callers in
    the hot query path can no-op if the cache wasn't wired up.
    """
    return _query_cache
