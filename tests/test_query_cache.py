"""Tests for api/query_cache.py -- SQLite-backed query response cache."""

import json
import threading
import time

import pytest

from api.query_cache import (
    DEFAULT_SEMANTIC_THRESHOLD,
    DEFAULT_TTL_SECONDS,
    QueryCache,
    compute_cache_key,
    cosine_similarity,
    get_query_cache,
    hash_filters,
    init_query_cache,
    normalize_question,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cache(tmp_path):
    """Fresh QueryCache bound to a throwaway SQLite file."""
    db_path = tmp_path / "query_cache.db"
    c = QueryCache(str(db_path), enabled=True, default_ttl=3600)
    yield c
    c.close()


@pytest.fixture
def sample_response():
    return {
        "answer": "The FY26 parks budget is $5.2M.",
        "sources": [
            {
                "clip_id": 6669,
                "date": "2026-01-22",
                "title": "Urban County Council",
                "meeting_body": "Council",
                "excerpt": "Parks: $5.2M",
                "granicus_url": "https://example.com/clip/6669?entrytime=600",
                "timestamp": 600,
            }
        ],
        "filters_applied": {},
        "chunks_retrieved": 1,
    }


# ---------------------------------------------------------------------------
# Normalization + hashing + keys
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_lowercase(self):
        assert normalize_question("BUDGET") == "budget"

    def test_strip_whitespace(self):
        assert normalize_question("  budget  ") == "budget"

    def test_collapse_internal_whitespace(self):
        assert normalize_question("what  is\tthe\nbudget") == "what is the budget"

    def test_strip_trailing_punctuation(self):
        assert normalize_question("what is the budget?") == "what is the budget"
        assert normalize_question("budget!!!") == "budget"
        assert normalize_question("'budget.'") == "'budget"

    def test_none_returns_empty(self):
        assert normalize_question(None) == ""

    def test_contractions_documented(self):
        """Contractions are NOT rewritten -- different keys.

        This is an intentional limitation of the exact-match layer; the
        semantic layer can still bridge these.
        """
        assert normalize_question("what's the budget?") != normalize_question(
            "what is the budget"
        )


class TestFilterHash:
    def test_none_filters_stable(self):
        assert hash_filters(None) == hash_filters({})

    def test_order_insensitive(self):
        assert hash_filters({"a": 1, "b": 2}) == hash_filters({"b": 2, "a": 1})

    def test_differs_on_value(self):
        assert hash_filters({"meeting_body": "Council"}) != hash_filters(
            {"meeting_body": "Planning"}
        )

    def test_ignores_tenant_id(self):
        """tenant_id is scoped elsewhere and should not affect the filter hash."""
        assert hash_filters({"tenant_id": "a"}) == hash_filters({"tenant_id": "b"})


class TestCacheKey:
    def test_same_question_different_tenants_different_keys(self):
        k1 = compute_cache_key("tenant-a", "budget?", None)
        k2 = compute_cache_key("tenant-b", "budget?", None)
        assert k1 != k2

    def test_same_question_different_filters_different_keys(self):
        k1 = compute_cache_key("t", "budget?", {"meeting_body": "Council"})
        k2 = compute_cache_key("t", "budget?", {"meeting_body": "Planning"})
        assert k1 != k2

    def test_normalized_question_collides(self):
        k1 = compute_cache_key("t", "What is the budget?", None)
        k2 = compute_cache_key("t", "  what is the budget  ", None)
        assert k1 == k2


# ---------------------------------------------------------------------------
# Schema + basic read/write
# ---------------------------------------------------------------------------


class TestSchema:
    def test_init_creates_db(self, tmp_path):
        db_path = tmp_path / "sub" / "query_cache.db"
        QueryCache(str(db_path))
        assert db_path.exists()

    def test_init_creates_table(self, cache):
        row = cache._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cache_entries'"
        ).fetchone()
        assert row is not None


class TestExactCache:
    def test_miss_returns_none(self, cache):
        assert cache.get_exact("t", "budget?", None) is None

    def test_put_then_get_hit(self, cache, sample_response):
        assert cache.put("t", "What is the budget?", None, sample_response) is True
        hit = cache.get_exact("t", "What is the budget?", None)
        assert hit is not None
        assert hit["answer"] == sample_response["answer"]

    def test_normalization_hit(self, cache, sample_response):
        """Different punctuation / casing should still hit."""
        cache.put("t", "What is the budget?", None, sample_response)
        assert cache.get_exact("t", "what is the budget", None) is not None
        assert cache.get_exact("t", "  WHAT IS THE BUDGET  ", None) is not None

    def test_different_filters_different_entries(self, cache, sample_response):
        f1 = {"meeting_body": "Council"}
        f2 = {"meeting_body": "Planning"}
        cache.put("t", "budget?", f1, sample_response)
        assert cache.get_exact("t", "budget?", f1) is not None
        assert cache.get_exact("t", "budget?", f2) is None

    def test_tenant_isolation(self, cache, sample_response):
        cache.put("tenant-a", "budget?", None, sample_response)
        assert cache.get_exact("tenant-a", "budget?", None) is not None
        assert cache.get_exact("tenant-b", "budget?", None) is None

    def test_ttl_expired_returns_none(self, cache, sample_response):
        cache.put("t", "budget?", None, sample_response, ttl_seconds=1)
        # Force expiry by rewinding expires_at in-place.
        cache._conn.execute(
            "UPDATE cache_entries SET expires_at = ? WHERE tenant_id = ?",
            (time.time() - 10, "t"),
        )
        cache._conn.commit()
        assert cache.get_exact("t", "budget?", None) is None

    def test_hit_count_increments(self, cache, sample_response):
        cache.put("t", "budget?", None, sample_response)
        cache.get_exact("t", "budget?", None)
        cache.get_exact("t", "budget?", None)
        cache.get_exact("t", "budget?", None)
        row = cache._conn.execute(
            "SELECT hit_count FROM cache_entries WHERE tenant_id = 't'"
        ).fetchone()
        assert row["hit_count"] == 3

    def test_put_same_key_updates(self, cache, sample_response):
        cache.put("t", "budget?", None, sample_response, ttl_seconds=60)
        first_expires = cache._conn.execute(
            "SELECT expires_at FROM cache_entries WHERE tenant_id='t'"
        ).fetchone()["expires_at"]

        time.sleep(0.05)
        new_response = dict(sample_response)
        new_response["answer"] = "updated answer"
        cache.put("t", "budget?", None, new_response, ttl_seconds=3600)

        row = cache._conn.execute(
            "SELECT expires_at, response_json, hit_count FROM cache_entries WHERE tenant_id='t'"
        ).fetchone()
        assert row["expires_at"] > first_expires
        stored = json.loads(row["response_json"])
        assert stored["answer"] == "updated answer"
        # Upsert should bump a synthetic "hit count" on update.
        assert row["hit_count"] == 1

        # Still only one row (upsert not insert).
        count = cache._conn.execute(
            "SELECT COUNT(*) as c FROM cache_entries WHERE tenant_id='t'"
        ).fetchone()["c"]
        assert count == 1


class TestInvalidation:
    def test_invalidate_tenant_only_removes_that_tenant(self, cache, sample_response):
        cache.put("tenant-a", "q1", None, sample_response)
        cache.put("tenant-a", "q2", None, sample_response)
        cache.put("tenant-b", "q1", None, sample_response)
        removed = cache.invalidate_tenant("tenant-a")
        assert removed == 2
        assert cache.get_exact("tenant-a", "q1", None) is None
        assert cache.get_exact("tenant-b", "q1", None) is not None

    def test_cleanup_expired_removes_only_expired(self, cache, sample_response):
        cache.put("t", "fresh", None, sample_response, ttl_seconds=3600)
        cache.put("t", "stale", None, sample_response, ttl_seconds=1)
        cache._conn.execute(
            "UPDATE cache_entries SET expires_at = ? WHERE question_text = ?",
            (time.time() - 10, "stale"),
        )
        cache._conn.commit()

        removed = cache.cleanup_expired()
        assert removed == 1
        assert cache.get_exact("t", "fresh", None) is not None
        assert cache.get_exact("t", "stale", None) is None


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_math(self, cache, sample_response):
        cache.put("t", "q1", None, sample_response)
        cache.put("t", "q2", None, sample_response)

        cache.get_exact("t", "q1", None)  # hit
        cache.get_exact("t", "q1", None)  # hit
        cache.get_exact("t", "missing", None)  # miss

        stats = cache.stats()
        assert stats["entries"] == 2
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == pytest.approx(2 / 3, rel=1e-3)
        assert stats["bytes_stored"] > 0
        assert stats["enabled"] is True

    def test_tenant_stats(self, cache, sample_response):
        cache.put("tenant-a", "q1", None, sample_response)
        cache.put("tenant-a", "q2", None, sample_response)
        cache.put("tenant-b", "q1", None, sample_response)

        stats = cache.tenant_stats("tenant-a")
        assert stats["entries"] == 2
        assert stats["bytes_stored"] > 0


# ---------------------------------------------------------------------------
# Semantic cache
# ---------------------------------------------------------------------------


class TestSemanticCache:
    def test_cosine_similarity_identical(self):
        v = [0.1, 0.2, 0.3, 0.4]
        assert cosine_similarity(v, v) == pytest.approx(1.0, rel=1e-6)

    def test_cosine_similarity_orthogonal(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-9)

    def test_cosine_similarity_empty(self):
        assert cosine_similarity([], []) == 0.0
        assert cosine_similarity([1.0], [1.0, 0.0]) == 0.0

    def test_semantic_hit_on_similar(self, cache, sample_response):
        emb = [1.0, 0.0, 0.0, 0.0]
        cache.put("t", "what is the budget", None, sample_response, embedding=emb)
        # Near-identical vector (tiny perturbation) should hit.
        near = [0.99, 0.01, 0.0, 0.0]
        hit = cache.get_semantic("t", near, None, threshold=0.95)
        assert hit is not None
        assert hit["answer"] == sample_response["answer"]

    def test_semantic_miss_on_dissimilar(self, cache, sample_response):
        emb = [1.0, 0.0, 0.0, 0.0]
        cache.put("t", "budget", None, sample_response, embedding=emb)
        far = [0.0, 1.0, 0.0, 0.0]
        assert cache.get_semantic("t", far, None, threshold=0.95) is None

    def test_semantic_respects_tenant_isolation(self, cache, sample_response):
        emb = [1.0, 0.0, 0.0, 0.0]
        cache.put("tenant-a", "budget", None, sample_response, embedding=emb)
        assert cache.get_semantic("tenant-b", emb, None, threshold=0.95) is None


# ---------------------------------------------------------------------------
# Disabled cache
# ---------------------------------------------------------------------------


class TestDisabled:
    def test_disabled_get_returns_none(self, tmp_path, sample_response):
        c = QueryCache(str(tmp_path / "q.db"), enabled=False)
        # put is a no-op when disabled
        assert c.put("t", "q", None, sample_response) is False
        assert c.get_exact("t", "q", None) is None
        c.close()

    def test_env_var_disable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUERY_CACHE_ENABLED", "false")
        cache = init_query_cache(str(tmp_path))
        assert cache.enabled is False


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_malformed_response_json_treated_as_miss(self, cache, sample_response):
        cache.put("t", "q", None, sample_response)
        # Corrupt the stored JSON directly.
        cache._conn.execute(
            "UPDATE cache_entries SET response_json = ? WHERE tenant_id = 't'",
            ("{ this is not valid json",),
        )
        cache._conn.commit()
        assert cache.get_exact("t", "q", None) is None

    def test_put_non_serializable_response_returns_false(self, cache):
        class Unserializable:
            pass

        # default=str will coerce most things, so craft an object that
        # raises on __str__ too.
        class Exploding:
            def __repr__(self):
                raise RuntimeError("nope")

            def __str__(self):
                raise RuntimeError("nope")

        # json.dumps(default=str) will call str(x) which raises.
        assert cache.put("t", "q", None, {"bad": Exploding()}) is False


# ---------------------------------------------------------------------------
# Concurrency (smoke)
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_writes_no_crash(self, cache, sample_response):
        def worker(i):
            for j in range(10):
                cache.put(f"t{i}", f"q{j}", None, sample_response)
                cache.get_exact(f"t{i}", f"q{j}", None)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stats = cache.stats()
        assert stats["entries"] == 40


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_init_and_get(self, tmp_path, monkeypatch):
        monkeypatch.delenv("QUERY_CACHE_ENABLED", raising=False)
        import api.query_cache as qc_mod

        qc_mod._query_cache = None
        cache = init_query_cache(str(tmp_path))
        assert cache is not None
        assert get_query_cache() is cache
        assert cache.enabled is True
        assert cache.default_ttl == DEFAULT_TTL_SECONDS
        assert cache.semantic_threshold == DEFAULT_SEMANTIC_THRESHOLD

    def test_env_ttl_honored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUERY_CACHE_TTL", "60")
        import api.query_cache as qc_mod

        qc_mod._query_cache = None
        cache = init_query_cache(str(tmp_path))
        assert cache.default_ttl == 60
