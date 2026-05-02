"""FastAPI server for RAG Q&A."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from clients import get_anthropic, get_openai
from rag.ingest import get_chroma_collection
from rag.query import ask, chat, load_clip_metadata
from rag.related import related as related_clips
from rag.search import facets as search_facets, search as search_clips, suggest as search_suggest

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("rag.query").setLevel(logging.INFO)

OUTPUT_DIR = os.environ.get("LFUCG_OUTPUT_DIR", "./lfucg_output")

# Per-process caches for things only this server needs (Chroma collection,
# clip metadata). The OpenAI/Anthropic clients are cached in clients.py.
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


# Wrappers (not aliases) so test patches against `rag.server.get_openai`
# / `rag.server.get_anthropic` are honored — direct assignment would
# resolve the function object at import time and bypass the patch.
def _get_openai_client():
    return get_openai()


def _get_anthropic_client():
    return get_anthropic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup — ChromaDB loads lazily on first request."""
    logger.info("RAG API starting (ChromaDB will load on first request)")
    yield


app = FastAPI(title="LFUCG Meeting RAG API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class AskRequest(BaseModel):
    question: str
    meeting_body: Optional[str] = None
    date_after: Optional[str] = None
    date_before: Optional[str] = None

    @field_validator("question")
    @classmethod
    def question_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be empty")
        if len(v) > 2000:
            raise ValueError("question must be under 2000 characters")
        return v


MAX_CHAT_MESSAGE_CHARS = 4000
MAX_CHAT_MESSAGES = 24


class ChatMessage(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: str) -> str:
        if v not in ("user", "assistant"):
            raise ValueError("role must be 'user' or 'assistant'")
        return v

    @field_validator("content")
    @classmethod
    def content_not_too_long(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message content must not be empty")
        if len(v) > MAX_CHAT_MESSAGE_CHARS:
            raise ValueError(f"message content must be under {MAX_CHAT_MESSAGE_CHARS} characters")
        return v


MAX_SEARCH_QUERY_CHARS = 200
MAX_SEARCH_LIMIT = 100


class SearchRequest(BaseModel):
    q: str
    meeting_body: Optional[str] = None
    speaker: Optional[str] = None
    date_after: Optional[str] = None
    date_before: Optional[str] = None
    limit: int = 50

    @field_validator("q")
    @classmethod
    def q_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("q must not be empty")
        if len(v) > MAX_SEARCH_QUERY_CHARS:
            raise ValueError(f"q must be under {MAX_SEARCH_QUERY_CHARS} characters")
        return v

    @field_validator("limit")
    @classmethod
    def limit_in_range(cls, v: int) -> int:
        if v < 1:
            return 1
        if v > MAX_SEARCH_LIMIT:
            return MAX_SEARCH_LIMIT
        return v


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    meeting_body: Optional[str] = None
    date_after: Optional[str] = None
    date_before: Optional[str] = None
    model_provider: str = "openai"

    @field_validator("messages")
    @classmethod
    def messages_not_empty(cls, v):
        if not v:
            raise ValueError("messages must not be empty")
        if len(v) > MAX_CHAT_MESSAGES:
            raise ValueError(f"messages must contain no more than {MAX_CHAT_MESSAGES} entries")
        if v[-1].role != "user":
            raise ValueError("last message must be from the user")
        return v

    @field_validator("model_provider")
    @classmethod
    def model_provider_must_be_valid(cls, v: str) -> str:
        if v not in ("openai", "anthropic"):
            raise ValueError("model_provider must be 'openai' or 'anthropic'")
        return v


@app.post("/ask")  # Direct endpoint for App Runner
def ask_endpoint_direct(request: AskRequest):
    return ask_endpoint(request)


@app.post("/api/ask")
def ask_endpoint(request: AskRequest):
    try:
        collection = _get_collection()
        openai_client = _get_openai_client()
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
    except Exception as e:
        logger.error("ask_endpoint failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred processing your question.")


@app.post("/api/chat")
def chat_endpoint(request: ChatRequest):
    try:
        collection = _get_collection()
        openai_client = _get_openai_client()
        clip_metadata = _get_clip_metadata()

        filters = {}
        if request.meeting_body:
            filters["meeting_body"] = request.meeting_body
        if request.date_after:
            filters["date_after"] = request.date_after
        if request.date_before:
            filters["date_before"] = request.date_before

        anthropic_client = None
        if request.model_provider == "anthropic":
            anthropic_client = _get_anthropic_client()

        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        result = chat(
            messages=messages,
            collection=collection,
            openai_client=openai_client,
            clip_metadata=clip_metadata,
            anthropic_client=anthropic_client,
            filters=filters if filters else None,
            model_provider=request.model_provider,
        )

        return result
    except Exception as e:
        logger.error("chat_endpoint failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred processing your chat.")


@app.post("/chat")
def chat_endpoint_direct(request: ChatRequest):
    return chat_endpoint(request)


def _search_handler(request: SearchRequest):
    try:
        results = search_clips(
            query=request.q,
            output_dir=OUTPUT_DIR,
            meeting_body=request.meeting_body,
            speaker=request.speaker,
            date_after=request.date_after,
            date_before=request.date_before,
            limit=request.limit,
        )
        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.error("search failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred running search.")


@app.post("/api/search")
def search_endpoint(request: SearchRequest):
    return _search_handler(request)


@app.post("/search")  # Direct endpoint for App Runner (matches /ask, /chat)
def search_endpoint_direct(request: SearchRequest):
    return _search_handler(request)


def _suggest_handler(q: str, limit: int):
    q = (q or "").strip()
    if not q:
        return {"results": []}
    if len(q) > MAX_SEARCH_QUERY_CHARS:
        raise HTTPException(status_code=400, detail="q too long")
    try:
        limit = max(1, min(int(limit or 10), 25))
        results = search_suggest(q, OUTPUT_DIR, limit=limit)
        return {"results": results}
    except Exception as e:
        logger.error("suggest failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred running suggest.")


@app.get("/api/suggest")
def suggest_endpoint(q: str = "", limit: int = 10):
    return _suggest_handler(q, limit)


@app.get("/suggest")
def suggest_endpoint_direct(q: str = "", limit: int = 10):
    return _suggest_handler(q, limit)


def _facets_handler():
    try:
        return search_facets(OUTPUT_DIR)
    except Exception as e:
        logger.error("facets failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred loading facets.")


@app.get("/api/facets")
def facets_endpoint():
    return _facets_handler()


@app.get("/facets")
def facets_endpoint_direct():
    return _facets_handler()


def _related_handler(clip_id: int, limit: int):
    try:
        limit = max(1, min(int(limit or 5), 20))
        collection = _get_collection()
        clip_metadata = _get_clip_metadata()
        results = related_clips(clip_id, collection, clip_metadata, limit=limit)
        return {"results": results}
    except Exception as e:
        logger.error("related failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred loading related clips.")


@app.get("/api/related/{clip_id}")
def related_endpoint(clip_id: int, limit: int = 5):
    return _related_handler(clip_id, limit)


@app.get("/related/{clip_id}")
def related_endpoint_direct(clip_id: int, limit: int = 5):
    return _related_handler(clip_id, limit)


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
