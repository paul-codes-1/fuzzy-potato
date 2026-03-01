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
MIN_CHUNK_WORDS = 200

# Procedural phrases that signal topic boundaries in meeting transcripts
TOPIC_BOUNDARY_PHRASES = [
    "next item", "moving on", "next order of business", "public comment",
    "roll call", "ordinance", "resolution",
]

# Short filler words to remove when they appear as standalone isolated segments
FILLER_WORDS = {"so", "um", "uh", "oh", "you know", "yeah", "okay", "right", "well"}


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


def clean_segments(segments: list[dict]) -> list[dict]:
    """Filter out noisy Whisper segments before chunking.

    Removes: dot/symbol-only segments, non-English gibberish, stuck repetition
    loops, very short filler, and song lyrics at meeting end.
    """
    if not segments:
        return []

    cleaned = []

    # --- Pass 1: Remove dot/symbol-only, non-ASCII gibberish, short filler ---
    for seg in segments:
        text = seg["text"].strip()

        # Dot/symbol-only segments
        if re.match(r'^[\s.\u266a\u266b\U0001f3b5\U0001f3b6♪🎵🎶]*$', text):
            continue
        if text.lower() in {"music", ".", "...", "♪", "🎵"}:
            continue

        # Non-English gibberish: >50% non-ASCII characters
        if text:
            non_ascii = sum(1 for c in text if ord(c) > 127)
            if non_ascii / len(text) > 0.5:
                continue

        # Very short filler (under 3 words, common filler as standalone)
        words = text.split()
        if len(words) < 3 and text.lower().strip(".,!?") in FILLER_WORDS:
            continue

        cleaned.append(seg)

    # --- Pass 2: Collapse stuck repetition loops (5+ identical consecutive) ---
    if cleaned:
        deduped = [cleaned[0]]
        run_count = 1
        for i in range(1, len(cleaned)):
            if cleaned[i]["text"].strip() == cleaned[i - 1]["text"].strip():
                run_count += 1
                if run_count <= 1:  # keep only the first occurrence
                    deduped.append(cleaned[i])
            else:
                run_count = 1
                deduped.append(cleaned[i])
        cleaned = deduped

    # --- Pass 3: Strip song lyrics at meeting end ---
    # Heuristic: if 10+ consecutive segments in the last 10% have short poetic
    # lines and no procedural language, strip them.
    if len(cleaned) >= 10:
        cutoff_idx = int(len(cleaned) * 0.9)
        tail = cleaned[cutoff_idx:]
        procedural_found = False
        short_poetic_count = 0
        for seg in tail:
            text_lower = seg["text"].strip().lower()
            if any(phrase in text_lower for phrase in TOPIC_BOUNDARY_PHRASES):
                procedural_found = True
                break
            words = seg["text"].split()
            if len(words) <= 12:
                short_poetic_count += 1
        if not procedural_found and short_poetic_count >= 10:
            cleaned = cleaned[:cutoff_idx]

    return cleaned


def chunk_transcript(segments: list[dict], clip_id: int, date: str, meeting_body: str,
                     target_words: int = TARGET_WORDS, overlap_words: int = OVERLAP_WORDS) -> list[dict]:
    """Group transcript segments into ~target_words passages with overlap.

    Uses topic-aware boundaries: silence gaps (>5s between segments) and
    procedural transition phrases trigger chunk boundaries when at least
    MIN_CHUNK_WORDS have been accumulated.
    """
    if not segments:
        return []

    # Clean noisy segments first
    segments = clean_segments(segments)
    if not segments:
        return []

    def _is_topic_boundary(prev_seg: dict, curr_seg: dict) -> bool:
        """Detect natural topic boundaries between segments."""
        # Silence gap: >5 seconds between previous end and current start
        if curr_seg["start"] - prev_seg["end"] > 5.0:
            return True
        # Procedural phrases in the current segment text
        text_lower = curr_seg["text"].strip().lower()
        if any(phrase in text_lower for phrase in TOPIC_BOUNDARY_PHRASES):
            return True
        return False

    def _emit_chunk(segs: list[dict]) -> dict:
        text = " ".join(s["text"] for s in segs)
        return {
            "text": text,
            "clip_id": clip_id,
            "date": date,
            "meeting_body": meeting_body,
            "source": "transcript",
            "start_time": segs[0]["start"],
            "end_time": segs[-1]["end"],
        }

    def _overlap_tail(segs: list[dict]) -> list[dict]:
        """Return trailing segments worth ~overlap_words."""
        overlap_segs = []
        overlap_count = 0
        for s in reversed(segs):
            overlap_count += len(s["text"].split())
            overlap_segs.insert(0, s)
            if overlap_count >= overlap_words:
                break
        return overlap_segs

    chunks = []
    current_segments = []
    current_word_count = 0

    for i, seg in enumerate(segments):
        seg_words = len(seg["text"].split())
        current_segments.append(seg)
        current_word_count += seg_words

        # Check for topic boundary (only if we have accumulated enough words)
        at_boundary = (
            i > 0
            and current_word_count >= MIN_CHUNK_WORDS
            and _is_topic_boundary(segments[i - 1], seg)
        )

        if at_boundary or current_word_count >= target_words:
            chunks.append(_emit_chunk(current_segments))
            current_segments = _overlap_tail(current_segments)
            current_word_count = sum(len(s["text"].split()) for s in current_segments)

    # Emit remaining segments as final chunk
    if current_segments:
        chunks.append(_emit_chunk(current_segments))

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


MAX_CHUNK_CHARS = 20000  # ~6K tokens for normal text; safe even for garbled OCR (~1 token/char)


def _truncate_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> str:
    """Truncate text by character count to stay within embedding token limits.

    Character-based is more reliable than word-based because OCR-garbled text
    can have very long 'words' (25+ chars each) that tokenize into many tokens.
    """
    if len(text) <= max_chars:
        return text
    # Truncate at char limit, then trim to last word boundary to avoid partial words
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars // 2:
        truncated = truncated[:last_space]
    return truncated


def _embed_single(text: str, openai_client) -> list[float]:
    """Embed a single text, progressively truncating on token limit errors."""
    limit = MAX_CHUNK_CHARS
    while limit >= 2000:
        truncated = _truncate_text(text, max_chars=limit)
        try:
            resp = openai_client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=[truncated],
            )
            return resp.data[0].embedding
        except Exception as e:
            if "maximum context length" in str(e):
                limit = limit // 2  # halve the limit and retry
            else:
                raise
    # Last resort: embed just the first 2000 chars
    resp = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[text[:2000]],
    )
    return resp.data[0].embedding


def _embed_batch(texts: list[str], openai_client) -> list[list[float]]:
    """Embed a batch of texts, falling back to one-at-a-time on token limit errors."""
    try:
        response = openai_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
        )
        return [item.embedding for item in response.data]
    except Exception as e:
        if "maximum context length" in str(e):
            # Batch too large or single text too long — embed one at a time
            return [_embed_single(text, openai_client) for text in texts]
        else:
            raise


def store_chunks(chunks: list[dict], collection, openai_client, batch_size: int = 100):
    """Embed chunks via OpenAI and store in ChromaDB collection."""
    if not chunks:
        return

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        texts = [_truncate_text(c["text"]) for c in batch]

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

    date = metadata.get("date") or ""
    meeting_body = metadata.get("meeting_body") or ""
    files = metadata.get("files", {})

    all_chunks = []

    # 1. Minutes chunks (highest priority — structured ground-truth source)
    minutes_file = files.get("minutes_txt")
    if minutes_file:
        minutes_path = clip_dir / minutes_file
        if minutes_path.exists():
            minutes_text = minutes_path.read_text()
            all_chunks.extend(chunk_document(minutes_text, clip_id, date, meeting_body, source="minutes"))

    # 2. Agenda chunks
    agenda_file = files.get("agenda_txt")
    if agenda_file:
        agenda_path = clip_dir / agenda_file
        if agenda_path.exists():
            agenda_text = agenda_path.read_text()
            all_chunks.extend(chunk_document(agenda_text, clip_id, date, meeting_body, source="agenda"))

    # 3. Transcript chunks (cleaned and topic-aware)
    segments_file = files.get("transcript_segments")
    if segments_file:
        segments_path = clip_dir / segments_file
        if segments_path.exists() and segments_path.stat().st_size > 0:
            with open(segments_path) as f:
                segments = json.load(f)
            all_chunks.extend(chunk_transcript(segments, clip_id, date, meeting_body))

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
