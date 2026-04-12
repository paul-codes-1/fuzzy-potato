# CivicLens Quickstart Guide

This guide walks you through your first query against the CivicLens Meeting Intelligence API.

---

## 1. Get Your API Key

Your instance administrator creates tenants and issues API keys. If you are the administrator, create a tenant using the admin API or the CLI:

**Via CLI:**

```bash
python -m api.tenants create \
  --id elk-grove-ca \
  --name "City of Elk Grove" \
  --granicus-host elkgrove.granicus.com \
  --granicus-view-id 5 \
  --plan starter
```

This prints your API key (prefixed with `mra_`). Treat it as a secret. The supported recovery flow is key rotation:

```bash
python -m api.tenants rotate-key --id elk-grove-ca
```

**Via Admin API:**

```bash
curl -X POST https://your-instance.example.com/api/v1/admin/tenants \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_ADMIN_KEY" \
  -d '{
    "id": "elk-grove-ca",
    "name": "City of Elk Grove",
    "granicus_host": "elkgrove.granicus.com",
    "granicus_view_id": "5",
    "plan": "starter"
  }'
```

The response includes the `api_key` field.

---

## 2. Your First Query

```bash
curl -X POST https://your-instance.example.com/api/v1/ask \
  -H "Content-Type: application/json" \
  -H "X-API-Key: mra_your_key_here" \
  -d '{"question": "What has the city council discussed about park improvements?"}'
```

You will get back a JSON response with:

- **`answer`** -- A synthesized, markdown-formatted answer with citations like `[Clip 6669, 25:15]`
- **`sources`** -- An array of meeting excerpts that informed the answer, each with a `granicus_url` that links directly to the relevant moment in the meeting video
- **`chunks_retrieved`** -- How many meeting chunks were used
- **`filters_applied`** -- Which filters were active

---

## 3. Using Filters

Filters narrow the search to specific meeting bodies or date ranges. All filters are optional and can be combined.

### Filter by meeting body

```json
{
  "question": "What votes were taken on rezoning?",
  "meeting_body": "Planning Commission"
}
```

Meeting body values match the names in your Granicus instance (e.g., "Council", "Planning Commission", "WQFB", "Board of Adjustment").

### Filter by date range

```json
{
  "question": "What budget items were approved?",
  "date_after": "2025-07-01",
  "date_before": "2025-12-31"
}
```

Dates use `YYYY-MM-DD` format. Use `date_after` alone for "since this date", `date_before` alone for "up to this date", or both for a specific window.

### Combined filters

```json
{
  "question": "Were there any public comments about traffic?",
  "meeting_body": "Council",
  "date_after": "2026-01-01"
}
```

---

## 4. Understanding Sources and Citations

Each answer includes citations in the format `[Clip ID, MM:SS]` that reference specific meetings. The `sources` array provides the details:

```json
{
  "clip_id": 6669,
  "date": "2026-01-08",
  "title": "January 8 2026 Urban County Council meeting",
  "meeting_body": "Council",
  "excerpt": "The first 200 characters of the retrieved chunk...",
  "granicus_url": "https://lexington.granicus.com/player/clip/6669?view_id=3&entrytime=1515",
  "timestamp": 1515
}
```

- **`granicus_url`** -- Click this to jump to the exact moment in the meeting video. The `entrytime` parameter is the timestamp in seconds.
- **`timestamp`** -- Seconds from the start of the meeting clip. Only present for sources where a timestamp could be determined (transcript chunks, timestamped summary sections).
- **`excerpt`** -- A preview of the retrieved text that informed the answer.

Sources are drawn from five types of meeting data:
1. **Summary** -- AI-generated narrative sections with timestamps
2. **Facts** -- Structured data (votes, financial items, attendance, public comments)
3. **Minutes** -- Official meeting minutes when available
4. **Agenda** -- Meeting agenda items
5. **Transcript** -- Verbatim transcription of the meeting audio

The retrieval system ensures diversity by limiting results to at most 4 chunks per meeting and 2 per source type per meeting.

---

## 5. Multi-Turn Chat

For follow-up questions, use the `/api/v1/chat` endpoint. Send the full conversation history so the model can understand context:

```bash
curl -X POST https://your-instance.example.com/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: mra_your_key_here" \
  -d '{
    "messages": [
      {"role": "user", "content": "What did the council decide about the new library?"},
      {"role": "assistant", "content": "In the February 2026 meeting, the council approved..."},
      {"role": "user", "content": "How much funding was allocated?"}
    ]
  }'
```

The last message must always have `role: "user"`. You can optionally set `model_provider` to `"anthropic"` for Claude-powered responses (default is `"openai"` for GPT-4o).

---

## 6. Tips for Effective Questions

**Be specific.** Instead of "What happened at the meeting?", ask "What zoning changes were approved in the January 2026 Council meeting?"

**Use filters to narrow scope.** If you know which body or time period you care about, set the filters. This reduces noise and improves answer quality.

**Ask about concrete data.** The system has structured facts (vote counts, dollar amounts, roll calls) and works well for questions like:
- "How did Council Member Smith vote on Ordinance 123?"
- "What was the total amount of contracts approved in Q1 2026?"
- "Who spoke during public comment about the zoning change?"

**Use follow-ups for exploration.** Start with a broad question, then drill down with chat:
1. "What were the major decisions in March 2026?"
2. "Tell me more about the infrastructure bond."
3. "Which council members voted against it?"

**Reference dates and identifiers when possible.** Questions like "What happened with Ordinance 0016-26?" or "Summarize the March 15 Planning Commission meeting" get precise results.

---

## 7. Check Your Usage

Use the health endpoint to see how many meetings are indexed:

```bash
curl -H "X-API-Key: mra_your_key_here" \
  https://your-instance.example.com/api/v1/health
```

Your plan's rate limit is visible in the `X-RateLimit-Remaining` and `X-RateLimit-Limit` response headers on every request. If you are approaching your limit, contact your administrator about upgrading your plan.

---

## Next Steps

- Read the full [API Reference](API.md) for all endpoints, error codes, and integration examples
- See [Self-Hosting Guide](SELF_HOSTING.md) if you are deploying your own instance
