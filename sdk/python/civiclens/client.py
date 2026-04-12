"""CivicLens Python SDK client.

Provides typed, async-first access to every CivicLens API endpoint with
automatic retry, rate-limit awareness, and full type hints.

Usage::

    import asyncio
    from civiclens import CivicLensClient

    async def main():
        async with CivicLensClient(api_key="cl_...") as cl:
            result = await cl.ask("What has the city done about short-term rentals?")
            print(result["answer"])

    asyncio.run(main())
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import httpx


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://api.civiclens.ai"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0  # seconds

# Retryable HTTP status codes
_RETRYABLE = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CivicLensError(Exception):
    """Base exception for CivicLens SDK errors."""

    def __init__(self, message: str, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class RateLimitError(CivicLensError):
    """Raised when the API returns 429 and all retries are exhausted."""

    def __init__(self, message: str, retry_after: float | None = None, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class AuthenticationError(CivicLensError):
    """Raised when the API returns 401 or 403."""
    pass


class NotFoundError(CivicLensError):
    """Raised when the API returns 404."""
    pass


# ---------------------------------------------------------------------------
# Rate-limit header parser
# ---------------------------------------------------------------------------

class RateLimitInfo:
    """Parsed rate-limit headers from a response."""

    def __init__(self, limit: int | None, remaining: int | None, retry_after: float | None):
        self.limit = limit
        self.remaining = remaining
        self.retry_after = retry_after

    @classmethod
    def from_headers(cls, headers: httpx.Headers) -> "RateLimitInfo":
        def _int(key: str) -> int | None:
            v = headers.get(key)
            if v is not None:
                try:
                    return int(v)
                except ValueError:
                    pass
            return None

        def _float(key: str) -> float | None:
            v = headers.get(key)
            if v is not None:
                try:
                    return float(v)
                except ValueError:
                    pass
            return None

        return cls(
            limit=_int("X-RateLimit-Limit"),
            remaining=_int("X-RateLimit-Remaining"),
            retry_after=_float("Retry-After"),
        )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class CivicLensClient:
    """Async HTTP client for the CivicLens API.

    Parameters
    ----------
    api_key:
        Tenant API key (``X-API-Key`` header).
    base_url:
        API base URL. Defaults to ``https://api.civiclens.ai``.
    timeout:
        Request timeout in seconds. Default 60.
    max_retries:
        Maximum number of retries on transient failures. Default 3.
    backoff_base:
        Base delay in seconds for exponential backoff. Default 1.0.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._client: httpx.AsyncClient | None = None
        self.last_rate_limit: RateLimitInfo | None = None

    # -- lifecycle -----------------------------------------------------------

    async def __aenter__(self) -> "CivicLensClient":
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "X-API-Key": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "civiclens-python/1.0.0",
            },
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def close(self) -> None:
        """Explicitly close the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "X-API-Key": self.api_key,
                    "Content-Type": "application/json",
                    "User-Agent": "civiclens-python/1.0.0",
                },
                timeout=self.timeout,
            )
        return self._client

    # -- transport -----------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Send a request with automatic retry and rate-limit handling."""
        client = self._ensure_client()

        # Strip None values from params
        if params:
            params = {k: v for k, v in params.items() if v is not None}

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await client.request(method, path, json=json, params=params)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(self.backoff_base * (2 ** (attempt - 1)))
                    continue
                raise CivicLensError(f"Connection failed after {self.max_retries} retries: {exc}")

            # Parse rate-limit headers on every response
            self.last_rate_limit = RateLimitInfo.from_headers(response.headers)

            if response.status_code < 400:
                return response

            # Retryable status codes
            if response.status_code in _RETRYABLE and attempt < self.max_retries:
                wait = self.backoff_base * (2 ** (attempt - 1))
                # Honour Retry-After header from 429
                if response.status_code == 429 and self.last_rate_limit.retry_after:
                    wait = max(wait, self.last_rate_limit.retry_after)
                await asyncio.sleep(wait)
                continue

            # Non-retryable errors
            self._raise_for_status(response)

        # Should not reach here, but just in case
        if last_exc:
            raise CivicLensError(f"Request failed after {self.max_retries} retries") from last_exc
        raise CivicLensError("Request failed unexpectedly")

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Raise the appropriate typed exception for an error response."""
        try:
            body = response.json()
        except Exception:
            body = response.text

        detail = body.get("detail", str(body)) if isinstance(body, dict) else str(body)
        status = response.status_code

        if status == 429:
            raise RateLimitError(
                f"Rate limit exceeded: {detail}",
                status_code=status,
                body=body,
                retry_after=self.last_rate_limit.retry_after if self.last_rate_limit else None,
            )
        if status in (401, 403):
            raise AuthenticationError(f"Authentication failed: {detail}", status_code=status, body=body)
        if status == 404:
            raise NotFoundError(f"Not found: {detail}", status_code=status, body=body)

        raise CivicLensError(f"API error {status}: {detail}", status_code=status, body=body)

    async def _get(self, path: str, **params: Any) -> Dict[str, Any]:
        resp = await self._request("GET", path, params=params)
        return resp.json()

    async def _post(self, path: str, body: Dict[str, Any] | None = None) -> Dict[str, Any]:
        resp = await self._request("POST", path, json=body)
        return resp.json()

    async def _put(self, path: str, body: Dict[str, Any] | None = None) -> Dict[str, Any]:
        resp = await self._request("PUT", path, json=body)
        return resp.json()

    async def _patch(self, path: str, body: Dict[str, Any] | None = None) -> Dict[str, Any]:
        resp = await self._request("PATCH", path, json=body)
        return resp.json()

    async def _delete(self, path: str) -> Dict[str, Any]:
        resp = await self._request("DELETE", path)
        return resp.json()

    # -----------------------------------------------------------------------
    # Q&A
    # -----------------------------------------------------------------------

    async def ask(
        self,
        question: str,
        *,
        meeting_body: str | None = None,
        date_after: str | None = None,
        date_before: str | None = None,
    ) -> Dict[str, Any]:
        """Ask a single question about meeting archives.

        Parameters
        ----------
        question:
            Natural-language question (max 2000 characters).
        meeting_body:
            Optional filter by meeting body name.
        date_after:
            Optional ISO date lower bound (YYYY-MM-DD).
        date_before:
            Optional ISO date upper bound (YYYY-MM-DD).

        Returns
        -------
        dict with ``answer``, ``sources``, ``filters_applied``, ``tenant``.
        """
        body: Dict[str, Any] = {"question": question}
        if meeting_body:
            body["meeting_body"] = meeting_body
        if date_after:
            body["date_after"] = date_after
        if date_before:
            body["date_before"] = date_before
        return await self._post("/api/v1/ask", body)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        meeting_body: str | None = None,
        date_after: str | None = None,
        date_before: str | None = None,
        model_provider: str = "openai",
    ) -> Dict[str, Any]:
        """Multi-turn conversational Q&A.

        Parameters
        ----------
        messages:
            List of ``{"role": "user"|"assistant", "content": "..."}`` dicts.
            Last message must have role ``user``.
        model_provider:
            ``"openai"`` or ``"anthropic"``.

        Returns
        -------
        dict with ``answer``, ``sources``, ``filters_applied``, ``tenant``.
        """
        body: Dict[str, Any] = {
            "messages": messages,
            "model_provider": model_provider,
        }
        if meeting_body:
            body["meeting_body"] = meeting_body
        if date_after:
            body["date_after"] = date_after
        if date_before:
            body["date_before"] = date_before
        return await self._post("/api/v1/chat", body)

    # -----------------------------------------------------------------------
    # Votes & Financial
    # -----------------------------------------------------------------------

    async def search_votes(
        self,
        *,
        member: str | None = None,
        date_after: str | None = None,
        date_before: str | None = None,
        outcome: str | None = None,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Search extracted votes across all meetings.

        Parameters
        ----------
        member:
            Filter by council member name.
        outcome:
            ``"passed"`` or ``"failed"``.
        keyword:
            Full-text search over vote descriptions.
        """
        return await self._get(
            "/api/v1/votes",
            member=member or "",
            date_after=date_after or "",
            date_before=date_before or "",
            outcome=outcome or "",
            keyword=keyword or "",
            limit=limit,
            offset=offset,
        )

    async def get_member_voting_record(self, name: str) -> Dict[str, Any]:
        """Get the full voting record for a specific council member."""
        return await self._get(f"/api/v1/votes/member/{name}")

    async def get_vote_stats(self) -> Dict[str, Any]:
        """Get aggregate vote statistics."""
        return await self._get("/api/v1/votes/stats")

    async def search_financial(
        self,
        *,
        min_amount: float | None = None,
        max_amount: float | None = None,
        item_type: str | None = None,
        date_after: str | None = None,
        date_before: str | None = None,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Search extracted financial items across all meetings.

        Parameters
        ----------
        min_amount:
            Minimum dollar amount.
        max_amount:
            Maximum dollar amount.
        item_type:
            Filter by type (``"appropriation"``, ``"contract"``, etc.).
        """
        return await self._get(
            "/api/v1/financial",
            min_amount=min_amount,
            max_amount=max_amount,
            item_type=item_type or "",
            date_after=date_after or "",
            date_before=date_before or "",
            keyword=keyword or "",
            limit=limit,
            offset=offset,
        )

    async def get_financial_summary(self) -> Dict[str, Any]:
        """Get aggregate financial summary statistics."""
        return await self._get("/api/v1/financial/summary")

    # -----------------------------------------------------------------------
    # Alerts
    # -----------------------------------------------------------------------

    async def create_alert(
        self,
        name: str,
        alert_type: str,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create a policy monitoring alert.

        Parameters
        ----------
        name:
            Human-readable alert name.
        alert_type:
            One of ``"keyword"``, ``"member"``, ``"financial_threshold"``, etc.
        config:
            Type-specific configuration dict.
        """
        return await self._post("/api/v1/alerts", {
            "name": name,
            "type": alert_type,
            "config": config,
        })

    async def list_alerts(self) -> Dict[str, Any]:
        """List all alerts for the authenticated tenant."""
        return await self._get("/api/v1/alerts")

    async def update_alert(
        self,
        alert_id: str,
        *,
        name: str | None = None,
        config: Dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> Dict[str, Any]:
        """Update an existing alert."""
        body: Dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if config is not None:
            body["config"] = config
        if enabled is not None:
            body["enabled"] = enabled
        return await self._put(f"/api/v1/alerts/{alert_id}", body)

    async def delete_alert(self, alert_id: str) -> Dict[str, Any]:
        """Delete an alert."""
        return await self._delete(f"/api/v1/alerts/{alert_id}")

    async def get_alert_matches(
        self,
        alert_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Get matches for a specific alert."""
        return await self._get(f"/api/v1/alerts/{alert_id}/matches", limit=limit, offset=offset)

    # -----------------------------------------------------------------------
    # Export & FOIA
    # -----------------------------------------------------------------------

    async def start_export(
        self,
        *,
        format: str = "json",
        filters: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Start an async data export job.

        Parameters
        ----------
        format:
            ``"json"``, ``"csv"``, or ``"zip"``.
        filters:
            Optional filter criteria.

        Returns
        -------
        Export job object with ``job_id`` and ``status``.
        """
        body: Dict[str, Any] = {"format": format}
        if filters:
            body["filters"] = filters
        return await self._post("/api/v1/export", body)

    async def get_export_status(self, job_id: str) -> Dict[str, Any]:
        """Check the status of an export job."""
        return await self._get(f"/api/v1/export/{job_id}")

    async def get_export_history(self, *, limit: int = 50) -> Dict[str, Any]:
        """List past export jobs."""
        return await self._get("/api/v1/export/history", limit=limit)

    async def download_export(self, job_id: str) -> bytes:
        """Download a completed export file as raw bytes (ZIP archive)."""
        client = self._ensure_client()
        response = await client.get(f"/api/v1/export/{job_id}/download")
        if response.status_code != 200:
            self._raise_for_status(response)
        return response.content

    async def foia_search(self, query: str) -> Dict[str, Any]:
        """Submit a FOIA-style search across the entire meeting archive.

        Parameters
        ----------
        query:
            Natural-language FOIA request (max 5000 characters).
        """
        return await self._post("/api/v1/foia", {"query": query})

    async def get_foia_status(self, request_id: str) -> Dict[str, Any]:
        """Check the status of a FOIA search request."""
        return await self._get(f"/api/v1/foia/{request_id}")

    # -----------------------------------------------------------------------
    # Analytics
    # -----------------------------------------------------------------------

    async def get_usage(self, *, period: str = "30d") -> Dict[str, Any]:
        """Get usage summary for the current billing period."""
        return await self._get("/api/v1/analytics/usage", period=period)

    async def get_query_volume(self, *, period: str = "7d") -> Dict[str, Any]:
        """Get query volume over time."""
        return await self._get("/api/v1/analytics/queries", period=period)

    async def get_popular_topics(self, *, period: str = "30d", limit: int = 10) -> Dict[str, Any]:
        """Get most queried meeting bodies and topics."""
        return await self._get("/api/v1/analytics/popular-topics", period=period, limit=limit)

    async def get_recent_queries(self, *, limit: int = 50) -> Dict[str, Any]:
        """Get recent queries."""
        return await self._get("/api/v1/analytics/recent", limit=limit)

    async def get_peak_hours(self, *, period: str = "30d") -> Dict[str, Any]:
        """Get peak usage hours (UTC)."""
        return await self._get("/api/v1/analytics/peak-hours", period=period)

    # -----------------------------------------------------------------------
    # Audit
    # -----------------------------------------------------------------------

    async def search_audit_log(
        self,
        *,
        action: str | None = None,
        resource_type: str | None = None,
        date_after: str | None = None,
        date_before: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Search the audit log for the authenticated tenant."""
        return await self._get(
            "/api/v1/audit",
            action=action,
            resource_type=resource_type,
            date_after=date_after,
            date_before=date_before,
            limit=limit,
            offset=offset,
        )

    async def get_audit_stats(self, *, days: int = 30) -> Dict[str, Any]:
        """Get audit log statistics."""
        return await self._get("/api/v1/audit/stats", days=days)

    async def verify_audit_integrity(self) -> Dict[str, Any]:
        """Verify the integrity of the audit hash chain."""
        return await self._get("/api/v1/audit/verify")

    # -----------------------------------------------------------------------
    # Billing
    # -----------------------------------------------------------------------

    async def create_checkout(self, *, success_url: str, cancel_url: str) -> Dict[str, Any]:
        """Create a Stripe Checkout session."""
        return await self._post("/api/v1/billing/checkout", {
            "success_url": success_url,
            "cancel_url": cancel_url,
        })

    async def create_billing_portal(self, *, return_url: str) -> Dict[str, Any]:
        """Create a Stripe Billing Portal session for self-service management."""
        return await self._post("/api/v1/billing/portal", {
            "return_url": return_url,
        })

    async def get_billing_usage(self) -> Dict[str, Any]:
        """Get current billing period usage."""
        return await self._get("/api/v1/billing/usage")

    # -----------------------------------------------------------------------
    # Webhooks
    # -----------------------------------------------------------------------

    async def register_webhook(
        self,
        url: str,
        events: List[str],
        *,
        secret: str | None = None,
    ) -> Dict[str, Any]:
        """Register a webhook to receive real-time event notifications.

        Parameters
        ----------
        url:
            HTTPS callback URL.
        events:
            List of event types to subscribe to.
        secret:
            Optional HMAC secret for payload verification.
        """
        body: Dict[str, Any] = {"url": url, "events": events}
        if secret:
            body["secret"] = secret
        return await self._post("/api/v1/webhooks", body)

    async def list_webhooks(self) -> Dict[str, Any]:
        """List all webhooks for the authenticated tenant."""
        return await self._get("/api/v1/webhooks")

    async def delete_webhook(self, webhook_id: str) -> Dict[str, Any]:
        """Delete a webhook."""
        return await self._delete(f"/api/v1/webhooks/{webhook_id}")

    async def get_webhook_deliveries(self, webhook_id: str, *, limit: int = 50) -> Dict[str, Any]:
        """Get recent delivery log for a webhook."""
        return await self._get(f"/api/v1/webhooks/{webhook_id}/deliveries", limit=limit)

    async def test_webhook(self, webhook_id: str) -> Dict[str, Any]:
        """Send a test event to a webhook."""
        return await self._post(f"/api/v1/webhooks/{webhook_id}/test")

    # -----------------------------------------------------------------------
    # Scheduler
    # -----------------------------------------------------------------------

    async def get_schedule(self) -> Dict[str, Any]:
        """Get the current meeting ingestion schedule."""
        return await self._get("/api/v1/schedule")

    async def update_schedule(
        self,
        *,
        cron_expression: str | None = None,
        max_clips: int | None = None,
        enabled: bool | None = None,
    ) -> Dict[str, Any]:
        """Update the meeting ingestion schedule.

        Parameters
        ----------
        cron_expression:
            Standard 5-field cron (e.g. ``"0 12 * * 1-5"``).
        max_clips:
            Maximum clips to process per run (1-100).
        enabled:
            Enable or disable the schedule.
        """
        body: Dict[str, Any] = {}
        if cron_expression is not None:
            body["cron_expression"] = cron_expression
        if max_clips is not None:
            body["max_clips"] = max_clips
        if enabled is not None:
            body["enabled"] = enabled
        return await self._put("/api/v1/schedule", body)

    async def get_schedule_history(self, *, limit: int = 20) -> Dict[str, Any]:
        """Get recent schedule execution history."""
        return await self._get("/api/v1/schedule/history", limit=limit)

    async def trigger_run_now(self) -> Dict[str, Any]:
        """Trigger an immediate meeting processing run."""
        return await self._post("/api/v1/schedule/run-now")

    # -----------------------------------------------------------------------
    # Branding
    # -----------------------------------------------------------------------

    async def get_branding(self) -> Dict[str, Any]:
        """Get the tenant's branding configuration."""
        return await self._get("/api/v1/branding")

    async def update_branding(self, **fields: Any) -> Dict[str, Any]:
        """Update branding configuration.

        Keyword arguments can include: ``display_name``, ``logo_url``,
        ``primary_color``, ``secondary_color``, ``accent_color``,
        ``favicon_url``, ``custom_css``, ``welcome_message``,
        ``footer_text``, ``support_email``.
        """
        body = {k: v for k, v in fields.items() if v is not None}
        return await self._put("/api/v1/branding", body)

    async def reset_branding(self) -> Dict[str, Any]:
        """Reset branding to CivicLens defaults."""
        return await self._post("/api/v1/branding/reset")

    # -----------------------------------------------------------------------
    # Status
    # -----------------------------------------------------------------------

    async def get_status(self) -> Dict[str, Any]:
        """Get the public system status summary."""
        return await self._get("/api/v1/status")

    async def get_incident_history(self, *, days: int = 90) -> Dict[str, Any]:
        """Get recent incident history."""
        return await self._get("/api/v1/status/history", days=days)

    async def get_uptime(self) -> Dict[str, Any]:
        """Get uptime percentages per component."""
        return await self._get("/api/v1/status/uptime")

    # -----------------------------------------------------------------------
    # Health
    # -----------------------------------------------------------------------

    async def health(self) -> Dict[str, Any]:
        """Lightweight health check (no auth required)."""
        client = self._ensure_client()
        response = await client.get("/health")
        return response.json()

    async def health_authenticated(self) -> Dict[str, Any]:
        """Authenticated health check with index stats."""
        return await self._get("/api/v1/health")
