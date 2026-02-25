# LFUCG Meeting Pipeline — Ideas for Leveraging the Complete Dataset

## Dataset at a Glance

- ~4,670 clips identified (Granicus), spanning **2007–2026**
- 2,620 with metadata; 337 fully transcribed + summarized so far
- ~3.8M words of transcript text currently; estimated **50M+ words** when complete
- 1,502 unique extracted topics, 2,406 agendas, 1,467 sets of minutes
- Meeting bodies: Council, Planning Commission, Budget & Finance, Board of Adjustment, and more
- Timestamped transcript segments enable deep-linking into Granicus video player

---

## 1. RAG-Powered Q&A Over the Full Corpus

Build a retrieval-augmented generation system so anyone can ask natural-language questions like _"What did the city decide about the Town Branch Trail?"_ and get an answer synthesized from every relevant meeting, with citations linking to specific video timestamps.

- **Why it works well here:** The structured summaries (votes, public comments, controversies) make chunking far more effective than raw transcript windows. Agenda and minutes text add a cross-referencing layer — the AI could answer _"Did the council follow through on what they discussed in the January work session?"_
- **Stack ideas:** Embeddings (OpenAI or local via sentence-transformers), vector store (Chroma, Qdrant, or Pinecone), LLM for synthesis. Could start simple with LangChain or LlamaIndex.
- **Key advantage:** Timestamp-linked citations. Answers can say "see 43:12 in the Feb 19 Planning Commission meeting" and link directly to the Granicus player at that second.

## 2. Civic Transparency & Journalism Tools

The most immediate public-interest application.

- **Vote tracker:** Summaries already extract key decisions. Surface how bodies have voted over time on recurring issues (zoning, budget, public safety).
- **Deep-link clips:** A reporter covering affordable housing could get every mention across 18 years with clickable timestamps.
- **Embeddable widget:** Local news outlets could embed a "what has the council said about [X]" search scoped to their article's topic.
- **Accountability dashboard:** Track promises, action items, and follow-ups across meetings. Did the committee actually come back with that report they said they'd prepare?

## 3. Trend & Sentiment Analysis Dashboard

18 years of meetings is enough for meaningful longitudinal analysis.

- Topic frequency over time (when does "affordable housing" spike? how has "annexation" discussion evolved?)
- Public comment volume and tone trends
- Seasonal patterns (budget discussions in spring, snow removal in winter)
- Meeting duration and agenda density trends
- **Prerequisite:** Normalize the 1,502 topics into ~50–100 canonical categories (e.g., collapse "Public Comments," "Public Comment," and "Community Engagement" into one)

## 4. Automated Meeting Prep / Briefing Generator

For council members, staff, lobbyists, or engaged citizens.

- Before any upcoming meeting, auto-generate a briefing that pulls every prior discussion of that meeting's agenda items.
- _"You're about to discuss the zoning amendment for Nicholasville Road — here's what was said about it in the Planning Commission on these three dates, and here's what concerns the Board of Adjustment raised."_
- Could be delivered as an email digest or a one-pager PDF linked to the relevant video timestamps.

## 5. Speaker Identification & Attribution

Currently transcripts are undiarized — a wall of text with no speaker labels.

- Add speaker diarization (pyannote.audio, AWS Transcribe, or AssemblyAI) to attribute statements to individuals.
- Enables queries like _"What has Council Member X said about the police budget over the last 5 years?"_
- Dramatically increases the dataset's usefulness for journalism and accountability.
- **Trade-off:** Significant compute cost and complexity. Could be done incrementally (new meetings first, backfill later).

## 6. Notification & Alert System

People subscribe to topics, keywords, neighborhoods, or meeting bodies.

- _"Email me whenever the Planning Commission discusses my neighborhood."_
- _"Alert me when the Budget Committee talks about parks funding."_
- Lambda already runs twice daily on weekdays — adding a notification layer on top of the existing pipeline would be relatively straightforward.
- Could integrate with email, SMS, or a simple RSS feed.

## 7. Open Data / Public API

Expose the structured data (metadata, topics, summaries, timestamped segments) as a public REST or GraphQL API.

- Other cities using Granicus could fork the pipeline for their own meetings (code is already open source).
- A standardized API for local government meeting data would be genuinely novel — think a municipal-level version of what Congress.gov does for federal proceedings.
- Could contribute to broader civic tech ecosystems (Open States, Civic Graph, etc.).

## 8. Training Data for Local Government Models

A curated dataset of government meeting transcripts paired with structured summaries is rare and valuable.

- Fine-tune a model specifically good at summarizing municipal proceedings, understanding government jargon, or extracting action items from public meetings.
- Could be packaged as a HuggingFace dataset for the civic tech and NLP communities.
- Other cities and civic tech orgs would find this useful as a foundation model for their own meeting processing.

---

## Quick Wins (Highest Value, Lowest Effort)

Once transcription is complete, these three things would add the most value soonest:

1. **Normalize the topic taxonomy** — Collapse 1,502 topics into ~50–100 canonical categories for meaningful filtering and trend analysis.
2. **Build the RAG Q&A layer** — Even a basic implementation over the existing summaries + transcripts would be transformative for usability.
3. **Add email/RSS alerts** — Let people subscribe to topics or keywords; piggyback on the existing Lambda schedule.

## Remaining Work to Complete the Dataset

- ~4,300 clips still need transcription (primarily an OpenAI Whisper API token budget issue)
- ~170 failed transcriptions and ~86 failed downloads to retry
- 2,283 clips have "unknown" meeting body classification — could be backfilled from titles
