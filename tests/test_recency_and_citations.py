"""Tests for the 2026-09-12 retrieval-quality changes in rag/query.py:

- date-aware recency decay applied before the top-k cap
- explicit-year / explicit-date-filter bypass of that decay
- reserved context slots for the newest material
- multi-ID citation parsing + verification
"""

from datetime import date

import pytest

from rag import query as q


TODAY = date(2026, 9, 12)


class TestRecencyWeight:
    def test_recent_dates_are_untouched(self):
        assert q.recency_weight("2026-09-01", TODAY) == 1.0
        assert q.recency_weight("2025-10-01", TODAY) == 1.0  # inside the 365d grace

    def test_undated_and_future_are_untouched(self):
        assert q.recency_weight("", TODAY) == 1.0
        assert q.recency_weight(None, TODAY) == 1.0
        assert q.recency_weight("garbage", TODAY) == 1.0
        assert q.recency_weight("2027-01-01", TODAY) == 1.0

    def test_decay_is_mild_and_monotonic(self):
        w2 = q.recency_weight("2024-09-12", TODAY)
        w5 = q.recency_weight("2021-09-12", TODAY)
        w10 = q.recency_weight("2016-09-12", TODAY)
        w20 = q.recency_weight("2006-09-12", TODAY)
        assert 1.0 > w2 > w5 > w10 > w20
        # ~0.7 at ten years, never below the floor.
        assert 0.65 <= w10 <= 0.75
        assert w20 >= q.RECENCY_FLOOR

    def test_half_life_zero_disables_decay(self):
        assert q.recency_weight("2006-09-12", TODAY, half_life_days=0) == 1.0

    def test_floor_is_respected(self):
        assert q.recency_weight("1990-01-01", TODAY, floor=0.8) == 0.8


class TestRecencyRankScores:
    def test_newer_chunk_outranks_slightly_closer_old_chunk(self):
        # The snow-plan case: a 2015 chunk at distance 0.37 vs the Aug 2026
        # report at 0.39. Raw distance puts 2015 first; the decay flips it.
        metas = [{"date": "2015-10-20"}, {"date": "2026-08-29"}]
        dists = [0.37, 0.39]
        scores = q.recency_rank_scores(metas, dists, TODAY)
        assert scores[1] < scores[0]

    def test_large_similarity_gap_still_wins(self):
        # Decay is mild: a much closer old chunk must still beat a weak new one.
        metas = [{"date": "2015-10-20"}, {"date": "2026-08-29"}]
        dists = [0.20, 0.55]
        scores = q.recency_rank_scores(metas, dists, TODAY)
        assert scores[0] < scores[1]

    def test_none_distance_sorts_last(self):
        scores = q.recency_rank_scores([{"date": "2026-01-01"}, {}], [None, 0.3], TODAY)
        assert scores[0] > scores[1]


class TestQuestionNamesYear:
    @pytest.mark.parametrize("question", [
        "What happened with the Red Mile TIF in 2011?",
        "who voted on the 2019 budget",
        "Zoning changes since 2020",
    ])
    def test_detects_year(self, question):
        assert q.question_names_year(question)

    @pytest.mark.parametrize("question", [
        "What did council decide on Ordinance 0016-26?",
        "Resolution 2023-456 status",          # identifier, not a year
        "How much did the snow plan cost?",
        "",
    ])
    def test_ignores_identifiers_and_plain_questions(self, question):
        assert not q.question_names_year(question)


class TestDedupWithRankScores:
    def test_rank_scores_override_distance_order(self):
        results = {
            "ids": [["old", "new"]],
            "documents": [["old doc", "new doc"]],
            "metadatas": [[{"clip_id": 1, "source": "summary", "date": "2015-10-20"},
                           {"clip_id": 2, "source": "summary", "date": "2026-08-29"}]],
            "distances": [[0.37, 0.39]],
        }
        scores = q.recency_rank_scores(results["metadatas"][0], results["distances"][0], TODAY)
        deduped = q.deduplicate_results(results, rank_scores=scores)
        assert deduped["ids"] == ["new", "old"]
        # Raw distances are preserved for logging / gating.
        assert deduped["distances"] == [0.39, 0.37]

    def test_default_per_clip_cap_is_three(self):
        assert q.MAX_CHUNKS_PER_CLIP == 3


class TestReservedRecentSlots:
    def _bundle(self, dates):
        return {
            "ids": [f"id{i}" for i in range(len(dates))],
            "documents": [f"doc{i}" for i in range(len(dates))],
            "metadatas": [{"clip_id": i, "date": d} for i, d in enumerate(dates)],
            "distances": [0.3 + i * 0.01 for i in range(len(dates))],
        }

    def test_no_change_when_recent_already_present(self):
        dates = ["2026-08-01", "2026-07-01", "2026-06-01", "2015-01-01", "2014-01-01"]
        out = q.apply_reserved_recent_slots(self._bundle(dates), top_k=4, reserved=3,
                                            recent_days=180, today=TODAY)
        assert out["ids"] == ["id0", "id1", "id2", "id3"]

    def test_recent_chunks_beyond_cap_replace_worst_old_ones(self):
        dates = ["2015-01-01", "2014-01-01", "2013-01-01", "2012-01-01",  # top-4 all old
                 "2026-08-01", "2026-07-01", "2011-01-01"]
        out = q.apply_reserved_recent_slots(self._bundle(dates), top_k=4, reserved=2,
                                            recent_days=180, today=TODAY)
        assert len(out["ids"]) == 4
        assert {"id4", "id5"} <= set(out["ids"])
        # The two best-ranked old chunks survive; the two worst were evicted.
        assert out["ids"] == ["id0", "id1", "id4", "id5"]

    def test_reserved_zero_is_plain_cap(self):
        dates = ["2015-01-01", "2014-01-01", "2026-08-01"]
        out = q.apply_reserved_recent_slots(self._bundle(dates), top_k=2, reserved=0,
                                            recent_days=180, today=TODAY)
        assert out["ids"] == ["id0", "id1"]

    def test_short_list_untouched(self):
        dates = ["2015-01-01", "2026-08-01"]
        out = q.apply_reserved_recent_slots(self._bundle(dates), top_k=5, today=TODAY)
        assert out["ids"] == ["id0", "id1"]


class TestCitationParsing:
    def test_single_and_timestamped(self):
        assert q.parse_citation_ids("See [Clip 6669] and [Clip 6670, 12:34].") == ["6669", "6670"]

    def test_range_timestamps_do_not_leak_ids(self):
        assert q.parse_citation_ids("[Clip 6669, 107:06-109:11]") == ["6669"]
        assert q.parse_citation_ids("[Clip 6669, 1:07:06–1:09:11]") == ["6669"]

    def test_multi_id_forms(self):
        assert q.parse_citation_ids("[Clip 6865, 5695, 6757]") == ["6865", "5695", "6757"]
        assert q.parse_citation_ids("[Clips 6865 and 5695]") == ["6865", "5695"]
        assert q.parse_citation_ids("[Clip 6865; Clip 5695, 12:34]") == ["6865", "5695"]

    def test_no_citations(self):
        assert q.parse_citation_ids("No brackets here.") == []
        assert q.parse_citation_ids("") == []


class TestVerifyCitationsMultiId:
    def _sources(self, *ids):
        return [{"clip_id": i, "title": f"Clip {i}"} for i in ids]

    def test_all_ids_in_multi_bracket_are_verified(self):
        answer = "The vote passed [Clip 6865, 5695, 6757]."
        out, sources = q.verify_citations(answer, self._sources(6865, 5695, 6757))
        assert out == answer
        assert all(s["cited"] for s in sources)

    def test_invented_id_removed_from_multi_bracket_but_bracket_kept(self):
        answer = "The vote passed [Clip 6865, 9999, 6757]."
        out, sources = q.verify_citations(answer, self._sources(6865, 6757))
        assert "9999" not in out
        assert "[Clip 6865, 6757]" in out
        assert {s["clip_id"] for s in sources if s["cited"]} == {6865, 6757}

    def test_bracket_dropped_when_every_id_invented(self):
        answer = "The vote passed [Clip 9998, 9999]."
        out, sources = q.verify_citations(answer, self._sources(6865))
        assert "[Clip" not in out
        assert not any(s["cited"] for s in sources)

    def test_invented_id_with_timestamp_removed(self):
        answer = "See [Clip 6865; Clip 9999, 12:34]."
        out, _ = q.verify_citations(answer, self._sources(6865))
        assert "9999" not in out and "12:34" not in out
        assert "[Clip 6865]" in out

    def test_cited_sources_sorted_first(self):
        answer = "See [Clips 6757 and 5695]."
        _, sources = q.verify_citations(answer, self._sources(6865, 5695, 6757))
        assert [s["clip_id"] for s in sources][:2] == [5695, 6757]
        assert sources[-1]["clip_id"] == 6865 and not sources[-1]["cited"]
