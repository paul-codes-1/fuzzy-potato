# CivicLens — Product Positioning

## One-Liner

AI-powered city council meeting intelligence that turns hours of public video into structured, searchable, queryable data.

## Problem

City council meetings generate thousands of hours of video annually, but this critical public record is effectively inaccessible. Finding a specific vote, dollar amount, or policy discussion requires watching recordings in real time. Staff spend dozens of hours per month creating manual digests. Journalists, advocates, and citizens lack practical tools to hold local government accountable.

## Solution

CivicLens automatically processes meeting videos from Granicus into structured intelligence:

- **AI Transcription** with timestamped segments linked to source video
- **Two-Pass Extraction** that pulls votes (with roll calls), financial items (with dollar amounts), attendance, public comments, and key decisions into structured JSON
- **Narrative Summaries** generated section-by-section from extracted facts — no hallucinated data
- **Natural Language Q&A** across the entire meeting archive with cited, verifiable sources
- **Full-Text Search** with filters by date, meeting body, and topic

## Target Customers

### Primary: City and County Governments
- **Buyer**: City Clerk, IT Director, City Manager
- **Pain**: Transparency mandates, ADA compliance for meeting content, FOIA request burden, staff time on manual meeting digests
- **Value prop**: Reduce FOIA burden, meet ADA requirements, improve public trust, save 40+ staff hours/month
- **Procurement**: Cooperative purchasing (NASPO, Sourcewell), purchase orders, multi-year contracts

### Secondary: Advocacy & Watchdog Organizations
- **Buyer**: Executive Director, Policy Director
- **Pain**: Tracking policy across dozens of committees, building evidence for campaigns, monitoring specific issues
- **Value prop**: Keyword alerts, vote tracking by member, cross-meeting issue timelines

### Tertiary: Journalists
- **Buyer**: Individual reporter or newsroom
- **Pain**: Research time, verifying claims, building story timelines
- **Value prop**: Ask questions, get sourced answers with video timestamps in seconds instead of days

### Emerging: Real Estate & Legal
- **Buyer**: Land use attorney, development VP
- **Pain**: Monitoring zoning changes, variance approvals, ordinance updates
- **Value prop**: Automated alerts on relevant decisions, complete decision timelines

## Competitive Positioning

| Competitor | What They Do | CivicLens Advantage |
|---|---|---|
| Granicus (native) | Video hosting + basic search | CivicLens adds AI transcription, structured extraction, Q&A |
| Otter.ai / Rev | Generic transcription | CivicLens extracts structured civic data (votes, financials), not just text |
| Legistar | Legislative tracking | CivicLens covers the *meeting itself*, not just the docket |
| Manual staff work | Human-created digests | 85% time reduction, higher accuracy on vote/financial data |

## Pricing Strategy

- **Starter ($299/mo)**: Small cities, single boards. Volume-limited. Entry point for budget-constrained municipalities.
- **Professional ($799/mo)**: Mid-size cities, multi-board. Unlimited Q&A, API, alerts, exports. This is the target tier for most government customers.
- **Enterprise (Custom)**: Large cities/counties. Self-hosted option, SSO, custom integrations, SLA. Handles procurement complexity.

All tiers include 30-day free trial (no credit card). Government customers can trial without procurement approval.

## Key Differentiators

1. **Two-pass extraction** — facts are extracted first, then narrated. This prevents hallucination and makes data auditable.
2. **Timestamped citations** — every claim links back to the exact moment in the source video. Trust is verifiable.
3. **Purpose-built for civic data** — not a generic transcription service. Understands motions, roll calls, ordinance numbers, appropriation language.
4. **Granicus-native integration** — no manual upload. New meetings are detected and processed automatically.
5. **Archive depth** — processes historical records, not just new meetings. Full institutional memory from day one.

## Messaging Framework

### Headline
*Every vote. Every dollar. Every decision.*

### Subhead
CivicLens transforms hours of city council video into structured, searchable intelligence — automatically.

### Proof Points
- 98% transcription accuracy
- 12,000+ meetings processed
- 85% reduction in research time
- 2.3-second average query response

### Trust Signals
- SOC 2 Type II audit in progress (Type I planned); controls mapped to Trust Services Criteria
- ADA/WCAG 2.1 AA compliant
- Data encrypted at rest and in transit
- No training on customer data
- Available on cooperative purchasing contracts

## Landing Page Structure

The marketing page (`index.html`) is a standalone HTML file using Tailwind CSS CDN. It includes:

1. **Fixed navigation** with blur backdrop
2. **Hero** with animated terminal mock showing a live Q&A query
3. **Social proof bar** (municipality logos placeholder)
4. **Stats** section (accuracy, volume, time savings, speed)
5. **Features** grid (6 cards: transcription, summaries, Q&A, votes, financial, documents)
6. **How it works** (4-step flow: connect, transcribe, extract, query)
7. **Use cases** grid (governments, advocacy, journalism, real estate, legal, research)
8. **Testimonials** (3 placeholder quotes with realistic personas)
9. **Pricing** (3 tiers with animated highlight on Pro)
10. **FAQ** accordion (6 questions covering integration, accuracy, security, ADA, procurement)
11. **Final CTA** with trial + demo buttons
12. **Footer** with product, company, and legal links

### Design Decisions
- **Dark navy + warm amber** palette: conveys authority and trust without feeling cold
- **DM Serif Display + Instrument Sans** font pairing: editorial gravitas with modern clarity
- **Scroll-reveal animations**: progressive disclosure keeps the page feeling alive
- **Terminal mock in hero**: demonstrates the Q&A capability immediately, shows the product not just describing it
- **Animated gradient border on Pro tier**: draws the eye to the target pricing tier
- **Government-specific FAQ**: addresses procurement, ADA compliance, and security — the actual concerns of a city procurement officer

### To customize
- Replace placeholder municipality names in the social proof bar
- Update testimonial quotes with real customer quotes when available
- Add real demo video link to the "Watch Demo" CTA
- Point "Start Free Trial" buttons to the signup flow
- Add analytics tracking (GA4, Segment, etc.)
