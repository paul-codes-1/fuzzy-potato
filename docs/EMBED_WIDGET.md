# CivicLens Embeddable Widget

The CivicLens widget is a drop-in chat interface that adds AI-powered meeting Q&A to any government website. It loads as a single `<script>` tag and runs entirely inside a Shadow DOM, so it will not conflict with existing page styles.

## Quick Start

Add this snippet before the closing `</body>` tag on any page:

```html
<script
  src="https://cdn.civiclens.ai/widget.js"
  data-api-key="mra_your_tenant_api_key"
  data-api-url="https://api.civiclens.ai">
</script>
```

A floating chat button will appear in the bottom-right corner. Clicking it opens a conversational Q&A panel backed by the CivicLens API.

## Configuration Attributes

All configuration is done via `data-*` attributes on the script tag.

| Attribute | Default | Description |
|---|---|---|
| `data-api-key` | *(required)* | Your tenant API key (`mra_...`). Obtain from the admin dashboard or the `/api/v1/admin/tenants` endpoint. |
| `data-api-url` | *(required)* | Base URL of your CivicLens API server (e.g. `https://api.civiclens.ai`). |
| `data-theme` | `light` | `light` or `dark`. Controls chat panel colors. |
| `data-position` | `bottom-right` | `bottom-right` or `bottom-left`. Position of the floating button and panel. |
| `data-accent-color` | `#1a56db` | Hex color for the button, header, and link highlights. Match to your city brand. |
| `data-welcome-message` | `Ask a question about city council meetings...` | Greeting shown when the chat panel first opens. |
| `data-title` | `Meeting Q&A` | Header text displayed at the top of the chat panel. |

## Example with Full Configuration

```html
<script
  src="https://cdn.civiclens.ai/widget.js"
  data-api-key="mra_abc123"
  data-api-url="https://api.civiclens.ai"
  data-theme="dark"
  data-position="bottom-left"
  data-accent-color="#2d5a87"
  data-welcome-message="Search Elk Grove city council meetings, votes, and budgets."
  data-title="Elk Grove Meeting Assistant">
</script>
```

## How It Works

1. The script creates a Shadow DOM container, isolating widget styles from the host page.
2. On the first user question, it sends a `POST` to `/api/v1/widget/ask` with the question and API key.
3. Follow-up questions use `/api/v1/widget/chat` for multi-turn conversation with full context.
4. Responses include source citations with links back to Granicus video clips at the relevant timestamp.

## API Endpoints Used

The widget calls two endpoints on your CivicLens API:

- **`POST /api/v1/widget/ask`** -- Single-turn question. Body: `{"question": "..."}`.
- **`POST /api/v1/widget/chat`** -- Multi-turn conversation. Body: `{"messages": [{"role": "user", "content": "..."}]}`.

Both require the `X-API-Key` header, which the widget sets automatically from `data-api-key`.

## CORS

The widget runs on your city website domain, making cross-origin requests to the CivicLens API. The API server includes permissive CORS headers for widget endpoints. If you are self-hosting, ensure your `CORS_ORIGINS` environment variable includes the domains where the widget will be embedded, or set it to `*` during development.

## Demo Page

A demo page is included at `widget/index.html`. To test locally:

```bash
# Start the API server
uv run uvicorn api.server:app --reload --port 8000

# Open the demo page in a browser
open widget/index.html
```

The demo page simulates a city government website with the widget embedded. Update `data-api-key` and `data-api-url` in the HTML to point to your running instance.

## Accessibility

The widget follows WAI-ARIA guidelines:

- The floating button has `aria-expanded` state and descriptive labels.
- The chat panel is a `role="dialog"` with keyboard focus trapping.
- Messages use `role="log"` with `aria-live="polite"` for screen reader announcements.
- The Escape key closes the panel.
- Focus returns to the floating button when the panel closes.

## Browser Support

The widget uses Shadow DOM (supported in all modern browsers). It does not require any build step, bundler, or framework -- just a single script tag.
