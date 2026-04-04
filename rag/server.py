"""FastAPI server for RAG Q&A."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, field_validator

from rag.ingest import get_chroma_collection
from rag.query import ask, chat, load_clip_metadata

load_dotenv()

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.environ.get("LFUCG_OUTPUT_DIR", "./lfucg_output")

# Singletons — initialized lazily on first request
_collection = None
_clip_metadata = None
_openai_client = None


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


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


_anthropic_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import Anthropic
        _anthropic_client = Anthropic()
    return _anthropic_client


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


class ChatMessage(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: str) -> str:
        if v not in ("user", "assistant"):
            raise ValueError("role must be 'user' or 'assistant'")
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
