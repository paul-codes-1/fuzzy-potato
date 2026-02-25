"""FastAPI server for RAG Q&A."""

import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel

from rag.ingest import get_chroma_collection
from rag.query import ask, load_clip_metadata

load_dotenv()

OUTPUT_DIR = os.environ.get("LFUCG_OUTPUT_DIR", "./lfucg_output")

app = FastAPI(title="LFUCG Meeting RAG API")

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


@app.post("/api/ask")
def ask_endpoint(request: AskRequest):
    collection = get_chroma_collection(OUTPUT_DIR)
    openai_client = OpenAI()
    clip_metadata = load_clip_metadata(OUTPUT_DIR)

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


@app.get("/api/health")
def health_endpoint():
    collection = get_chroma_collection(OUTPUT_DIR)
    clip_metadata = load_clip_metadata(OUTPUT_DIR)

    return {
        "status": "ok",
        "chunks_indexed": collection.count(),
        "clips_indexed": len(clip_metadata),
    }
