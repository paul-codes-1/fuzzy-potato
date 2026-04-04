import json
import os
import tempfile
from unittest.mock import MagicMock

import pytest


# --- Sample data matching real LFUCG output formats ---

SAMPLE_SUMMARY = """## Meeting Overview
- **Date and Time**: January 22, 2026, 6:00 PM
- **Type of Meeting**: Urban County Council Meeting
- **Presiding Officer**: Mayor Linda Gorton
- **Summary**: The meeting included zoning changes and budget amendments.

## Key Decisions & Votes
1. **Ordinance 0016-26**: Zoning change from Agricultural-Rural to Medium Density Residential. Passed 8-0.
2. **Ordinance 0052-26**: Budget amendments for municipal expenditures. Passed unanimously.

## Public Comments & Citizen Input
- **Speakers**: None signed up for agenda items.

## Budget & Financial Items
- **General Obligation Bonds**: $18,040,000 authorized for issuance.

## Implications for Residents
- **Zoning Changes**: Potential for increased residential development in District 12.
"""

SAMPLE_SEGMENTS = [
    {"start": 0.0, "end": 10.88, "text": "so"},
    {"start": 60.0, "end": 81.1, "text": "Welcome, everyone. I'd like to go ahead and call to order the Lexington Fayette-Urban"},
    {"start": 81.1, "end": 89.6, "text": "County Council's council meeting, and it's January 22, 2026. And the first item of business"},
    {"start": 89.66, "end": 97.6, "text": "is our roll call. Councilmember Beasley. Yes, ma'am. Councilmember Boone. Yes, ma'am."},
    {"start": 97.6, "end": 103.78, "text": "Councilmember Brown. Yes, ma'am. Councilmember Curtis. Yes, ma'am."},
    {"start": 103.78, "end": 109.18, "text": "Councilmember Elliot Baxter. Yes, ma'am. Councilmember Hale."},
    {"start": 109.18, "end": 115.82, "text": "Councilmember Lynch. Present. Councilmember Morton. Yes, ma'am. Councilmember Reynolds."},
    {"start": 115.82, "end": 120.26, "text": "Yes, ma'am. Councilmember Savigny. Yes, ma'am. Councilmember Sheehan. Yes, ma'am."},
    {"start": 120.26, "end": 125.3, "text": "Thank you. All right. We have a quorum."},
]

SAMPLE_AGENDA = """Lexington-Fayette Urban County Government
200 E. Main St
Lexington, KY 40507
Docket
Thursday, January 22, 2026
6:00 PM
Council Chambers
Urban County Council

I. Roll Call
II. Invocation
III. Minutes of the Previous Meetings
IV. Presentations
V. Public Comment - Issues on Agenda
VI. Ordinances - Second Reading
1. 0016-26 An Ordinance changing the zone from Agricultural-Rural to Medium Density Residential.
2. 0052-26 An Ordinance amending certain of the Budgets.
VII. Resolutions
"""

SAMPLE_MINUTES = """URBAN COUNTY COUNCIL
REGULAR SESSION
January 22, 2026

The Council of the Lexington-Fayette Urban County Government met in regular session on January 22, 2026.

Roll Call
The following members were present: Beasley, Boone, Brown, Curtis, Ellinger.

Minutes
The minutes of the January 15, 2026 meeting were approved.

Ordinances
Ordinance 0016-26 was passed 8-0.
Ordinance 0052-26 was passed unanimously.
"""

SAMPLE_SEGMENTS_WITH_GAPS = [
    {"start": 0.0, "end": 10.0, "text": "Welcome everyone to the meeting today."},
    {"start": 10.0, "end": 20.0, "text": "We will begin with roll call. Councilmember Beasley."},
    {"start": 20.0, "end": 30.0, "text": "Yes ma'am. Councilmember Boone. Yes ma'am."},
    {"start": 30.0, "end": 40.0, "text": "Thank you. We have a quorum present tonight."},
    # 15-second silence gap here (40.0 -> 55.0)
    {"start": 55.0, "end": 65.0, "text": "Moving on to the next item on the agenda."},
    {"start": 65.0, "end": 75.0, "text": "We have ordinance 0016-26 regarding zoning changes."},
    {"start": 75.0, "end": 85.0, "text": "This ordinance would change the zone from agricultural to residential."},
    {"start": 85.0, "end": 95.0, "text": "The planning commission has recommended approval of this change."},
    {"start": 95.0, "end": 105.0, "text": "Are there any questions from council members on this item?"},
]

SAMPLE_NOISY_SEGMENTS = [
    {"start": 0.0, "end": 1.0, "text": "♪"},
    {"start": 1.0, "end": 2.0, "text": "🎵"},
    {"start": 2.0, "end": 3.0, "text": "."},
    {"start": 3.0, "end": 4.0, "text": "..."},
    {"start": 4.0, "end": 5.0, "text": "Music"},
    {"start": 5.0, "end": 10.0, "text": "Welcome to the council meeting today."},
    {"start": 10.0, "end": 11.0, "text": "so"},
    {"start": 11.0, "end": 12.0, "text": "um"},
    # Non-ASCII gibberish (Cyrillic/Georgian artifacts from Whisper)
    {"start": 12.0, "end": 13.0, "text": "Ыфвафыв дфыва фдыва фыдва"},
    {"start": 13.0, "end": 14.0, "text": "კარგი საღამოა"},
    {"start": 14.0, "end": 20.0, "text": "The first item of business is the roll call."},
    # Stuck repetition loop (7 identical segments)
    {"start": 20.0, "end": 21.0, "text": "Of the"},
    {"start": 21.0, "end": 22.0, "text": "Of the"},
    {"start": 22.0, "end": 23.0, "text": "Of the"},
    {"start": 23.0, "end": 24.0, "text": "Of the"},
    {"start": 24.0, "end": 25.0, "text": "Of the"},
    {"start": 25.0, "end": 26.0, "text": "Of the"},
    {"start": 26.0, "end": 27.0, "text": "Of the"},
    {"start": 27.0, "end": 35.0, "text": "Councilmember Beasley voted yes on the motion."},
]

SAMPLE_EXTRACTED_FACTS = {
    "meeting_info": {
        "date": "2026-01-22",
        "time": "6:00 PM",
        "body": "Urban County Council",
        "presiding_officer": "Mayor Linda Gorton",
        "location": "Council Chambers",
    },
    "attendance": {
        "present": ["Beasley", "Boone", "Brown", "Curtis", "Ellinger",
                     "Elliot Baxter", "Hale", "Lynch", "Morton", "Reynolds",
                     "Savigny", "Sheehan"],
        "absent": [],
        "late": [],
    },
    "motions_and_votes": [
        {
            "identifier": "Ordinance 0016-26",
            "description": "Zoning change from Agricultural-Rural to Medium Density Residential",
            "motion_by": "Brown",
            "second_by": "Curtis",
            "outcome": "passed",
            "vote_type": "roll_call",
            "ayes": 8,
            "nays": 0,
            "abstentions": 0,
            "votes_for": ["Beasley", "Boone", "Brown", "Curtis"],
            "votes_against": [],
            "conditions": None,
            "transcript_approx_time": "25:15",
        },
        {
            "identifier": "Ordinance 0052-26",
            "description": "Budget amendments for municipal expenditures",
            "motion_by": None,
            "second_by": None,
            "outcome": "passed",
            "vote_type": "unanimous",
            "ayes": 12,
            "nays": 0,
            "abstentions": 0,
            "votes_for": [],
            "votes_against": [],
            "conditions": None,
            "transcript_approx_time": None,
        },
    ],
    "financial_items": [
        {
            "description": "General Obligation Bonds authorized for issuance",
            "amount": "$18,040,000",
            "type": "appropriation",
            "identifier": None,
            "vendor_or_recipient": None,
        },
    ],
    "public_comments": [],
    "agenda_items": [
        {
            "identifier": "Ordinance 0016-26",
            "title": "Zoning Change - Agricultural to Residential",
            "type": "ordinance",
            "summary": "Changed zone from Agricultural-Rural to Medium Density Residential in District 12.",
            "key_speakers": ["Brown"],
            "outcome": "approved",
            "transcript_approx_time": "25:15",
        },
    ],
    "appointments": [],
    "contentious_items": [],
}

SAMPLE_METADATA = {
    "clip_id": 6669,
    "url": "https://lfucg.granicus.com/player/clip/6669?view_id=14&redirect=true",
    "date": "2026-01-22",
    "meeting_body": "Council",
    "title": "Urban County Council (1)",
    "topics": ["Zoning Ordinances", "Budget Amendments", "Public Safety"],
    "files": {
        "audio": "Urban_County_Council_1.mp3",
        "transcript": "transcript_Urban_County_Council_1.txt",
        "transcript_segments": "transcript_Urban_County_Council_1_segments.json",
        "agenda_pdf": "agenda_6669.pdf",
        "agenda_txt": "agenda_6669.txt",
        "summary_txt": "summary.txt",
        "extracted_facts": "extracted_facts.json",
        "minutes_txt": "2026-01-22_minutes_Urban_County_Council.txt",
    },
    "processed_at": "2026-01-31T22:04:02.617983",
    "processing_time_seconds": 169.95,
    "transcript_words": 6835,
    "audio_kept": True,
    "models": {
        "transcribe": "whisper-1",
        "summary": "gpt-4o",
        "topics": "gpt-4o-mini",
    },
}


@pytest.fixture
def sample_clip_dir(tmp_path):
    """Create a temporary directory mirroring lfucg_output/clips/{clip_id}/ structure."""
    clip_id = 6669
    clip_dir = tmp_path / "clips" / str(clip_id)
    clip_dir.mkdir(parents=True)

    # Write summary
    (clip_dir / "summary.txt").write_text(SAMPLE_SUMMARY)

    # Write extracted facts
    (clip_dir / "extracted_facts.json").write_text(json.dumps(SAMPLE_EXTRACTED_FACTS, indent=2))

    # Write transcript segments
    (clip_dir / "transcript_Urban_County_Council_1_segments.json").write_text(
        json.dumps(SAMPLE_SEGMENTS)
    )

    # Write agenda text
    (clip_dir / "agenda_6669.txt").write_text(SAMPLE_AGENDA)

    # Write minutes text
    (clip_dir / "2026-01-22_minutes_Urban_County_Council.txt").write_text(SAMPLE_MINUTES)

    # Write metadata
    (clip_dir / "metadata.json").write_text(json.dumps(SAMPLE_METADATA, indent=2))

    return tmp_path


@pytest.fixture
def sample_metadata():
    """Return a copy of the sample metadata dict."""
    return SAMPLE_METADATA.copy()


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client for embedding and chat completion calls."""
    client = MagicMock()

    # Mock embeddings.create
    mock_embedding = MagicMock()
    mock_embedding.embedding = [0.1] * 1536  # text-embedding-3-small dimensions
    mock_response = MagicMock()
    mock_response.data = [mock_embedding]
    client.embeddings.create.return_value = mock_response

    # Mock chat.completions.create
    mock_choice = MagicMock()
    mock_choice.message.content = "This is a synthesized answer."
    mock_chat_response = MagicMock()
    mock_chat_response.choices = [mock_choice]
    client.chat.completions.create.return_value = mock_chat_response

    return client


@pytest.fixture
def mock_openai_batch_embeddings(mock_openai_client):
    """Mock OpenAI client that handles batch embedding calls (multiple texts)."""

    def batch_embed_side_effect(**kwargs):
        inputs = kwargs.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        mock_response = MagicMock()
        mock_response.data = []
        for i, _ in enumerate(inputs):
            mock_emb = MagicMock()
            mock_emb.embedding = [0.1 + i * 0.01] * 1536
            mock_response.data.append(mock_emb)
        return mock_response

    mock_openai_client.embeddings.create.side_effect = batch_embed_side_effect
    return mock_openai_client


@pytest.fixture
def mock_anthropic_client():
    """Mock Anthropic client for chat synthesis."""
    client = MagicMock()
    mock_content = MagicMock()
    mock_content.text = "This is an Anthropic-synthesized answer."
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    client.messages.create.return_value = mock_response
    return client


@pytest.fixture
def chroma_collection():
    """Ephemeral ChromaDB collection for testing (no persistence)."""
    import chromadb

    client = chromadb.Client()  # ephemeral in-memory
    collection = client.get_or_create_collection(
        name="test_lfucg_meetings",
        metadata={"hnsw:space": "cosine"},
    )
    yield collection
    # Cleanup
    client.delete_collection("test_lfucg_meetings")
