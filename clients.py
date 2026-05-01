"""Singleton accessors for the OpenAI and Anthropic SDK clients.

Centralizes env-var validation and client construction. Six OpenAI sites
and three Anthropic sites used to do this ad-hoc — now they all go through
here. Singletons are not thread-locked, but `OpenAI()` / `Anthropic()` are
idempotent so the worst case is a brief race that builds two clients and
keeps one.
"""

from __future__ import annotations

import os
from typing import Optional

from openai import OpenAI

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None  # type: ignore[assignment]


_openai_client: Optional[OpenAI] = None
_anthropic_client = None


class MissingAPIKey(RuntimeError):
    """Raised when a required API key env var is unset."""


def get_openai(api_key: Optional[str] = None) -> OpenAI:
    """Return a process-wide OpenAI client. Validates OPENAI_API_KEY presence.

    `api_key` is mostly for tests / one-off CLI flags that pass an explicit
    key. When provided, builds a fresh client (does not poison the singleton).
    """
    if api_key:
        return OpenAI(api_key=api_key)

    global _openai_client
    if _openai_client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise MissingAPIKey(
                "OPENAI_API_KEY environment variable required. "
                "Set it in .env or export it before running."
            )
        _openai_client = OpenAI()
    return _openai_client


def get_anthropic():
    """Return a process-wide Anthropic client. Validates ANTHROPIC_API_KEY presence."""
    if Anthropic is None:
        raise MissingAPIKey(
            "anthropic package is not installed. "
            "Install with `uv sync` or `pip install anthropic`."
        )

    global _anthropic_client
    if _anthropic_client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise MissingAPIKey(
                "ANTHROPIC_API_KEY environment variable required. "
                "Set it in .env or export it before running."
            )
        _anthropic_client = Anthropic()
    return _anthropic_client
