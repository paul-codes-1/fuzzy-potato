# @civiclens/sdk

Official JavaScript/Node SDK for the [CivicLens API](https://docs.civiclens.ai) -- turn local government meeting archives into a searchable, queryable knowledge base.

## Installation

```bash
npm install @civiclens/sdk
```

## Quick Start

```js
import { CivicLensClient } from '@civiclens/sdk';

const cl = new CivicLensClient({ apiKey: 'cl_your_key_here' });

// Ask a question
const result = await cl.ask('What has the city done about short-term rentals?');
console.log(result.answer);

// Multi-turn chat
const chatResult = await cl.chat([
  { role: 'user', content: 'Tell me about the parks budget' },
  { role: 'assistant', content: 'The parks department budget...' },
  { role: 'user', content: 'How does that compare to last year?' },
]);

// Search votes
const votes = await cl.searchVotes({ member: 'Smith', outcome: 'passed' });

// Search financial items
const financials = await cl.searchFinancial({ minAmount: 100000 });

// Create a policy alert
const alert = await cl.createAlert('Zoning changes', 'keyword', {
  keywords: ['zoning', 'rezoning', 'land use'],
});
```

## Features

- **Promise-based** -- works with async/await
- **ESM and CJS** -- dual-format package
- **Node 18+ and browser** -- uses native `fetch`
- **Automatic retry** -- exponential backoff on transient failures (429, 5xx)
- **Rate limit awareness** -- parses `X-RateLimit-*` headers; available via `client.lastRateLimit`
- **Typed errors** -- `AuthenticationError`, `RateLimitError`, `NotFoundError`
- **Full API coverage** -- Q&A, chat, votes, financial, alerts, export, FOIA, analytics, audit, billing, webhooks, scheduler, branding, status

## Configuration

```js
const client = new CivicLensClient({
  apiKey: 'cl_...',
  baseUrl: 'https://api.civiclens.ai', // default
  timeout: 60000,                       // ms (default)
  maxRetries: 3,                        // retries on transient errors
  backoffBase: 1000,                    // ms backoff base
  fetchFn: customFetch,                 // optional custom fetch implementation
});
```

## Available Methods

### Q&A
| Method | Description |
|---|---|
| `ask(question, opts?)` | Single-turn RAG question answering |
| `chat(messages, opts?)` | Multi-turn conversational Q&A |

### Votes & Financial
| Method | Description |
|---|---|
| `searchVotes(opts?)` | Search extracted votes |
| `getMemberVotingRecord(name)` | Full voting record for a member |
| `getVoteStats()` | Aggregate vote statistics |
| `searchFinancial(opts?)` | Search financial items |
| `getFinancialSummary()` | Aggregate financial summary |

### Alerts
| Method | Description |
|---|---|
| `createAlert(name, type, config)` | Create a policy alert |
| `listAlerts()` | List all alerts |
| `updateAlert(id, updates?)` | Update an alert |
| `deleteAlert(id)` | Delete an alert |
| `getAlertMatches(id, opts?)` | Get alert matches |

### Export & FOIA
| Method | Description |
|---|---|
| `startExport(opts?)` | Start async data export |
| `getExportStatus(jobId)` | Check export status |
| `downloadExport(jobId)` | Download as ArrayBuffer |
| `foiaSearch(query)` | FOIA-style search |

### Analytics & Audit
| Method | Description |
|---|---|
| `getUsage(opts?)` | Usage summary |
| `getQueryVolume(opts?)` | Query volume over time |
| `getPopularTopics(opts?)` | Popular topics |
| `searchAuditLog(opts?)` | Search audit log |
| `verifyAuditIntegrity()` | Verify audit hash chain |

### Billing
| Method | Description |
|---|---|
| `createCheckout(opts)` | Stripe checkout session |
| `createBillingPortal(opts)` | Stripe billing portal |
| `getBillingUsage()` | Current billing usage |

### Webhooks
| Method | Description |
|---|---|
| `registerWebhook(url, events, opts?)` | Register a webhook |
| `listWebhooks()` | List webhooks |
| `deleteWebhook(id)` | Delete a webhook |
| `testWebhook(id)` | Send test event |

### Scheduler
| Method | Description |
|---|---|
| `getSchedule()` | Get ingestion schedule |
| `updateSchedule(opts?)` | Update schedule |
| `triggerRunNow()` | Trigger immediate run |

### Status & Health
| Method | Description |
|---|---|
| `getStatus()` | System status |
| `getUptime()` | Uptime percentages |
| `health()` | Health check (no auth) |

## Error Handling

```js
import { CivicLensClient, AuthenticationError, RateLimitError, NotFoundError } from '@civiclens/sdk';

try {
  const result = await client.ask('...');
} catch (err) {
  if (err instanceof AuthenticationError) {
    console.error('Invalid API key');
  } else if (err instanceof RateLimitError) {
    console.error(`Rate limited. Retry after ${err.retryAfter}s`);
  } else if (err instanceof NotFoundError) {
    console.error('Resource not found');
  } else {
    console.error(`API error: ${err.message}`);
  }
}
```

## Browser Usage

The SDK uses the global `fetch` API, which is available in all modern browsers. No polyfills needed.

```html
<script type="module">
  import { CivicLensClient } from 'https://cdn.jsdelivr.net/npm/@civiclens/sdk/index.js';

  const cl = new CivicLensClient({ apiKey: 'cl_...' });
  const result = await cl.ask('What happened at the last council meeting?');
</script>
```

## License

Proprietary. See [Terms of Service](https://civiclens.ai/terms).
