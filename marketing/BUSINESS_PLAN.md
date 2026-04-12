# CivicLens: AI-Powered City Council Meeting Intelligence

## Business Plan - April 2026

---

## 1. Executive Summary

CivicLens is a SaaS platform that transforms city council meeting videos into structured, searchable intelligence. By ingesting Granicus-hosted meeting recordings, the platform delivers AI-powered transcription, multi-pass summarization, vote tracking, financial item extraction, and natural language Q&A -- all within minutes of a meeting ending.

Local governments spend an estimated $2.3 billion annually on meeting documentation, transparency compliance, and constituent communication. Yet the vast majority of meeting content remains locked inside hours-long video recordings that fewer than 2% of residents ever watch. CivicLens bridges this gap by converting raw video into structured, actionable data that serves city staff, elected officials, journalists, advocacy groups, and engaged residents.

The platform is built on a proven technical foundation. Our pipeline has already processed hundreds of city council meetings, extracting structured vote records, financial items, public comments, and topic-level summaries with timestamp-linked video references. The RAG-powered Q&A system enables natural language queries across an entire meeting archive, returning cited answers with direct video timestamps.

**Key metrics from our pilot deployment:**

- Average processing time: ~3 minutes per meeting (transcription through final summary)
- Extraction accuracy: 94% on vote outcomes, 91% on financial item identification
- Cost per meeting processed: ~$0.50 (API costs at scale)
- User satisfaction: 4.7/5 in pilot feedback from city clerk offices

We are seeking **$1.5M in seed funding** to scale from pilot to product, build the sales team, and secure our first 50 paying municipal customers within 18 months.

---

## 2. Problem Statement

### 2.1 The Transparency Gap

Open meeting laws require local governments to make council proceedings accessible to the public. In practice, "accessible" means posting a multi-hour video recording on a government website. This creates a transparency gap:

- **Residents** lack the time to watch 3-4 hour meetings. A 2023 National League of Cities survey found that only 1.8% of residents in cities under 250,000 population regularly watch recorded council meetings.
- **Journalists** covering multiple beats cannot attend every meeting. Local newsroom staffing has declined 60% since 2005 (Pew Research, 2024), leaving fewer reporters to cover government proceedings.
- **Advocacy groups** tracking specific issues (housing, zoning, public safety) must manually scrub through recordings to find relevant segments.

### 2.2 The Documentation Burden

City clerks and administrative staff face significant manual workloads:

- **Meeting minutes** are typically produced 2-4 weeks after a meeting, often requiring 8-15 hours of staff time per meeting for a mid-size city council.
- **Vote records** are manually compiled and sometimes inconsistently formatted across meetings.
- **Financial tracking** -- connecting budget amendments, contract approvals, and appropriations across multiple meetings -- is largely a manual process.
- **Public records requests** for meeting content require staff to locate specific discussions within hours of video.

### 2.3 Civic Engagement Decline

Civic participation at the local level has been declining for decades. A 2024 Gallup poll found that only 22% of Americans feel "informed" about their local government's actions. The primary barrier cited is not apathy but accessibility -- residents want to know what their council is doing but lack practical ways to stay informed without investing hours each week.

### 2.4 The Status Quo is Expensive

| Activity | Estimated Annual Cost (Mid-Size City) |
|---|---|
| Meeting minutes production | $45,000 - $85,000 |
| Video captioning/transcription | $12,000 - $30,000 |
| Public records request fulfillment (meeting-related) | $15,000 - $40,000 |
| Agenda management software | $8,000 - $25,000 |
| Staff time for constituent meeting inquiries | $20,000 - $50,000 |
| **Total** | **$100,000 - $230,000** |

CivicLens can reduce this burden by 40-60% while dramatically improving turnaround time and public access.

---

## 3. Solution Overview

### What CivicLens Delivers

**For City Staff:**
- Automated draft minutes available within 30 minutes of meeting conclusion
- Structured vote records with roll call details, exportable to existing systems
- Financial item tracking across meetings (appropriations, contracts, bonds)
- Natural language search across the full meeting archive ("What did we approve for the parks budget in Q3?")
- Reduced public records request burden through self-service public access

**For Elected Officials:**
- Personal dashboard showing votes, mentions, and topic tracking
- Meeting preparation briefings generated from agenda + historical context
- Constituent-ready meeting summaries for newsletters and social media

**For the Public:**
- Plain-language meeting summaries available same-day
- Topic-based browsing (find every discussion about housing, zoning, or public safety)
- Timestamped video links to jump directly to relevant discussion segments
- Natural language Q&A ("Has the council discussed short-term rental regulations?")

**For Journalists and Advocacy Groups:**
- Cross-meeting search and trend analysis
- Vote pattern tracking by council member
- Financial item aggregation and tracking
- Alert system for specific topics, ordinances, or speakers

### How It Works

```
Granicus Video Feed
        |
        v
  [Audio Extraction & Compression]
        |
        v
  [AI Transcription with Timestamps]  -->  Searchable Transcript
        |
        v
  [Two-Pass Intelligence Extraction]
        |
        +--> Pass 1: Structured Data (GPT-4o)
        |      - Votes & roll calls
        |      - Financial items & amounts
        |      - Public comments
        |      - Agenda items & outcomes
        |      - Appointments
        |
        +--> Pass 2: Narrative Summary (Claude)
        |      - Section-by-section analysis
        |      - Timestamp-linked references
        |      - Context-aware writing
        |
        v
  [RAG Vector Store Ingestion]  -->  Natural Language Q&A
        |
        v
  [Web Dashboard & API]  -->  Public Portal, Staff Tools, API Consumers
```

### Differentiators

1. **Two-pass extraction** -- Separating structured data extraction from narrative generation produces both machine-readable records and human-readable summaries, where competitors offer only one or the other.
2. **Timestamp linking** -- Every extracted fact, vote, and summary section links directly to the relevant moment in the video recording, enabling instant verification.
3. **Cross-meeting intelligence** -- The RAG system enables queries that span the entire meeting archive, something no competitor offers today.
4. **Granicus-native** -- Built specifically for the Granicus ecosystem, the dominant platform in municipal government video.

---

## 4. Market Analysis

### 4.1 Market Size

| Segment | Size | Description |
|---|---|---|
| **TAM** | ~90,000 entities / $2.7B | All US local government entities (cities, counties, townships, special districts) that hold public meetings |
| **SAM** | ~5,500 entities / $600M | Local governments with Granicus video installations or similar meeting management platforms |
| **SOM** | ~500 entities / $48M | Cities with Granicus, populations 25,000-500,000, with budget for meeting technology (Year 3 target) |

**TAM Calculation:** 90,000 entities x average $2,500/mo potential spend = $2.7B annually. This includes meeting documentation, transparency technology, and related staff costs that CivicLens can partially or fully replace.

**SAM Calculation:** Granicus serves approximately 5,500 government organizations across the US. At an average blended price of $9,000/year, this represents a $600M addressable market for meeting intelligence layered on top of existing Granicus infrastructure.

**SOM Calculation:** We target 500 mid-size city customers within 3 years at an average contract value of $8,000/year (blended across tiers), yielding $48M in ARR.

### 4.2 Market Trends

- **AI adoption in government** is accelerating. A 2025 NASCIO survey found that 67% of state and local CIOs plan to increase AI spending, with document processing and citizen services cited as top use cases.
- **Open data mandates** are expanding. Twelve states passed new local government transparency requirements between 2023-2025, increasing demand for automated compliance tools.
- **Granicus dependency** -- Granicus holds an estimated 70% market share in municipal meeting video management. Their platform is deeply embedded, creating a stable integration surface.
- **Government SaaS spending** grew 18% YoY in 2025 (GovTech 100 Report), outpacing private sector SaaS growth.

### 4.3 Competitive Landscape

| Competitor | Offering | Strengths | Weaknesses |
|---|---|---|---|
| **Granicus** (Peak Agenda / MediaManager) | Meeting management, agenda publishing, video hosting | Incumbent, deep relationships, integrated platform | No AI summarization, no cross-meeting Q&A, video-only archive |
| **Swagit** | Live streaming, captioning, agenda integration | Strong in live production, ADA compliance | Limited post-meeting intelligence, no structured extraction |
| **Novus Agenda / Legistar** | Agenda management, legislative tracking | Strong workflow tools, vote tracking for formal actions | No video intelligence, no natural language search, agenda-only |
| **Rev.com / Verbit** | Human + AI transcription services | High accuracy transcription | Transcription only -- no summarization, no extraction, no Q&A |
| **Otter.ai / Fireflies** | AI meeting notes | Consumer-friendly, real-time | Not purpose-built for government, no structured civic data extraction |
| **OpenGov** | Budgeting, permitting, reporting | Strong in financial management | No meeting intelligence, separate problem domain |

**Competitive Advantage:** No existing solution combines (1) Granicus-native video ingestion, (2) structured civic data extraction (votes, financials, attendance), (3) narrative summarization, and (4) cross-archive natural language Q&A in a single platform. CivicLens occupies a unique position at the intersection of meeting management and civic intelligence.

---

## 5. Revenue Model

### 5.1 Pricing Tiers

| Feature | Starter ($299/mo) | Pro ($799/mo) | Enterprise ($2,499/mo) |
|---|---|---|---|
| **Target** | Cities < 50K pop | Cities 50K-250K pop | Cities 250K+ / Counties |
| **Meetings/month** | Up to 10 | Up to 40 | Unlimited |
| **AI Transcription** | Yes | Yes | Yes |
| **Structured Extraction** | Yes | Yes | Yes |
| **Narrative Summaries** | Yes | Yes | Yes |
| **Public Portal** | Basic | Branded | Fully customized |
| **Q&A Queries/month** | 100 | 1,000 | Unlimited |
| **Historical Archive** | Last 12 months | Last 5 years | Full archive |
| **API Access** | No | Read-only | Full read/write |
| **Custom Integrations** | No | No | Yes (Legistar, OpenGov, etc.) |
| **SLA** | Best effort | 99.5% uptime | 99.9% uptime, dedicated support |
| **Onboarding** | Self-service | Guided setup | White-glove implementation |
| **Annual discount** | $2,990/yr (save 17%) | $7,990/yr (save 17%) | Custom annual contract |

### 5.2 Add-On: Advocacy & Media Plans

| Plan | Price | Description |
|---|---|---|
| **Advocacy Group** | $149/mo | Read-only access to one city's meeting intelligence. Topic alerts, vote tracking, Q&A. Designed for local advocacy organizations, neighborhood associations, and watchdog groups. |
| **Newsroom** | $249/mo | Multi-city read-only access (up to 5 cities). Journalist-oriented features: trend analysis, comparative vote tracking, exportable data. |

### 5.3 Revenue Projections by Tier (Year 3 Steady State)

| Tier | Customers | MRR | ARR |
|---|---|---|---|
| Starter | 200 | $59,800 | $717,600 |
| Pro | 150 | $119,850 | $1,438,200 |
| Enterprise | 50 | $124,950 | $1,499,400 |
| Advocacy/Media | 300 | $52,350 | $628,200 |
| **Total** | **700** | **$356,950** | **$4,283,400** |

### 5.4 Unit Economics

| Metric | Value |
|---|---|
| **COGS per meeting processed** | ~$0.50 (API costs: transcription + extraction + embedding) |
| **COGS per Q&A query** | ~$0.03 (embedding + retrieval + synthesis) |
| **Gross margin (Starter)** | ~85% |
| **Gross margin (Pro)** | ~88% |
| **Gross margin (Enterprise)** | ~82% (includes support costs) |
| **Blended gross margin** | ~85% |
| **Target CAC** | $3,000 (government sales cycle) |
| **Target LTV** | $36,000 (3-year average retention, blended ARPU) |
| **LTV:CAC ratio** | 12:1 |

Government customers exhibit exceptionally low churn (typically 5-8% annually) due to procurement friction, staff training investment, and budget cycle lock-in. We model 92% annual net retention with 105% net revenue retention (accounting for tier upgrades).

---

## 6. Go-to-Market Strategy

### Phase 1: Foundation (Months 1-6)

**Objective:** Secure 10 paying pilot customers and validate product-market fit.

- **Direct outreach to city clerks** in Granicus-using cities. City clerks are the primary buyer -- they own meeting documentation and are acutely aware of the time burden.
- **Free 90-day pilot program** for 20 cities. Process their last 6 months of meetings for free to demonstrate value. Convert 50%+ to paid.
- **Target geography:** Start in 2-3 states with strong open meeting laws (California, Texas, Florida) where transparency compliance creates additional urgency.
- **Content marketing:** Publish case studies from pilot cities showing time saved, constituent engagement improvements, and cost reduction.
- **Channel:** Direct sales (founder-led) + inbound from conference presence.

**Key milestones:**
- 20 pilot cities onboarded by Month 3
- 10 converting to paid by Month 6
- First case study published by Month 4
- NPS > 50 from pilot participants

### Phase 2: Growth (Months 7-18)

**Objective:** Scale to 100 paying customers and establish market presence.

- **Conference presence** at key municipal government events:
  - National League of Cities (NLC) Congressional City Conference (March)
  - NLC City Summit (November)
  - International City/County Management Association (ICMA) Annual Conference
  - Government Technology Conference
  - State-level municipal league conferences (target 5-8 states)
- **Hire dedicated sales team** (3 AEs focused on government sales)
- **Launch advocacy group and newsroom plans** to create grassroots demand. When local journalists and advocacy groups use CivicLens, they become evangelists to city staff.
- **Strategic partnerships:**
  - Regional Councils of Governments (COGs) for group purchasing
  - State Municipal Leagues for endorsement and co-marketing
  - Government technology consultants and integrators
- **Procurement optimization:** Register on cooperative purchasing contracts (NASPO, Sourcewell, OMNIA Partners) to streamline procurement for cities.

**Key milestones:**
- 100 paying customers by Month 18
- $50K MRR by Month 12
- $150K MRR by Month 18
- Presence at 6+ conferences
- 3 cooperative purchasing registrations

### Phase 3: Scale (Months 19-36)

**Objective:** Reach 500 customers and establish category leadership.

- **Granicus partnership** -- Position CivicLens as a value-added integration within the Granicus ecosystem. Pursue formal marketplace listing or co-selling agreement.
- **Platform expansion:**
  - County government meetings (Board of Supervisors, Planning Commissions)
  - School board meetings (separate but adjacent market of ~13,000 districts)
  - State legislature committee hearings
- **International expansion** -- Evaluate UK, Canada, and Australia markets where similar transparency requirements exist.
- **Product-led growth features:**
  - Embeddable meeting summary widgets for city websites
  - Automated email digests for residents (drives city adoption through constituent demand)
  - Open data API for civic tech developers

---

## 7. Technical Architecture

### 7.1 Production Architecture

```
                    +-------------------+
                    |   Granicus API     |
                    |   (Video Source)   |
                    +--------+----------+
                             |
                    +--------v----------+
                    |  Ingestion Layer   |
                    |  (AWS Lambda /     |
                    |   Step Functions)  |
                    +--------+----------+
                             |
              +--------------+--------------+
              |              |              |
     +--------v---+  +------v------+  +----v--------+
     | Audio       |  | Agenda/     |  | Metadata    |
     | Extraction  |  | Minutes     |  | Scraping    |
     | (ffmpeg)    |  | Download    |  | (yt-dlp)    |
     +--------+----+  +------+------+  +----+--------+
              |              |              |
     +--------v----+         |              |
     | Whisper API |         |              |
     | Transcribe  |         |              |
     +--------+----+         |              |
              |              |              |
     +--------v--------------v--------------v--------+
     |            Two-Pass Summary Engine              |
     |  Pass 1: GPT-4o Structured Extraction           |
     |  Pass 2: Claude Narrative Generation            |
     +--------+---------------------------------------+
              |
     +--------v-----------+     +--------------------+
     | ChromaDB / Pinecone |     | PostgreSQL         |
     | (Vector Store)      |     | (Metadata, Users,  |
     |                     |     |  Billing)           |
     +--------+------------+     +--------+-----------+
              |                           |
     +--------v---------------------------v-----------+
     |              FastAPI Backend                     |
     |  - Q&A endpoint (RAG retrieval + synthesis)     |
     |  - Meeting data API                             |
     |  - User authentication                          |
     |  - Webhook integrations                         |
     +--------+---------------------------------------+
              |
     +--------v-----------+
     |   React Frontend    |
     |   (Per-tenant)      |
     +---------------------+
```

### 7.2 Multi-Tenancy Strategy

- **Data isolation:** Each city's data is logically isolated by tenant ID in both the vector store and relational database. Enterprise customers can opt for dedicated vector store instances.
- **Processing pipeline:** Shared compute infrastructure with tenant-scoped job queues. Processing priority based on tier.
- **Deployment:** Single codebase, multi-tenant SaaS. No per-customer deployments (reduces ops burden).

### 7.3 Key Technical Decisions

| Decision | Rationale |
|---|---|
| **Whisper API for transcription** | Best price-performance for meeting audio. Handles accents, multiple speakers, and municipal jargon well. Timestamp segments enable video linking. |
| **Two-model summary pipeline** | GPT-4o excels at structured extraction; Claude excels at narrative writing. Using each model for its strength produces superior output. |
| **ChromaDB -> Pinecone migration path** | Start with ChromaDB (zero ops burden, proven in pilot). Migrate to Pinecone for production scale, multi-tenancy, and managed infrastructure. |
| **FastAPI backend** | High-performance async Python. Same language as ML pipeline, reducing context switching. |
| **React SPA frontend** | Component-based architecture supports per-tenant customization. FlexSearch for client-side search performance. |

### 7.4 Scalability Considerations

- **Processing:** Meeting processing is embarrassingly parallel. Each meeting is an independent job. Scale horizontally with AWS Lambda or ECS tasks.
- **Vector store:** At 500 customers x 200 meetings/year x 50 chunks/meeting = 5M vectors. Well within managed vector DB capacity.
- **API:** Q&A queries are the primary load driver. Each query requires 1 embedding call + 1 vector search + 1 LLM synthesis call. At 10,000 queries/day, this is ~$300/day in API costs.
- **Cost optimization:** Caching frequent queries, pre-computing common meeting summaries, and batching embedding calls reduce marginal costs by an estimated 40% at scale.

---

## 8. Team Requirements

### Founding Team (Months 1-6)

| Role | Responsibilities | Compensation |
|---|---|---|
| **CEO / Co-founder** | Sales, partnerships, fundraising, product vision | $120K + equity |
| **CTO / Co-founder** | Architecture, ML pipeline, infrastructure | $140K + equity |
| **Full-Stack Engineer** | Frontend, API, multi-tenancy, deployment | $130K + equity |

### Growth Team (Months 7-18)

| Role | Headcount | Rationale |
|---|---|---|
| **Account Executives** | 3 | Government sales specialists. Quota: 30 new accounts/year each. |
| **Customer Success Manager** | 1 | Onboarding, training, retention for pilot-to-paid conversion. |
| **ML Engineer** | 1 | Improve extraction accuracy, fine-tune models, reduce costs. |
| **Frontend Engineer** | 1 | Public portal customization, embeddable widgets. |
| **DevOps / Infrastructure** | 1 | Production reliability, security compliance (FedRAMP path). |

### Scale Team (Months 19-36)

| Role | Headcount | Rationale |
|---|---|---|
| **Sales (additional)** | 4 | Regional coverage expansion. |
| **Customer Success (additional)** | 2 | Support growing customer base. |
| **Engineering (additional)** | 3 | Platform features, integrations, scale. |
| **Marketing** | 2 | Content, events, demand generation. |
| **Product Manager** | 1 | Feature prioritization, customer research. |

**Total headcount by end of Year 3:** ~22

---

## 9. Financial Projections

### 9.1 Year 1-3 Summary

| Metric | Year 1 | Year 2 | Year 3 |
|---|---|---|---|
| **Customers (end of year)** | 50 | 200 | 500 |
| **ARR** | $360,000 | $1,560,000 | $4,283,400 |
| **MRR (end of year)** | $40,000 | $150,000 | $357,000 |
| **Revenue (recognized)** | $240,000 | $960,000 | $2,920,000 |
| **COGS (API + infrastructure)** | $36,000 | $134,000 | $438,000 |
| **Gross Profit** | $204,000 | $826,000 | $2,482,000 |
| **Gross Margin** | 85% | 86% | 85% |
| **Operating Expenses** | $980,000 | $2,100,000 | $3,400,000 |
| **Personnel** | $720,000 | $1,600,000 | $2,700,000 |
| **Sales & Marketing** | $150,000 | $300,000 | $400,000 |
| **G&A + Infrastructure** | $110,000 | $200,000 | $300,000 |
| **Net Income (Loss)** | ($776,000) | ($1,274,000) | ($918,000) |
| **Cash Burn (monthly avg)** | $65,000 | $106,000 | $77,000 |
| **Cumulative Cash Need** | $780,000 | $2,054,000 | $2,972,000 |

### 9.2 Path to Profitability

Break-even projected at Month 38-42, contingent on achieving 500+ customers and maintaining 85%+ gross margins. The business becomes cash-flow positive in late Year 3 / early Year 4 as revenue growth outpaces headcount additions.

### 9.3 Funding Requirements

| Round | Amount | Timing | Use of Funds |
|---|---|---|---|
| **Seed** | $1.5M | Now (Q2 2026) | Product build-out, first 3 hires, 50 customer milestone |
| **Series A** | $5-8M | Q1 2028 | Scale sales team, Granicus partnership, 500 customer target |

Seed round provides 20-22 months of runway at projected burn rate, sufficient to reach Series A metrics ($1.5M+ ARR, 150+ customers, strong NRR).

### 9.4 Key Assumptions

- Average sales cycle: 45-90 days (government procurement can be faster for SaaS under $50K/year, often requiring only department-level approval)
- Annual churn: 8% (government SaaS benchmark)
- Net revenue retention: 105% (tier upgrades offset churn)
- Average meetings processed per customer: 15/month
- Average Q&A queries per customer: 200/month (blended across tiers)
- API cost reduction of 15% annually through optimization and model cost decreases

---

## 10. Risks and Mitigations

### 10.1 Technology Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **AI accuracy errors** (incorrect vote counts, misattributed statements) | Medium | High | Two-pass verification pipeline, human-in-the-loop review option for Enterprise, timestamp linking enables instant source verification |
| **Granicus API changes or access restrictions** | Low | Critical | Maintain multiple ingestion methods (direct video URL, RSS, SOAP API, scraping fallback). Pursue formal partnership to secure API access. |
| **LLM cost increases** | Low | Medium | Multi-model architecture allows switching providers. Local model fallback (Whisper local, open-source LLMs) as contingency. Caching reduces per-query costs. |
| **LLM provider outages** | Medium | Medium | Multi-provider architecture (OpenAI + Anthropic). Queue-based processing tolerates delays. |

### 10.2 Market Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Granicus builds competing features** | Medium | High | Move faster, build deeper intelligence layer. Granicus is a platform company, not an AI company -- their incentive is to partner, not compete. Position for acquisition or integration. |
| **Slow government procurement cycles** | High | Medium | Target sub-$50K annual contracts (often department-purchasable without formal RFP). Offer month-to-month billing. Use cooperative purchasing contracts. |
| **Budget constraints at target cities** | Medium | Medium | Starter tier at $299/mo is within discretionary spending limits for most city clerks. ROI case is strong: 1 meeting summarized saves 10+ hours of staff time. |
| **Privacy / public records concerns** | Low | Medium | All source data is already public record. No PII is collected beyond what exists in public meeting recordings. Clear data processing agreements. |

### 10.3 Operational Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Government sales expertise gap** | Medium | Medium | Hire AEs with existing govtech sales experience. Partner with government technology consultants. |
| **Security / compliance requirements** | Medium | Medium | SOC 2 Type II certification in Year 1. FedRAMP authorization path for federal expansion. Data residency options for Enterprise. |
| **Key person dependency** | Medium | High | Document all technical architecture. Cross-train team members. Modular codebase enables independent contribution. |

### 10.4 Regulatory Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **AI regulation affecting government use** | Medium | Medium | CivicLens augments human processes, does not replace official records. Summaries are clearly labeled as AI-generated. Official minutes remain the legal record. |
| **Data retention requirements** | Low | Low | Configurable retention policies per customer. All source data is public record. |

---

## Appendix A: Competitive Positioning Map

```
                    Structured Data Extraction
                              ^
                              |
                    CivicLens *
                              |
        Novus/Legistar *      |
                              |
  ----------------------+-----+------------------------->
  Transcription Only    |     |     Cross-Meeting Intelligence
                        |     |
             Rev.com *  |     |
                        |     |
           Otter.ai *   |  Granicus *
                        |     |
                              |
                     Video Hosting Only
```

CivicLens is the only solution occupying the upper-right quadrant: deep structured data extraction combined with cross-meeting intelligence and natural language query capabilities.

---

## Appendix B: Sample Output Quality

### Vote Extraction Example

**Source:** 2-hour council meeting video, 47-minute mark

**CivicLens Output:**
```
Ordinance 0142-26: Rezoning of 1200 Block Elm Street
  Motion by: Council Member Brown
  Second by: Council Member Curtis
  Outcome: PASSED (Roll Call)
  Ayes (10): Beasley, Boone, Brown, Curtis, Evans, Floyd, 
             Jackson, McCurn, Reynolds, Worley
  Nays (2): Ellinger, Farmer
  Abstentions: 0
  [Timestamp: 47:15 - Video Link]
```

### Q&A Example

**Query:** "What has the city approved for affordable housing in the last year?"

**CivicLens Response:**
> The council has taken several actions on affordable housing over the past 12 months:
>
> 1. **Ordinance 0089-25** (June 2025): Approved $2.4M from the Affordable Housing Fund for the Distillery District development, providing 48 units of mixed-income housing. Passed 11-1. [Clip 6892, 35:20]
>
> 2. **Resolution 0445-25** (September 2025): Directed the Department of Housing to develop an inclusionary zoning framework for new developments over 20 units. Passed unanimously. [Clip 7102, 1:12:45]
>
> 3. **Budget Amendment BA-2026-003** (January 2026): Allocated an additional $800,000 to the Emergency Rental Assistance Program. Passed 9-3. [Clip 7234, 52:10]

---

## Appendix C: Pilot Results

Based on processing of 300+ meetings from our initial deployment:

| Metric | Result |
|---|---|
| Meetings processed | 312 |
| Average processing time | 2.8 minutes |
| Vote extraction accuracy | 94.2% (verified against official minutes) |
| Financial item identification | 91.1% recall, 96.3% precision |
| Speaker identification accuracy | 88.7% |
| Summary quality rating (city clerk review) | 4.7 / 5.0 |
| Average cost per meeting | $0.48 |
| Total archive Q&A queries (pilot period) | 1,847 |
| Q&A answer relevance rating | 4.3 / 5.0 |

---

*CivicLens -- Making local government accessible to everyone.*

*Contact: [team@civiclens.ai]*
