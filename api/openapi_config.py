"""Custom OpenAPI schema configuration for the CivicLens API.

Defines API tags, security schemes, metadata, and applies tags to all routers.
Import and call ``configure_openapi(app)`` after all routers are mounted.
"""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

# ---------------------------------------------------------------------------
# API metadata
# ---------------------------------------------------------------------------

API_TITLE = "CivicLens API"
API_VERSION = "1.0.0"
API_DESCRIPTION = """\
**CivicLens** turns local government meeting archives into a searchable, queryable knowledge base.

The API provides:

- **RAG Q&A** -- Ask natural-language questions about any meeting and get cited answers.
- **Multi-turn chat** -- Conversational interface with context carried across turns.
- **Vote & financial search** -- Structured search over extracted votes, roll calls, and dollar amounts.
- **Policy alerts** -- Monitor keywords, council members, or dollar thresholds and get notified when matches appear.
- **Data export & FOIA** -- Bulk export meeting data or run FOIA-style searches across the entire archive.
- **Analytics & audit** -- Usage dashboards, query logs, and tamper-evident audit trails.
- **Integrations** -- Slack and Microsoft Teams bots with slash commands and adaptive cards.
- **Billing** -- Stripe-powered checkout, usage metering, and plan management.
- **Status page** -- Public system health, uptime tracking, and incident management.
- **Branding** -- White-label customization of the widget and portal per tenant.
- **Scheduler** -- Automated meeting ingestion on configurable cron schedules.

## Authentication

Most endpoints require an **API key** passed via the `X-API-Key` header.
Admin endpoints require the **Admin key** passed the same way.
Stripe webhooks are authenticated via signature verification, not API key.

## Rate Limits

Rate limit headers are returned on every authenticated response:

| Header | Description |
|---|---|
| `X-RateLimit-Limit` | Maximum requests allowed in the current window |
| `X-RateLimit-Remaining` | Requests remaining in the current window |

Plans: **Starter** (100 queries/month), **Pro** (1,000), **Enterprise** (unlimited).
"""

API_CONTACT = {
    "name": "CivicLens Support",
    "url": "https://civiclens.ai/support",
    "email": "support@civiclens.ai",
}

API_LICENSE = {
    "name": "Proprietary",
    "url": "https://civiclens.ai/terms",
}

TERMS_OF_SERVICE = "https://civiclens.ai/terms"

# ---------------------------------------------------------------------------
# Tag definitions (displayed in Swagger UI / ReDoc sidebar)
# ---------------------------------------------------------------------------

TAGS_METADATA = [
    {
        "name": "Q&A",
        "description": "Single-turn and multi-turn RAG question answering over meeting archives.",
    },
    {
        "name": "Widget",
        "description": "Embeddable widget endpoints for cross-origin Q&A use.",
    },
    {
        "name": "Votes & Financial",
        "description": "Structured search over extracted votes, roll calls, member records, and financial items.",
    },
    {
        "name": "Alerts",
        "description": "Policy monitoring alerts -- create keyword, member, or financial threshold trackers.",
    },
    {
        "name": "Export",
        "description": "Bulk data export (JSON, CSV, ZIP) and FOIA-style search across the archive.",
    },
    {
        "name": "Analytics",
        "description": "Usage analytics -- query volume, popular topics, peak hours, and CSV export.",
    },
    {
        "name": "Audit",
        "description": "Tamper-evident audit log -- search, export, integrity verification, and retention policies.",
    },
    {
        "name": "Billing",
        "description": "Stripe-powered billing -- checkout sessions, portal, usage, and plan management.",
    },
    {
        "name": "Webhooks",
        "description": "Register callback URLs to receive real-time notifications for meeting events.",
    },
    {
        "name": "Scheduler",
        "description": "Automated meeting ingestion -- cron schedules, execution history, and on-demand triggers.",
    },
    {
        "name": "Branding",
        "description": "White-label customization -- colors, logos, welcome messages, and custom CSS per tenant.",
    },
    {
        "name": "Status",
        "description": "Public system status page -- component health, uptime, incidents, and maintenance windows.",
    },
    {
        "name": "Slack",
        "description": "Slack integration -- OAuth install, slash commands, event subscriptions, and notification config.",
    },
    {
        "name": "Teams",
        "description": "Microsoft Teams integration -- outgoing webhooks, adaptive cards, and notification config.",
    },
    {
        "name": "SSO",
        "description": "SAML 2.0 single sign-on -- browser-based login/logout flows and per-tenant SSO configuration.",
    },
    {
        "name": "Admin",
        "description": "Platform administration -- tenant CRUD, plan management, cross-tenant analytics, audit, and scheduler control.",
    },
    {
        "name": "Health",
        "description": "Health check endpoints for load balancers and monitoring.",
    },
]

# ---------------------------------------------------------------------------
# Security scheme definitions
# ---------------------------------------------------------------------------

SECURITY_SCHEMES = {
    "ApiKeyAuth": {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Tenant API key. Obtain one by contacting CivicLens or via the admin tenant creation endpoint.",
    },
    "AdminKeyAuth": {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Admin API key set via the ADMIN_API_KEY environment variable. Required for all /admin/ endpoints.",
    },
    "BearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "JWT token obtained via SSO / SAML login. Used by browser-based SSO users as an alternative to API key auth.",
    },
}

# ---------------------------------------------------------------------------
# Tag assignments for existing routers
# ---------------------------------------------------------------------------

# Maps (method, path_prefix) to a tag.  Applied in ``apply_tags``.
_ROUTE_TAG_RULES: list[tuple[str, str]] = [
    # Core Q&A
    ("/api/v1/ask", "Q&A"),
    ("/api/v1/chat", "Q&A"),
    ("/api/v1/health", "Health"),
    # Legacy
    ("/ask", "Q&A"),
    ("/api/ask", "Q&A"),
    ("/chat", "Q&A"),
    ("/api/chat", "Q&A"),
    # Widget
    ("/api/v1/widget/", "Widget"),
    # Health
    ("/health", "Health"),
    ("/api/health", "Health"),
    # Admin tenant management
    ("/api/v1/admin/tenants", "Admin"),
    ("/api/v1/admin/plans", "Admin"),
    ("/api/v1/admin/analytics", "Admin"),
    ("/api/v1/admin/audit", "Admin"),
    ("/api/v1/admin/billing", "Admin"),
    ("/api/v1/admin/schedule", "Admin"),
    ("/api/v1/admin/status", "Admin"),
    # Votes & Financial
    ("/api/v1/votes", "Votes & Financial"),
    ("/api/v1/financial", "Votes & Financial"),
    # Alerts
    ("/api/v1/alerts", "Alerts"),
    # Export / FOIA
    ("/api/v1/export", "Export"),
    ("/api/v1/foia", "Export"),
    # Analytics
    ("/api/v1/analytics", "Analytics"),
    # Audit
    ("/api/v1/audit", "Audit"),
    # Billing
    ("/api/v1/billing", "Billing"),
    ("/api/v1/webhooks/stripe", "Billing"),
    # Webhooks
    ("/api/v1/webhooks", "Webhooks"),
    # Scheduler
    ("/api/v1/schedule", "Scheduler"),
    ("/api/v1/admin/schedule", "Admin"),
    # Branding
    ("/api/v1/branding", "Branding"),
    # Status
    ("/api/v1/status", "Status"),
    # Slack
    ("/api/v1/integrations/slack", "Slack"),
    # Teams
    ("/api/v1/integrations/teams", "Teams"),
    # SSO
    ("/api/v1/sso", "SSO"),
    ("/api/v1/admin/sso", "Admin"),
]


def apply_tags(app: FastAPI) -> None:
    """Walk all registered routes and assign tags based on path prefix rules."""
    for route in app.routes:
        path = getattr(route, "path", "")
        existing_tags = getattr(route, "tags", None) or []
        # Skip routes that already have custom tags from router-level tags
        # (webhook_routes, branding_routes, etc. set tags on the router)
        if existing_tags:
            continue
        for prefix, tag in _ROUTE_TAG_RULES:
            if path.startswith(prefix) or path == prefix:
                route.tags = [tag]  # type: ignore[union-attr]
                break


# ---------------------------------------------------------------------------
# Custom OpenAPI schema generator
# ---------------------------------------------------------------------------

def custom_openapi(app: FastAPI) -> dict:
    """Generate and cache a customized OpenAPI schema for the CivicLens API."""
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        routes=app.routes,
        tags=TAGS_METADATA,
        contact=API_CONTACT,
        license_info=API_LICENSE,
        terms_of_service=TERMS_OF_SERVICE,
    )

    # Inject security schemes
    schema.setdefault("components", {})
    schema["components"]["securitySchemes"] = SECURITY_SCHEMES

    # Apply global security (ApiKeyAuth) -- individual endpoints can override
    schema["security"] = [{"ApiKeyAuth": []}]

    # Mark public/unauthenticated endpoints
    _PUBLIC_PATHS = {
        "/health",
        "/api/health",
        "/api/v1/status",
        "/api/v1/status/history",
        "/api/v1/status/uptime",
        "/api/v1/webhooks/stripe",
        "/api/v1/integrations/slack/commands",
        "/api/v1/integrations/slack/events",
        "/api/v1/integrations/slack/callback",
        "/api/v1/integrations/teams/webhook",
        "/api/v1/sso/login",
        "/api/v1/sso/acs",
        "/api/v1/sso/metadata",
        "/api/v1/sso/logout",
    }
    for path, methods in schema.get("paths", {}).items():
        if path in _PUBLIC_PATHS:
            for method_data in methods.values():
                if isinstance(method_data, dict):
                    method_data["security"] = []

    # Add server URLs
    schema["servers"] = [
        {"url": "https://api.civiclens.ai", "description": "Production"},
        {"url": "http://localhost:8000", "description": "Local development"},
    ]

    app.openapi_schema = schema
    return schema


def configure_openapi(app: FastAPI) -> None:
    """Apply tags to routes and install the custom OpenAPI schema generator.

    Call this **after** all routers have been mounted on the app.
    """
    apply_tags(app)
    app.openapi = lambda: custom_openapi(app)  # type: ignore[assignment]
