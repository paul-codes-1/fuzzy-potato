"""Two-pass summary generation: structured extraction + narrative synthesis.

Pass 1 (GPT-4o): Extract structured facts (votes, amounts, names, timestamps) into JSON.
Pass 2 (Claude Sonnet): Generate section-by-section narrative from extracted facts.
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def _coerce_token_count(value) -> int | None:
    """Convert a usage field to int only when it is actually numeric."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _usage_value(usage, *names: str) -> int:
    """Read the first available integer token field from a usage object."""
    if usage is None:
        return 0
    for name in names:
        value = _coerce_token_count(getattr(usage, name, None))
        if value is not None:
            return value
    return 0


def _record_llm_cost(
    tenant_id: str | None,
    usage,
    *,
    model: str,
    operation: str,
    request_id: str | None = None,
) -> None:
    """Best-effort completion cost tracking; never raises."""
    if not tenant_id or usage is None:
        return

    input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
    output_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
    if input_tokens <= 0 and output_tokens <= 0:
        return

    try:
        from api.cost import get_cost_tracker

        get_cost_tracker().record_llm_call(
            tenant_id=tenant_id,
            model=model,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_id=request_id,
            module="summary_v2",
        )
    except Exception:
        logger.debug("cost tracking failed for summary_v2 llm call", exc_info=True)


# ============================================================
# Pass 1: Structured extraction prompt (GPT-4o)
# ============================================================

EXTRACTION_SYSTEM_PROMPT = (
    "You are a government meeting transcription analyst. Extract structured "
    "data from the provided meeting materials. Be precise with names, numbers, "
    "resolution/ordinance identifiers, and vote counts. If information is not "
    "present in the source materials, use null rather than guessing."
)

EXTRACTION_SCHEMA = """\
Respond with valid JSON matching this schema:

{
  "meeting_info": {
    "date": "YYYY-MM-DD or null",
    "time": "string or null",
    "body": "string",
    "presiding_officer": "string or null",
    "location": "string or null"
  },
  "attendance": {
    "present": ["name1", "name2"],
    "absent": ["name1"],
    "late": ["name1"]
  },
  "motions_and_votes": [
    {
      "identifier": "Ordinance 0130-26 or null",
      "description": "Brief description of what was voted on",
      "motion_by": "name or null",
      "second_by": "name or null",
      "outcome": "passed|failed|tabled|postponed|withdrawn",
      "vote_type": "unanimous|roll_call|voice|unknown",
      "ayes": 0,
      "nays": 0,
      "abstentions": 0,
      "votes_for": ["name1", "name2"],
      "votes_against": ["name1"],
      "conditions": "any conditions attached to approval or null",
      "transcript_approx_time": "MM:SS or null"
    }
  ],
  "financial_items": [
    {
      "description": "what the money is for",
      "amount": "$474,477",
      "type": "contract|appropriation|amendment|grant|purchase",
      "identifier": "Resolution 0073-26 or null",
      "vendor_or_recipient": "string or null"
    }
  ],
  "public_comments": [
    {
      "speaker": "name or null",
      "topic": "brief topic",
      "summary": "1-2 sentence summary of their remarks",
      "transcript_approx_time": "MM:SS or null"
    }
  ],
  "agenda_items": [
    {
      "identifier": "item number or null",
      "title": "item title",
      "type": "ordinance|resolution|presentation|discussion|appointment|proclamation",
      "summary": "2-3 sentences on what was discussed",
      "key_speakers": ["name1"],
      "outcome": "approved|denied|tabled|deferred|first_reading|informational",
      "transcript_approx_time": "MM:SS or null"
    }
  ],
  "appointments": [
    {
      "person": "name",
      "body_or_role": "commission or board name",
      "action": "appointed|reappointed|resigned"
    }
  ],
  "contentious_items": [
    {
      "topic": "brief description",
      "nature": "split_vote|heated_discussion|community_opposition|procedural_dispute",
      "details": "1-2 sentences"
    }
  ]
}"""


def build_extraction_prompt(transcript: str, agenda_text: Optional[str],
                            minutes_text: Optional[str]) -> str:
    """Build the user prompt for Pass 1 extraction."""
    parts = ["Extract structured meeting data from these sources.\n"]

    if agenda_text:
        parts.append(f"MEETING AGENDA:\n{agenda_text}")

    if minutes_text:
        truncated = minutes_text[:30000] if len(minutes_text) > 30000 else minutes_text
        parts.append(f"OFFICIAL MEETING MINUTES:\n{truncated}")

    parts.append(f"MEETING TRANSCRIPT:\n{transcript}")
    parts.append(EXTRACTION_SCHEMA)

    return "\n\n---\n\n".join(parts)


def extract_meeting_facts(openai_client, transcript: str, agenda_text: Optional[str],
                          minutes_text: Optional[str], model: str = "gpt-4o",
                          tenant_id: Optional[str] = None,
                          request_id: Optional[str] = None) -> dict:
    """Pass 1: Extract structured facts from meeting sources using GPT-4o.

    Returns parsed JSON dict, or empty dict on failure.
    """
    prompt = build_extraction_prompt(transcript, agenda_text, minutes_text)

    response = openai_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=8000,
        response_format={"type": "json_object"},
    )
    _record_llm_cost(
        tenant_id,
        getattr(response, "usage", None),
        model=model,
        operation="summary_v2_extract",
        request_id=request_id,
    )

    raw = response.choices[0].message.content.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown code fence
        match = re.search(r'```(?:json)?\s*\n(.*?)```', raw, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        return {}


# ============================================================
# Pass 2: Section-by-section narrative (Claude Sonnet)
# ============================================================

NARRATION_SYSTEM_PROMPT = (
    "You are a government meeting analyst writing a public-facing summary. "
    "Write clear, factual prose from the provided structured data and source materials. "
    "Include specific names, numbers, and identifiers. Do not editorialize or speculate. "
    "Each section should be self-contained and understandable without the other sections.\n\n"
    "For each section, include transcript timestamps where available in the format "
    "[timestamp: MM:SS] so readers can find the relevant portion of the meeting video.\n\n"
    "Use markdown formatting with bullet points for lists. Begin with a ## header."
)

# Section definitions: (section_name, condition_check, data_builder)
# condition_check takes the extracted facts dict and returns True if section should be generated
# data_builder takes the extracted facts dict and returns the relevant subset as a string

SECTIONS = [
    {
        "name": "Meeting Overview",
        "always": True,
        "instruction": "Write a 3-5 sentence overview of the meeting: date, time, body, presiding officer, and what was accomplished overall. Mention the total number of votes taken, presentations given, and public comments heard.",
    },
    {
        "name": "Attendance",
        "condition": lambda f: bool(f.get("attendance", {}).get("present")),
        "instruction": "List who was present, absent, and late. Use a compact format.",
    },
    {
        "name": "Votes and Decisions",
        "condition": lambda f: bool(f.get("motions_and_votes")),
        "instruction": "For each vote: state the identifier, what was voted on, the outcome with vote count, and who voted for/against if it was a roll call vote. Include transcript timestamps.",
    },
    {
        "name": "Budget and Financial Actions",
        "condition": lambda f: bool(f.get("financial_items")),
        "instruction": "Summarize each financial item with specific dollar amounts, vendors/recipients, and resolution numbers.",
    },
    {
        "name": "Public Comment",
        "condition": lambda f: bool(f.get("public_comments")),
        "instruction": "Summarize who spoke, what topics they addressed, and any notable concerns. Include timestamps.",
    },
    {
        "name": "Appointments",
        "condition": lambda f: bool(f.get("appointments")),
        "instruction": "List each appointment with the person's name, the body/role, and the action taken.",
    },
    {
        "name": "Contested Items",
        "condition": lambda f: bool(f.get("contentious_items")),
        "instruction": "Describe each contested item: what the disagreement was about, who was involved, and the outcome.",
    },
]


def _get_relevant_data(facts: dict, section_name: str) -> str:
    """Extract the relevant subset of facts for a given section."""
    if section_name == "Meeting Overview":
        subset = {
            "meeting_info": facts.get("meeting_info"),
            "motions_and_votes_count": len(facts.get("motions_and_votes", [])),
            "public_comments_count": len(facts.get("public_comments", [])),
            "agenda_items_count": len(facts.get("agenda_items", [])),
            "agenda_items": [
                {"title": item.get("title"), "outcome": item.get("outcome")}
                for item in facts.get("agenda_items", [])
            ],
        }
    elif section_name == "Attendance":
        subset = {"attendance": facts.get("attendance")}
    elif section_name == "Votes and Decisions":
        subset = {"motions_and_votes": facts.get("motions_and_votes")}
    elif section_name == "Budget and Financial Actions":
        subset = {"financial_items": facts.get("financial_items")}
    elif section_name == "Public Comment":
        subset = {"public_comments": facts.get("public_comments")}
    elif section_name == "Appointments":
        subset = {"appointments": facts.get("appointments")}
    elif section_name == "Contested Items":
        subset = {"contentious_items": facts.get("contentious_items")}
    else:
        subset = facts
    return json.dumps(subset, indent=2)


def _generate_agenda_item_sections(facts: dict) -> list[dict]:
    """Build section specs for significant agenda items not already covered by other sections."""
    sections = []
    vote_identifiers = {
        v.get("identifier") for v in facts.get("motions_and_votes", []) if v.get("identifier")
    }

    for item in facts.get("agenda_items", []):
        title = item.get("title", "")
        summary = item.get("summary", "")
        # Skip trivial items (very short summary)
        if len(summary) < 20:
            continue
        # Skip items that are purely votes (already covered in Votes section)
        if item.get("identifier") in vote_identifiers and item.get("type") in ("ordinance", "resolution"):
            if len(summary) < 80:
                continue

        sections.append({
            "name": title[:80],
            "always": True,
            "instruction": "Summarize the discussion on this agenda item. Include key speakers, what was presented or debated, any concerns raised, and the outcome.",
            "data": json.dumps({"agenda_item": item}, indent=2),
        })

    return sections


def generate_section(anthropic_client, section_name: str, instruction: str,
                     facts_json: str, meeting_body: str, date: str,
                     model: str = "claude-sonnet-4-20250514",
                     tenant_id: Optional[str] = None,
                     request_id: Optional[str] = None) -> Optional[str]:
    """Generate a single summary section using Claude Sonnet.

    Returns the section text including the ## header, or None on failure.
    """
    user_prompt = (
        f'Write the "{section_name}" section for the {meeting_body} meeting on {date}.\n\n'
        f"EXTRACTED DATA:\n{facts_json}\n\n"
        f"Instructions: {instruction}\n\n"
        f"Write 100-400 words. Begin with ## {section_name}"
    )

    response = anthropic_client.messages.create(
        model=model,
        max_tokens=1000,
        system=NARRATION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.3,
    )
    _record_llm_cost(
        tenant_id,
        getattr(response, "usage", None),
        model=model,
        operation="summary_v2_narrate",
        request_id=request_id,
    )

    text = response.content[0].text.strip()
    # Ensure it starts with the ## header
    if not text.startswith("## "):
        text = f"## {section_name}\n{text}"
    return text


def generate_summary_v2(openai_client, anthropic_client, transcript: str,
                        agenda_text: Optional[str], minutes_text: Optional[str],
                        meeting_body: str = "Unknown", date: str = "Unknown",
                        extraction_model: str = "gpt-4o",
                        narration_model: str = "claude-sonnet-4-20250514",
                        log_fn=None,
                        tenant_id: Optional[str] = None,
                        request_id: Optional[str] = None) -> tuple[Optional[str], Optional[dict]]:
    """Two-pass summary generation.

    Returns (summary_text, extracted_facts) tuple.
    summary_text is the assembled markdown summary, or None on failure.
    extracted_facts is the raw JSON dict from Pass 1, or None on failure.
    """
    def log(msg):
        if log_fn:
            log_fn(msg)

    # === Pass 1: Structured extraction ===
    log(f"Pass 1: Extracting structured facts with {extraction_model}...")
    facts = extract_meeting_facts(openai_client, transcript, agenda_text,
                                  minutes_text, model=extraction_model,
                                  tenant_id=tenant_id, request_id=request_id)
    if not facts:
        log("Pass 1 failed: no facts extracted")
        return None, None

    log(f"Pass 1 complete: {len(facts.get('motions_and_votes', []))} votes, "
        f"{len(facts.get('agenda_items', []))} agenda items, "
        f"{len(facts.get('financial_items', []))} financial items")

    # === Pass 2: Section-by-section narrative ===
    log(f"Pass 2: Generating narrative sections with {narration_model}...")
    all_sections = []

    # Fixed sections
    for section_def in SECTIONS:
        if not section_def.get("always"):
            condition = section_def.get("condition", lambda f: True)
            if not condition(facts):
                continue

        facts_json = _get_relevant_data(facts, section_def["name"])
        section_text = generate_section(
            anthropic_client, section_def["name"], section_def["instruction"],
            facts_json, meeting_body, date, model=narration_model,
            tenant_id=tenant_id, request_id=request_id,
        )
        if section_text:
            all_sections.append(section_text)
            log(f"  Generated: {section_def['name']}")

    # Dynamic agenda item sections
    agenda_sections = _generate_agenda_item_sections(facts)
    for section_def in agenda_sections:
        facts_json = section_def.get("data", "{}")
        section_text = generate_section(
            anthropic_client, section_def["name"], section_def["instruction"],
            facts_json, meeting_body, date, model=narration_model,
            tenant_id=tenant_id, request_id=request_id,
        )
        if section_text:
            all_sections.append(section_text)
            log(f"  Generated: {section_def['name']}")

    if not all_sections:
        log("Pass 2 failed: no sections generated")
        return None, facts

    summary = "\n\n".join(all_sections)
    log(f"Summary complete: {len(summary)} chars, {len(all_sections)} sections")
    return summary, facts
