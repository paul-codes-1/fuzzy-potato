"""Retrieval and synthesis logic for RAG Q&A."""

import argparse
import json
import logging
import os
from collections import defaultdict

logger = logging.getLogger(__name__)

from rag.ingest import EMBEDDING_MODEL, get_chroma_collection
from rag.prompts import SYNTHESIS_SYSTEM_PROMPT, CHAT_SYSTEM_PROMPT, QUERY_REWRITE_PROMPT

DEFAULT_MODEL = "gpt-4o"
MAX_HISTORY_PAIRS = 10
MAX_REWRITTEN_QUERIES = 3


def granicus_clip_url(clip_id, timestamp: int = 0) -> str:
    """Build a Granicus deep-link URL. Reads GRANICUS_HOST / GRANICUS_VIEW_ID
    at call time so deployments that load .env after import still pick up
    the right tenant."""
    host = os.getenv("GRANICUS_HOST", "lfucg.granicus.com")
    view_id = os.getenv("GRANICUS_VIEW_ID", "14")
    return f"https://{host}/player/clip/{clip_id}?view_id={view_id}&entrytime={timestamp}"


def build_chroma_filter(filters: dict | None) -> dict | None:
    """Build a ChromaDB where clause from filter parameters."""
    if not filters:
        return None

    conditions = []

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
                          top_k: int = 15) -> tuple[list[dict], list[dict]] | None:
    """Rewrite query, embed, retrieve from ChromaDB, deduplicate, build chunks and sources.

    Returns (synthesis_chunks, sources) or None if collection is empty.
    """
    clip_metadata = clip_metadata or {}

    total_chunks = collection.count()
    if total_chunks == 0:
        return None

    # 1. Rewrite the question into focused search queries
    search_queries = rewrite_query(question, openai_client)

    # 2. Embed all queries
    q_response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=search_queries,
    )
    embeddings = [item.embedding for item in q_response.data]

    # 3. Build metadata filter
    where_clause = build_chroma_filter(filters)

    # 4. Query ChromaDB for each rewritten query and merge results
    all_ids = []
    all_documents = []
    all_metadatas = []
    all_distances = []
    seen_ids = set()

    per_query_k = max(top_k, 10)

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
                if id_ not in seen_ids:
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

    deduped_count = len(deduped["ids"])
    logger.info("Retrieved %d unique chunks (%d after dedup) for question: %s",
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

        synthesis_chunks.append(chunk_info)

        # Build source entry
        timestamp = int(meta.get("start_time", 0)) if "start_time" in meta else None
        source_entry = {
            "clip_id": clip_id,
            "date": meta.get("date", ""),
            "title": clip_meta.get("title", "Unknown Meeting"),
            "meeting_body": meta.get("meeting_body", ""),
            "excerpt": doc[:200] + "..." if len(doc) > 200 else doc,
            "granicus_url": granicus_clip_url(clip_id, timestamp or 0),
        }
        if timestamp is not None:
            source_entry["timestamp"] = timestamp
        sources.append(source_entry)

    return synthesis_chunks, sources


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
    )
    answer = chat_response.choices[0].message.content
    logger.info("=== LLM Response (%d chars) ===", len(answer))

    return {
        "answer": answer,
        "sources": sources,
        "filters_applied": filters or {},
        "chunks_retrieved": len(sources),
    }


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


def synthesize_with_anthropic(messages: list[dict], anthropic_client,
                              model: str = "claude-sonnet-4-6") -> str:
    """Synthesize an answer using the Anthropic API."""
    system_content = messages[0]["content"]
    remaining = messages[1:]

    response = anthropic_client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_content,
        messages=remaining,
    )
    return response.content[0].text


def chat(messages: list[dict], collection, openai_client,
         clip_metadata: dict = None, anthropic_client=None,
         filters: dict = None, model_provider: str = "openai",
         top_k: int = 15) -> dict:
    """Multi-turn chat: retrieve context for latest question, synthesize with history."""
    # Extract latest user message
    question = messages[-1]["content"]

    result = _retrieve_and_prepare(question, collection, openai_client,
                                   clip_metadata, filters, top_k)
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
            model="gpt-4o",
            messages=synth_messages,
        )
        content = chat_response.choices[0].message.content
        model_used = "gpt-4o"

    logger.info("=== Chat LLM Response (%s, %d chars) ===", model_used, len(content))

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
