"""Related-clips lookup ("more like this") backed by the vector store.

Surfaces ~5 nearest meetings for any given clip_id. Used by the
MeetingDetail page to surface other meetings on the same topic — a
direct civic-journalism use case (ideas.md §2: "deep-link clips" and
"every mention across 18 years").

Strategy: take the clip's existing summary chunks (already embedded
into the vector store), use the centroid of those embeddings as the
query, fetch the K nearest, drop chunks belonging to the same clip,
then collapse to one entry per related clip with the highest
similarity.

This avoids re-embedding anything — we reuse the embeddings the
ingest pipeline already paid for.
"""

from __future__ import annotations

import logging
from typing import Optional

from rag.vecstore import as_vecstore

logger = logging.getLogger(__name__)


def related(
    clip_id: int,
    store,
    clip_metadata: dict,
    *,
    limit: int = 5,
    fetch_multiplier: int = 8,
) -> list[dict]:
    """Return up to ``limit`` clips most similar to ``clip_id``.

    Output rows carry just enough metadata to render a card on the
    detail page: clip_id, title, date, meeting_body, similarity score
    (1.0 = same content, 0.0 = unrelated). The score is derived from
    cosine distance: ``1 - distance``.
    """
    store = as_vecstore(store)
    total = store.count()
    if total == 0:
        return []

    # Pull the source clip's summary chunks; fall back to any chunks
    # if the clip wasn't summarized (legacy / failed-summary clips).
    src = store.get_chunks(
        filters={"clip_id": int(clip_id), "source": "summary"},
        limit=10,
        include_embeddings=True,
    )
    if not src:
        src = store.get_chunks(
            filters={"clip_id": int(clip_id)},
            limit=5,
            include_embeddings=True,
        )
    if not src:
        return []

    # Centroid of the source embeddings — averaged so we don't
    # overweight one short summary section.
    dim = len(src[0]["embedding"])
    centroid = [0.0] * dim
    for chunk in src:
        for i, v in enumerate(chunk["embedding"]):
            centroid[i] += float(v)
    n = len(src)
    centroid = [v / n for v in centroid]

    n_results = min(limit * fetch_multiplier, max(total, 1))
    hits = store.query(centroid, k=n_results)

    seen: dict[int, dict] = {}
    for hit in hits:
        meta = hit["metadata"]
        dist = hit["distance"]
        cid = meta.get("clip_id")
        if cid is None or int(cid) == int(clip_id):
            continue
        cid = int(cid)
        if cid in seen:
            # Keep the closest (smallest distance) entry per clip.
            if dist < seen[cid]["_dist"]:
                seen[cid]["_dist"] = dist
            continue

        clip_meta = clip_metadata.get(cid) or {}
        seen[cid] = {
            "clip_id": cid,
            "title": clip_meta.get("title") or meta.get("title") or f"Clip {cid}",
            "date": clip_meta.get("date") or meta.get("date"),
            "meeting_body": clip_meta.get("meeting_body") or meta.get("meeting_body"),
            "_dist": dist,
        }
        if len(seen) >= limit:
            break

    out = sorted(seen.values(), key=lambda r: r["_dist"])[:limit]
    return [
        {
            "clip_id": r["clip_id"],
            "title": r["title"],
            "date": r["date"],
            "meeting_body": r["meeting_body"],
            "similarity": max(0.0, 1.0 - float(r["_dist"])),
        }
        for r in out
    ]
