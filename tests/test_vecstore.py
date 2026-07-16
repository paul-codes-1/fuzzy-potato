"""Tests for rag/vecstore.py — both backends' store semantics.

The parametrized ``store`` fixture runs every shared test against BOTH
the chroma backend (real ephemeral ChromaDB) and the sqlite backend
(real sqlite-vec, tiny 4-dim embeddings), proving the two return the
same hits, the same filter behavior, and — critically for the
RAG_MAX_DISTANCE=0.75 gate — the same cosine-distance values.
"""

import pytest

from rag.vecstore import (
    ChromaVecStore,
    SqliteVecStore,
    as_vecstore,
    build_chroma_filter,
    embedding_dims,
    get_vecstore,
    vec_db_path,
)
from tests.conftest import sqlite_vec_available

DIMS = 4

# Known geometry: cosine distances from [1,0,0,0] are
#   a=0.0, b≈0.00612, d≈0.29289, c=1.0
SEED = [
    ("a", [1.0, 0.0, 0.0, 0.0],
     {"clip_id": 1, "source": "summary", "meeting_body": "Council",
      "date": "2026-01-22", "section_type": "votes"}),
    ("b", [0.9, 0.1, 0.0, 0.0],
     {"clip_id": 1, "source": "facts", "meeting_body": "Council",
      "date": "2026-01-22"}),
    ("c", [0.0, 1.0, 0.0, 0.0],
     {"clip_id": 2, "source": "summary", "meeting_body": "Commission",
      "date": "2025-06-01"}),
    ("d", [0.5, 0.5, 0.0, 0.0],
     {"clip_id": 3, "source": "minutes", "meeting_body": "Council",
      "date": ""}),
]

QUERY = [1.0, 0.0, 0.0, 0.0]


def _seed(store):
    store.upsert_chunks(
        ids=[row[0] for row in SEED],
        embeddings=[row[1] for row in SEED],
        documents=[f"doc {row[0]}" for row in SEED],
        metadatas=[row[2] for row in SEED],
    )


@pytest.fixture(params=["chroma", "sqlite"])
def store(request, tmp_path):
    """One VecStore per backend — shared tests run against both."""
    if request.param == "sqlite":
        if not sqlite_vec_available():
            pytest.skip("this Python's sqlite3 can't load extensions (sqlite-vec)")
        s = SqliteVecStore(str(tmp_path / "vec.db"), dims=DIMS)
        yield s
        s.close()
    else:
        import chromadb

        client = chromadb.Client()
        collection = client.get_or_create_collection(
            name="test_vecstore_backends",
            metadata={"hnsw:space": "cosine"},
        )
        yield ChromaVecStore(collection)
        client.delete_collection("test_vecstore_backends")


# ============================================================
# Shared backend semantics
# ============================================================

class TestStoreSemantics:
    def test_upsert_and_count(self, store):
        _seed(store)
        assert store.count() == 4

    def test_upsert_same_id_replaces(self, store):
        _seed(store)
        store.upsert_chunks(
            ids=["a"], embeddings=[[0.0, 0.0, 1.0, 0.0]],
            documents=["replaced"], metadatas=[SEED[0][2]],
        )
        assert store.count() == 4
        hit = next(h for h in store.get_chunks(filters={"clip_id": 1})
                   if h["id"] == "a")
        assert hit["document"] == "replaced"

    def test_query_distance_ordering(self, store):
        _seed(store)
        hits = store.query(QUERY, k=10)
        assert [h["id"] for h in hits] == ["a", "b", "d", "c"]
        distances = [h["distance"] for h in hits]
        assert distances == sorted(distances)

    def test_cosine_distance_values_match_chroma_calibration(self, store):
        """Both backends must return 1 − cosine similarity — the values the
        RAG_MAX_DISTANCE=0.75 gate was calibrated on."""
        _seed(store)
        by_id = {h["id"]: h["distance"] for h in store.query(QUERY, k=10)}
        assert by_id["a"] == pytest.approx(0.0, abs=1e-6)
        assert by_id["b"] == pytest.approx(0.0061162, abs=1e-4)
        assert by_id["d"] == pytest.approx(0.2928932, abs=1e-4)
        assert by_id["c"] == pytest.approx(1.0, abs=1e-4)

    def test_query_k_caps_results(self, store):
        _seed(store)
        assert len(store.query(QUERY, k=2)) == 2

    def test_query_returns_documents_and_metadata(self, store):
        _seed(store)
        hit = store.query(QUERY, k=1)[0]
        assert hit["document"] == "doc a"
        assert hit["metadata"]["clip_id"] == 1
        assert hit["metadata"]["section_type"] == "votes"

    def test_query_meeting_body_filter(self, store):
        _seed(store)
        hits = store.query(QUERY, k=10, filters={"meeting_body": "Council"})
        assert {h["id"] for h in hits} == {"a", "b", "d"}

    def test_query_date_after_excludes_older_and_undated(self, store):
        _seed(store)
        hits = store.query(QUERY, k=10, filters={"date_after": "2026-01-01"})
        assert {h["id"] for h in hits} == {"a", "b"}

    def test_query_date_before_excludes_newer_and_undated(self, store):
        _seed(store)
        hits = store.query(QUERY, k=10, filters={"date_before": "2025-12-31"})
        assert {h["id"] for h in hits} == {"c"}

    def test_query_date_range_excludes_undated(self, store):
        """Empty dates are excluded whenever any bound is set (matches
        _date_in_range semantics)."""
        _seed(store)
        hits = store.query(QUERY, k=10, filters={
            "date_after": "2025-01-01", "date_before": "2026-12-31"})
        assert {h["id"] for h in hits} == {"a", "b", "c"}

    def test_query_body_and_date_combined(self, store):
        _seed(store)
        hits = store.query(QUERY, k=10, filters={
            "meeting_body": "Council", "date_after": "2026-01-01"})
        assert {h["id"] for h in hits} == {"a", "b"}

    def test_query_arbitrary_equality_filter(self, store):
        _seed(store)
        hits = store.query(QUERY, k=10, filters={"section_type": "votes"})
        assert [h["id"] for h in hits] == ["a"]

    def test_query_empty_store(self, store):
        assert store.query(QUERY, k=5) == []

    def test_get_chunks_clip_filter(self, store):
        _seed(store)
        hits = store.get_chunks(filters={"clip_id": 1})
        assert {h["id"] for h in hits} == {"a", "b"}

    def test_get_chunks_clip_and_source(self, store):
        _seed(store)
        hits = store.get_chunks(filters={"clip_id": 1, "source": "summary"})
        assert [h["id"] for h in hits] == ["a"]

    def test_get_chunks_non_indexed_key(self, store):
        _seed(store)
        hits = store.get_chunks(filters={"section_type": "votes"})
        assert [h["id"] for h in hits] == ["a"]

    def test_get_chunks_limit(self, store):
        _seed(store)
        assert len(store.get_chunks(limit=2)) == 2

    def test_get_chunks_include_embeddings(self, store):
        _seed(store)
        hit = store.get_chunks(filters={"clip_id": 2}, include_embeddings=True)[0]
        assert hit["embedding"] == pytest.approx([0.0, 1.0, 0.0, 0.0], abs=1e-6)

    def test_delete_clip_removes_only_that_clip(self, store):
        _seed(store)
        store.delete_clip(1)
        assert store.count() == 2
        assert store.get_chunks(filters={"clip_id": 1}) == []
        assert {h["id"] for h in store.query(QUERY, k=10)} == {"c", "d"}

    def test_delete_then_reingest_leaves_no_stale_chunks(self, store):
        """The delete-before-reingest invariant: a re-chunked clip must fully
        replace its old chunks even when chunk ids shift."""
        _seed(store)
        store.delete_clip(1)
        store.upsert_chunks(
            ids=["a2"], embeddings=[[1.0, 0.0, 0.0, 0.0]],
            documents=["fresh chunk"],
            metadatas=[{"clip_id": 1, "source": "summary",
                        "meeting_body": "Council", "date": "2026-01-22"}],
        )
        hits = store.get_chunks(filters={"clip_id": 1})
        assert [h["id"] for h in hits] == ["a2"]
        assert store.count() == 3

    def test_delete_missing_clip_is_noop(self, store):
        _seed(store)
        store.delete_clip(999)
        assert store.count() == 4

    def test_stats_summary(self, store):
        _seed(store)
        stats = store.stats()
        assert stats["total_chunks"] == 4
        assert stats["unique_clips"] == 3
        assert stats["backend"] in ("chroma", "sqlite")


# ============================================================
# sqlite-specific behavior
# ============================================================

needs_sqlite_vec = pytest.mark.skipif(
    not sqlite_vec_available(),
    reason="this Python's sqlite3 can't load extensions (sqlite-vec)",
)


@needs_sqlite_vec
class TestSqliteBackend:
    def test_persists_across_reopen_with_stored_dims(self, tmp_path):
        """A reopened DB uses ITS OWN stamped dims, not the env default —
        the serving process must never mis-read a 512-dim rebuild."""
        path = str(tmp_path / "vec.db")
        s = SqliteVecStore(path, dims=DIMS)
        _seed(s)
        s.close()

        reopened = SqliteVecStore(path)  # env default is 1536; DB says 4
        try:
            assert reopened.dims == DIMS
            assert reopened.count() == 4
            assert reopened.query(QUERY, k=1)[0]["id"] == "a"
        finally:
            reopened.close()

    def test_explicit_dims_mismatch_rejected(self, tmp_path):
        path = str(tmp_path / "vec.db")
        SqliteVecStore(path, dims=DIMS).close()
        with pytest.raises(ValueError, match="dims"):
            SqliteVecStore(path, dims=8)

    def test_wrong_length_embedding_rejected(self, tmp_path):
        s = SqliteVecStore(str(tmp_path / "vec.db"), dims=DIMS)
        try:
            with pytest.raises(ValueError, match="dims"):
                s.upsert_chunks(ids=["x"], embeddings=[[1.0, 0.0]],
                                documents=["short"], metadatas=[{"clip_id": 1}])
        finally:
            s.close()

    def test_stats_reports_dims_and_file(self, tmp_path):
        s = SqliteVecStore(str(tmp_path / "vec.db"), dims=DIMS)
        try:
            _seed(s)
            stats = s.stats()
            assert stats["backend"] == "sqlite"
            assert stats["dims"] == DIMS
            assert stats["db_bytes"] > 0
        finally:
            s.close()


# ============================================================
# Factory / env selection
# ============================================================

class TestFactory:
    def test_default_backend_is_chroma(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VECTOR_BACKEND", raising=False)
        store = get_vecstore(str(tmp_path))
        assert isinstance(store, ChromaVecStore)

    @needs_sqlite_vec
    def test_sqlite_backend_selected_by_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTOR_BACKEND", "sqlite")
        monkeypatch.setenv("RAG_EMBED_DIMS", "8")
        store = get_vecstore(str(tmp_path))
        try:
            assert isinstance(store, SqliteVecStore)
            assert store.db_path == vec_db_path(str(tmp_path))
            assert store.dims == 8
        finally:
            store.close()

    def test_unknown_backend_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VECTOR_BACKEND", "pinecone")
        with pytest.raises(ValueError, match="VECTOR_BACKEND"):
            get_vecstore(str(tmp_path))

    def test_embedding_dims_env(self, monkeypatch):
        monkeypatch.delenv("RAG_EMBED_DIMS", raising=False)
        assert embedding_dims() == 1536
        monkeypatch.setenv("RAG_EMBED_DIMS", "512")
        assert embedding_dims() == 512

    def test_as_vecstore_passthrough_and_coercion(self, store):
        assert as_vecstore(store) is store

    def test_as_vecstore_wraps_bare_collection(self, chroma_collection):
        wrapped = as_vecstore(chroma_collection)
        assert isinstance(wrapped, ChromaVecStore)


# ============================================================
# build_chroma_filter (moved here from rag.query; re-exported there)
# ============================================================

class TestBuildChromaFilterEquality:
    def test_arbitrary_equality_keys_included(self):
        result = build_chroma_filter({"clip_id": 5, "source": "summary"})
        assert result == {"$and": [{"clip_id": 5}, {"source": "summary"}]}

    def test_dates_always_dropped(self):
        assert build_chroma_filter(
            {"date_after": "2025-01-01", "date_before": "2026-01-01"}) is None


# ============================================================
# scripts/rebuild_vec_db.py — rebuild + ledger drain
# ============================================================

@needs_sqlite_vec
class TestRebuildScript:
    def test_full_rebuild_swaps_db_atomically(
            self, sample_clip_dir, mock_openai_batch_embeddings, monkeypatch):
        from scripts.rebuild_vec_db import run_rebuild

        monkeypatch.delenv("RAG_EMBED_DIMS", raising=False)
        rc = run_rebuild(sample_clip_dir, dims=1536, max_clips=None,
                         openai_client=mock_openai_batch_embeddings)
        assert rc == 0
        final = sample_clip_dir / "vec.db"
        assert final.exists()
        assert not (sample_clip_dir / "vec.db.tmp").exists()
        assert not (sample_clip_dir / "vec.db.rebuild_state.json").exists()

        store = SqliteVecStore(str(final))
        try:
            assert store.count() > 0
            assert store.get_chunks(filters={"clip_id": 6669})
        finally:
            store.close()

    def test_partial_run_keeps_tmp_and_resumes(
            self, sample_clip_dir, mock_openai_batch_embeddings, monkeypatch):
        import json as _json

        from scripts.rebuild_vec_db import run_rebuild

        monkeypatch.delenv("RAG_EMBED_DIMS", raising=False)
        # Add a second clip so --max 1 leaves work behind.
        second = sample_clip_dir / "clips" / "7000"
        second.mkdir(parents=True)
        (second / "metadata.json").write_text(_json.dumps({
            "clip_id": 7000, "date": "2026-02-01", "meeting_body": "Council",
            "title": "Second Meeting", "files": {"minutes_txt": "minutes.txt"},
        }))
        (second / "minutes.txt").write_text(
            "COUNCIL MEETING\nRoll Call\nMembers present: Smith.")

        rc = run_rebuild(sample_clip_dir, dims=1536, max_clips=1,
                         openai_client=mock_openai_batch_embeddings)
        assert rc == 0
        assert (sample_clip_dir / "vec.db.tmp").exists()
        assert not (sample_clip_dir / "vec.db").exists()

        # Second run finishes the remainder and swaps in.
        rc = run_rebuild(sample_clip_dir, dims=1536, max_clips=None,
                         openai_client=mock_openai_batch_embeddings)
        assert rc == 0
        assert (sample_clip_dir / "vec.db").exists()
        assert not (sample_clip_dir / "vec.db.tmp").exists()

        store = SqliteVecStore(str(sample_clip_dir / "vec.db"))
        try:
            assert store.stats()["unique_clips"] == 2
        finally:
            store.close()

    def test_ledger_drain_ingests_and_clears(
            self, sample_clip_dir, mock_openai_batch_embeddings, monkeypatch):
        from scripts.rebuild_vec_db import run_ledger_drain

        monkeypatch.delenv("RAG_EMBED_DIMS", raising=False)
        ledger = sample_clip_dir / "pending_rag_ingest.txt"
        ledger.write_text("6669\n6669\n")  # duplicate ids collapse

        rc = run_ledger_drain(sample_clip_dir, ledger, dims=1536,
                              openai_client=mock_openai_batch_embeddings)
        assert rc == 0
        assert not ledger.exists()

        store = SqliteVecStore(str(sample_clip_dir / "vec.db"))
        try:
            assert store.get_chunks(filters={"clip_id": 6669})
        finally:
            store.close()


# ============================================================
# Concurrency: /admin/reload closing under in-flight queries
# ============================================================


@needs_sqlite_vec
class TestCloseUnderLoad:
    def test_close_waits_for_inflight_query(self, tmp_path):
        """close() takes the store lock, so an /admin/reload can't tear the
        connection down mid-statement under a FastAPI/MCP worker thread."""
        import threading

        s = SqliteVecStore(str(tmp_path / "vec.db"), dims=DIMS)
        _seed(s)

        # Simulate an in-flight query holding the store lock.
        assert s._lock.acquire(timeout=1)
        closer = threading.Thread(target=s.close)
        closer.start()
        closer.join(timeout=0.3)
        try:
            assert closer.is_alive(), "close() must block behind the query lock"
        finally:
            s._lock.release()
        closer.join(timeout=5)
        assert not closer.is_alive()

    def test_query_after_close_fails_cleanly(self, tmp_path):
        import sqlite3

        s = SqliteVecStore(str(tmp_path / "vec.db"), dims=DIMS)
        _seed(s)
        s.close()
        with pytest.raises(sqlite3.ProgrammingError):
            s.query(QUERY, k=1)


# ============================================================
# scripts/rebuild_vec_db.py — pipeline flock for the ledger drain
# ============================================================


class TestPipelineLock:
    def test_second_acquire_times_out(self, tmp_path):
        from scripts.rebuild_vec_db import acquire_pipeline_lock

        lock_path = str(tmp_path / "pipeline.lock")
        holder = acquire_pipeline_lock(lock_path, timeout=5)
        try:
            with pytest.raises(SystemExit, match="Could not acquire"):
                acquire_pipeline_lock(lock_path, timeout=0)
        finally:
            holder.close()

    def test_reacquire_after_release(self, tmp_path):
        from scripts.rebuild_vec_db import acquire_pipeline_lock

        lock_path = str(tmp_path / "pipeline.lock")
        holder = acquire_pipeline_lock(lock_path, timeout=5)
        holder.close()  # releases the flock
        second = acquire_pipeline_lock(lock_path, timeout=0)
        second.close()

    def test_lock_path_is_per_jurisdiction(self):
        from scripts.rebuild_vec_db import pipeline_lock_path

        assert pipeline_lock_path() == "/tmp/lfucg-pipeline.lock"
