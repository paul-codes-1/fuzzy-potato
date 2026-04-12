# Accessibility Conformance Report

**Product:** CivicLens Meeting Archive
**WCAG Version:** 2.1 Level AA
**Date:** 2026-04-08
**Status:** Partially conformant

---

## VPAT Summary (Voluntary Product Accessibility Template)

This document describes the current accessibility posture of the CivicLens web frontend, following the VPAT 2.5 Rev 508 format (Section 508 / WCAG 2.1 AA).

### Applicable Standards

| Standard | Conformance Level |
|---|---|
| WCAG 2.1 Level A | Supports |
| WCAG 2.1 Level AA | Partially Supports |
| Section 508 (Revised) | Partially Supports |

---

## WCAG 2.1 AA Criteria

### 1 Perceivable

| Criterion | Status | Notes |
|---|---|---|
| 1.1.1 Non-text Content | Supports | Images have alt text; decorative icons use `aria-hidden`; SVG charts have `role="img"` with descriptive `aria-label` |
| 1.2.1 Audio-only / Video-only | Not Applicable | Video is third-party embedded (Granicus); transcripts are provided for all meetings |
| 1.3.1 Info and Relationships | Supports | Semantic HTML: headings (h1-h3), tables with `scope="col"`, landmark regions, lists, form labels |
| 1.3.2 Meaningful Sequence | Supports | DOM order matches visual order |
| 1.4.1 Use of Color | Supports | Vote badges use text labels ("passed"/"failed") alongside color |
| 1.4.3 Contrast (Minimum) | Supports | Primary text meets 4.5:1; high-contrast mode override via `prefers-contrast: high` |
| 1.4.4 Resize Text | Supports | All text uses relative units (rem); layout reflows at 200% zoom |
| 1.4.10 Reflow | Supports | Responsive layout down to 320px viewport |
| 1.4.11 Non-text Contrast | Supports | Focus indicators, form borders, and interactive elements meet 3:1 |
| 1.4.12 Text Spacing | Supports | No fixed-height containers that clip text when spacing is overridden |
| 1.4.13 Content on Hover/Focus | Supports | No custom tooltips that block content |

### 2 Operable

| Criterion | Status | Notes |
|---|---|---|
| 2.1.1 Keyboard | Supports | All interactive elements are keyboard-accessible; skip-to-content link provided |
| 2.1.2 No Keyboard Trap | Supports | Modal dialogs use focus trap with Escape to close |
| 2.4.1 Bypass Blocks | Supports | Skip-to-main-content link; landmark regions (`banner`, `main`, `contentinfo`, `nav`) |
| 2.4.2 Page Titled | Supports | Browser title set via React Router |
| 2.4.3 Focus Order | Supports | Tab order follows visual layout; tab panels use roving tabindex |
| 2.4.4 Link Purpose | Supports | Links have descriptive text or `aria-label` |
| 2.4.6 Headings and Labels | Supports | Heading hierarchy (h1 > h2 > h3); form inputs have associated labels |
| 2.4.7 Focus Visible | Supports | `focus-visible` outlines on all interactive elements; enhanced on dark backgrounds |
| 2.5.5 Target Size | Supports | Minimum 44x44px touch targets for buttons and links |

### 3 Understandable

| Criterion | Status | Notes |
|---|---|---|
| 3.1.1 Language of Page | Supports | `lang` attribute set on `<html>` element |
| 3.2.1 On Focus | Supports | No context changes on focus |
| 3.2.2 On Input | Supports | Filter changes update results without unexpected navigation |
| 3.3.1 Error Identification | Supports | Errors use `role="alert"` with descriptive messages |
| 3.3.2 Labels or Instructions | Supports | All form fields have visible or screen-reader labels |

### 4 Robust

| Criterion | Status | Notes |
|---|---|---|
| 4.1.1 Parsing | Supports | Valid HTML; no duplicate IDs |
| 4.1.2 Name, Role, Value | Supports | ARIA roles for tabs, dialogs, alerts, status; `aria-selected`, `aria-expanded`, `aria-pressed`, `aria-live` used correctly |
| 4.1.3 Status Messages | Supports | Dynamic content changes announced via `aria-live` regions (search results, loading states, errors, chat responses) |

---

## Implementation Details

### Landmark Regions

- `<header role="banner">` -- site header and navigation
- `<nav aria-label="Main navigation">` -- primary navigation
- `<main id="main-content">` -- primary content area, skip-link target
- `<footer role="contentinfo">` -- site footer

### Screen Reader Announcements

The `AnnounceProvider` component provides a global `useAnnounce()` hook that pushes messages to two visually hidden `aria-live` regions:
- **polite**: search result counts, data loaded, responses received
- **assertive**: errors, form validation failures

Used in: MeetingList (result counts), AskQuestion (loading/error/results), ChatMeetings (typing/error/response)

### Tab Interfaces

Both MeetingDetail and VoteTracker implement the WAI-ARIA tabs pattern:
- `role="tablist"` with `aria-label` on the container
- `role="tab"` with `aria-selected`, `aria-controls`, roving `tabIndex`
- `role="tabpanel"` with `id` and `aria-labelledby`

### Data Tables

All data tables (votes, financial items, query logs) include:
- `aria-label` on the `<table>` element
- `scope="col"` on all `<th>` elements

### Modal Dialogs

The CreateTenantForm modal implements:
- `role="dialog"` with `aria-modal="true"`
- Focus trap (Tab/Shift+Tab cycles within modal)
- Escape key closes the modal
- Auto-focus on first input when opened

### Media Preferences

- **`prefers-reduced-motion: reduce`** -- disables all animations and transitions
- **`prefers-contrast: high`** -- increases border visibility, sets text to pure black/white, removes shadows, uses solid borders on cards

---

## Known Limitations

1. **Embedded video player (Granicus)**: The third-party Granicus video player iframe is outside our control. Its internal accessibility depends on Granicus. We provide an `iframe title` attribute and link to open the video directly.

2. **SVG chart interactivity**: The voting heatmap and analytics bar chart provide descriptive `aria-label` text and `<title>` elements on individual data points, but do not provide a full data table alternative for complex charts. Screen reader users can access the same data through the corresponding table views.

3. **`dangerouslySetInnerHTML` content**: AI-generated answer content (AskQuestion, ChatMessage) is rendered via simple markdown-to-HTML conversion. This content is not validated for accessibility; however, it uses only basic HTML elements (`<p>`, `<strong>`, `<em>`, `<br>`).

4. **Color-only vote indicators**: The voting heatmap uses green/red/grey. We include `<title>` text on each cell to provide the vote status. A text-based table view is available as an alternative.

---

## Testing Procedures

### Automated Testing

1. **axe-core**: Run browser extension on all routes
   ```
   npm install -D @axe-core/react
   ```

2. **Lighthouse Accessibility audit**: Target score >= 90 on all routes

3. **eslint-plugin-jsx-a11y**: Installed and configured to catch common issues at build time

### Manual Testing

1. **Keyboard-only navigation**: Tab through all pages without using a mouse
   - Verify skip-link appears on first Tab press
   - Verify all interactive elements are reachable
   - Verify focus ring is visible on each element
   - Verify modals trap focus and close with Escape

2. **Screen reader testing**:
   - macOS: VoiceOver (Safari)
   - Windows: NVDA (Firefox) or JAWS (Chrome)
   - Verify all content is announced
   - Verify dynamic changes (search results, loading, errors) are announced
   - Verify tables are navigable by header

3. **Zoom testing**: Set browser zoom to 200%, verify no content is clipped or overlapping

4. **High contrast mode**: Enable `prefers-contrast: high` in browser DevTools, verify all text and interactive elements are visible

5. **Reduced motion**: Enable `prefers-reduced-motion: reduce` in browser DevTools, verify no animations play

---

## Accessibility Contact

For accessibility issues or accommodation requests, please contact:

- **Repository**: https://github.com/paul-codes-1/fuzzy-potato/
- **Issues**: Open a GitHub issue with the `accessibility` label
