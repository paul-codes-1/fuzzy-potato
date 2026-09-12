"""Retrieval and synthesis logic for RAG Q&A."""

import argparse
import calendar
import json
import logging
import math
import os
import re
from collections import defaultdict
from datetime import date, timedelta

logger = logging.getLogger(__name__)

from rag.ingest import EMBEDDING_MODEL
from rag.vecstore import (  # noqa: F401 — build_chroma_filter/_date_in_range re-exported for back-compat
    _date_in_range,
    as_vecstore,
    build_chroma_filter,
    embedding_dims,
    get_vecstore,
)
from rag.prompts import (
    SYNTHESIS_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    CONDENSE_QUESTION_PROMPT,
    QUERY_REWRITE_PROMPT,
)

DEFAULT_MODEL = "gpt-4o"
# Claude model for the opt-in model_provider="anthropic" chat path. Low
# volume (user-selected alternate provider), so quality-tier by default;
# overridable per deployment without a code change.
DEFAULT_ANTHROPIC_MODEL = os.getenv("LFUCG_ANTHROPIC_MODEL", "claude-sonnet-4-6")


def _synthesis_model() -> str:
    """Resolve the OpenAI synthesis model at CALL time.

    rag.query is imported by rag.server BEFORE that module runs load_dotenv(),
    so a module-level ``os.getenv("RAG_SYNTHESIS_MODEL")`` would read the env
    before .env is applied and silently ignore the override. Resolving here —
    the same call-time pattern as ``granicus_clip_url`` — lets a .env-provided
    RAG_SYNTHESIS_MODEL take effect. An explicit ``model=`` argument to ask()
    still wins over this (see ask()).
    """
    return os.getenv("RAG_SYNTHESIS_MODEL", DEFAULT_MODEL)
MAX_HISTORY_PAIRS = 10
MAX_REWRITTEN_QUERIES = 3

# Synthesis must be deterministic-ish: it reports votes, names, and tallies
# from retrieved text. The OpenAI default (temperature 1.0) was a major
# hallucination source.
SYNTHESIS_TEMPERATURE = 0.1
SYNTHESIS_MAX_TOKENS = 2048

# Chunks with cosine distance above this never reach the LLM. Both vector
# backends (Chroma hnsw:space=cosine, sqlite-vec distance_metric=cosine)
# return 1 − cosine similarity for text-embedding-3-small vectors; relevant
# chunks land well under this, blatant nearest-neighbor junk lands above it.
#
# Calibration history:
# - 2026-06-11: 0.75 on Chroma cosine distances at 1536 dims.
# - 2026-09-12: re-measured on the prod sqlite-vec store at 512 dims
#   (217k chunks) with 20 real user questions from telemetry, 5 borderline
#   (weak-coverage / off-archive Kentucky) questions and 10 nonsense
#   questions, retrieval-only, k=15:
#       real       top-1 0.18–0.47   top-15 0.29–0.52
#       borderline top-1 0.32–0.47   top-15 0.37–0.52
#       nonsense   top-1 0.51–0.79   top-15 0.53–0.84
#   At 0.75 seven of ten nonsense queries still pushed chunks to the LLM.
#   0.55 keeps every real question's top-10 and blocks all nonsense except
#   the top 1–3 hits of the three closest ones (0.51–0.54). Defaults to
#   0.55 for the 512-dim prod store; a 1536-dim store sits lower on the
#   scale and can tighten further via env. Tunable per deployment without
#   a code change (RAG_MAX_DISTANCE).
DEFAULT_MAX_DISTANCE = 0.55
MAX_DISTANCE = float(os.getenv("RAG_MAX_DISTANCE", str(DEFAULT_MAX_DISTANCE)))

# Per-clip diversity cap for the retrieved context. Was 4; lowered to 3 so a
# single meeting can't monopolise 4 of the ~15 context slots and crowd out
# the newer / differently-sourced clips that usually hold the actual answer.
MAX_CHUNKS_PER_CLIP = 3

# Date-aware re-ranking (2026-09-12). Retrieval was date-blind: a snow-plan
# question surfaced a 2015 chunk over the Aug 2026 report because cosine
# similarity alone doesn't know the archive spans 15 years. Each hit's
# similarity (1 − distance) is multiplied by a mild recency weight BEFORE
# the top-k cap so newer-but-slightly-less-similar chunks win ties:
#
#   weight(age) = 1.0                                  for age <= grace
#               = max(floor, 0.5 ** ((age − grace) / half_life))  otherwise
#
# Defaults: grace 365d (the last 12 months are untouched), half-life 6400d
# (≈0.7 at 10 years, 0.5 at ~18.5 years), floor 0.5. All env-tunable;
# RAG_RECENCY_HALF_LIFE_DAYS=0 disables the decay entirely. The decay is
# ALSO skipped when the question names a specific year ("in 2019") or the
# caller passed explicit date filters — the user is scoping the time
# window themselves and must not be penalised for it.
RECENCY_HALF_LIFE_DAYS = float(os.getenv("RAG_RECENCY_HALF_LIFE_DAYS", "6400"))
RECENCY_GRACE_DAYS = float(os.getenv("RAG_RECENCY_GRACE_DAYS", "365"))
RECENCY_FLOOR = float(os.getenv("RAG_RECENCY_FLOOR", "0.5"))
# In addition to the decay, reserve a few of the top_k context slots for the
# best chunks from the last RECENCY_RECENT_DAYS days (when any exist and pass
# the distance gate) so a recent development is always represented even if
# older chunks out-score it. Set RAG_RECENCY_RESERVED_SLOTS=0 to disable.
RECENCY_RESERVED_SLOTS = int(os.getenv("RAG_RECENCY_RESERVED_SLOTS", "3"))
RECENCY_RECENT_DAYS = int(os.getenv("RAG_RECENCY_RECENT_DAYS", "180"))

# A bare 4-digit year in the question ("what happened in 2019?"). Same
# lookarounds as extract_temporal_signals so "Resolution 2023-456" and
# "0016-26" don't count as a year.
_EXPLICIT_YEAR_RE = re.compile(r"(?<![\d-])\b(?:19|20)\d{2}\b(?![\d-])")

NO_COVERAGE_ANSWER = (
    "The meeting archive's indexed excerpts don't appear to cover this topic. "
    "It may not have come up in an indexed meeting, or the relevant meeting "
    "may not be transcribed yet."
)


def granicus_clip_url(clip_id, timestamp: int = 0) -> str:
    """Build a Granicus deep-link URL. Reads GRANICUS_HOST / GRANICUS_VIEW_ID
    at call time so deployments that load .env after import still pick up
    the right tenant."""
    host = os.getenv("GRANICUS_HOST", "lfucg.granicus.com")
    view_id = os.getenv("GRANICUS_VIEW_ID", "14")
    return f"https://{host}/player/clip/{clip_id}?view_id={view_id}&entrytime={timestamp}"


def clip_citation_url(clip_id, canonical_url: str = "", timestamp: int = 0) -> str:
    """The citation/source link for a clip, honoring non-Granicus sources.

    PR-6: document-driven jurisdictions (e.g. Paris on CivicClerk) store their
    own ``canonical_url`` in the chunk metadata (== ``source.canonical_url`` at
    process time). Use it verbatim for those — they have no Granicus host and
    no video timestamp deep-link.

    Granicus stays byte-identical: a Granicus ``player/clip`` permalink (or no
    stored URL at all, the OLD already-ingested LFUCG case) falls back to
    ``granicus_clip_url(clip_id, timestamp)`` so the ``&entrytime=`` deep-link
    is preserved exactly as before.
    """
    if canonical_url and "/player/clip/" not in canonical_url:
        return canonical_url
    return granicus_clip_url(clip_id, timestamp or 0)

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
_RECENCY_RE = re.compile(r"\b(most recent|latest|newest|last|recent|recently|lately)\b", re.IGNORECASE)


def extract_temporal_signals(question: str) -> dict:
    """Pull date filters and a recency hint out of the user's question.

    Returns a dict with any of: `date_after`, `date_before`, `prefer_recent`.
    Recognized patterns: year ("in 2025"), month+year ("April 2025"),
    since/before ("since 2023", "before 2020"), "past year"/"last 12 months",
    and recency keywords ("last", "recent", "latest").
    """
    signals: dict = {}
    q = question.lower()

    # Specific month + year: "April 2025", "in April 2025", "April of 2025"
    m = re.search(r"\b(" + "|".join(_MONTHS.keys()) + r")\w*\s+(?:of\s+)?(\d{4})\b", q)
    if m:
        month = _MONTHS[m.group(1)]
        year = int(m.group(2))
        last_day = calendar.monthrange(year, month)[1]
        signals["date_after"] = f"{year:04d}-{month:02d}-01"
        signals["date_before"] = f"{year:04d}-{month:02d}-{last_day:02d}"
    else:
        # "since 2023"
        m = re.search(r"\bsince\s+(\d{4})\b", q)
        if m:
            signals["date_after"] = f"{int(m.group(1)):04d}-01-01"
        # "before 2020" (but not "before 2020-01")
        m = re.search(r"\bbefore\s+(\d{4})\b", q)
        if m:
            signals["date_before"] = f"{int(m.group(1)) - 1:04d}-12-31"
        # Bare year — only if no month+year matched and no since/before.
        # Lookarounds keep this from firing on identifiers like
        # "Resolution 2023-456" or "0016-26".
        if "date_after" not in signals and "date_before" not in signals:
            years = re.findall(r"(?<![\d-])\b(19|20)(\d{2})\b(?![\d-])", q)
            if len(years) == 1:
                year = int(years[0][0] + years[0][1])
                signals["date_after"] = f"{year:04d}-01-01"
                signals["date_before"] = f"{year:04d}-12-31"

    # "past year", "last 12 months", "past 6 months" — only when no explicit
    # date range was already parsed (e.g. "April 2025" must not be overridden).
    if "date_after" not in signals and "date_before" not in signals:
        m = re.search(r"\b(?:past|last)\s+(\d+)\s+months?\b", q)
        if m:
            cutoff = date.today() - timedelta(days=int(m.group(1)) * 31)
            signals["date_after"] = cutoff.isoformat()
        elif re.search(r"\b(?:past|last)\s+year\b", q):
            signals["date_after"] = (date.today() - timedelta(days=365)).isoformat()

    # Recency keywords → post-retrieval re-rank toward newest
    if _RECENCY_RE.search(q):
        signals["prefer_recent"] = True

    return signals


def question_names_year(question: str) -> bool:
    """True when the question contains an explicit 4-digit year.

    Used to bypass the recency decay: someone asking about 2019 has scoped
    the time window themselves, so older chunks must not be down-weighted.
    """
    return bool(_EXPLICIT_YEAR_RE.search(question or ""))


def _age_days(iso_date: str, today: date | None = None) -> float | None:
    """Days between ``iso_date`` (YYYY-MM-DD) and today; None if unparseable."""
    if not iso_date:
        return None
    try:
        d = date.fromisoformat(str(iso_date)[:10])
    except (TypeError, ValueError):
        return None
    return float(((today or date.today()) - d).days)


def recency_weight(iso_date: str, today: date | None = None, *,
                   half_life_days: float | None = None,
                   grace_days: float | None = None,
                   floor: float | None = None) -> float:
    """Multiplicative recency weight in [floor, 1.0] for a chunk's meeting date.

    1.0 inside the grace window (default: last 12 months), then an
    exponential decay with the configured half-life, clamped at ``floor``.
    Undated chunks and future-dated chunks get 1.0 (never penalise what we
    can't date). A half-life of 0 disables the decay (always 1.0).
    """
    hl = RECENCY_HALF_LIFE_DAYS if half_life_days is None else half_life_days
    grace = RECENCY_GRACE_DAYS if grace_days is None else grace_days
    fl = RECENCY_FLOOR if floor is None else floor
    if hl <= 0:
        return 1.0
    age = _age_days(iso_date, today)
    if age is None or age <= grace:
        return 1.0
    return max(fl, 0.5 ** ((age - grace) / hl))


def recency_rank_scores(metadatas: list[dict], distances: list[float],
                        today: date | None = None) -> list[float]:
    """Effective distance per hit after the recency weight: ``1 − sim·w``.

    Lower is better, same orientation as the raw cosine distance, so it can
    be fed straight into :func:`deduplicate_results` as ``rank_scores``.
    Hits with an unknown distance keep ``inf`` so they sort last.
    """
    scores = []
    for meta, dist in zip(metadatas, distances):
        if dist is None:
            scores.append(math.inf)
            continue
        sim = 1.0 - float(dist)
        w = recency_weight((meta or {}).get("date", ""), today)
        scores.append(1.0 - sim * w)
    return scores


def apply_reserved_recent_slots(deduped: dict, top_k: int,
                                reserved: int | None = None,
                                recent_days: int | None = None,
                                today: date | None = None) -> dict:
    """Cap ``deduped`` (already rank-ordered) to ``top_k`` while guaranteeing
    up to ``reserved`` slots for chunks dated within ``recent_days``.

    If the plain top-k already holds ``reserved`` recent chunks nothing
    changes. Otherwise the best-ranked recent chunks beyond the cap replace
    the worst-ranked NON-recent chunks inside it (never evicting a recent
    one, never exceeding top_k). Order within the result stays rank order.
    """
    n_reserved = RECENCY_RESERVED_SLOTS if reserved is None else reserved
    window = RECENCY_RECENT_DAYS if recent_days is None else recent_days
    n = len(deduped["ids"])
    if n <= top_k or n_reserved <= 0:
        return {k: v[:top_k] for k, v in deduped.items()}

    def _is_recent(meta: dict) -> bool:
        age = _age_days((meta or {}).get("date", ""), today)
        return age is not None and 0 <= age <= window

    keep = list(range(top_k))
    recent_in = sum(1 for i in keep if _is_recent(deduped["metadatas"][i]))
    need = n_reserved - recent_in
    if need > 0:
        candidates = [i for i in range(top_k, n) if _is_recent(deduped["metadatas"][i])]
        evictable = [i for i in reversed(keep) if not _is_recent(deduped["metadatas"][i])]
        for cand in candidates[:need]:
            if not evictable:
                break
            victim = evictable.pop(0)
            keep[keep.index(victim)] = cand
        keep.sort()
    return {k: [v[i] for i in keep] for k, v in deduped.items()}


def deduplicate_results(results: dict, max_per_clip: int = MAX_CHUNKS_PER_CLIP,
                        max_per_source_per_clip: int = 2,
                        rank_scores: list[float] | None = None) -> dict:
    """Deduplicate ChromaDB results: limit chunks per clip with source diversity.

    Keeps up to max_per_clip chunks per clip, but no more than
    max_per_source_per_clip from any single source type (summary, facts,
    minutes, agenda, transcript). This ensures retrieval surfaces a mix
    of source types rather than e.g. 3 summary sections from one clip.

    ``rank_scores`` (optional, aligned with ``results["ids"][0]``, lower is
    better) overrides the raw distance as the sort key — the recency
    re-rank feeds its effective distances through here so the per-clip
    cap keeps the best-ranked chunks, not merely the nearest.
    """
    if not results["ids"] or not results["ids"][0]:
        return {"ids": [], "documents": [], "metadatas": [], "distances": []}

    ids = results["ids"][0]
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]
    keys = rank_scores if rank_scores is not None else distances

    # Zip and sort by rank key (lower = better)
    items = list(zip(ids, documents, metadatas, distances, keys))
    items.sort(key=lambda x: x[4])

    # Keep max_per_clip per clip_id, max_per_source_per_clip per source within each clip
    clip_counts = defaultdict(int)
    clip_source_counts = defaultdict(lambda: defaultdict(int))
    deduped = {"ids": [], "documents": [], "metadatas": [], "distances": []}

    for id_, doc, meta, dist, _key in items:
        clip_id = meta.get("clip_id")
        source = meta.get("source", "")

        if clip_counts[clip_id] >= max_per_clip:
            continue
        if clip_source_counts[clip_id][source] >= max_per_source_per_clip:
            continue

        deduped["ids"].append(id_)
        deduped["documents"].append(doc)
        deduped["metadatas"].append(meta)
        deduped["distances"].append(dist)
        clip_counts[clip_id] += 1
        clip_source_counts[clip_id][source] += 1

    return deduped


def _fmt_timestamp(seconds: float) -> str:
    """Format seconds as MM:SS for human-readable timestamps."""
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def rewrite_query(question: str, openai_client) -> list[str]:
    """Use LLM to rewrite a user question into focused search queries.

    Returns at most MAX_REWRITTEN_QUERIES so a misbehaving model can't fan
    out to dozens of ChromaDB queries per request. On any failure we log
    and fall back to the original question — but with enough signal that
    a broken rewrite pass surfaces in production logs.
    """
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": QUERY_REWRITE_PROMPT},
                {"role": "user", "content": question},
            ],
            temperature=0,
            max_tokens=200,
        )
        raw = response.choices[0].message.content.strip()
        queries = json.loads(raw)
        if isinstance(queries, list) and all(isinstance(q, str) for q in queries) and queries:
            queries = queries[:MAX_REWRITTEN_QUERIES]
            logger.info("Query rewrite: %r -> %r", question[:80], queries)
            return queries
        logger.error("Query rewrite returned unexpected shape: %r", raw[:200])
    except json.JSONDecodeError:
        logger.error("Query rewrite returned non-JSON, falling back: raw=%r", raw[:200])
    except Exception:
        logger.exception("Query rewrite call failed, falling back to original")

    return [question]


def _format_chunk_context(chunk: dict) -> str:
    """Render one retrieved chunk as a clearly-delimited excerpt block."""
    header = f"--- Meeting: {chunk.get('title', 'Unknown')} | {chunk['date']} | {chunk['meeting_body']} | Clip {chunk['clip_id']} ---"
    source_info = f"[Source: {chunk['source']}"
    if "start_time" in chunk and "end_time" in chunk:
        source_info += f", Timestamp: {_fmt_timestamp(chunk['start_time'])}-{_fmt_timestamp(chunk['end_time'])}"
    elif "start_time" in chunk:
        source_info += f", Timestamp: {_fmt_timestamp(chunk['start_time'])}"
    if chunk.get("placeholder_transcript"):
        source_info += ", closed-caption placeholder (noisy)"
    source_info += "]"
    return f"{header}\n{source_info}\n{chunk['text']}"


def build_synthesis_messages(question: str, chunks: list[dict]) -> list[dict]:
    """Build the messages array for the LLM synthesis call."""
    context_parts = [_format_chunk_context(chunk) for chunk in chunks]

    context = "\n\n".join(context_parts)

    return [
        {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
        {"role": "user", "content": f"Meeting excerpts:\n\n{context}\n\nQuestion: {question}"},
    ]


def _retrieve_and_prepare(question: str, store, openai_client,
                          clip_metadata: dict = None, filters: dict = None,
                          top_k: int = 15) -> tuple[list[dict], list[dict]] | None:
    """Rewrite query, embed, retrieve from the vector store, deduplicate,
    build chunks and sources.

    Returns (synthesis_chunks, sources) or None if the store is empty.
    """
    clip_metadata = clip_metadata or {}
    store = as_vecstore(store)

    total_chunks = store.count()
    if total_chunks == 0:
        return None

    # 1. Rewrite the question into focused search queries. Always embed the
    # original question too — a lossy rewrite (dropped name spelling,
    # ordinance number) must not be able to silently sink retrieval.
    search_queries = rewrite_query(question, openai_client)
    if question not in search_queries:
        search_queries = search_queries[:MAX_REWRITTEN_QUERIES] + [question]

    # 2. Embed all queries
    q_response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=search_queries,
        dimensions=embedding_dims(),
    )
    embeddings = [item.embedding for item in q_response.data]

    # 3. Build metadata filter (merge explicit filters with temporal signals from the question)
    temporal = extract_temporal_signals(question)
    prefer_recent = temporal.pop("prefer_recent", False)
    merged_filters = {**temporal, **(filters or {})}  # explicit filters win on conflicts
    if temporal:
        logger.info("Temporal signals from question: %r (prefer_recent=%s)", temporal, prefer_recent)
    # Date filters are applied by the store (the chroma backend post-filters
    # in Python, so a date-filtered query can return fewer than k hits).
    date_after = merged_filters.get("date_after")
    date_before = merged_filters.get("date_before")
    # Over-fetch so the store's post-filter still leaves a useful set
    fetch_multiplier = 4 if (date_after or date_before) else 1

    # 4. Query the store for each rewritten query and merge results
    all_ids = []
    all_documents = []
    all_metadatas = []
    all_distances = []
    seen_ids = set()

    per_query_k = max(top_k, 10) * fetch_multiplier

    for i, embedding in enumerate(embeddings):
        hits = store.query(
            embedding,
            k=min(per_query_k, total_chunks),
            filters=merged_filters or None,
        )

        for hit in hits:
            id_ = hit["id"]
            if id_ in seen_ids:
                continue
            dist = hit["distance"]
            # Relevance gate: nearest-neighbor search always returns
            # *something*; far-away chunks must not reach the LLM, where
            # they read as plausible meeting excerpts and invite fusion.
            if dist is not None and dist > MAX_DISTANCE:
                continue
            seen_ids.add(id_)
            all_ids.append(id_)
            all_documents.append(hit["document"])
            all_metadatas.append(hit["metadata"])
            all_distances.append(dist)

        logger.info("  Query %d/%d %r: %d results",
                    i + 1, len(embeddings), search_queries[i][:60],
                    len(hits))

    merged = {
        "ids": [all_ids],
        "documents": [all_documents],
        "metadatas": [all_metadatas],
        "distances": [all_distances],
    }

    # 5. Date-aware re-rank, then deduplicate. The decay is skipped when the
    # question names a year or the caller scoped the dates explicitly (the
    # user chose the window; don't fight them). Applied BEFORE the per-clip
    # cap and the top_k cap so recency can actually change what survives.
    use_decay = not (question_names_year(question) or date_after or date_before)
    rank_scores = (recency_rank_scores(all_metadatas, all_distances)
                   if use_decay else None)
    deduped = deduplicate_results(merged, rank_scores=rank_scores)

    # 5b. Final cap BEFORE any recency reorder: top_k is the contract for how
    # much context reaches the LLM. Capping while still rank-sorted keeps
    # the most relevant chunks; capping after a date-desc reorder would let
    # new-but-marginal chunks evict relevant ones. With the decay active we
    # also hold a few slots for the newest material (see
    # apply_reserved_recent_slots) so a recent development is never
    # crowded out by a pile of older near-duplicates.
    if len(deduped["ids"]) > top_k:
        if use_decay:
            deduped = apply_reserved_recent_slots(deduped, top_k)
        else:
            deduped = {k: v[:top_k] for k, v in deduped.items()}

    # 5c. Recency re-rank: for "last/recent/latest" queries, prefer newer clips
    # (dated chunks first, sorted by date desc; undated chunks trail in original order).
    if prefer_recent and deduped["metadatas"]:
        bundle = list(zip(deduped["ids"], deduped["documents"],
                          deduped["metadatas"], deduped["distances"]))
        dated = [x for x in bundle if x[2].get("date")]
        undated = [x for x in bundle if not x[2].get("date")]
        dated.sort(key=lambda x: x[2].get("date", ""), reverse=True)
        reordered = dated + undated
        deduped = {
            "ids": [x[0] for x in reordered],
            "documents": [x[1] for x in reordered],
            "metadatas": [x[2] for x in reordered],
            "distances": [x[3] for x in reordered],
        }

    deduped_count = len(deduped["ids"])
    logger.info("Retrieved %d unique chunks (%d after dedup/cap) for question: %s",
                len(all_ids), deduped_count, question[:120])

    for i, (doc, meta, dist) in enumerate(zip(
        deduped["documents"], deduped["metadatas"], deduped["distances"]
    )):
        logger.info(
            "  Chunk %d: clip=%s source=%s dist=%.4f date=%s body=%s | %s",
            i + 1,
            meta.get("clip_id"),
            meta.get("source", "?"),
            dist,
            meta.get("date", "?"),
            meta.get("meeting_body", "?"),
            doc[:100].replace("\n", " "),
        )

    # 5. Build chunks with metadata for synthesis
    synthesis_chunks = []
    sources = []

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
            chunk_info["end_time"] = meta.get("end_time", meta["start_time"])
        if meta.get("transcript_source") == "granicus_vtt":
            chunk_info["placeholder_transcript"] = True

        synthesis_chunks.append(chunk_info)

        # Build source entry
        timestamp = int(meta.get("start_time", 0)) if "start_time" in meta else None
        source_entry = {
            "clip_id": clip_id,
            "date": meta.get("date", ""),
            "title": clip_meta.get("title", "Unknown Meeting"),
            "meeting_body": meta.get("meeting_body", ""),
            "excerpt": doc[:200] + "..." if len(doc) > 200 else doc,
            # Honor a non-Granicus canonical_url (Paris/CivicClerk) stored at
            # ingest; Granicus + old LFUCG chunks fall back to the Granicus
            # deep-link (byte-identical). Key stays "granicus_url" for
            # backward compat with the frontend / MCP consumers.
            "granicus_url": clip_citation_url(
                clip_id, meta.get("canonical_url", ""), timestamp or 0),
        }
        if timestamp is not None:
            source_entry["timestamp"] = timestamp
        sources.append(source_entry)

    return synthesis_chunks, sources


# One bracket citation: [Clip 6669], [Clip 6669, 12:34], the range form
# [Clip 6669, 107:06-109:11], AND the multi-ID forms the model actually
# emits — [Clip 6865, 5695, 6757], [Clips 6865 and 5695],
# [Clip 6865; Clip 5695, 12:34]. The bracket body is parsed by
# parse_citation_ids so every ID is verified, not just the first.
_CITATION_RE = re.compile(r"\[Clips?\s+([^\]]*)\]")
# Timestamp tokens inside a bracket (MM:SS, H:MM:SS, and ranges) — removed
# before ID extraction so "107:06-109:11" can't shed a fake "107" / "109".
_TIMESTAMP_TOKEN_RE = re.compile(r"\d+:\d{2}(?::\d{2})?(?:\s*[-–]\s*\d+:\d{2}(?::\d{2})?)?")
_ID_TOKEN_RE = re.compile(r"(?<![\d:])\d{1,8}(?![\d:])")


def _bracket_ids(body: str) -> list[str]:
    """All clip IDs named inside one citation bracket body, in order."""
    scrubbed = _TIMESTAMP_TOKEN_RE.sub(" ", body)
    return _ID_TOKEN_RE.findall(scrubbed)


def parse_citation_ids(answer: str) -> list[str]:
    """Every clip ID cited anywhere in ``answer`` (duplicates kept, in order)."""
    ids: list[str] = []
    for m in _CITATION_RE.finditer(answer or ""):
        ids.extend(_bracket_ids(m.group(1)))
    return ids


def verify_citations(answer: str, sources: list[dict]) -> tuple[str, list[dict]]:
    """Post-hoc grounding check on the synthesized answer.

    - Strips bracket citations whose Clip ID was never retrieved (the model
      invented them) and logs the event. In a multi-ID bracket only the
      invented IDs are removed; the bracket survives if any real ID remains.
    - Marks each source with ``cited: true/false`` and reorders cited
      sources first so the UI can collapse the uncited remainder.
    """
    retrieved_ids = {str(s.get("clip_id")) for s in sources}
    cited_ids = set(parse_citation_ids(answer))

    invented = cited_ids - retrieved_ids
    if invented:
        logger.warning("Answer cited clip IDs not in retrieved set (stripped): %s",
                       sorted(invented))

        def _strip_invented(m: re.Match) -> str:
            body = m.group(1)
            ids = _bracket_ids(body)
            keep = [i for i in ids if i not in invented]
            if not keep:
                return ""
            if len(keep) == len(ids):
                return m.group(0)
            # Drop each invented ID (with its own "Clip" label and any
            # attached timestamp) from the bracket body, then tidy separators.
            for bad in set(ids) - set(keep):
                body = re.sub(
                    r"(?:Clips?\s+)?(?<![\d:])" + re.escape(bad)
                    + r"(?![\d:])(?:\s*,\s*" + _TIMESTAMP_TOKEN_RE.pattern + r")?",
                    "", body)
            body = re.sub(r"\s*(?:[,;]|\band\b)\s*(?=[,;]|$)", "", body)
            body = re.sub(r"^\s*(?:[,;]|\band\b)\s*", "", body)
            body = re.sub(r"\s{2,}", " ", body).strip(" ,;")
            return f"[Clip {body}]"

        answer = _CITATION_RE.sub(_strip_invented, answer)
        cited_ids &= retrieved_ids

    for s in sources:
        s["cited"] = str(s.get("clip_id")) in cited_ids
    sources = sorted(sources, key=lambda s: not s["cited"])
    return answer, sources


def ask(question: str, store, openai_client, clip_metadata: dict = None,
        filters: dict = None, top_k: int = 15, model: str | None = None) -> dict:
    """Full RAG Q&A: embed question, retrieve, deduplicate, synthesize.

    ``store`` is a :class:`rag.vecstore.VecStore` (a bare ChromaDB collection
    is coerced for back-compat).

    ``model`` defaults to None so the synthesis model is resolved from the
    RAG_SYNTHESIS_MODEL env at call time (see _synthesis_model). An explicitly
    passed ``model`` (e.g. the CLI's --model) wins over the env.
    """
    resolved_model = model or _synthesis_model()
    result = _retrieve_and_prepare(question, store, openai_client,
                                   clip_metadata, filters, top_k)
    if result is None:
        return {
            "answer": "I don't have enough information to answer this question. The meeting archive may not have been indexed yet.",
            "sources": [],
            "model_used": resolved_model,
            "filters_applied": filters or {},
            "chunks_retrieved": 0,
        }

    synthesis_chunks, sources = result

    # Nothing relevant survived the distance/date/dedup gates: answer
    # honestly WITHOUT calling the LLM. An empty-context synthesis call is
    # an invitation to answer from parametric memory.
    if not synthesis_chunks:
        return {
            "answer": NO_COVERAGE_ANSWER,
            "sources": [],
            "model_used": resolved_model,
            "filters_applied": filters or {},
            "chunks_retrieved": 0,
        }

    # Synthesize answer via LLM
    messages = build_synthesis_messages(question, synthesis_chunks)

    logger.info("=== LLM Prompt (%s) ===", resolved_model)
    for msg in messages:
        logger.info("[%s] %s", msg["role"], msg["content"][:500])
        if len(msg["content"]) > 500:
            logger.info("  ... (%d chars total)", len(msg["content"]))

    chat_response = openai_client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        temperature=SYNTHESIS_TEMPERATURE,
        max_tokens=SYNTHESIS_MAX_TOKENS,
    )
    answer = chat_response.choices[0].message.content
    logger.info("=== LLM Response (%d chars) ===", len(answer))

    answer, sources = verify_citations(answer, sources)

    return {
        "answer": answer,
        "sources": sources,
        "model_used": resolved_model,
        "filters_applied": filters or {},
        "chunks_retrieved": len(sources),
    }


def build_chat_synthesis_messages(messages: list[dict], chunks: list[dict]) -> list[dict]:
    """Build messages array for multi-turn chat synthesis."""
    # Build context block (same format as build_synthesis_messages)
    context = "\n\n".join(_format_chunk_context(chunk) for chunk in chunks)

    # Trim history to last MAX_HISTORY_PAIRS * 2 messages
    trimmed = messages[-(MAX_HISTORY_PAIRS * 2):]

    # Strip to only role and content
    clean_messages = [{"role": m["role"], "content": m["content"]} for m in trimmed]

    # Extract the last user question (must be from user)
    if not clean_messages or clean_messages[-1]["role"] != "user":
        raise ValueError("Last message must be from the user")
    last_question = clean_messages[-1]["content"]

    # Build final messages: system + history (minus last) + augmented last user message
    result = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    result.extend(clean_messages[:-1])
    result.append({
        "role": "user",
        "content": f"Meeting excerpts:\n\n{context}\n\nQuestion: {last_question}",
    })

    return result


def synthesize_with_anthropic(messages: list[dict], anthropic_client,
                              model: str = DEFAULT_ANTHROPIC_MODEL) -> str:
    """Synthesize an answer using the Anthropic API."""
    system_content = messages[0]["content"]
    remaining = messages[1:]

    response = anthropic_client.messages.create(
        model=model,
        max_tokens=SYNTHESIS_MAX_TOKENS,
        temperature=SYNTHESIS_TEMPERATURE,
        system=system_content,
        messages=remaining,
    )
    return response.content[0].text


def condense_question(messages: list[dict], openai_client) -> str:
    """Rewrite the latest chat message as a standalone question for retrieval.

    Follow-ups like "what about the vote?" carry no entities — retrieving on
    them returns unrelated chunks, and the model then answers from its own
    prior turn (the classic multi-turn hallucination amplifier). Uses the
    last few turns of history to resolve references; falls back to the raw
    last message on any failure.
    """
    question = messages[-1]["content"]
    if len(messages) < 2:
        return question

    history = messages[-7:-1]  # up to 3 prior exchange pairs
    transcript = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in history)
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": CONDENSE_QUESTION_PROMPT},
                {"role": "user",
                 "content": f"Conversation:\n{transcript}\n\nLatest message: {question}"},
            ],
            temperature=0,
            max_tokens=200,
        )
        condensed = (response.choices[0].message.content or "").strip()
        if condensed:
            if condensed != question:
                logger.info("Condensed follow-up %r -> %r", question[:80], condensed[:120])
            return condensed
    except Exception:
        logger.exception("Question condensation failed, using raw last message")
    return question


def chat(messages: list[dict], store, openai_client,
         clip_metadata: dict = None, anthropic_client=None,
         filters: dict = None, model_provider: str = "openai",
         top_k: int = 15) -> dict:
    """Multi-turn chat: retrieve context for latest question, synthesize with history."""
    # Resolve the OpenAI synthesis model at call time (env-configurable). The
    # anthropic path keeps its own configured model.
    synthesis_model = _synthesis_model()
    openai_no_cov = synthesis_model if model_provider == "openai" else DEFAULT_ANTHROPIC_MODEL

    # Retrieval works on a standalone version of the latest question so
    # follow-ups ("what about the vote?") still retrieve the right chunks.
    question = condense_question(messages, openai_client)

    result = _retrieve_and_prepare(question, store, openai_client,
                                   clip_metadata, filters, top_k)
    if result is None:
        return {
            "role": "assistant",
            "content": "I don't have enough information to answer this question. The meeting archive may not have been indexed yet.",
            "sources": [],
            "model_used": openai_no_cov,
            "filters_applied": filters or {},
            "chunks_retrieved": 0,
        }

    synthesis_chunks, sources = result

    if not synthesis_chunks:
        return {
            "role": "assistant",
            "content": NO_COVERAGE_ANSWER,
            "sources": [],
            "model_used": openai_no_cov,
            "filters_applied": filters or {},
            "chunks_retrieved": 0,
        }

    # Build chat messages with context
    synth_messages = build_chat_synthesis_messages(messages, synthesis_chunks)

    logger.info("=== Chat LLM Prompt (%s, %d messages) ===", model_provider, len(synth_messages))
    for msg in synth_messages:
        logger.info("[%s] %s", msg["role"], msg["content"][:500])
        if len(msg["content"]) > 500:
            logger.info("  ... (%d chars total)", len(msg["content"]))

    # Route to appropriate provider
    if model_provider == "anthropic":
        if anthropic_client is None:
            raise ValueError("anthropic_client is required when model_provider='anthropic'")
        content = synthesize_with_anthropic(synth_messages, anthropic_client)
        model_used = DEFAULT_ANTHROPIC_MODEL
    else:
        chat_response = openai_client.chat.completions.create(
            model=synthesis_model,
            messages=synth_messages,
            temperature=SYNTHESIS_TEMPERATURE,
            max_tokens=SYNTHESIS_MAX_TOKENS,
        )
        content = chat_response.choices[0].message.content
        model_used = synthesis_model

    logger.info("=== Chat LLM Response (%s, %d chars) ===", model_used, len(content))

    content, sources = verify_citations(content, sources)

    return {
        "role": "assistant",
        "content": content,
        "sources": sources,
        "model_used": model_used,
        "filters_applied": filters or {},
        "chunks_retrieved": len(sources),
    }


# ============================================================
# CLI
# ============================================================

def load_clip_metadata(output_dir: str) -> dict:
    """Load metadata for all clips to provide titles in answers."""
    clips_dir = os.path.join(output_dir, "clips")
    metadata = {}
    if not os.path.isdir(clips_dir):
        return metadata

    for name in os.listdir(clips_dir):
        meta_path = os.path.join(clips_dir, name, "metadata.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                metadata[meta["clip_id"]] = meta
            except (json.JSONDecodeError, KeyError):
                continue

    return metadata


def main():
    parser = argparse.ArgumentParser(description="Ask questions about LFUCG meetings")
    parser.add_argument("question", help="Your question")
    parser.add_argument("--body", help="Filter by meeting body")
    parser.add_argument("--after", help="Filter by date (YYYY-MM-DD)")
    parser.add_argument("--before", help="Filter by date (YYYY-MM-DD)")
    parser.add_argument("--model", default=None,
                        help="LLM model for synthesis (overrides RAG_SYNTHESIS_MODEL env)")
    parser.add_argument("--top-k", type=int, default=15, help="Number of chunks to retrieve")
    parser.add_argument("--output-dir", default="./lfucg_output", help="Output directory")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()
    from clients import get_openai
    openai_client = get_openai()

    store = get_vecstore(args.output_dir)
    clip_metadata = load_clip_metadata(args.output_dir)

    filters = {}
    if args.body:
        filters["meeting_body"] = args.body
    if args.after:
        filters["date_after"] = args.after
    if args.before:
        filters["date_before"] = args.before

    result = ask(
        question=args.question,
        store=store,
        openai_client=openai_client,
        clip_metadata=clip_metadata,
        filters=filters if filters else None,
        top_k=args.top_k,
        model=args.model,
    )

    print(f"\n{result['answer']}\n")
    print(f"--- Sources ({len(result['sources'])}) ---")
    for s in result["sources"]:
        timestamp_str = f" [{s['timestamp'] // 60}:{s['timestamp'] % 60:02d}]" if "timestamp" in s else ""
        print(f"  - {s['title']} ({s['date']}){timestamp_str}")
        print(f"    {s['granicus_url']}")


if __name__ == "__main__":
    main()
