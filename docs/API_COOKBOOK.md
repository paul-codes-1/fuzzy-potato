# CivicLens API Cookbook

A task-oriented guide for developers integrating CivicLens into other
products, Zapier flows, custom dashboards, or research pipelines. Every
recipe is self-contained: copy, paste, tweak.

If you want the full endpoint reference instead, see
[`docs/API.md`](./API.md). If you want to know *how* to do something,
read on.

---

## How to use this cookbook

Each recipe is structured the same way:

1. **Use case** — a one-line description of who would do this and why.
2. **Request** — a `curl` command plus a Python and a JavaScript
   equivalent, either via the official SDK or raw HTTP.
3. **Response** — the shape you should expect back.
4. **Notes** — filters, pagination, error handling, gotchas.

Recipes are independent. If you only care about webhooks, jump to
Recipe 4.

### Auth cheat sheet

All tenant-scoped endpoints live under `/api/v1/*` and expect an API
key in the `X-API-Key` header:

```
X-API-Key: mra_your_api_key_here
```

API keys are `mra_`-prefixed and issued per tenant. Get one by either
(a) asking your instance admin to run
`uv run python -m api.tenants create --id <id> --name <name> --granicus-host <host>`,
or (b) hitting the admin API (see Recipe 12). The create call returns
the plaintext key exactly once — store it immediately.

SSO browser sessions also work for the same endpoints via
`Authorization: Bearer <jwt>`. Admin endpoints under
`/api/v1/admin/*` require a separate `ADMIN_API_KEY`, passed in the
same `X-API-Key` header.

If no tenants exist and `AUTH_REQUIRED` is unset, the server runs in
**dev mode** and accepts any request with a default tenant context.
Use this for local experiments; never for production.

### Rate limit note

Every authenticated response includes:

| Header                  | Meaning                                                   |
|-------------------------|-----------------------------------------------------------|
| `X-RateLimit-Limit`     | Monthly query cap for this plan (starter 100, pro 1,000)  |
| `X-RateLimit-Remaining` | Queries remaining in the rolling 30-day window            |
| `X-Request-ID`          | Correlate with server logs if you file a support ticket   |

Exceeding the limit returns `429 Too Many Requests` with
`Retry-After: 3600`. Back off and retry.

### Common response shape

For Q&A endpoints, responses look like:

```json
{
  "answer": "The council approved...",
  "sources": [
    {
      "clip_id": 6702,
      "date": "2026-01-08",
      "title": "January 8 2026 Council Meeting",
      "meeting_body": "Council",
      "excerpt": "Motion to approve...",
      "timestamp": 1215,
      "granicus_url": "https://.../player/clip/6702?view_id=2&entrytime=1215"
    }
  ],
  "filters_applied": { "meeting_body": "Council" },
  "chunks_retrieved": 8,
  "tenant": "elk-grove-ca"
}
```

Errors use standard HTTP codes with a `detail` field:

```json
{ "detail": "question must not be empty" }
```

---

## Environment setup

Pick one stack. The rest of the cookbook shows all three side-by-side.

### Python

If you want the official SDK:

```bash
pip install civiclens
```

Your first call:

```python
import asyncio, os
from civiclens import CivicLensClient

async def main():
    async with CivicLensClient(api_key=os.environ["CIVICLENS_API_KEY"]) as cl:
        print((await cl.ask("What did the council decide about parks?"))["answer"])

asyncio.run(main())
```

Prefer raw HTTP? Use `httpx` or `requests`:

```python
import os, requests
r = requests.post(
    "https://api.civiclens.ai/api/v1/ask",
    headers={"X-API-Key": os.environ["CIVICLENS_API_KEY"]},
    json={"question": "What did the council decide about parks?"},
)
print(r.json()["answer"])
```

### JavaScript / Node

Official SDK:

```bash
npm install @civiclens/sdk
```

Your first call:

```javascript
import { CivicLensClient } from "@civiclens/sdk";
const cl = new CivicLensClient({ apiKey: process.env.CIVICLENS_API_KEY });
const { answer } = await cl.ask("What did the council decide about parks?");
console.log(answer);
```

Raw fetch works too:

```javascript
const r = await fetch("https://api.civiclens.ai/api/v1/ask", {
  method: "POST",
  headers: {
    "X-API-Key": process.env.CIVICLENS_API_KEY,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ question: "What did the council decide about parks?" }),
});
console.log((await r.json()).answer);
```

Export your key once per shell session so no sample leaks it:

```bash
export CIVICLENS_API_KEY=mra_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
export CIVICLENS_API=https://api.civiclens.ai
```

All curl examples below assume those two variables are set.

---

## Recipe 1: Ask a single question about your city's meetings

**Use case:** A reporter, policy analyst, or dashboard that needs a
direct answer to a natural-language question with linked citations.
This is the single most common call.

**Endpoint:** `POST /api/v1/ask`

**Request body:**

| Field          | Type    | Required | Notes                                   |
|----------------|---------|----------|-----------------------------------------|
| `question`     | string  | yes      | 1–2000 characters                       |
| `meeting_body` | string  | no       | Filter to one body, e.g. `"Council"`    |
| `date_after`   | string  | no       | ISO `YYYY-MM-DD` lower bound (inclusive)|
| `date_before`  | string  | no       | ISO `YYYY-MM-DD` upper bound (inclusive)|

```bash
curl -X POST "$CIVICLENS_API/api/v1/ask" \
  -H "X-API-Key: $CIVICLENS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What has the city done about short-term rentals?",
    "meeting_body": "Council",
    "date_after": "2025-01-01"
  }'
```

```python
import asyncio, os
from civiclens import CivicLensClient

async def main():
    async with CivicLensClient(api_key=os.environ["CIVICLENS_API_KEY"]) as cl:
        result = await cl.ask(
            "What has the city done about short-term rentals?",
            meeting_body="Council",
            date_after="2025-01-01",
        )
        print(result["answer"])
        for s in result["sources"]:
            print(f"- {s['title']} ({s['date']}) → {s['granicus_url']}")

asyncio.run(main())
```

```javascript
import { CivicLensClient } from "@civiclens/sdk";
const cl = new CivicLensClient({ apiKey: process.env.CIVICLENS_API_KEY });
const result = await cl.ask(
  "What has the city done about short-term rentals?",
  { meetingBody: "Council", dateAfter: "2025-01-01" }
);
console.log(result.answer);
result.sources.forEach((s) => console.log(`- ${s.title} (${s.date})`));
```

Response times: `/ask` typically returns in **2–5 seconds** — it
embeds the question, retrieves 15 chunks from ChromaDB, deduplicates,
and synthesizes with GPT-4o.

**Common errors:**

| Code | Meaning | Fix |
|------|---------|-----|
| 401  | Missing or invalid `X-API-Key` | Double-check `CIVICLENS_API_KEY` is exported; rotate if needed (Recipe 12). |
| 422  | Question empty or > 2000 chars | Trim or split the question. |
| 429  | Monthly plan limit hit         | Wait for the window to reset, or upgrade plan via Recipe 12. |
| 500  | Internal error                 | Grab `X-Request-ID` from the response headers and file a ticket. |

---

## Recipe 2: Multi-turn chat with meeting context

**Use case:** A chat UI or agent where follow-up questions keep the
prior turn's context. Use this instead of `/ask` when the user is
having a conversation.

**Endpoint:** `POST /api/v1/chat`

**Request body:**

| Field            | Type  | Required | Notes                                          |
|------------------|-------|----------|------------------------------------------------|
| `messages`       | array | yes      | `{role, content}` objects; last must be `user` |
| `meeting_body`   | string| no       | Scope filter                                   |
| `date_after`     | string| no       | Scope filter                                   |
| `date_before`    | string| no       | Scope filter                                   |
| `model_provider` | string| no       | `"openai"` (default) or `"anthropic"`          |

Unlike `/ask`, `/chat` does **not** use a `conversation_id`; the
client is responsible for replaying the full message history each
turn. The server trims to the last 10 turn pairs internally.

```bash
curl -X POST "$CIVICLENS_API/api/v1/chat" \
  -H "X-API-Key: $CIVICLENS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Tell me about the parks budget"},
      {"role": "assistant", "content": "The parks budget for FY2025..."},
      {"role": "user", "content": "How does that compare to last year?"}
    ],
    "model_provider": "anthropic"
  }'
```

```python
async with CivicLensClient(api_key=os.environ["CIVICLENS_API_KEY"]) as cl:
    history = [
        {"role": "user", "content": "Tell me about the parks budget"},
    ]
    reply = await cl.chat(history, model_provider="anthropic")
    history.append({"role": "assistant", "content": reply["content"]})
    history.append({"role": "user", "content": "How does that compare to last year?"})
    reply = await cl.chat(history, model_provider="anthropic")
    print(reply["content"])
```

```javascript
const history = [{ role: "user", content: "Tell me about the parks budget" }];
let reply = await cl.chat(history, { modelProvider: "anthropic" });
history.push({ role: "assistant", content: reply.content });
history.push({ role: "user", content: "How does that compare to last year?" });
reply = await cl.chat(history, { modelProvider: "anthropic" });
console.log(reply.content);
```

Response includes `role`, `content`, `sources`, `model_used`,
`chunks_retrieved`. Expect **3–7 second** latency for Anthropic,
slightly faster for OpenAI.

---

## Recipe 3: Full-text search across meetings

**Use case:** Power a search bar, autocomplete, or "find meetings that
mention X" feature. This is keyword-level search, not the RAG pipeline.

**Endpoint:** `GET /api/v1/search`

**Query parameters:**

| Param          | Type    | Default | Notes                                                                 |
|----------------|---------|---------|-----------------------------------------------------------------------|
| `q`            | string  | `""`    | Full-text query                                                       |
| `type`         | string  | `all`   | `all`, `meeting`, `vote`, `financial`, `speaker`, `topic`, `agenda_item` |
| `limit`        | int     | 20      | 1–100                                                                 |
| `offset`       | int     | 0       | Pagination                                                            |
| `meeting_body` | string  | —       | Filter to one body                                                    |
| `date_after`   | string  | —       | `YYYY-MM-DD`                                                          |
| `date_before`  | string  | —       | `YYYY-MM-DD`                                                          |

```bash
curl -G "$CIVICLENS_API/api/v1/search" \
  -H "X-API-Key: $CIVICLENS_API_KEY" \
  --data-urlencode "q=short-term rental" \
  --data-urlencode "type=meeting" \
  --data-urlencode "date_after=2025-01-01" \
  --data-urlencode "limit=20"
```

```python
import httpx, os
async with httpx.AsyncClient(
    base_url=os.environ["CIVICLENS_API"],
    headers={"X-API-Key": os.environ["CIVICLENS_API_KEY"]},
) as http:
    r = await http.get("/api/v1/search", params={
        "q": "short-term rental",
        "type": "meeting",
        "date_after": "2025-01-01",
        "limit": 20,
        "offset": 0,
    })
    data = r.json()
    for row in data["results"]:
        print(row["title"], row["date"])
```

```javascript
const params = new URLSearchParams({
  q: "short-term rental",
  type: "meeting",
  date_after: "2025-01-01",
  limit: "20",
  offset: "0",
});
const r = await fetch(`${process.env.CIVICLENS_API}/api/v1/search?${params}`, {
  headers: { "X-API-Key": process.env.CIVICLENS_API_KEY },
});
console.log(await r.json());
```

Pagination: the response includes `total`. Walk it with
`offset = offset + limit` until `offset >= total`. Companion endpoints
you may want:

- `GET /api/v1/search/autocomplete?q=<prefix>` — fast prefix suggestions
- `GET /api/v1/search/recent` — this tenant's recently searched terms

---

## Recipe 4: Watch for new meetings matching a keyword (webhook)

**Use case:** Your pipeline should fire every time a new meeting has
been processed so you can run custom logic (post to Slack, enrich a
database, trigger an analysis).

**Endpoint:** `POST /api/v1/webhooks`

**Valid event types:** `meeting.processed`, `meeting.summary_ready`,
`vote.detected`, `financial_item.detected`, `query.answered`.

**Subscribe:**

```bash
curl -X POST "$CIVICLENS_API/api/v1/webhooks" \
  -H "X-API-Key: $CIVICLENS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://hooks.example.com/civiclens",
    "events": ["meeting.processed", "meeting.summary_ready"],
    "secret": "generate-your-own-32-byte-random-secret"
  }'
```

```python
await cl.register_webhook(
    url="https://hooks.example.com/civiclens",
    events=["meeting.processed", "meeting.summary_ready"],
    secret="generate-your-own-32-byte-random-secret",
)
```

```javascript
await cl.registerWebhook(
  "https://hooks.example.com/civiclens",
  ["meeting.processed", "meeting.summary_ready"],
  { secret: "generate-your-own-32-byte-random-secret" }
);
```

The response includes `id`, `url`, `events`, and — **only on creation**
— the `secret`. Store the secret now; you cannot fetch it back.

**Filter on your side, not theirs.** CivicLens webhooks fire on every
new meeting for your tenant; drop events whose topics/title don't
match your keyword:

```python
if "zoning" not in payload["meeting"]["title"].lower() \
   and not any("zoning" in t.lower() for t in payload["meeting"].get("topics", [])):
    return  # not interesting
```

**HMAC signature verification** — CivicLens sends
`X-Webhook-Signature: sha256=<hex>` computed as
`HMAC-SHA256(secret, raw_body)`:

```python
import hmac, hashlib
def verify(raw_body: bytes, header: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    received = header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)
```

```javascript
import crypto from "node:crypto";
function verify(rawBody, header, secret) {
  const expected = crypto.createHmac("sha256", secret).update(rawBody).digest("hex");
  const received = header.replace(/^sha256=/, "");
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(received));
}
```

**Retries and idempotency:** CivicLens retries up to 3 times with
exponential backoff (1s, 2s, 4s). Each delivery carries a unique `id`
in the delivery log — de-duplicate on your side using the webhook
payload's event identifier, because a retry may deliver the same
payload twice if your 200 response is slow.

Handy helpers:

- `GET /api/v1/webhooks` — list all your webhooks
- `GET /api/v1/webhooks/{id}/deliveries` — recent delivery attempts
- `POST /api/v1/webhooks/{id}/test` — fire a synthetic test event
- `DELETE /api/v1/webhooks/{id}` — unsubscribe

---

## Recipe 5: Track all votes by a specific council member

**Use case:** Build a voting-record scorecard for a politician,
journalism site, or constituent dashboard.

**Endpoint:** `GET /api/v1/votes` (search) and
`GET /api/v1/votes/member/{name}` (pre-aggregated record).

**Search parameters:**

| Param         | Notes                                  |
|---------------|----------------------------------------|
| `member`      | Council member name (substring match)  |
| `outcome`     | `passed` or `failed`                   |
| `keyword`     | Full-text over vote descriptions       |
| `date_after`  | `YYYY-MM-DD`                           |
| `date_before` | `YYYY-MM-DD`                           |
| `limit`       | 1–200 (default 50)                     |
| `offset`      | Pagination                             |

```bash
curl -G "$CIVICLENS_API/api/v1/votes" \
  -H "X-API-Key: $CIVICLENS_API_KEY" \
  --data-urlencode "member=Smith" \
  --data-urlencode "outcome=passed" \
  --data-urlencode "date_after=2025-01-01"
```

```python
votes = await cl.search_votes(
    member="Smith", outcome="passed", date_after="2025-01-01"
)
record = await cl.get_member_voting_record("Smith")
print(f"Participation rate: {record['participation_rate']}")
```

```javascript
const votes = await cl.searchVotes({
  member: "Smith", outcome: "passed", dateAfter: "2025-01-01",
});
const record = await cl.getMemberVotingRecord("Smith");
```

To build a scorecard, combine the two: use `search_votes` to pull raw
rows you can show as a list, and `member/{name}` for the summary box
at the top of the profile card.

---

## Recipe 6: Alert on specific agenda items

**Use case:** Get notified when a specific keyword appears on any
agenda, or when a council member is mentioned in a motion.

**Endpoint:** `POST /api/v1/alerts`

**Valid alert types:** `keyword`, `member`, `financial`, `vote`.

```bash
curl -X POST "$CIVICLENS_API/api/v1/alerts" \
  -H "X-API-Key: $CIVICLENS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Zoning watch",
    "type": "keyword",
    "config": {
      "keywords": ["zoning", "rezoning", "land use"],
      "meeting_body": "Planning Commission"
    }
  }'
```

```python
alert = await cl.create_alert(
    name="Zoning watch",
    alert_type="keyword",
    config={"keywords": ["zoning", "rezoning"], "meeting_body": "Planning Commission"},
)
matches = await cl.get_alert_matches(alert["id"])
```

```javascript
const alert = await cl.createAlert("Zoning watch", "keyword", {
  keywords: ["zoning", "rezoning"],
  meeting_body: "Planning Commission",
});
```

**Delivery:** alerts are evaluated against every newly-ingested
meeting. Inspect matches by polling
`GET /api/v1/alerts/{id}/matches`. For push delivery, subscribe a
webhook to `meeting.processed` (Recipe 4) and cross-reference your
alert rules on the receiving side. A dedicated "alert fired" webhook
event is **not** currently in the `VALID_EVENT_TYPES` list, so
polling or webhook-plus-filter is the wired path.

Other handy routes: `GET /api/v1/alerts` (list), `PUT /api/v1/alerts/{id}` (update),
`DELETE /api/v1/alerts/{id}` (delete).

---

## Recipe 7: Export all meetings for archiving / FOIA

**Use case:** Bulk download every meeting's transcripts, summaries,
and extracted facts — for archival, legal compliance, or handing a
FOIA response to a journalist.

**Endpoint flow:** async job → poll → download.

1. **Start the job** — `POST /api/v1/export`:

```bash
JOB=$(curl -s -X POST "$CIVICLENS_API/api/v1/export" \
  -H "X-API-Key: $CIVICLENS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"format": "json", "filters": {"meeting_body": "Council"}}' \
  | jq -r .job_id)
echo "Started job $JOB"
```

`format` can be `json`, `csv`, or `zip`.

2. **Poll** — `GET /api/v1/export/{job_id}`:

```bash
while true; do
  STATUS=$(curl -s "$CIVICLENS_API/api/v1/export/$JOB" \
    -H "X-API-Key: $CIVICLENS_API_KEY" | jq -r .status)
  echo "status=$STATUS"
  [ "$STATUS" = "completed" ] && break
  [ "$STATUS" = "failed" ] && exit 1
  sleep 5
done
```

3. **Download** — `GET /api/v1/export/{job_id}/download`:

```bash
curl -L -o export.zip \
  -H "X-API-Key: $CIVICLENS_API_KEY" \
  "$CIVICLENS_API/api/v1/export/$JOB/download"
```

SDK equivalents:

```python
job = await cl.start_export(format="zip", filters={"meeting_body": "Council"})
while True:
    status = await cl.get_export_status(job["job_id"])
    if status["status"] == "completed": break
    if status["status"] == "failed": raise RuntimeError("export failed")
    await asyncio.sleep(5)
with open("export.zip", "wb") as f:
    f.write(await cl.download_export(job["job_id"]))
```

For FOIA specifically, use `POST /api/v1/foia` with a
natural-language `query` — it runs RAG across the whole archive and
packages responsive segments:

```bash
curl -X POST "$CIVICLENS_API/api/v1/foia" \
  -H "X-API-Key: $CIVICLENS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "All discussions of the Maple Street development between 2024 and 2026"}'
```

---

## Recipe 8: Build a custom meeting feed widget

**Use case:** Drop a live Q&A widget into a city or nonprofit website
without building your own frontend.

You have two choices:

### Option A: drop-in `<script>` tag

The repository ships a fully-styled widget at
[`widget/civiclens-widget.js`](../widget/civiclens-widget.js). Host it
on your CDN and drop one line into your HTML:

```html
<script src="https://cdn.example.com/civiclens-widget.js"
  data-api-key="PUBLIC_WIDGET_KEY"
  data-api-url="https://api.civiclens.ai"
  data-theme="light"
  data-position="bottom-right"
  data-accent-color="#0066cc"
  data-welcome-message="Ask anything about our city council meetings"
  data-title="City Meeting Assistant"></script>
```

Configurable via the `data-*` attributes above. See
[`docs/EMBED_WIDGET.md`](./EMBED_WIDGET.md) for the full reference.

### Option B: build your own UI on the widget endpoints

`POST /api/v1/widget/ask` and `POST /api/v1/widget/chat` take the
exact same payload as their `/api/v1/ask` and `/api/v1/chat`
counterparts, but are intended for browser-side use with a public
widget key. Good for a nonprofit that wants to embed Q&A inside a
Webflow / Squarespace / WordPress page:

```html
<script>
async function ask(question) {
  const r = await fetch("https://api.civiclens.ai/api/v1/widget/ask", {
    method: "POST",
    headers: {
      "X-API-Key": "WIDGET_API_KEY",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ question }),
  });
  return r.json();
}
</script>
```

Issue a dedicated low-traffic tenant key for public widgets so you
can rotate it independently of your back-office integrations.

---

## Recipe 9: Sync CivicLens to your data warehouse

**Use case:** Mirror meetings, votes, and financial items into
Snowflake / BigQuery / Postgres so you can join them against your own
datasets (campaign finance, census, 311 complaints, etc).

The fastest pattern is webhook → your ingest endpoint → warehouse.

1. **Subscribe** to `meeting.processed`, `vote.detected`, and
   `financial_item.detected` (Recipe 4).
2. **Receive** at a tiny HTTPS service (Cloud Run, Lambda URL, Fly.io
   machine) and verify the HMAC signature.
3. **Upsert** into three tables:

```sql
-- meetings
CREATE TABLE meetings (
  clip_id       BIGINT PRIMARY KEY,
  tenant_id     TEXT,
  date          DATE,
  meeting_body  TEXT,
  title         TEXT,
  topics        JSONB,
  summary       TEXT,
  granicus_url  TEXT,
  processed_at  TIMESTAMPTZ
);

-- votes (one row per motion)
CREATE TABLE votes (
  vote_id        TEXT PRIMARY KEY,
  clip_id        BIGINT REFERENCES meetings(clip_id),
  identifier     TEXT,
  description    TEXT,
  motion_by      TEXT,
  second_by      TEXT,
  outcome        TEXT,
  ayes           INT,
  nays           INT,
  abstentions    INT,
  votes_for      JSONB,
  votes_against  JSONB,
  approx_time    INT
);

-- financial_items
CREATE TABLE financial_items (
  item_id      TEXT PRIMARY KEY,
  clip_id      BIGINT REFERENCES meetings(clip_id),
  description  TEXT,
  amount_cents BIGINT,
  item_type    TEXT
);
```

4. **Incremental backfill** on restart: call `GET /api/v1/search?type=meeting`
   paginated and filter locally on `processed_at > last_sync`
   (`processed_at` is in the meeting metadata returned by each
   matching row). Save `last_sync` on disk after each successful
   insert so a crash restarts from the right point.

For deep backfills, prefer Recipe 7's full export — it's a single
ZIP rather than hundreds of paginated calls.

---

## Recipe 10: Generate a weekly newsletter from meeting summaries

**Use case:** Every Monday, email subscribers a rundown of last
week's meetings with links and AI-generated one-paragraph recaps.

CivicLens does not currently expose a dedicated `/api/v1/meetings`
list endpoint, so use `/api/v1/search` with `type=meeting` and a
date filter:

```bash
LAST_WEEK=$(date -u -v-7d +%Y-%m-%d 2>/dev/null || date -u -d "7 days ago" +%Y-%m-%d)

curl -G "$CIVICLENS_API/api/v1/search" \
  -H "X-API-Key: $CIVICLENS_API_KEY" \
  --data-urlencode "q=" \
  --data-urlencode "type=meeting" \
  --data-urlencode "date_after=$LAST_WEEK" \
  --data-urlencode "limit=50"
```

```python
import httpx, os, datetime as dt
last_week = (dt.date.today() - dt.timedelta(days=7)).isoformat()
async with httpx.AsyncClient(
    base_url=os.environ["CIVICLENS_API"],
    headers={"X-API-Key": os.environ["CIVICLENS_API_KEY"]},
) as http:
    r = await http.get("/api/v1/search", params={
        "q": "", "type": "meeting", "date_after": last_week, "limit": 50,
    })
    meetings = r.json()["results"]

# For each meeting, ask for a one-paragraph summary
async with CivicLensClient(api_key=os.environ["CIVICLENS_API_KEY"]) as cl:
    for m in meetings:
        summary = await cl.ask(
            f"One short paragraph: what happened at the {m['date']} "
            f"{m['meeting_body']} meeting? Include the most important vote.",
            meeting_body=m["meeting_body"],
            date_after=m["date"],
            date_before=m["date"],
        )
        m["recap"] = summary["answer"]

# Render to Markdown and hand off to your own SMTP/Sendgrid/etc
md = "\n\n".join(
    f"### {m['title']} ({m['date']})\n\n{m['recap']}" for m in meetings
)
```

Tip: cap parallelism at 4–6 concurrent `ask` calls to stay well under
the rate limit on pro plans.

---

## Recipe 11: Add CivicLens to a Slack channel

**Use case:** Let staff ask questions about meetings directly from
Slack with `/civiclens ask …` or `@CivicLens`.

CivicLens ships a first-party Slack integration. Endpoints live under
`/api/v1/integrations/slack/*`:

| Endpoint                                   | Purpose                        |
|--------------------------------------------|--------------------------------|
| `POST /api/v1/integrations/slack/install`  | Start the OAuth install flow   |
| `GET  /api/v1/integrations/slack/callback` | OAuth redirect handler         |
| `POST /api/v1/integrations/slack/commands` | Slash-command handler (`/civiclens ...`) |
| `POST /api/v1/integrations/slack/events`   | Events API for `@` mentions    |
| `GET  /api/v1/integrations/slack/config`   | Read current tenant config     |
| `PUT  /api/v1/integrations/slack/config`   | Update tenant config           |

Set `SLACK_SIGNING_SECRET`, `SLACK_CLIENT_ID`, and
`SLACK_CLIENT_SECRET` in your environment, then point your Slack app
at those URLs. For local development, expose your dev server via
ngrok:

```bash
ngrok http 8000
# Then set request URLs in api.slack.com/apps/<your-app> to:
#   Slash: https://<ngrok>.ngrok.io/api/v1/integrations/slack/commands
#   Events: https://<ngrok>.ngrok.io/api/v1/integrations/slack/events
#   Redirect: https://<ngrok>.ngrok.io/api/v1/integrations/slack/callback
```

Full step-by-step walkthrough with screenshots:
[`docs/SLACK_INTEGRATION.md`](./SLACK_INTEGRATION.md).

---

## Recipe 12: Programmatic tenant provisioning

**Use case:** You're a reseller, integrator, or consulting firm that
signs up one city after another and wants to automate tenant creation
end-to-end.

These endpoints require `ADMIN_API_KEY` (separate from any tenant
key) passed as the `X-API-Key` header.

```bash
# 1. Create a tenant
curl -X POST "$CIVICLENS_API/api/v1/admin/tenants" \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "lexington-ky",
    "name": "Lexington, KY",
    "granicus_host": "lexington.granicus.com",
    "granicus_view_id": "2",
    "plan": "pro"
  }'
# → response includes the plaintext "api_key" — store it now.

# 2. List all tenants
curl -H "X-API-Key: $ADMIN_API_KEY" "$CIVICLENS_API/api/v1/admin/tenants"

# 3. Rotate a tenant's API key (if compromised)
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  "$CIVICLENS_API/api/v1/admin/tenants/lexington-ky/rotate-key"

# 4. Upgrade a plan
curl -X PATCH "$CIVICLENS_API/api/v1/admin/tenants/lexington-ky/plan" \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"plan": "enterprise"}'

# 5. Delete
curl -X DELETE -H "X-API-Key: $ADMIN_API_KEY" \
  "$CIVICLENS_API/api/v1/admin/tenants/lexington-ky"
```

If you're managing tenants from the same machine that runs the
pipeline, the CLI is more convenient:

```bash
uv run python -m api.tenants create \
    --id elk-grove-ca --name "Elk Grove, CA" \
    --granicus-host elkgrove.granicus.com --granicus-view-id 2 --plan pro
uv run python -m api.tenants list
uv run python -m api.tenants rotate-key --id elk-grove-ca
uv run python -m api.tenants update-plan --id elk-grove-ca --plan enterprise
uv run python -m api.tenants process --id elk-grove-ca --max 20
```

Plan ceilings are visible via `GET /api/v1/admin/plans` (starter 100
queries/mo, pro 1,000, enterprise unlimited).

---

## Recipe 13: Query analytics — top questions this month

**Use case:** Prep a customer QBR. "What are the top 10 things your
staff are asking CivicLens about?"

**Endpoints** (all tenant-scoped):

| Endpoint                                  | Returns                                        |
|-------------------------------------------|------------------------------------------------|
| `GET /api/v1/analytics/usage?period=30d`  | Summary: queries used, remaining, plan         |
| `GET /api/v1/analytics/queries?period=7d` | Query counts by day                            |
| `GET /api/v1/analytics/popular-topics?period=30d&limit=10` | Top meeting bodies + top questions |
| `GET /api/v1/analytics/recent?limit=50`   | Most recent raw queries                        |
| `GET /api/v1/analytics/peak-hours`        | UTC peak-hour histogram                        |
| `GET /api/v1/analytics/export/queries?period=30d` | CSV dump for pivoting in a spreadsheet |

```python
usage = await cl.get_usage(period="30d")
topics = await cl.get_popular_topics(period="30d", limit=10)
print(f"{usage['queries_used']}/{usage['queries_limit']} queries used")
for q in topics["top_questions"]:
    print(f"  {q['count']}x  {q['question']}")
```

```javascript
const usage = await cl.getUsage({ period: "30d" });
const topics = await cl.getPopularTopics({ period: "30d", limit: 10 });
```

Drop the CSV endpoint into a scheduled job if you want to feed your
BI tool without writing SDK code.

Admin users (with `ADMIN_API_KEY`) can also call
`GET /api/v1/admin/analytics/overview` for cross-tenant totals.

---

## Recipe 14: SSO login with your IdP (SAML 2.0)

**Use case:** Your city IT department insists on Okta / Azure AD /
Google Workspace single sign-on, not plain API keys, for the web app.

High-level flow:

1. **Collect IdP metadata** — SSO URL, entity ID, X.509 signing cert,
   and (optionally) SLO URL from your IdP admin.
2. **Register the IdP config** against your tenant. Once registered,
   CivicLens exposes an SP metadata document at
   `GET /api/v1/sso/metadata` that you hand back to the IdP to
   complete the trust relationship.
3. **Start the login** — send the browser to
   `GET /api/v1/sso/login`. CivicLens 302s to the IdP.
4. **ACS callback** — the IdP posts the SAMLResponse to
   `POST /api/v1/sso/acs`. CivicLens verifies the signature,
   auto-provisions the user if needed, mints a JWT, and sets it as
   both a cookie and the `Authorization: Bearer` token for SPA use.
5. **Who am I?** — the client can call
   `GET /api/v1/auth/me` at any time to resolve the current user.
6. **Logout** — `GET /api/v1/sso/logout` kills the session and, if
   the IdP supports single logout, redirects through it.

Map IdP groups to CivicLens roles (`admin`, `analyst`, `viewer`)
during the admin config step. The permission sets, email-domain
restrictions, and attribute-mapping options are documented in
[`docs/SSO_SETUP.md`](./SSO_SETUP.md).

Test checklist before handing the URL to end users:

- [ ] SP metadata downloadable at `/api/v1/sso/metadata`
- [ ] `/api/v1/sso/login` redirects to the IdP
- [ ] ACS returns a `civiclens_session` cookie
- [ ] `/api/v1/auth/me` returns `{user, roles, tenant}`
- [ ] `/api/v1/sso/logout` clears the cookie

---

## Recipe 15: Self-hosted deployment smoke test

**Use case:** Prove a fresh self-hosted CivicLens install works
before pointing real users at it.

```bash
# 1. Build and run
docker build -t civiclens .
docker run -d --name civiclens \
  -p 8000:8000 \
  -e OPENAI_API_KEY=sk-... \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e ADMIN_API_KEY=$(openssl rand -hex 32) \
  -e MEETINGS_OUTPUT_DIR=/data \
  -v civiclens-data:/data \
  civiclens

# 2. Public health check (no auth)
curl http://localhost:8000/health
# → {"status":"ok"}

# 3. Create your first tenant
curl -X POST http://localhost:8000/api/v1/admin/tenants \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-city",
    "name": "Test City",
    "granicus_host": "test.granicus.com",
    "plan": "enterprise"
  }'
# → copy the returned "api_key" into $CIVICLENS_API_KEY

# 4. Run the pipeline against a small range to seed the index
docker exec civiclens uv run python main.py \
  --tenant-id test-city --scrape --max 3 --rag

# 5. Authenticated health check
curl -H "X-API-Key: $CIVICLENS_API_KEY" \
  http://localhost:8000/api/v1/health
# → {"status":"ok","tenant":"test-city","chunks_indexed":N,"clips_indexed":N}

# 6. First question
curl -X POST http://localhost:8000/api/v1/ask \
  -H "X-API-Key: $CIVICLENS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "What was discussed at the most recent meeting?"}'
```

Full production-grade checklist:
[`docs/SELF_HOSTING.md`](./SELF_HOSTING.md).

---

## Appendix: Troubleshooting

| Code | Likely cause                                          | First things to try |
|------|--------------------------------------------------------|--------------------|
| 401  | Missing/mis-typed `X-API-Key`                          | Re-export env var; check for stray whitespace; confirm key still exists via `api.tenants list` or admin API. |
| 403  | Using a tenant key against an admin-only endpoint      | Swap in `ADMIN_API_KEY`. |
| 404  | Wrong path, or the resource belongs to another tenant  | Check the path against this cookbook; tenants cannot read each other's data. |
| 409  | Export job not yet `completed` when you asked to download | Poll `/api/v1/export/{job_id}` until `status=completed`. |
| 422  | Pydantic validation failure                            | Read the `detail` field — it names the bad field. Common issues: empty question, `date_after` not ISO, last chat message not from user. |
| 429  | Monthly plan limit hit                                 | Wait for the 30-day window to roll or upgrade (Recipe 12). `Retry-After: 3600` is the hint. |
| 500  | Unhandled server error                                 | Grab `X-Request-ID` from the response headers and pass it to support; they can jump straight to the correlating log line. |

**Get the request id** — every response (even successful ones)
includes `X-Request-ID`:

```bash
curl -i -X POST "$CIVICLENS_API/api/v1/ask" \
  -H "X-API-Key: $CIVICLENS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question":"test"}' | grep -i x-request-id
```

You can also *set* `X-Request-ID` yourself so the id threads across
client and server logs:

```bash
-H "X-Request-ID: $(uuidgen)"
```

**Where to file issues:** GitHub issues on the CivicLens repository,
or email `support@civiclens.ai` with the request id.

---

## Appendix: API changelog reference

See [`docs/CHANGELOG.md`](./CHANGELOG.md) for a chronological list of
breaking and non-breaking API changes.
