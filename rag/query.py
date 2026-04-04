"""Retrieval and synthesis logic for RAG Q&A."""

import argparse
import json
import os
from collections import defaultdict

from rag.ingest import EMBEDDING_MODEL, get_chroma_collection
from rag.prompts import SYNTHESIS_SYSTEM_PROMPT

GRANICUS_URL_TEMPLATE = "https://lfucg.granicus.com/player/clip/{clip_id}?view_id=14&entrytime={timestamp}"
DEFAULT_MODEL = "gpt-4o"


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


def ask(question: str, collection, openai_client, clip_metadata: dict = None,
        filters: dict = None, top_k: int = 15, model: str = DEFAULT_MODEL) -> dict:
    """Full RAG Q&A: embed question, retrieve, deduplicate, synthesize."""
    clip_metadata = clip_metadata or {}

    # 1. Embed the question
    q_response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[question],
    )
    q_embedding = q_response.data[0].embedding

    # 2. Build metadata filter
    where_clause = build_chroma_filter(filters)

    # 3. Query ChromaDB
    total_chunks = collection.count()
    if total_chunks == 0:
        return {
            "answer": "I don't have enough information to answer this question. The meeting archive may not have been indexed yet.",
            "sources": [],
            "filters_applied": filters or {},
            "chunks_retrieved": 0,
        }

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
            "granicus_url": GRANICUS_URL_TEMPLATE.format(
                clip_id=clip_id,
                timestamp=timestamp or 0,
            ),
        }
        if timestamp is not None:
            source_entry["timestamp"] = timestamp
        sources.append(source_entry)

    # 6. Synthesize answer via LLM
    messages = build_synthesis_messages(question, synthesis_chunks)
    chat_response = openai_client.chat.completions.create(
        model=model,
        messages=messages,
    )
    answer = chat_response.choices[0].message.content

    return {
        "answer": answer,
        "sources": sources,
        "filters_applied": filters or {},
        "chunks_retrieved": len(deduped["documents"]),
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
