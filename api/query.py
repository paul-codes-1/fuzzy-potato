"""Retrieval and synthesis logic for RAG Q&A."""

import argparse
import json
import logging
import os
from collections import defaultdict
from urllib.parse import parse_qs, urlparse

from api.ingest import EMBEDDING_MODEL, get_chroma_collection
from api.prompts import SYNTHESIS_SYSTEM_PROMPT, CHAT_SYSTEM_PROMPT

DEFAULT_MODEL = "gpt-4o"
MAX_HISTORY_PAIRS = 10
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
        logger.debug("cost tracking failed for embedding call", exc_info=True)


def _record_llm_cost(
    tenant_id: str | None,
    usage,
    *,
    model: str,
    operation: str,
    request_id: str | None = None,
    module: str = "query",
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
            module=module,
        )
    except Exception:
        logger.debug("cost tracking failed for llm call", exc_info=True)


def build_chroma_filter(filters: dict | None) -> dict | None:
    """Build a ChromaDB where clause from filter parameters."""
    if not filters:
        return None

    conditions = []

    if "tenant_id" in filters:
        conditions.append({"tenant_id": filters["tenant_id"]})

    if "meeting_body" in filters:
        conditions.append({"meeting_body": filters["meeting_body"]})

    if "date_after" in filters:
        conditions.append({"date": {"$gte": filters["date_after"]}})

    if "date_before" in filters:
        conditions.append({"date": {"$lte": filters["date_before"]}})

    if not conditions:
        return None

    if len(conditions) == 1:
        return conditions[0]

    return {"$and": conditions}


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


def _granicus_env_url_template() -> str | None:
    """Build a URL template from the current environment, if configured."""
    granicus_host = os.getenv("GRANICUS_HOST", "").strip()
    if not granicus_host:
        return None

    granicus_view_id = os.getenv("GRANICUS_VIEW_ID", "").strip()
    return (
        f"https://{granicus_host}/player/clip/{{clip_id}}"
        f"?view_id={granicus_view_id}&entrytime={{timestamp}}"
    )


def _build_granicus_url(
    clip_id: int | str,
    timestamp: int,
    clip_meta: dict,
    granicus_url_override: str | None = None,
) -> str:
    """Build the best available Granicus deep link for a source.

    Priority:
    1. Explicit override from the API layer
    2. Clip metadata URL parsed from the indexed meeting
    3. Environment-configured host/view id fallback
    """
    if granicus_url_override:
        return granicus_url_override.format(clip_id=clip_id, timestamp=timestamp)

    source_url = clip_meta.get("url", "")
    if source_url:
        parsed = urlparse(source_url)
        if parsed.scheme and parsed.netloc:
            view_id = parse_qs(parsed.query).get("view_id", [""])[0]
            return (
                f"{parsed.scheme}://{parsed.netloc}/player/clip/{clip_id}"
                f"?view_id={view_id}&entrytime={timestamp}"
            )

    granicus_url_template = _granicus_env_url_template()
    if granicus_url_template:
        return granicus_url_template.format(clip_id=clip_id, timestamp=timestamp)

    return f"/player/clip/{clip_id}?entrytime={timestamp}"


def build_synthesis_messages(question: str, chunks: list[dict]) -> list[dict]:
    """Build the messages array for the LLM synthesis call."""
    context_parts = []
    for chunk in chunks:
        header = f"--- Meeting: {chunk.get('title', 'Unknown')} | {chunk['date']} | {chunk['meeting_body']} | Clip {chunk['clip_id']} ---"
        source_info = f"[Source: {chunk['source']}"
        if "start_time" in chunk and "end_time" in chunk:
            source_info += f", Timestamp: {_fmt_timestamp(chunk['start_time'])}-{_fmt_timestamp(chunk['end_time'])}"
        elif "start_time" in chunk:
            source_info += f", Timestamp: {_fmt_timestamp(chunk['start_time'])}"
        source_info += "]"
        context_parts.append(f"{header}\n{source_info}\n{chunk['text']}")

    context = "\n\n".join(context_parts)

    return [
        {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
        {"role": "user", "content": f"Meeting excerpts:\n\n{context}\n\nQuestion: {question}"},
    ]


def _retrieve_and_prepare(question: str, collection, openai_client,
                          clip_metadata: dict = None, filters: dict = None,
                          top_k: int = 15,
                          granicus_url_override: str = None,
                          return_embedding: bool = False,
                          tenant_id: str | None = None,
                          request_id: str | None = None,
                          embedding_operation: str = "rag.retrieve.embed_query"):
    """Embed question, retrieve from ChromaDB, deduplicate, build chunks and sources.

    Returns (synthesis_chunks, sources) or None if collection is empty.
    When ``return_embedding`` is True, returns
    (synthesis_chunks, sources, question_embedding) instead -- callers
    that want to reuse the embedding for caching can opt in without
    forcing a second OpenAI embeddings call.
    """
    clip_metadata = clip_metadata or {}

    # 1. Embed the question
    q_response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[question],
    )
    q_embedding = q_response.data[0].embedding
    _record_embedding_cost(
        tenant_id,
        q_response,
        operation=embedding_operation,
        request_id=request_id,
    )

    # 2. Build metadata filter
    where_clause = build_chroma_filter(filters)

    # 3. Query ChromaDB
    total_chunks = collection.count()
    if total_chunks == 0:
        return None

    query_kwargs = {
        "query_embeddings": [q_embedding],
        "n_results": min(top_k, total_chunks),
        "include": ["documents", "metadatas", "distances"],
    }
    if where_clause:
        query_kwargs["where"] = where_clause

    results = collection.query(**query_kwargs)

    # 4. Deduplicate
    deduped = deduplicate_results(results)

    # 5. Build chunks with metadata for synthesis
    synthesis_chunks = []
    sources = []

    for doc, meta in zip(deduped["documents"], deduped["metadatas"]):
        clip_id = meta.get("clip_id")
        clip_meta = clip_metadata.get(clip_id, {})
        if "url" in meta and "url" not in clip_meta:
            clip_meta = {**clip_meta, "url": meta["url"]}

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

        synthesis_chunks.append(chunk_info)

        # Build source entry
        timestamp = int(meta.get("start_time", 0)) if "start_time" in meta else None
        source_entry = {
            "clip_id": clip_id,
            "date": meta.get("date", ""),
            "title": clip_meta.get("title", "Unknown Meeting"),
            "meeting_body": meta.get("meeting_body", ""),
            "excerpt": doc[:200] + "..." if len(doc) > 200 else doc,
            "granicus_url": _build_granicus_url(
                clip_id=clip_id,
                timestamp=timestamp or 0,
                clip_meta=clip_meta,
                granicus_url_override=granicus_url_override,
            ),
        }
        if timestamp is not None:
            source_entry["timestamp"] = timestamp
        sources.append(source_entry)

    if return_embedding:
        return synthesis_chunks, sources, q_embedding
    return synthesis_chunks, sources


def ask(question: str, collection, openai_client, clip_metadata: dict = None,
        filters: dict = None, top_k: int = 15, model: str = DEFAULT_MODEL,
        granicus_url_override: str = None,
        tenant_id: str | None = None, request_id: str | None = None) -> dict:
    """Full RAG Q&A: embed question, retrieve, deduplicate, synthesize.

    If a process-wide query cache is wired up via ``init_query_cache`` the
    ask path will:

    1. Check the tenant-scoped exact-match cache on entry and, on hit,
       return the cached response (flagged ``cached: True``) and log a
       $0 cost event so cache hits show up in per-tenant spend views.
    2. On miss, proceed with the normal retrieval + synthesis path.
    3. Persist the fresh response to the cache on successful exit along
       with the question embedding (used for future semantic-match hits).

    Cache failures must never break the query path -- every cache call is
    wrapped in try/except at this layer.
    """
    # --- Cache lookup (exact match) ---------------------------------------
    # Only the authenticated API path passes a tenant_id; the CLI path does
    # not, so the CLI will never hit the cache. That matches the intent:
    # caching is a production perf optimization, not a dev convenience.
    cache = None
    if tenant_id:
        try:
            from api.query_cache import get_query_cache  # local import
            cache = get_query_cache()
        except Exception:
            cache = None

    if cache is not None and tenant_id:
        try:
            cached_resp = cache.get_exact(tenant_id, question, filters)
            if cached_resp is not None:
                # Log a $0 cost event so cache savings are visible.
                try:
                    from api.cost import get_cost_tracker
                    get_cost_tracker().record_llm_call(
                        tenant_id=tenant_id,
                        model=model,
                        operation="cache_hit",
                        input_tokens=0,
                        output_tokens=0,
                        request_id=request_id,
                        module="query_cache",
                    )
                except Exception:
                    import logging as _logging
                    _logging.getLogger(__name__).debug(
                        "cost tracking failed for cache hit", exc_info=True
                    )
                # Mark the response so frontends can show a "Cached" badge.
                if isinstance(cached_resp, dict):
                    cached_resp["cached"] = True
                return cached_resp
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "query_cache.lookup_failed", exc_info=True
            )

    result = _retrieve_and_prepare(question, collection, openai_client,
                                   clip_metadata, filters, top_k,
                                   granicus_url_override=granicus_url_override,
                                   return_embedding=True,
                                   tenant_id=tenant_id,
                                   request_id=request_id,
                                   embedding_operation="rag.ask.embed_query")
    if result is None:
        return {
            "answer": "I don't have enough information to answer this question. The meeting archive may not have been indexed yet.",
            "sources": [],
            "filters_applied": filters or {},
            "chunks_retrieved": 0,
            "cached": False,
        }

    synthesis_chunks, sources, q_embedding = result

    # Synthesize answer via LLM
    messages = build_synthesis_messages(question, synthesis_chunks)
    chat_response = openai_client.chat.completions.create(
        model=model,
        messages=messages,
    )
    answer = chat_response.choices[0].message.content

    _record_llm_cost(
        tenant_id,
        getattr(chat_response, "usage", None),
        model=model,
        operation="rag.ask.synthesize",
        request_id=request_id,
        module="query",
    )

    response = {
        "answer": answer,
        "sources": sources,
        "filters_applied": filters or {},
        "chunks_retrieved": len(sources),
        "cached": False,
    }

    # --- Cache write -------------------------------------------------------
    # Persist the fresh response for future exact-match (and semantic)
    # lookups. We store the question embedding alongside so a future
    # semantic-cache lookup can skip re-embedding. Never break the query
    # path on cache write failure.
    if cache is not None and tenant_id:
        try:
            cache.put(
                tenant_id=tenant_id,
                question=question,
                filters=filters,
                response=response,
                embedding=q_embedding,
            )
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "query_cache.put_failed", exc_info=True
            )

    return response


def build_chat_synthesis_messages(messages: list[dict], chunks: list[dict]) -> list[dict]:
    """Build messages array for multi-turn chat synthesis."""
    # Build context block (same format as build_synthesis_messages)
    context_parts = []
    for chunk in chunks:
        header = f"--- Meeting: {chunk.get('title', 'Unknown')} | {chunk['date']} | {chunk['meeting_body']} | Clip {chunk['clip_id']} ---"
        source_info = f"[Source: {chunk['source']}"
        if "start_time" in chunk and "end_time" in chunk:
            source_info += f", Timestamp: {_fmt_timestamp(chunk['start_time'])}-{_fmt_timestamp(chunk['end_time'])}"
        elif "start_time" in chunk:
            source_info += f", Timestamp: {_fmt_timestamp(chunk['start_time'])}"
        source_info += "]"
        context_parts.append(f"{header}\n{source_info}\n{chunk['text']}")

    context = "\n\n".join(context_parts)

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


def synthesize_with_anthropic_response(messages: list[dict], anthropic_client,
                                       model: str = "claude-sonnet-4-6"):
    """Call Anthropic and return the raw response object."""
    system_content = messages[0]["content"]
    remaining = messages[1:]

    return anthropic_client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_content,
        messages=remaining,
    )


def synthesize_with_anthropic(messages: list[dict], anthropic_client,
                              model: str = "claude-sonnet-4-6") -> str:
    """Synthesize an answer using the Anthropic API."""
    response = synthesize_with_anthropic_response(messages, anthropic_client, model=model)
    return response.content[0].text


def chat(messages: list[dict], collection, openai_client,
         clip_metadata: dict = None, anthropic_client=None,
         filters: dict = None, model_provider: str = "openai",
         top_k: int = 15, granicus_url_override: str = None,
         tenant_id: str | None = None, request_id: str | None = None) -> dict:
    """Multi-turn chat: retrieve context for latest question, synthesize with history."""
    # Extract latest user message
    question = messages[-1]["content"]

    result = _retrieve_and_prepare(question, collection, openai_client,
                                   clip_metadata, filters, top_k,
                                   granicus_url_override=granicus_url_override,
                                   tenant_id=tenant_id,
                                   request_id=request_id,
                                   embedding_operation="rag.chat.embed_query")
    if result is None:
        return {
            "role": "assistant",
            "content": "I don't have enough information to answer this question. The meeting archive may not have been indexed yet.",
            "sources": [],
            "model_used": "gpt-4o" if model_provider == "openai" else "claude-sonnet",
            "filters_applied": filters or {},
            "chunks_retrieved": 0,
        }

    synthesis_chunks, sources = result

    # Build chat messages with context
    synth_messages = build_chat_synthesis_messages(messages, synthesis_chunks)

    # Route to appropriate provider
    if model_provider == "anthropic":
        if anthropic_client is None:
            raise ValueError("anthropic_client is required when model_provider='anthropic'")
        anthropic_response = synthesize_with_anthropic_response(synth_messages, anthropic_client)
        content = anthropic_response.content[0].text
        model_used = "claude-sonnet"
        _record_llm_cost(
            tenant_id,
            getattr(anthropic_response, "usage", None),
            model=model_used,
            operation="rag.chat.synthesize",
            request_id=request_id,
            module="query",
        )
    else:
        chat_response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=synth_messages,
        )
        content = chat_response.choices[0].message.content
        model_used = "gpt-4o"
        _record_llm_cost(
            tenant_id,
            getattr(chat_response, "usage", None),
            model=model_used,
            operation="rag.chat.synthesize",
            request_id=request_id,
            module="query",
        )

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
    parser = argparse.ArgumentParser(description="Ask questions about city meetings")
    parser.add_argument("question", help="Your question")
    parser.add_argument("--body", help="Filter by meeting body")
    parser.add_argument("--after", help="Filter by date (YYYY-MM-DD)")
    parser.add_argument("--before", help="Filter by date (YYYY-MM-DD)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="LLM model for synthesis")
    parser.add_argument("--top-k", type=int, default=15, help="Number of chunks to retrieve")
    parser.add_argument("--output-dir", default="./meetings_output", help="Output directory")
    args = parser.parse_args()

    from openai import OpenAI
    from dotenv import load_dotenv
    load_dotenv()
    openai_client = OpenAI()

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
