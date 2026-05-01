# LFUCG Meeting Pipeline — Ideas for Leveraging the Complete Dataset

## Dataset at a Glance

- ~4,716 clips identified (Granicus), spanning **2007–2026**
- 2,737 with metadata
- Transcript coverage (May 2026 backfill):
  - **411** clips with Whisper transcripts + VTT-aligned speaker labels
  - **909** clips with placeholder transcripts synthesized from Granicus VTT (until Whisper runs)
  - **1,417** clips with no transcript (no audio + no captions, or both)
- 1,502 unique extracted topics, 2,406 agendas, 1,467 sets of minutes
- Meeting bodies: Council, Planning Commission, Budget & Finance, Board of Adjustment, and more
- Timestamped transcript segments enable deep-linking into Granicus video player; speaker-attributed segments enable per-person queries on covered clips

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

**Partially shipped (May 2026).** The pipeline now pulls Granicus's live-CC WebVTT track, which carries `>> Speaker:` attributions from the stenographer. ~411 clips have Whisper transcripts with VTT-aligned speaker labels (`whisper-1+vtt-speakers`); ~909 un-Whispered clips have placeholder transcripts synthesized directly from VTT (`granicus_vtt`). See `granicus_captions.py` and the VTT section in `granicus.md`.

What's still open:
- ~1,400 clips have audio but no VTT track (Granicus didn't capture stenography). True acoustic diarization (pyannote.audio, AWS Transcribe, AssemblyAI) would attribute these.
- VTT speaker quality varies — many turns are marked `>>` without a name. Acoustic diarization on top of VTT would add granularity.
- Even with speaker labels, named-entity resolution across clips ("Councilmember Hale" → a canonical person ID) is needed for queries like _"What has Council Member X said over the last 5 years?"_
- **Trade-off:** Acoustic diarization on the audio backlog is significant compute. Could be done incrementally (new meetings first, backfill later).

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

- ~1,400 clips still need a Whisper pass (down from ~4,300 — the rest are now covered by either Whisper or Granicus VTT placeholder transcripts; see section 5)
- ~170 failed transcriptions and ~86 failed downloads to retry
- 2,283 clips have "unknown" meeting body classification — could be backfilled from titles
- 8 clips with corrupted Granicus VTT (DEL-byte streams) have no captions usable as a placeholder; they show as "no transcript" until Whisper runs on their audio
