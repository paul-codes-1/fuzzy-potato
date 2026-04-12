"""Locust-based load test for the CivicLens FastAPI service.

Usage
-----
Run headless against a local server:

    locust -f scripts/load_test.py \
        --host http://localhost:8000 \
        --users 50 --spawn-rate 5 --run-time 2m --headless

Or launch the web UI:

    locust -f scripts/load_test.py --host http://localhost:8000

Authentication
--------------
The load test sends ``X-API-Key`` on every authenticated request. The key is
read from ``CIVICLENS_API_KEY``; if unset, the literal string ``dev`` is sent,
which works with the API's dev fallback (``api/auth.py::_build_dev_tenant``)
as long as no real tenants are provisioned in ``tenants.db`` and
``AUTH_REQUIRED`` is not set.

Because ``/api/v1/ask`` and ``/api/v1/chat`` are subject to per-tenant rate
limiting (sliding 30-day window: starter=100, pro=1000, enterprise=unlimited),
you should point the test at an **enterprise** tenant -- otherwise you will
saturate the plan's monthly quota in seconds and every subsequent call will
return 429.
"""

from __future__ import annotations

import os
import random
from typing import Any, Dict, List

try:
    from locust import HttpUser, between, events, task
except ImportError as exc:  # pragma: no cover - locust is an optional dev dep
    raise SystemExit(
        "locust is not installed. Install it with `uv sync --extra dev` "
        "(locust is declared under [project.optional-dependencies.dev])."
    ) from exc


# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------

SEARCH_QUERIES: List[str] = [
    "budget",
    "zoning",
    "affordable housing",
    "police funding",
    "parks",
    "downtown",
    "short-term rentals",
]

QUESTION_TOPICS: List[str] = [
    "short-term rentals",
    "affordable housing",
    "the downtown master plan",
    "police funding",
    "the parks budget",
    "the zoning overhaul",
    "homelessness",
]

# Sample clip IDs to read. These are best-guess IDs for a seeded dev tenant;
# adjust via CIVICLENS_SAMPLE_CLIP_IDS (comma-separated) if your tenant uses
# different identifiers.
_default_sample_clip_ids = [44, 45, 6669, 6670, 6675, 6702, 6704]
_env_clip_ids = os.environ.get("CIVICLENS_SAMPLE_CLIP_IDS", "")
SAMPLE_CLIP_IDS: List[int] = (
    [int(x) for x in _env_clip_ids.split(",") if x.strip().isdigit()]
    if _env_clip_ids
    else _default_sample_clip_ids
)


def _api_key() -> str:
    """Return the API key to use. Falls back to ``dev`` for the dev tenant."""
    return os.environ.get("CIVICLENS_API_KEY", "dev")


def _auth_headers() -> Dict[str, str]:
    return {
        "X-API-Key": _api_key(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# Per-endpoint latency tracking (p50/p95/p99)
# ---------------------------------------------------------------------------

class _LatencyBuckets:
    """Collects per-endpoint response times and computes percentiles on demand."""

    def __init__(self) -> None:
        self._samples: Dict[str, List[float]] = {}

    def record(self, name: str, ms: float) -> None:
        self._samples.setdefault(name, []).append(ms)

    def summary(self) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for name, samples in self._samples.items():
            if not samples:
                continue
            ordered = sorted(samples)
            out[name] = {
                "count": float(len(ordered)),
                "p50": _percentile(ordered, 50),
                "p95": _percentile(ordered, 95),
                "p99": _percentile(ordered, 99),
            }
        return out


def _percentile(sorted_samples: List[float], pct: float) -> float:
    if not sorted_samples:
        return 0.0
    k = (len(sorted_samples) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_samples) - 1)
    frac = k - lo
    return sorted_samples[lo] * (1 - frac) + sorted_samples[hi] * frac


_latency_buckets = _LatencyBuckets()


@events.request.add_listener
def _on_request(
    request_type: str,
    name: str,
    response_time: float,
    response_length: int,
    exception: Any,
    context: Any,
    **kwargs: Any,
) -> None:
    """Capture every Locust request for our own p50/p95/p99 summary."""
    if exception is None:
        _latency_buckets.record(f"{request_type} {name}", float(response_time))


@events.quitting.add_listener
def _on_quit(environment: Any, **kwargs: Any) -> None:
    """Dump a per-endpoint percentile table when the run finishes."""
    summary = _latency_buckets.summary()
    if not summary:
        return
    print("\n=== CivicLens load test: per-endpoint latency (ms) ===")
    header = f"{'endpoint':<48} {'n':>8} {'p50':>10} {'p95':>10} {'p99':>10}"
    print(header)
    print("-" * len(header))
    for name in sorted(summary):
        row = summary[name]
        print(
            f"{name[:48]:<48} "
            f"{int(row['count']):>8} "
            f"{row['p50']:>10.1f} "
            f"{row['p95']:>10.1f} "
            f"{row['p99']:>10.1f}"
        )
    print("=" * len(header))


# ---------------------------------------------------------------------------
# User class
# ---------------------------------------------------------------------------

class CivicLensUser(HttpUser):
    """Simulates a mix of CivicLens API traffic.

    Task weights (relative frequency):
      - browse_meetings: 10  -- search the meeting index
      - read_meeting:     5  -- fetch a single meeting's detail
      - ask_question:     3  -- single-turn RAG Q&A
      - chat:             2  -- two-turn conversational chat
      - vote_search:      1  -- vote tracker lookup
      - health:           1  -- unauthenticated health probe
    """

    wait_time = between(1, 3)

    def on_start(self) -> None:  # noqa: D401
        """Warm the connection with a public health check."""
        self.client.get("/health", name="/health (warmup)")

    # -- Browse / search --------------------------------------------------

    @task(10)
    def browse_meetings(self) -> None:
        q = random.choice(SEARCH_QUERIES)
        self.client.get(
            f"/api/v1/search?q={q}&limit=20",
            headers=_auth_headers(),
            name="/api/v1/search?q=[query]",
        )

    # -- Read single meeting ---------------------------------------------

    @task(5)
    def read_meeting(self) -> None:
        clip_id = random.choice(SAMPLE_CLIP_IDS)
        # NOTE: as of this test's creation, the API does not expose a
        # dedicated ``/api/v1/meetings/{id}`` endpoint. We proxy a meeting
        # read through the search endpoint with a clip-specific query.
        # Replace with the real route once it lands in api/server.py.
        self.client.get(
            f"/api/v1/search?q=clip_{clip_id}&limit=1",
            headers=_auth_headers(),
            name="/api/v1/meetings/{id}",
        )

    # -- Single-turn RAG --------------------------------------------------

    @task(3)
    def ask_question(self) -> None:
        topic = random.choice(QUESTION_TOPICS)
        payload = {"question": f"What has the city done about {topic}?"}
        self.client.post(
            "/api/v1/ask",
            json=payload,
            headers=_auth_headers(),
            name="/api/v1/ask",
        )

    # -- Multi-turn RAG chat ---------------------------------------------

    @task(2)
    def chat(self) -> None:
        topic = random.choice(QUESTION_TOPICS)
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": f"Tell me about {topic} in recent meetings.",
                },
                {
                    "role": "assistant",
                    "content": (
                        "Here is a brief overview of recent discussion. "
                        "Would you like more detail on a specific meeting?"
                    ),
                },
                {
                    "role": "user",
                    "content": "Yes, which meeting had the most debate about it?",
                },
            ],
            "model_provider": "openai",
        }
        self.client.post(
            "/api/v1/chat",
            json=payload,
            headers=_auth_headers(),
            name="/api/v1/chat",
        )

    # -- Vote tracker -----------------------------------------------------

    @task(1)
    def vote_search(self) -> None:
        # Tracker router exposes votes at /api/v1/votes (see api/tracker_routes.py)
        self.client.get(
            "/api/v1/votes?limit=20",
            headers=_auth_headers(),
            name="/api/v1/votes",
        )

    # -- Public health (no auth) -----------------------------------------

    @task(1)
    def health(self) -> None:
        self.client.get("/health", name="/health")


# ---------------------------------------------------------------------------
# CLI sanity check -- lets you run `python scripts/load_test.py --help` to
# confirm the file is import-clean without actually launching Locust.
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Locust load test for CivicLens. This file is meant to be run via "
            "the `locust` CLI, not directly: "
            "`locust -f scripts/load_test.py --host http://localhost:8000`."
        )
    )
    parser.add_argument(
        "--print-config",
        action="store_true",
        help="Print the resolved config (API key source, sample clip IDs) and exit.",
    )
    args = parser.parse_args()

    if args.print_config:
        print(f"CIVICLENS_API_KEY: {'set' if os.environ.get('CIVICLENS_API_KEY') else 'unset (using dev fallback)'}")
        print(f"Sample clip IDs: {SAMPLE_CLIP_IDS}")
        print(f"Search queries: {SEARCH_QUERIES}")
        print(f"Question topics: {QUESTION_TOPICS}")
        return

    print(__doc__)
    print("\nThis script is intended to be executed by the `locust` CLI.")
    print("Run: locust -f scripts/load_test.py --host http://localhost:8000")


if __name__ == "__main__":
    _main()
