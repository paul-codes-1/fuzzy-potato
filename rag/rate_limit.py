"""In-memory per-IP rate limiter.

Two tiers:
- "expensive" — calls that hit OpenAI / Anthropic (30/hour, 200/day). Used
  by /ask, /chat, and the ask_meetings MCP tool. A determined caller would
  burn ~$1/hour at the hour-cap, or ~$6/day at the day-cap.
- "cheap" — calls that only hit local SQLite/Chroma (300/hour). Used by
  /search, /suggest, /related, /facets, and the other four MCP tools.
  This tier exists purely to absorb bot floods.

The limiter uses a sliding-window-via-deque per (tier, ip) bucket. Memory
footprint is O(unique IPs × requests in window); for the meetings archive's
traffic shape that's <1 MB.

Why in-memory instead of disk-persisted (the paulBot !ask pattern):
App Runner's filesystem is ephemeral — a deploy or scale event wipes any
local state regardless. In-memory at least matches the deploy boundary
behavior, and the consequence of a reset is "the attacker gets to start
their next 30/hour budget early" which is fine for credit protection.

If we move to multi-instance someday, this needs to become Redis-backed
(or fronted by something like API Gateway with WAF rate rules).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Tuple

from rag.telemetry import get_client_ip

# (hour-budget, day-budget). day-budget of None means no daily cap.
EXPENSIVE_LIMITS: Tuple[int, int] = (30, 200)
CHEAP_LIMITS: Tuple[int, int] = (300, 0)  # 0 == no daily cap

HOUR_SECONDS = 3600
DAY_SECONDS = 86400


@dataclass
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int
    reason: str  # "ok" | "hour_limit" | "day_limit"
    remaining_hour: int
    remaining_day: int


class _Bucket:
    __slots__ = ("hour", "day")

    def __init__(self) -> None:
        self.hour: Deque[float] = deque()
        self.day: Deque[float] = deque()


class RateLimiter:
    """Thread-safe sliding-window limiter for two tiers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # keyed by (tier, ip)
        self._buckets: Dict[Tuple[str, str], _Bucket] = defaultdict(_Bucket)

    def _prune(self, dq: Deque[float], cutoff: float) -> None:
        while dq and dq[0] < cutoff:
            dq.popleft()

    def check(self, tier: str, ip: str) -> RateLimitDecision:
        """Decide whether to allow this request. Records the hit if allowed.

        `ip` may be "unknown" — the caller can use a constant fallback so a
        bogus / proxied request still gets bucketed (and rate-limited together
        with every other unknown-IP caller, which is fine).
        """
        if tier == "expensive":
            hour_cap, day_cap = EXPENSIVE_LIMITS
        elif tier == "cheap":
            hour_cap, day_cap = CHEAP_LIMITS
        else:
            raise ValueError(f"unknown rate-limit tier: {tier}")

        now = time.time()
        hour_cutoff = now - HOUR_SECONDS
        day_cutoff = now - DAY_SECONDS

        with self._lock:
            bucket = self._buckets[(tier, ip)]
            self._prune(bucket.hour, hour_cutoff)
            if day_cap:
                self._prune(bucket.day, day_cutoff)

            hour_used = len(bucket.hour)
            day_used = len(bucket.day) if day_cap else 0

            if hour_used >= hour_cap:
                # oldest entry in window expires after HOUR_SECONDS from its ts
                retry = int(bucket.hour[0] + HOUR_SECONDS - now) + 1
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=max(retry, 1),
                    reason="hour_limit",
                    remaining_hour=0,
                    remaining_day=max(0, day_cap - day_used) if day_cap else -1,
                )

            if day_cap and day_used >= day_cap:
                retry = int(bucket.day[0] + DAY_SECONDS - now) + 1
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=max(retry, 1),
                    reason="day_limit",
                    remaining_hour=max(0, hour_cap - hour_used),
                    remaining_day=0,
                )

            bucket.hour.append(now)
            if day_cap:
                bucket.day.append(now)

            return RateLimitDecision(
                allowed=True,
                retry_after_seconds=0,
                reason="ok",
                remaining_hour=hour_cap - hour_used - 1,
                remaining_day=(day_cap - day_used - 1) if day_cap else -1,
            )

    def reset(self) -> None:
        """For tests."""
        with self._lock:
            self._buckets.clear()


# Module-level singleton — shared by HTTP middleware and MCP impls.
limiter = RateLimiter()


def check(tier: str, ip: str | None = None) -> RateLimitDecision:
    """Convenience wrapper. Reads IP from the request contextvar when None."""
    resolved_ip = ip or get_client_ip() or "unknown"
    return limiter.check(tier, resolved_ip)
