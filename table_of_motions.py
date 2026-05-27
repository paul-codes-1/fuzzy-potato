"""Table of Motions extraction from LFUCG agenda packets.

LFUCG Council *Work Sessions* don't produce official minutes. Instead, the
authoritative structured record of a work session's motions is the
**"Table of Motions"** — and it's published in the *next* session's agenda
packet (under agenda section III, "Approval of Summary"). So meeting N's
official motion record lives inside meeting N+1's agenda PDF.

A packet embeds the table as a self-contained block:

    1                               <- packet page number
    URBAN COUNTY COUNCIL            <- body header (1-2 lines)
    WORK SESSION
    TABLE OF MOTIONS                <- marker line
    May 12, 2026                    <- date of the meeting being recorded
    Mayor Gorton called the meeting to order at 3:01 p.m. ... were present.
    I. Public Comment - Issues on Agenda
    II. Requested Rezonings/Docket Approval
        Motion by Ellinger to approve the May 14, 2026 Council Meeting
        Docket. Seconded by Wu. Motion passed without dissent.
    ...
    XIII. Adjournment
        Motion by Baxter to adjourn at 3:51 p.m. Seconded by Sevigny.
        Motion passed without dissent.

The block ends where the agenda packet items resume (a bare page number
followed by a `NNNN-NN` file number / "MAYOR LINDA GORTON" / a packet
section header like "BUDGET AMENDMENT REQUEST LIST").

This module slices each block out of an agenda's extracted text, parses the
motions onto the same schema as ``extracted_facts.json``'s
``motions_and_votes``, and resolves which clip in the archive the table
refers to (date + body-class match, ambiguity-guarded). The official motions
then *replace* the Whisper-derived motions on the target clip — see
``main.py --backfill-tables-of-motions``.

Parser hardening mirrors ``granicus_captions.py``: ASCII control bytes are
stripped and bare page-number lines are dropped before motion text is
reassembled across the packet's hard line-wrapping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Lexical patterns
# ---------------------------------------------------------------------------

# The marker line that opens a block. Matched on a stripped, upper-cased line.
_MARKER = "TABLE OF MOTIONS"

# Old Granicus/agenda PDFs occasionally carry ASCII control-byte corruption
# (same failure mode the captions parser guards against). Strip them before
# anything else looks at the text.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# A bare page-number line (e.g. "1", "23"). Inside a block these are page
# breaks, never motion content — motions only ever carry numbers inline
# (vote tallies like "10-5"), never on a line of their own.
_PAGE_NUM = re.compile(r"^\d{1,3}$")

# A packet-item file number ("0363-26", "L0392-26") heading each agenda item.
# Its appearance after the block's body marks the end of the table.
_FILE_NUM = re.compile(r"^(?:L)?\d{3,4}-\d{2}$")

# Packet section headers / boilerplate that also mark the end of the table.
_PACKET_RESUME = re.compile(
    r"^(?:MAYOR LINDA GORTON"
    r"|BUDGET AMENDMENT REQUEST(?: LIST| SUMMARY)?"
    r"|BUDGET ADJUSTMENT(?: LIST| SUMMARY)?"
    r"|INFORMATION ONLY"
    r"|ADMINISTRATIVE SYNOPSIS"
    r"|NEIGHBORHOOD DEVELOPMENT FUNDS)\b",
    re.IGNORECASE,
)

# Roman-numeral agenda section header, e.g. "II. Requested Rezonings/Docket
# Approval". Roman numerals I–XX cover any real council agenda.
_ROMAN = (
    "I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX"
)
_SECTION = re.compile(rf"^(?P<num>{_ROMAN})\.\s+(?P<title>.+)$")

# A motion sentence opens with "Motion by <name> to <action>".
_MOTION_START = re.compile(r"^Motion by\b", re.IGNORECASE)

# Mover: "Motion by Ellinger to approve ..." -> "Ellinger". Non-greedy up to
# the infinitive " to ".
_MOVER = re.compile(r"\bMotion by\s+(?P<name>.+?)\s+to\s+", re.IGNORECASE)
# Seconder: "Seconded by Wu." / "Seconded by Wu, the motion passed ...".
_SECONDER = re.compile(r"\bSeconded by\s+(?P<name>[A-Za-z][A-Za-z .'\-]*?)\s*[,.]")
# Outcome verb.
_OUTCOME = re.compile(
    r"\bmotion\s+(?:was\s+)?(passed|carried|failed|defeated|tabled|postponed|withdrawn)\b",
    re.IGNORECASE,
)
# "without dissent" -> unanimous. Match on the distinctive word "dissent"
# alone so OCR mangling of "without" (real example: "witho0ut dissent") still
# registers as a unanimous vote.
_WITHOUT_DISSENT = re.compile(r"\bdissent\b", re.IGNORECASE)
# A split-vote tally: "passed 10-5", "passed with a 11 - 4 vote".
_TALLY = re.compile(r"(?<!\d)(\d{1,2})\s*-\s*(\d{1,2})\b")
# Roll-call name lists: "(yes: A, B; no: C, D)" (also accepts ayes/nays).
_ROLLCALL = re.compile(
    r"\(\s*(?:yes|ayes?)\s*:\s*(?P<yes>.*?)\s*;\s*(?:no|nays?)\s*:\s*(?P<no>.*?)\s*\)",
    re.IGNORECASE | re.DOTALL,
)
# A parenthetical condition on the outcome, e.g. "(as amended)".
_CONDITION = re.compile(r"\(((?:as |with )[^)]+)\)", re.IGNORECASE)

# Preamble: "Mayor Gorton called the meeting to order at 3:01 p.m."
_CALLED_TO_ORDER = re.compile(
    r"called (?:it|the meeting) to order at\s+(?P<time>[\d:]+\s*[ap]\.?m\.?)",
    re.IGNORECASE,
)

# Month names -> number, for "May 12, 2026" -> 2026-05-12.
_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january", "february", "march", "april", "may", "june", "july",
            "august", "september", "october", "november", "december",
        ],
        start=1,
    )
}
_DATE = re.compile(
    r"\b(?P<mon>January|February|March|April|May|June|July|August|September"
    r"|October|November|December)\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+"
    r"(?P<year>\d{4})\b",
    re.IGNORECASE,
)

_OUTCOME_CANON = {
    "passed": "passed",
    "carried": "passed",
    "failed": "failed",
    "defeated": "failed",
    "tabled": "tabled",
    "postponed": "postponed",
    "withdrawn": "withdrawn",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Motion:
    """One parsed motion, shaped to merge into ``motions_and_votes``."""

    section: Optional[str] = None
    description: str = ""
    motion_by: Optional[str] = None
    second_by: Optional[str] = None
    outcome: str = "passed"
    vote_type: str = "unknown"  # unanimous | roll_call | voice | unknown
    ayes: int = 0
    nays: int = 0
    abstentions: int = 0
    votes_for: List[str] = field(default_factory=list)
    votes_against: List[str] = field(default_factory=list)
    conditions: Optional[str] = None

    def to_fact(self) -> Dict[str, Any]:
        """Render as an ``extracted_facts.json`` motion dict.

        ``transcript_approx_time`` is intentionally ``None`` — a table of
        motions has no video timestamp. ``main.py`` opportunistically copies
        a timestamp over from the displaced Whisper motion when one clearly
        aligns, so video deep-links survive the replace.
        """
        return {
            "identifier": None,
            "description": self.description,
            "motion_by": self.motion_by,
            "second_by": self.second_by,
            "outcome": self.outcome,
            "vote_type": self.vote_type,
            "ayes": self.ayes,
            "nays": self.nays,
            "abstentions": self.abstentions,
            "votes_for": self.votes_for,
            "votes_against": self.votes_against,
            "conditions": self.conditions,
            "transcript_approx_time": None,
            "section": self.section,
        }


@dataclass
class TableOfMotions:
    """A Table of Motions block lifted from an agenda packet."""

    body_header: str  # raw header, e.g. "Urban County Council Work Session"
    body_class: str  # canonical class for clip matching
    date: Optional[str]  # ISO YYYY-MM-DD, or None if unparseable
    raw_date: str  # the date string as printed, e.g. "May 12, 2026"
    called_to_order: Optional[str]
    attendees: List[str]
    motions: List[Motion]
    narrative: List[str]  # non-motion lines (committee reports, asides)

    def matches_clip(self, clip: Dict[str, Any]) -> bool:
        """Does an index.json clip record correspond to this table's meeting?

        Date equality is checked by the caller; this is the body-class test.
        """
        title = (clip.get("title") or "").lower()
        if self.body_class == "council_work_session":
            # Council work session — exclude Planning Commission work sessions
            # that can share the same date.
            if "work session" not in title:
                return False
            return "planning" not in title and "commission" not in title
        # Generic fallback: require the leading word of the header to appear
        # in the title (e.g. "committee", "commission").
        lead = self.body_header.split()[0].lower() if self.body_header else ""
        return bool(lead) and lead in title


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_date(text: str) -> Optional[str]:
    """Parse "May 12, 2026" -> "2026-05-12". Returns None if no match."""
    m = _DATE.search(text)
    if not m:
        return None
    mon = _MONTHS.get(m.group("mon").lower())
    if not mon:
        return None
    return f"{int(m.group('year')):04d}-{mon:02d}-{int(m.group('day')):02d}"


def _classify_body(header: str) -> str:
    """Map a block's body header to a canonical class for clip matching."""
    h = header.lower()
    if "work session" in h:
        if "planning" in h or "commission" in h:
            return "planning_commission_work_session"
        return "council_work_session"
    if "committee of the whole" in h:
        return "committee_of_the_whole"
    return "other"


def _split_names(blob: str) -> List[str]:
    """Split a roll-call name list ("Wu, Brown, and Morton") into names."""
    blob = blob.replace(" and ", ", ").replace(",and ", ", ")
    names = [n.strip(" .") for n in re.split(r"[,;]", blob)]
    return [n for n in names if n]


def parse_motion_line(text: str, section: Optional[str] = None) -> Motion:
    """Parse one reassembled motion sentence into a :class:`Motion`."""
    text = re.sub(r"\s+", " ", text).strip()
    motion = Motion(section=section)

    mover = _MOVER.search(text)
    if mover:
        motion.motion_by = mover.group("name").strip(" .")

    seconder = _SECONDER.search(text)
    if seconder:
        motion.second_by = seconder.group("name").strip(" .")

    # Description: everything between "to " (after the mover) and "Seconded by".
    desc = text
    if mover:
        desc = text[mover.end():]
    sec_split = re.split(r"\bSeconded by\b", desc, maxsplit=1, flags=re.IGNORECASE)
    desc = sec_split[0]
    motion.description = desc.strip(" .,")

    # Outcome.
    out = _OUTCOME.search(text)
    if out:
        motion.outcome = _OUTCOME_CANON.get(out.group(1).lower(), "passed")

    # Vote type + tallies.
    roll = _ROLLCALL.search(text)
    if _WITHOUT_DISSENT.search(text):
        motion.vote_type = "unanimous"
    if roll:
        motion.vote_type = "roll_call"
        motion.votes_for = _split_names(roll.group("yes"))
        motion.votes_against = _split_names(roll.group("no"))
    # A numeric tally ("10-5") — only trust it when a split vote is indicated,
    # to avoid catching dates/identifiers in the description.
    if roll or re.search(r"\bvote\b|\d+\s*-\s*\d+\s*\(", text, re.IGNORECASE):
        tally = _TALLY.search(text)
        if tally:
            motion.ayes = int(tally.group(1))
            motion.nays = int(tally.group(2))
            if motion.vote_type == "unknown":
                motion.vote_type = "roll_call"
    # Reconcile counts with parsed name lists when the tally was absent.
    if motion.vote_type == "roll_call":
        if not motion.ayes and motion.votes_for:
            motion.ayes = len(motion.votes_for)
        if not motion.nays and motion.votes_against:
            motion.nays = len(motion.votes_against)

    cond = _CONDITION.search(text)
    if cond:
        motion.conditions = cond.group(1).strip()

    return motion


def _parse_preamble(lines: List[str]) -> tuple[Optional[str], List[str]]:
    """Pull call-to-order time + attendee names from the preamble lines."""
    blob = " ".join(lines)
    called = None
    start = 0
    cm = _CALLED_TO_ORDER.search(blob)
    if cm:
        called = re.sub(r"\s+", " ", cm.group("time")).strip()
        start = cm.end()  # skip the "Mayor X called ... to order ..." prose
    attendees: List[str] = []
    # "... Vice Mayor Wu and Council Members Brown, Ellinger II, ... were present."
    present = re.search(r"(.*?)\s+were present", blob[start:], re.IGNORECASE)
    if present:
        # Strip role labels (they aren't names) and split what remains.
        seg = re.sub(
            r"\b(?:Vice Mayor|Council Members?|Mayor)\b",
            ",",
            present.group(1),
            flags=re.IGNORECASE,
        )
        attendees = _split_names(seg)
    return called, attendees


# ---------------------------------------------------------------------------
# Block extraction
# ---------------------------------------------------------------------------


def _find_block_bounds(lines: List[str], marker_idx: int) -> int:
    """Return the exclusive end index of the block opened at ``marker_idx``."""
    i = marker_idx + 1
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if _FILE_NUM.match(stripped) or _PACKET_RESUME.match(stripped):
            return i
        if stripped.upper() == _MARKER:  # a second table starts; stop here
            return i
        i += 1
    return n


def _header_above(lines: List[str], marker_idx: int) -> str:
    """Collect the body-header lines immediately above the marker."""
    header: List[str] = []
    j = marker_idx - 1
    while j >= 0:
        stripped = lines[j].strip()
        if not stripped or _PAGE_NUM.match(stripped):
            break
        header.append(stripped)
        j -= 1
    header.reverse()
    return " ".join(header).title() if header else ""


def extract_tables(agenda_text: str) -> List[TableOfMotions]:
    """Extract every Table of Motions block embedded in an agenda's text."""
    if not agenda_text:
        return []
    text = _CONTROL_CHARS.sub("", agenda_text)
    lines = text.split("\n")

    marker_idxs = [
        i for i, ln in enumerate(lines) if ln.strip().upper() == _MARKER
    ]

    tables: List[TableOfMotions] = []
    for mi in marker_idxs:
        end = _find_block_bounds(lines, mi)
        header = _header_above(lines, mi)

        # The date is the first dated line after the marker. The first
        # section header bounds the search so a date inside a motion (e.g.
        # "the May 14, 2026 Council Meeting Docket") is never mistaken for
        # the meeting date.
        body_lines = lines[mi + 1 : end]
        raw_date = ""
        date_iso = None
        date_line_idx = 0
        for k, ln in enumerate(body_lines):
            if _SECTION.match(ln.strip()):
                break
            dm = _DATE.search(ln)
            if dm:
                raw_date = dm.group(0)
                date_iso = parse_date(ln)
                date_line_idx = k
                break
        # Fallback: older "Special Committee" tables print the date in the
        # title block above the marker rather than below it.
        if date_iso is None:
            dm = _DATE.search(header)
            if dm:
                raw_date = dm.group(0)
                date_iso = parse_date(header)

        # Everything from after the date line up to the first section header
        # is preamble (call-to-order + attendance).
        rest = body_lines[date_line_idx + 1 :]
        preamble: List[str] = []
        content_start = len(rest)
        for k, ln in enumerate(rest):
            if _SECTION.match(ln.strip()):
                content_start = k
                break
            preamble.append(ln.strip())
        called, attendees = _parse_preamble(preamble)

        motions, narrative = _parse_content(rest[content_start:])

        tables.append(
            TableOfMotions(
                body_header=header,
                body_class=_classify_body(header),
                date=date_iso,
                raw_date=raw_date,
                called_to_order=called,
                attendees=attendees,
                motions=motions,
                narrative=narrative,
            )
        )
    return tables


def _parse_content(lines: List[str]) -> tuple[List[Motion], List[str]]:
    """Walk the section/motion body, reassembling hard-wrapped sentences."""
    motions: List[Motion] = []
    narrative: List[str] = []
    section: Optional[str] = None
    buf: List[str] = []
    buf_is_motion = False

    def flush() -> None:
        nonlocal buf, buf_is_motion
        if not buf:
            return
        joined = " ".join(buf).strip()
        if buf_is_motion and joined:
            motions.append(parse_motion_line(joined, section))
        elif joined:
            narrative.append(joined)
        buf = []
        buf_is_motion = False

    for raw in lines:
        stripped = raw.strip()
        if not stripped or _PAGE_NUM.match(stripped):
            continue  # blank line or page break
        sec = _SECTION.match(stripped)
        if sec:
            flush()
            section = f"{sec.group('num')}. {sec.group('title').strip()}"
            continue
        if _MOTION_START.match(stripped):
            flush()
            buf = [stripped]
            buf_is_motion = True
        else:
            buf.append(stripped)

    flush()
    return motions, narrative


# ---------------------------------------------------------------------------
# Clip resolution
# ---------------------------------------------------------------------------


@dataclass
class Resolution:
    """Outcome of matching a table to a clip in the archive."""

    clip_id: Optional[int]
    status: str  # matched | no_date | no_match | ambiguous
    candidates: List[int] = field(default_factory=list)


def resolve_target_clip(
    table: TableOfMotions, clips: List[Dict[str, Any]]
) -> Resolution:
    """Find the archive clip a table refers to (date + body-class match).

    ``clips`` is the ``clips`` list from ``index.json``. Returns a
    :class:`Resolution`; ``clip_id`` is set only on an unambiguous single
    match. Multiple same-date matches (duplicate Granicus uploads) are
    reported as ``ambiguous`` and left for manual handling rather than
    guessed.
    """
    if not table.date:
        return Resolution(None, "no_date")
    candidates = [
        c
        for c in clips
        if c.get("date") == table.date and table.matches_clip(c)
    ]
    ids = [int(c["clip_id"]) for c in candidates]
    if len(ids) == 1:
        return Resolution(ids[0], "matched", ids)
    if not ids:
        return Resolution(None, "no_match")
    return Resolution(None, "ambiguous", ids)


# ---------------------------------------------------------------------------
# Merge into extracted_facts.json
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    "the a an to of and or for on in by with approve approval motion".split()
)


def _desc_tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _carry_over_timestamps(
    official: List[Dict[str, Any]], displaced: List[Dict[str, Any]]
) -> int:
    """Copy video timestamps from displaced Whisper motions onto the official
    motions when descriptions clearly align. Returns how many were carried.

    "Replace (official wins)" drops the Whisper motions, which are the only
    motions that carry ``transcript_approx_time`` (a table of motions has no
    video clock). To keep the Overview tab's clickable video deep-links, we
    inherit *just the timestamp* from the best-matching displaced motion —
    the official text/names/tallies still win.
    """
    pool = [
        m for m in displaced if m.get("transcript_approx_time")
    ]
    carried = 0
    for off in official:
        off_tokens = _desc_tokens(off.get("description"))
        if not off_tokens:
            continue
        best, best_score, best_idx = None, 0.0, -1
        for idx, old in enumerate(pool):
            old_tokens = _desc_tokens(old.get("description"))
            if not old_tokens:
                continue
            overlap = len(off_tokens & old_tokens)
            score = overlap / len(off_tokens | old_tokens)
            # A shared mover is a strong signal; nudge the score.
            if off.get("motion_by") and off["motion_by"] == old.get("motion_by"):
                score += 0.15
            if score > best_score:
                best, best_score, best_idx = old, score, idx
        if best and best_score >= 0.3:
            off["transcript_approx_time"] = best["transcript_approx_time"]
            pool.pop(best_idx)  # one displaced motion feeds one official motion
            carried += 1
    return carried


def merge_table_into_facts(
    facts: Optional[Dict[str, Any]],
    table: TableOfMotions,
    source_clip_id: int,
    source_page: Optional[int] = None,
) -> tuple[Dict[str, Any], Dict[str, int]]:
    """Replace a clip's ``motions_and_votes`` with the official table.

    Returns ``(new_facts, stats)``. ``facts`` may be ``None``/empty (a clip
    that never got a Whisper extraction still gets official motions). The
    transcript-derived motions are replaced wholesale, but their video
    timestamps are carried over where descriptions align.
    """
    facts = dict(facts) if facts else {}
    displaced = facts.get("motions_and_votes") or []
    official = [m.to_fact() for m in table.motions]
    carried = _carry_over_timestamps(official, displaced)

    facts["motions_and_votes"] = official
    facts["motions_source"] = "table_of_motions"
    facts["table_of_motions_ref"] = {
        "source_clip_id": source_clip_id,
        "source_page": source_page,
        "meeting_date": table.date,
    }

    # Fill attendance only when the transcript extraction left it empty —
    # never clobber a populated roster.
    if table.attendees:
        att = facts.setdefault("attendance", {})
        if not att.get("present"):
            att["present"] = table.attendees

    return facts, {
        "official_motions": len(official),
        "displaced_motions": len(displaced),
        "timestamps_carried": carried,
    }
