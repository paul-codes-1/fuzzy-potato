"""FastAPI server for RAG Q&A."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel

from rag.ingest import get_chroma_collection
from rag.query import ask, load_clip_metadata

load_dotenv()

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.environ.get("LFUCG_OUTPUT_DIR", "./lfucg_output")

# Pre-load at module level so startup completes before health checks
_collection = None
_clip_metadata = None


def _get_collection():
    global _collection
    if _collection is None:
        _collection = get_chroma_collection(OUTPUT_DIR)
    return _collection


def _get_clip_metadata():
    global _clip_metadata
    if _clip_metadata is None:
        _clip_metadata = load_clip_metadata(OUTPUT_DIR)
    return _clip_metadata


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup — ChromaDB loads lazily on first request."""
    logger.info("RAG API starting (ChromaDB will load on first request)")
    yield


app = FastAPI(title="LFUCG Meeting RAG API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str
    meeting_body: Optional[str] = None
    date_after: Optional[str] = None
    date_before: Optional[str] = None


@app.post("/ask")  # Direct endpoint for App Runner
def ask_endpoint_direct(request: AskRequest):
    return ask_endpoint(request)


@app.post("/api/ask")
def ask_endpoint(request: AskRequest):
    collection = _get_collection()
    openai_client = OpenAI()
    clip_metadata = _get_clip_metadata()

    filters = {}
    if request.meeting_body:
        filters["meeting_body"] = request.meeting_body
    if request.date_after:
        filters["date_after"] = request.date_after
    if request.date_before:
        filters["date_before"] = request.date_before

    result = ask(
        question=request.question,
        collection=collection,
        openai_client=openai_client,
        clip_metadata=clip_metadata,
        filters=filters if filters else None,
    )

    return result


@app.get("/health")
def health_endpoint():
    """Lightweight health check — no ChromaDB loading."""
    result = {"status": "ok"}
    if _collection is not None:
        result["chunks_indexed"] = _collection.count()
    if _clip_metadata is not None:
        result["clips_indexed"] = len(_clip_metadata)
    return result


@app.get("/api/health")  # Keep for CloudFront routing
def health_endpoint_api():
    return health_endpoint()
