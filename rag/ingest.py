"""Ingestion pipeline: chunk existing clip outputs and store in ChromaDB."""

import argparse
import json
import os
import re
import sys
import hashlib
from pathlib import Path

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 1536
COLLECTION_NAME = "lfucg_meetings"
RAG_STATE_FILE = "rag_state.json"
CHROMA_DIR = "chroma_db"
TARGET_WORDS = 500
OVERLAP_WORDS = 100


# ============================================================
# Chunking functions
# ============================================================

def chunk_summary(summary_text: str, clip_id: int, date: str, meeting_body: str) -> list[dict]:
    """Parse summary.txt by ## section headers into chunks."""
    if not summary_text.strip():
        return []

    sections = re.split(r'^## ', summary_text, flags=re.MULTILINE)
    chunks = []

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # Extract section header (first line) and body
        lines = section.split('\n', 1)
        section_type = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""

        # If no ## headers found, the entire text lands in sections[0]
        # with no header prefix
        if not chunks and not summary_text.lstrip().startswith('## '):
            section_type = "General"
            body = section

        text = f"{section_type}\n{body}" if body else section_type

        chunks.append({
            "text": text,
            "clip_id": clip_id,
            "date": date,
            "meeting_body": meeting_body,
            "source": "summary",
            "section_type": section_type,
        })

    return chunks


def chunk_transcript(segments: list[dict], clip_id: int, date: str, meeting_body: str,
                     target_words: int = TARGET_WORDS, overlap_words: int = OVERLAP_WORDS) -> list[dict]:
    """Group transcript segments into ~target_words passages with overlap."""
    if not segments:
        return []

    chunks = []
    current_segments = []
    current_word_count = 0

    for seg in segments:
        seg_words = len(seg["text"].split())
        current_segments.append(seg)
        current_word_count += seg_words

        if current_word_count >= target_words:
            # Emit a chunk
            text = " ".join(s["text"] for s in current_segments)
            chunks.append({
                "text": text,
                "clip_id": clip_id,
                "date": date,
                "meeting_body": meeting_body,
                "source": "transcript",
                "start_time": current_segments[0]["start"],
                "end_time": current_segments[-1]["end"],
            })

            # Keep overlap: walk backward from end to find ~overlap_words worth of segments
            overlap_segs = []
            overlap_count = 0
            for s in reversed(current_segments):
                overlap_count += len(s["text"].split())
                overlap_segs.insert(0, s)
                if overlap_count >= overlap_words:
                    break

            current_segments = overlap_segs
            current_word_count = sum(len(s["text"].split()) for s in current_segments)

    # Emit remaining segments as final chunk
    if current_segments:
        text = " ".join(s["text"] for s in current_segments)
        chunks.append({
            "text": text,
            "clip_id": clip_id,
            "date": date,
            "meeting_body": meeting_body,
            "source": "transcript",
            "start_time": current_segments[0]["start"],
            "end_time": current_segments[-1]["end"],
        })

    return chunks


def chunk_document(text: str, clip_id: int, date: str, meeting_body: str, source: str,
                   target_words: int = TARGET_WORDS) -> list[dict]:
    """Split agenda/minutes text by section boundaries into ~target_words chunks."""
    if not text.strip():
        return []

    # Split on Roman numerals (I., II., III., etc.) or numbered sections or blank-line-separated blocks
    section_pattern = r'(?:^|\n)(?=[IVX]+\.\s|\d+\.\s|[A-Z][A-Z ]+\n)'
    raw_sections = re.split(section_pattern, text)

    # Merge small sections and split large ones to stay near target_words
    chunks = []
    current_text = ""
    current_word_count = 0

    for section in raw_sections:
        section = section.strip()
        if not section:
            continue

        section_words = len(section.split())

        if current_word_count + section_words <= target_words:
            current_text += ("\n\n" if current_text else "") + section
            current_word_count += section_words
        else:
            # Emit current buffer if non-empty
            if current_text.strip():
                chunks.append({
                    "text": current_text.strip(),
                    "clip_id": clip_id,
                    "date": date,
                    "meeting_body": meeting_body,
                    "source": source,
                })

            # If this section alone exceeds target, split it by paragraphs
            if section_words > target_words:
                paragraphs = section.split('\n')
                current_text = ""
                current_word_count = 0
                for para in paragraphs:
                    para = para.strip()
                    if not para:
                        continue
                    para_words = len(para.split())
                    if current_word_count + para_words <= target_words:
                        current_text += ("\n" if current_text else "") + para
                        current_word_count += para_words
                    else:
                        if current_text.strip():
                            chunks.append({
                                "text": current_text.strip(),
                                "clip_id": clip_id,
                                "date": date,
                                "meeting_body": meeting_body,
                                "source": source,
                            })
                        current_text = para
                        current_word_count = para_words
            else:
                current_text = section
                current_word_count = section_words

    # Emit remaining text
    if current_text.strip():
        chunks.append({
            "text": current_text.strip(),
            "clip_id": clip_id,
            "date": date,
            "meeting_body": meeting_body,
            "source": source,
        })

    return chunks


# ============================================================
# Embedding and storage
# ============================================================

def _chunk_id(chunk: dict, index: int) -> str:
    """Generate a deterministic unique ID for a chunk."""
    key = f"{chunk['clip_id']}_{chunk['source']}_{chunk.get('section_type', '')}_{index}"
    return hashlib.md5(key.encode()).hexdigest()


MAX_CHUNK_WORDS = 5000  # ~6K tokens, safely under 8192 token embedding limit


def _truncate_text(text: str, max_words: int = MAX_CHUNK_WORDS) -> str:
    """Truncate text to max_words to stay within embedding token limits."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def _embed_batch(texts: list[str], openai_client) -> list[list[float]]:
    """Embed a batch of texts, falling back to one-at-a-time on token limit errors."""
    try:
        response = openai_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
        )
        return [item.embedding for item in response.data]
    except Exception as e:
        if "maximum context length" in str(e) and len(texts) > 1:
            # Batch too large — embed one at a time
            embeddings = []
            for text in texts:
                truncated = _truncate_text(text)
                resp = openai_client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=[truncated],
                )
                embeddings.append(resp.data[0].embedding)
            return embeddings
        elif "maximum context length" in str(e):
            # Single text too long — truncate
            truncated = _truncate_text(texts[0])
            resp = openai_client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=[truncated],
            )
            return [resp.data[0].embedding]
        else:
            raise


def store_chunks(chunks: list[dict], collection, openai_client, batch_size: int = 100):
    """Embed chunks via OpenAI and store in ChromaDB collection."""
    if not chunks:
        return

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        texts = [c["text"] for c in batch]

        # Get embeddings (with automatic fallback for token limits)
        embeddings = _embed_batch(texts, openai_client)

        # Build IDs, documents, and metadata
        ids = []
        documents = []
        metadatas = []

        for j, chunk in enumerate(batch):
            ids.append(_chunk_id(chunk, i + j))
            documents.append(chunk["text"])

            meta = {
                "clip_id": chunk["clip_id"],
                "date": chunk["date"],
                "meeting_body": chunk["meeting_body"],
                "source": chunk["source"],
            }
            if "section_type" in chunk:
                meta["section_type"] = chunk["section_type"]
            if "start_time" in chunk:
                meta["start_time"] = chunk["start_time"]
            if "end_time" in chunk:
                meta["end_time"] = chunk["end_time"]

            metadatas.append(meta)

        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )


# ============================================================
# Clip ingestion
# ============================================================

def ingest_clip(clip_id: int, output_dir, collection, openai_client,
                skip_if_ingested: bool = False, verbose: bool = False):
    """Ingest a single clip: read its files, chunk, embed, and store."""
    output_dir = Path(output_dir)

    if skip_if_ingested:
        state = load_rag_state(output_dir)
        if clip_id in state["ingested_clips"]:
            if verbose:
                print(f"  Skipping clip {clip_id} (already ingested)")
            return

    clip_dir = output_dir / "clips" / str(clip_id)
    metadata_path = clip_dir / "metadata.json"

    if not metadata_path.exists():
        if verbose:
            print(f"  Skipping clip {clip_id} (no metadata.json)")
        return

    with open(metadata_path) as f:
        metadata = json.load(f)

    date = metadata.get("date", "")
    meeting_body = metadata.get("meeting_body", "")
    files = metadata.get("files", {})

    all_chunks = []

    # 1. Summary chunks
    summary_file = files.get("summary_txt")
    if summary_file:
        summary_path = clip_dir / summary_file
        if summary_path.exists():
            summary_text = summary_path.read_text()
            all_chunks.extend(chunk_summary(summary_text, clip_id, date, meeting_body))

    # 2. Transcript chunks
    segments_file = files.get("transcript_segments")
    if segments_file:
        segments_path = clip_dir / segments_file
        if segments_path.exists():
            with open(segments_path) as f:
                segments = json.load(f)
            all_chunks.extend(chunk_transcript(segments, clip_id, date, meeting_body))

    # 3. Agenda chunks
    agenda_file = files.get("agenda_txt")
    if agenda_file:
        agenda_path = clip_dir / agenda_file
        if agenda_path.exists():
            agenda_text = agenda_path.read_text()
            all_chunks.extend(chunk_document(agenda_text, clip_id, date, meeting_body, source="agenda"))

    # 4. Minutes chunks
    minutes_file = files.get("minutes_txt")
    if minutes_file:
        minutes_path = clip_dir / minutes_file
        if minutes_path.exists():
            minutes_text = minutes_path.read_text()
            all_chunks.extend(chunk_document(minutes_text, clip_id, date, meeting_body, source="minutes"))

    if all_chunks:
        store_chunks(all_chunks, collection, openai_client)
        if verbose:
            print(f"  Ingested clip {clip_id}: {len(all_chunks)} chunks")


# ============================================================
# RAG state management
# ============================================================

def load_rag_state(output_dir) -> dict:
    """Load RAG ingestion state from rag_state.json."""
    state_path = Path(output_dir) / RAG_STATE_FILE
    if state_path.exists():
        with open(state_path) as f:
            return json.load(f)
    return {"ingested_clips": []}


def save_rag_state(state: dict, output_dir):
    """Save RAG ingestion state to rag_state.json."""
    state_path = Path(output_dir) / RAG_STATE_FILE
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


# ============================================================
# Stats
# ============================================================

def get_stats(collection, output_dir: str = None) -> dict:
    """Get statistics about the ChromaDB collection."""
    total_chunks = collection.count()

    # Get unique clip count from rag_state (avoids fetching all metadatas)
    unique_clips = 0
    if output_dir:
        state = load_rag_state(output_dir)
        unique_clips = len(state.get("ingested_clips", []))
    elif total_chunks > 0:
        # Fallback: paginate through collection
        unique_clip_ids = set()
        batch_size = 5000
        offset = 0
        while offset < total_chunks:
            results = collection.get(
                include=["metadatas"],
                limit=batch_size,
                offset=offset,
            )
            for meta in results["metadatas"]:
                unique_clip_ids.add(meta.get("clip_id"))
            if len(results["metadatas"]) < batch_size:
                break
            offset += batch_size
        unique_clips = len(unique_clip_ids)

    return {
        "total_chunks": total_chunks,
        "unique_clips": unique_clips,
    }


# ============================================================
# CLI
# ============================================================

def get_chroma_collection(output_dir: str):
    """Get or create the ChromaDB collection with persistent storage."""
    import chromadb

    chroma_path = os.path.join(output_dir, CHROMA_DIR)
    client = chromadb.PersistentClient(path=chroma_path)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def main():
    parser = argparse.ArgumentParser(description="RAG ingestion for LFUCG meetings")
    parser.add_argument("--all", action="store_true", help="Ingest all existing clips")
    parser.add_argument("--clip", type=int, help="Ingest a specific clip ID")
    parser.add_argument("--new", action="store_true", help="Ingest only new (not yet embedded) clips")
    parser.add_argument("--stats", action="store_true", help="Show collection statistics")
    parser.add_argument("--output-dir", default="./lfucg_output", help="Output directory")
    args = parser.parse_args()

    output_dir = args.output_dir

    if args.stats:
        collection = get_chroma_collection(output_dir)
        stats = get_stats(collection, output_dir)
        print(f"Total chunks: {stats['total_chunks']}")
        print(f"Unique clips: {stats['unique_clips']}")
        return

    # Initialize OpenAI client
    from openai import OpenAI
    from dotenv import load_dotenv
    load_dotenv()
    openai_client = OpenAI()

    collection = get_chroma_collection(output_dir)

    if args.clip:
        ingest_clip(args.clip, output_dir, collection, openai_client, verbose=True)
        state = load_rag_state(output_dir)
        if args.clip not in state["ingested_clips"]:
            state["ingested_clips"].append(args.clip)
            save_rag_state(state, output_dir)
        print("Done.")
        return

    # Find all clip directories with metadata.json
    clips_dir = os.path.join(output_dir, "clips")
    if not os.path.isdir(clips_dir):
        print("No clips directory found.")
        return

    clip_ids = []
    for name in sorted(os.listdir(clips_dir)):
        clip_path = os.path.join(clips_dir, name)
        if os.path.isdir(clip_path) and os.path.exists(os.path.join(clip_path, "metadata.json")):
            try:
                clip_ids.append(int(name))
            except ValueError:
                continue

    state = load_rag_state(output_dir)

    if args.new:
        clip_ids = [cid for cid in clip_ids if cid not in state["ingested_clips"]]

    print(f"Ingesting {len(clip_ids)} clips...")
    failed = []
    for i, clip_id in enumerate(clip_ids):
        print(f"[{i + 1}/{len(clip_ids)}] Clip {clip_id}")
        try:
            ingest_clip(clip_id, output_dir, collection, openai_client,
                        skip_if_ingested=args.new, verbose=True)
            if clip_id not in state["ingested_clips"]:
                state["ingested_clips"].append(clip_id)
                save_rag_state(state, output_dir)
        except Exception as e:
            print(f"  ERROR: {e}")
            failed.append(clip_id)

    print(f"Done. {len(clip_ids) - len(failed)} clips ingested.")
    if failed:
        print(f"Failed: {len(failed)} clips: {failed}")
    stats = get_stats(collection, output_dir)
    print(f"Total chunks: {stats['total_chunks']}, Unique clips: {stats['unique_clips']}")


if __name__ == "__main__":
    main()
