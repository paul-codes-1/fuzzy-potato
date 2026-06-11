"""Retrieval and synthesis logic for RAG Q&A."""

import argparse
import calendar
import json
import logging
import os
import re
from collections import defaultdict
from datetime import date, timedelta

logger = logging.getLogger(__name__)

from rag.ingest import EMBEDDING_MODEL, get_chroma_collection
from rag.prompts import (
    SYNTHESIS_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    CONDENSE_QUESTION_PROMPT,
    QUERY_REWRITE_PROMPT,
)

DEFAULT_MODEL = "gpt-4o"
MAX_HISTORY_PAIRS = 10
MAX_REWRITTEN_QUERIES = 3

# Synthesis must be deterministic-ish: it reports votes, names, and tallies
# from retrieved text. The OpenAI default (temperature 1.0) was a major
# hallucination source.
SYNTHESIS_TEMPERATURE = 0.1
SYNTHESIS_MAX_TOKENS = 2048

# Chunks with cosine distance above this never reach the LLM. The collection
# uses hnsw:space=cosine with text-embedding-3-small; relevant chunks land
# well under this, blatant nearest-neighbor junk lands above it. Tunable per
# deployment without a code change.
MAX_DISTANCE = float(os.getenv("RAG_MAX_DISTANCE", "0.75"))

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


def build_chroma_filter(filters: dict | None) -> dict | None:
    """Build a ChromaDB where clause from filter parameters.

    Note: date_after/date_before are NOT passed to ChromaDB (this version rejects
    string comparisons on $gte/$lte). They're applied post-retrieval in Python instead.
    """
    if not filters:
        return None

    conditions = []

    if "meeting_body" in filters:
        conditions.append({"meeting_body": filters["meeting_body"]})

    if not conditions:
        return None

    if len(conditions) == 1:
        return conditions[0]

    return {"$and": conditions}


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


def deduplicate_results(results: dict, max_per_clip: int = 4,
                        max_per_source_per_clip: int = 2) -> dict:
    """Deduplicate ChromaDB results: limit chunks per clip with source diversity.

    Keeps up to max_per_clip chunks per clip, but no more than
    max_per_source_per_clip from any single source type (summary, facts,
    minutes, agenda, transcript). This ensures retrieval surfaces a mix
    of source types rather than e.g. 4 summary sections from one clip.
    """
    if not results["ids"] or not results["ids"][0]:
        return {"ids": [], "documents": [], "metadatas": [], "distances": []}

    ids = results["ids"][0]
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    # Zip and sort by distance (lower = better)
    items = list(zip(ids, documents, metadatas, distances))
    items.sort(key=lambda x: x[3])

    # Keep max_per_clip per clip_id, max_per_source_per_clip per source within each clip
    clip_counts = defaultdict(int)
    clip_source_counts = defaultdict(lambda: defaultdict(int))
    deduped = {"ids": [], "documents": [], "metadatas": [], "distances": []}

    for id_, doc, meta, dist in items:
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


def _retrieve_and_prepare(question: str, collection, openai_client,
                          clip_metadata: dict = None, filters: dict = None,
                          top_k: int = 15) -> tuple[list[dict], list[dict]] | None:
    """Rewrite query, embed, retrieve from ChromaDB, deduplicate, build chunks and sources.

    Returns (synthesis_chunks, sources) or None if collection is empty.
    """
    clip_metadata = clip_metadata or {}

    total_chunks = collection.count()
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
    )
    embeddings = [item.embedding for item in q_response.data]

    # 3. Build metadata filter (merge explicit filters with temporal signals from the question)
    temporal = extract_temporal_signals(question)
    prefer_recent = temporal.pop("prefer_recent", False)
    merged_filters = {**temporal, **(filters or {})}  # explicit filters win on conflicts
    if temporal:
        logger.info("Temporal signals from question: %r (prefer_recent=%s)", temporal, prefer_recent)
    where_clause = build_chroma_filter(merged_filters)
    # Dates are filtered in Python (this ChromaDB version rejects $gte/$lte on strings).
    date_after = merged_filters.get("date_after")
    date_before = merged_filters.get("date_before")
    # Over-fetch so post-filter still leaves a useful set
    fetch_multiplier = 4 if (date_after or date_before) else 1

    # 4. Query ChromaDB for each rewritten query and merge results
    all_ids = []
    all_documents = []
    all_metadatas = []
    all_distances = []
    seen_ids = set()

    per_query_k = max(top_k, 10) * fetch_multiplier

    for i, embedding in enumerate(embeddings):
        query_kwargs = {
            "query_embeddings": [embedding],
            "n_results": min(per_query_k, total_chunks),
            "include": ["documents", "metadatas", "distances"],
        }
        if where_clause:
            query_kwargs["where"] = where_clause

        results = collection.query(**query_kwargs)

        if results["ids"] and results["ids"][0]:
            for id_, doc, meta, dist in zip(
                results["ids"][0], results["documents"][0],
                results["metadatas"][0], results["distances"][0]
            ):
                if id_ in seen_ids:
                    continue
                # Relevance gate: nearest-neighbor search always returns
                # *something*; far-away chunks must not reach the LLM, where
                # they read as plausible meeting excerpts and invite fusion.
                if dist is not None and dist > MAX_DISTANCE:
                    continue
                if (date_after or date_before) and not _date_in_range(
                    meta.get("date", ""), date_after, date_before
                ):
                    continue
                seen_ids.add(id_)
                all_ids.append(id_)
                all_documents.append(doc)
                all_metadatas.append(meta)
                all_distances.append(dist)

            logger.info("  Query %d/%d %r: %d results",
                        i + 1, len(embeddings), search_queries[i][:60],
                        len(results["ids"][0]))

    merged = {
        "ids": [all_ids],
        "documents": [all_documents],
        "metadatas": [all_metadatas],
        "distances": [all_distances],
    }

    # 5. Deduplicate
    deduped = deduplicate_results(merged)

    # 5b. Final cap BEFORE any recency reorder: top_k is the contract for how
    # much context reaches the LLM. Capping while still distance-sorted keeps
    # the most relevant chunks; capping after a date-desc reorder would let
    # new-but-marginal chunks evict relevant ones.
    if len(deduped["ids"]) > top_k:
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


# Matches [Clip 6669], [Clip 6669, 12:34], and range forms like
# [Clip 6669, 107:06-109:11].
_CITATION_RE = re.compile(r"\[Clip\s+(\d+)[^\]]*\]")


def verify_citations(answer: str, sources: list[dict]) -> tuple[str, list[dict]]:
    """Post-hoc grounding check on the synthesized answer.

    - Strips bracket citations whose Clip ID was never retrieved (the model
      invented them) and logs the event.
    - Marks each source with ``cited: true/false`` and reorders cited
      sources first so the UI can collapse the uncited remainder.
    """
    retrieved_ids = {str(s.get("clip_id")) for s in sources}
    cited_ids = set(_CITATION_RE.findall(answer))

    invented = cited_ids - retrieved_ids
    if invented:
        logger.warning("Answer cited clip IDs not in retrieved set (stripped): %s",
                       sorted(invented))

        def _strip_invented(m: re.Match) -> str:
            return "" if m.group(1) in invented else m.group(0)

        answer = _CITATION_RE.sub(_strip_invented, answer)
        cited_ids &= retrieved_ids

    for s in sources:
        s["cited"] = str(s.get("clip_id")) in cited_ids
    sources = sorted(sources, key=lambda s: not s["cited"])
    return answer, sources


def ask(question: str, collection, openai_client, clip_metadata: dict = None,
        filters: dict = None, top_k: int = 15, model: str = DEFAULT_MODEL) -> dict:
    """Full RAG Q&A: embed question, retrieve, deduplicate, synthesize."""
    result = _retrieve_and_prepare(question, collection, openai_client,
                                   clip_metadata, filters, top_k)
    if result is None:
        return {
            "answer": "I don't have enough information to answer this question. The meeting archive may not have been indexed yet.",
            "sources": [],
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
            "filters_applied": filters or {},
            "chunks_retrieved": 0,
        }

    # Synthesize answer via LLM
    messages = build_synthesis_messages(question, synthesis_chunks)

    logger.info("=== LLM Prompt (%s) ===", model)
    for msg in messages:
        logger.info("[%s] %s", msg["role"], msg["content"][:500])
        if len(msg["content"]) > 500:
            logger.info("  ... (%d chars total)", len(msg["content"]))

    chat_response = openai_client.chat.completions.create(
        model=model,
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
                              model: str = "claude-sonnet-4-6") -> str:
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


def chat(messages: list[dict], collection, openai_client,
         clip_metadata: dict = None, anthropic_client=None,
         filters: dict = None, model_provider: str = "openai",
         top_k: int = 15) -> dict:
    """Multi-turn chat: retrieve context for latest question, synthesize with history."""
    # Retrieval works on a standalone version of the latest question so
    # follow-ups ("what about the vote?") still retrieve the right chunks.
    question = condense_question(messages, openai_client)

    result = _retrieve_and_prepare(question, collection, openai_client,
                                   clip_metadata, filters, top_k)
    if result is None:
        return {
            "role": "assistant",
            "content": "I don't have enough information to answer this question. The meeting archive may not have been indexed yet.",
            "sources": [],
            "model_used": DEFAULT_MODEL if model_provider == "openai" else "claude-sonnet",
            "filters_applied": filters or {},
            "chunks_retrieved": 0,
        }

    synthesis_chunks, sources = result

    if not synthesis_chunks:
        return {
            "role": "assistant",
            "content": NO_COVERAGE_ANSWER,
            "sources": [],
            "model_used": DEFAULT_MODEL if model_provider == "openai" else "claude-sonnet",
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
        model_used = "claude-sonnet"
    else:
        chat_response = openai_client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=synth_messages,
            temperature=SYNTHESIS_TEMPERATURE,
            max_tokens=SYNTHESIS_MAX_TOKENS,
        )
        content = chat_response.choices[0].message.content
        model_used = DEFAULT_MODEL

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
    parser.add_argument("--model", default=DEFAULT_MODEL, help="LLM model for synthesis")
    parser.add_argument("--top-k", type=int, default=15, help="Number of chunks to retrieve")
    parser.add_argument("--output-dir", default="./lfucg_output", help="Output directory")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()
    from clients import get_openai
    openai_client = get_openai()

    collection = get_chroma_collection(args.output_dir)
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
        collection=collection,
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
