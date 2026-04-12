# CivicLens Meeting Intelligence API

Base URL: `https://your-instance.example.com`

Most tenant-scoped `/api/v1/*` endpoints accept authentication via `X-API-Key`.
Browser-based SSO routes are the main exception and use JWT bearer tokens instead.

---

## Authentication

Most tenant-scoped API requests use:

```
X-API-Key: mra_your_api_key_here
```

API keys are prefixed with `mra_` and issued by your instance administrator. Keys are tied to a tenant and a plan that determines your monthly query limit.

If SSO is enabled, routes that use the normal tenant auth dependency also accept:

```bash
Authorization: Bearer <jwt>
```

SSO/browser routes such as `/api/v1/sso/login`, `/api/v1/sso/acs`, and `/api/v1/auth/me` do not follow the same API-key-only pattern.

| Plan       | Queries per month |
|------------|-------------------|
| starter    | 100               |
| pro        | 1,000             |
| enterprise | Unlimited         |

If no tenants have been provisioned and `AUTH_REQUIRED` is not set, the server runs in **dev mode** and allows unauthenticated access with a default tenant context.

---

## Rate Limiting

Rate limit information is returned in response headers on every authenticated request:

| Header                  | Description                                          |
|-------------------------|------------------------------------------------------|
| `X-RateLimit-Limit`     | Maximum queries allowed in the current billing window |
| `X-RateLimit-Remaining` | Queries remaining in the current billing window       |

When the limit is exceeded, the API returns `429 Too Many Requests` with a `Retry-After: 3600` header. The billing window is a rolling 30-day period.

---

## Request Tracking

Every response includes an `X-Request-ID` header. You can also pass your own `X-Request-ID` in the request to correlate logs on both sides.

---

## Endpoints

### POST /api/v1/ask

Single-question Q&A over the meeting archive. The server retrieves relevant meeting chunks, deduplicates them, and synthesizes an answer with source citations.

**Request body:**

| Field          | Type   | Required | Description                                      |
|----------------|--------|----------|--------------------------------------------------|
| `question`     | string | Yes      | Your question (1-2000 characters)                |
| `meeting_body` | string | No       | Filter by meeting body (e.g. "Council", "WQFB")  |
| `date_after`   | string | No       | Only include meetings on or after this date (YYYY-MM-DD) |
| `date_before`  | string | No       | Only include meetings on or before this date (YYYY-MM-DD) |

**Response:**

```json
{
  "answer": "The council approved the rezoning ordinance on January 8, 2026 with an 8-0 vote [Clip 6669, 25:15]...",
  "sources": [
    {
      "clip_id": 6669,
      "date": "2026-01-08",
      "title": "January 8 2026 Urban County Council meeting",
      "meeting_body": "Council",
      "excerpt": "Ordinance 0016-26 was approved with an 8-0 roll call vote...",
      "granicus_url": "https://lexington.granicus.com/player/clip/6669?view_id=3&entrytime=1515",
      "timestamp": 1515
    }
  ],
  "filters_applied": {
    "meeting_body": "Council"
  },
  "chunks_retrieved": 8,
  "tenant": "lexington-ky"
}
```

Each source includes a `granicus_url` that deep-links to the exact point in the meeting video. The `timestamp` field is in seconds from the start of the clip.

---

### POST /api/v1/chat

Multi-turn conversational Q&A. Send the full message history and the server retrieves context based on the latest user message, then synthesizes a response that accounts for the conversation so far.

**Request body:**

| Field            | Type   | Required | Description                                          |
|------------------|--------|----------|------------------------------------------------------|
| `messages`       | array  | Yes      | Conversation history (see below). Last message must have `role: "user"`. |
| `meeting_body`   | string | No       | Filter by meeting body                               |
| `date_after`     | string | No       | Only include meetings on or after this date (YYYY-MM-DD) |
| `date_before`    | string | No       | Only include meetings on or before this date (YYYY-MM-DD) |
| `model_provider` | string | No       | `"openai"` (default) or `"anthropic"`                |

Each message in the `messages` array:

| Field     | Type   | Description                        |
|-----------|--------|------------------------------------|
| `role`    | string | `"user"` or `"assistant"`          |
| `content` | string | The message text                   |

The server retains up to the last 10 user/assistant message pairs for context.

**Response:**

```json
{
  "role": "assistant",
  "content": "Following up on the rezoning discussion, the Planning Commission had previously recommended approval in their December meeting [Clip 6655, 14:20]...",
  "sources": [
    {
      "clip_id": 6655,
      "date": "2025-12-15",
      "title": "December 15 2025 Planning Commission meeting",
      "meeting_body": "Planning Commission",
      "excerpt": "The commission voted 7-2 to recommend approval...",
      "granicus_url": "https://lexington.granicus.com/player/clip/6655?view_id=3&entrytime=860",
      "timestamp": 860
    }
  ],
  "model_used": "gpt-4o",
  "filters_applied": {},
  "chunks_retrieved": 6,
  "tenant": "lexington-ky"
}
```

---

### GET /api/v1/health

Authenticated health check that returns the status and index statistics for your tenant.

**Response:**

```json
{
  "status": "ok",
  "tenant": "lexington-ky",
  "chunks_indexed": 45230,
  "clips_indexed": 312
}
```

`chunks_indexed` and `clips_indexed` are only present after the first query has initialized the data stores.

---

### GET /health

Lightweight, unauthenticated health check. Use this for load balancer probes.

**Response:**

```json
{
  "status": "ok"
}
```

---

## Admin Endpoints

Admin endpoints require the `ADMIN_API_KEY` environment variable to be set on the server. Authenticate with that key in the `X-API-Key` header.

### GET /api/v1/admin/tenants

List all tenants.

**Response:**

```json
{
  "tenants": [
    {
      "id": "lexington-ky",
      "name": "City of Lexington",
      "granicus_host": "lexington.granicus.com",
      "granicus_view_id": "3",
      "plan": "pro",
      "created_at": "2026-03-01T12:00:00+00:00"
    }
  ]
}
```

### POST /api/v1/admin/tenants

Create a new tenant. Returns the generated API key -- treat it as a secret and rotate it if it is lost.

**Request body:**

| Field              | Type   | Required | Description                                      |
|--------------------|--------|----------|--------------------------------------------------|
| `id`               | string | Yes      | Unique tenant identifier (e.g. `"elk-grove-ca"`) |
| `name`             | string | Yes      | Display name                                     |
| `granicus_host`    | string | Yes      | Granicus hostname (e.g. `"elkgrove.granicus.com"`) |
| `granicus_view_id` | string | No       | Granicus view ID (default: `""`)                  |
| `plan`             | string | No       | `"starter"`, `"pro"`, or `"enterprise"` (default: `"starter"`) |

**Response:**

```json
{
  "id": "elk-grove-ca",
  "name": "City of Elk Grove",
  "granicus_host": "elkgrove.granicus.com",
  "granicus_view_id": "5",
  "plan": "starter",
  "api_key": "mra_abc123...",
  "created_at": "2026-04-08T15:30:00+00:00"
}
```

### DELETE /api/v1/admin/tenants/{tenant_id}

Delete a tenant and revoke their API key.

**Response:**

```json
{
  "deleted": "elk-grove-ca"
}
```

### POST /api/v1/admin/tenants/{tenant_id}/rotate-key

Generate a new API key for a tenant. The old key is immediately invalidated.

**Response:**

```json
{
  "tenant_id": "elk-grove-ca",
  "api_key": "mra_newkey456..."
}
```

### PATCH /api/v1/admin/tenants/{tenant_id}/plan

Update a tenant's plan.

**Request body:**

| Field  | Type   | Required | Description                        |
|--------|--------|----------|------------------------------------|
| `plan` | string | Yes      | `"starter"`, `"pro"`, or `"enterprise"` |

**Response:**

```json
{
  "tenant_id": "elk-grove-ca",
  "plan": "pro"
}
```

### GET /api/v1/admin/plans

List all available plans and their limits.

**Response:**

```json
{
  "plans": {
    "starter": { "queries_per_month": 100 },
    "pro": { "queries_per_month": 1000 },
    "enterprise": { "queries_per_month": "unlimited" }
  }
}
```

---

## Error Responses

All errors follow a consistent format:

```json
{
  "detail": "Human-readable error message."
}
```

| Status Code | Meaning                    | Common Causes                                    |
|-------------|----------------------------|--------------------------------------------------|
| 401         | Unauthorized               | Missing or invalid `X-API-Key` header            |
| 403         | Forbidden                  | Non-admin key used on admin endpoint             |
| 404         | Not Found                  | Tenant ID does not exist (admin operations)      |
| 422         | Validation Error           | Missing required field, empty question, question over 2000 chars, invalid role in messages, invalid plan name |
| 429         | Rate Limit Exceeded        | Monthly query quota exhausted for your plan      |
| 500         | Internal Server Error      | Server-side failure during retrieval or synthesis |
| 503         | Service Unavailable        | `ADMIN_API_KEY` not configured on the server     |

Validation errors (422) return a structured body with field-level details:

```json
{
  "detail": [
    {
      "loc": ["body", "question"],
      "msg": "question must not be empty",
      "type": "value_error"
    }
  ]
}
```

---

## Code Examples

### cURL

**Single question:**

```bash
curl -X POST https://your-instance.example.com/api/v1/ask \
  -H "Content-Type: application/json" \
  -H "X-API-Key: mra_your_key_here" \
  -d '{
    "question": "What has the city done about short-term rentals?",
    "meeting_body": "Council",
    "date_after": "2025-01-01"
  }'
```

**Multi-turn chat:**

```bash
curl -X POST https://your-instance.example.com/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: mra_your_key_here" \
  -d '{
    "messages": [
      {"role": "user", "content": "What was discussed about the parks budget?"},
      {"role": "assistant", "content": "In the March 2026 Council meeting, the parks department budget of $4.2M was approved..."},
      {"role": "user", "content": "How did individual council members vote on that?"}
    ],
    "model_provider": "anthropic"
  }'
```

**Health check:**

```bash
curl -H "X-API-Key: mra_your_key_here" \
  https://your-instance.example.com/api/v1/health
```

### Python

```python
import requests

API_URL = "https://your-instance.example.com"
API_KEY = "mra_your_key_here"
HEADERS = {"X-API-Key": API_KEY}


def ask(question, meeting_body=None, date_after=None, date_before=None):
    payload = {"question": question}
    if meeting_body:
        payload["meeting_body"] = meeting_body
    if date_after:
        payload["date_after"] = date_after
    if date_before:
        payload["date_before"] = date_before

    resp = requests.post(f"{API_URL}/api/v1/ask", json=payload, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


# Single question
result = ask("What zoning changes were approved in 2025?", date_after="2025-01-01")
print(result["answer"])
for source in result["sources"]:
    print(f"  - {source['title']} ({source['date']}): {source['granicus_url']}")


# Multi-turn chat
def chat(messages, model_provider="openai", **filters):
    payload = {
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "model_provider": model_provider,
        **filters,
    }
    resp = requests.post(f"{API_URL}/api/v1/chat", json=payload, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


history = []
history.append({"role": "user", "content": "What did the council say about affordable housing?"})
reply = chat(history)
history.append({"role": "assistant", "content": reply["content"]})
print(reply["content"])

# Follow-up
history.append({"role": "user", "content": "Were there any dissenting votes?"})
reply = chat(history)
print(reply["content"])
```

### JavaScript (Node.js / Browser fetch)

```javascript
const API_URL = "https://your-instance.example.com";
const API_KEY = "mra_your_key_here";

async function ask(question, filters = {}) {
  const response = await fetch(`${API_URL}/api/v1/ask`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify({ question, ...filters }),
  });

  if (!response.ok) {
    const err = await response.json();
    throw new Error(`API error ${response.status}: ${err.detail}`);
  }

  return response.json();
}

// Usage
const result = await ask("What public comments were made about traffic?", {
  meeting_body: "Council",
  date_after: "2025-06-01",
});

console.log(result.answer);
result.sources.forEach((s) => {
  console.log(`  ${s.title} (${s.date}): ${s.granicus_url}`);
});
```

### JavaScript (Multi-turn Chat)

```javascript
async function chat(messages, options = {}) {
  const response = await fetch(`${API_URL}/api/v1/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify({
      messages,
      model_provider: options.model_provider || "openai",
      meeting_body: options.meeting_body,
      date_after: options.date_after,
      date_before: options.date_before,
    }),
  });

  if (!response.ok) {
    const err = await response.json();
    throw new Error(`API error ${response.status}: ${err.detail}`);
  }

  return response.json();
}

// Conversation
const history = [{ role: "user", content: "Summarize the last budget hearing." }];
const reply1 = await chat(history);
history.push({ role: "assistant", content: reply1.content });

history.push({ role: "user", content: "What was the total amount approved?" });
const reply2 = await chat(history);
console.log(reply2.content);
```

---

## Webhook / Integration Examples

### Slack Bot Integration

Post meeting answers to a Slack channel using incoming webhooks:

```python
import requests

CIVICLENS_URL = "https://your-instance.example.com"
CIVICLENS_KEY = "mra_your_key_here"
SLACK_WEBHOOK = "https://hooks.slack.com/services/T00/B00/xxxxx"


def answer_and_post(question):
    # Query CivicLens
    result = requests.post(
        f"{CIVICLENS_URL}/api/v1/ask",
        json={"question": question},
        headers={"X-API-Key": CIVICLENS_KEY},
    ).json()

    # Format for Slack
    sources_text = "\n".join(
        f"- <{s['granicus_url']}|{s['title']} ({s['date']})>"
        for s in result["sources"][:5]
    )

    slack_message = {
        "text": f"*Q:* {question}\n\n{result['answer']}\n\n*Sources:*\n{sources_text}",
    }

    requests.post(SLACK_WEBHOOK, json=slack_message)


answer_and_post("What decisions were made about parking meters this year?")
```

### Scheduled Digest (Cron / GitHub Actions)

Generate a weekly summary of recent meeting activity:

```python
import requests
from datetime import datetime, timedelta

CIVICLENS_URL = "https://your-instance.example.com"
CIVICLENS_KEY = "mra_your_key_here"

one_week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

result = requests.post(
    f"{CIVICLENS_URL}/api/v1/ask",
    json={
        "question": "Summarize all major decisions, votes, and public comments from this week's meetings.",
        "date_after": one_week_ago,
    },
    headers={"X-API-Key": CIVICLENS_KEY},
).json()

print(result["answer"])
# Send via email, post to website, etc.
```

---

## Legacy Endpoints

For backward compatibility, the following paths also work and behave identically to their `/api/v1/` counterparts (all require authentication):

| Legacy Path   | Equivalent V1 Path  |
|---------------|----------------------|
| `POST /ask`   | `POST /api/v1/ask`   |
| `POST /api/ask` | `POST /api/v1/ask` |
| `POST /chat`  | `POST /api/v1/chat`  |
| `POST /api/chat` | `POST /api/v1/chat` |
| `GET /api/health` | `GET /health` (unauthenticated) |

New integrations should use the `/api/v1/` paths.
