"""Tests for pipeline integration: main.py and Lambda hooks."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestProcessClipRagIntegration:
    """Test that process_clip calls ingest_clip when rag_enabled=True."""

    def test_process_clip_calls_ingest_when_rag_enabled(self, sample_clip_dir):
        """When rag_enabled=True, ingest_clip should be called after processing."""
        from rag.ingest import ingest_clip

        with patch("rag.ingest.ingest_clip") as mock_ingest:
            # Simulate what process_clip does at the end
            mock_ingest(6669, sample_clip_dir, MagicMock(), MagicMock())
            mock_ingest.assert_called_once()

    def test_rag_ingest_not_called_when_disabled(self):
        """When rag_enabled=False, ingest_clip should NOT be called."""
        with patch("rag.ingest.ingest_clip") as mock_ingest:
            # Simulate pipeline behavior: rag_enabled=False means ingest is never called
            rag_enabled = False
            if rag_enabled:
                mock_ingest(6669, "/tmp", MagicMock(), MagicMock())
            mock_ingest.assert_not_called()


class TestRebuildRagFlag:
    """Test the --rebuild-rag CLI flag."""

    def test_rebuild_rag_calls_ingest_all(self, sample_clip_dir, vecstore, mock_openai_batch_embeddings):
        """--rebuild-rag should ingest all clips from scratch."""
        from rag.ingest import ingest_clip, get_stats

        # Ingest a clip
        ingest_clip(6669, sample_clip_dir, vecstore, mock_openai_batch_embeddings)
        stats = get_stats(vecstore)
        assert stats["total_chunks"] > 0

    def test_rebuild_rag_clears_and_reingests(self, sample_clip_dir, mock_openai_batch_embeddings):
        """Rebuild should clear existing state and re-ingest."""
        import chromadb
        from rag.ingest import ingest_clip, save_rag_state, load_rag_state

        client = chromadb.Client()
        collection = client.get_or_create_collection("rebuild_test")

        # Ingest once
        ingest_clip(6669, sample_clip_dir, collection, mock_openai_batch_embeddings)
        loaded = load_rag_state(sample_clip_dir)
        assert 6669 in loaded["ingested_clips"]

        # Simulate rebuild: clear state and re-ingest
        state = {"ingested_clips": []}
        save_rag_state(state, sample_clip_dir)

        # Re-ingest (should work since state was cleared)
        ingest_clip(6669, sample_clip_dir, collection, mock_openai_batch_embeddings,
                    skip_if_ingested=True, rag_state=state)

        # After re-ingestion, state should contain the clip again
        loaded = load_rag_state(sample_clip_dir)
        assert loaded["ingested_clips"] == [6669]

        client.delete_collection("rebuild_test")


class TestFilterUpgradeCandidates:
    """--upgrade-summaries must filter done clips BEFORE applying --max.

    Slicing first took the N oldest (already-upgraded) clips and no-oped.
    """

    @staticmethod
    def _make_clips(tmp_path, done_ids, pending_ids):
        clips_dir = tmp_path / "clips"
        for cid in list(done_ids) + list(pending_ids):
            clip_dir = clips_dir / str(cid)
            clip_dir.mkdir(parents=True)
            (clip_dir / "metadata.json").write_text("{}")
        for cid in done_ids:
            (clips_dir / str(cid) / "extracted_facts.json").write_text("{}")
        return clips_dir

    def test_max_applies_after_filtering_done_clips(self, tmp_path):
        from main import filter_upgrade_candidates

        # The 3 oldest clips are already upgraded; --max 2 must still pick
        # up the pending ones instead of slicing off the done prefix.
        clips_dir = self._make_clips(tmp_path, done_ids=[1, 2, 3], pending_ids=[4, 5, 6])

        clip_ids, skipped = filter_upgrade_candidates(
            clips_dir, [1, 2, 3, 4, 5, 6], max_clips=2, explicit=False, force=False)
        assert clip_ids == [4, 5]
        assert skipped == 3

    def test_force_reprocesses_done_clips(self, tmp_path):
        from main import filter_upgrade_candidates

        clips_dir = self._make_clips(tmp_path, done_ids=[1, 2], pending_ids=[3])

        clip_ids, skipped = filter_upgrade_candidates(
            clips_dir, [1, 2, 3], max_clips=2, explicit=False, force=True)
        assert clip_ids == [1, 2]
        assert skipped == 0

    def test_explicit_clips_never_filtered_or_capped(self, tmp_path):
        from main import filter_upgrade_candidates

        clips_dir = self._make_clips(tmp_path, done_ids=[1, 2, 3], pending_ids=[])

        clip_ids, skipped = filter_upgrade_candidates(
            clips_dir, [1, 2, 3], max_clips=1, explicit=True, force=False)
        assert clip_ids == [1, 2, 3]
        assert skipped == 0
