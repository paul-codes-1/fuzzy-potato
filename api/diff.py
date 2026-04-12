"""Meeting-over-meeting diff and change tracking engine.

Compares consecutive meetings of the same body to surface:
- New vs carried-over agenda items
- Attendance changes
- New public commenters
- Budget/financial changes
- Ordinance lifecycle (introduced -> passed/failed)
- "What's new" natural-language summaries
"""

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

SIGNIFICANCE_HIGH = "high"
SIGNIFICANCE_MEDIUM = "medium"
SIGNIFICANCE_LOW = "low"


@dataclass
class Change:
    type: str  # agenda_new, agenda_carried, attendance, speaker_new, budget, ordinance, other
    description: str
    significance: str  # high, medium, low
    details: Optional[dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MeetingDiff:
    clip_id_before: int
    clip_id_after: int
    meeting_body: str
    date_before: str
    date_after: str
    changes: list[Change] = field(default_factory=list)
    whats_new_summary: str = ""
    generated_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["changes"] = [c.to_dict() for c in self.changes]
        return d


# ---------------------------------------------------------------------------
# SQLite storage
# ---------------------------------------------------------------------------

_CREATE_DIFFS_TABLE = """
CREATE TABLE IF NOT EXISTS meeting_diffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id_before INTEGER NOT NULL,
    clip_id_after INTEGER NOT NULL,
    meeting_body TEXT NOT NULL,
    date_before TEXT NOT NULL,
    date_after TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    whats_new_summary TEXT NOT NULL DEFAULT '',
    generated_at TEXT NOT NULL,
    UNIQUE(clip_id_before, clip_id_after)
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_diffs_body ON meeting_diffs(meeting_body);
"""

_CREATE_INDEX_AFTER = """
CREATE INDEX IF NOT EXISTS idx_diffs_clip_after ON meeting_diffs(clip_id_after);
"""


class DiffStore:
    """SQLite-backed storage for meeting diffs."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_CREATE_DIFFS_TABLE)
            conn.execute(_CREATE_INDEX)
            conn.execute(_CREATE_INDEX_AFTER)
            conn.commit()

    def save(self, diff: MeetingDiff):
        """Insert or replace a diff."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO meeting_diffs
                   (clip_id_before, clip_id_after, meeting_body, date_before, date_after,
                    changes_json, whats_new_summary, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    diff.clip_id_before,
                    diff.clip_id_after,
                    diff.meeting_body,
                    diff.date_before,
                    diff.date_after,
                    json.dumps([c.to_dict() for c in diff.changes]),
                    diff.whats_new_summary,
                    diff.generated_at,
                ),
            )
            conn.commit()

    def get_by_clip_after(self, clip_id: int) -> Optional[MeetingDiff]:
        """Get the diff where clip_id is the 'after' meeting."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM meeting_diffs WHERE clip_id_after = ?", (clip_id,)
            ).fetchone()
            if row:
                return self._row_to_diff(row)
        return None

    def get_by_pair(self, clip_id_a: int, clip_id_b: int) -> Optional[MeetingDiff]:
        """Get diff between two specific clips."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM meeting_diffs WHERE clip_id_before = ? AND clip_id_after = ?",
                (clip_id_a, clip_id_b),
            ).fetchone()
            if row:
                return self._row_to_diff(row)
        return None

    def get_latest(self, limit: int = 10) -> list[MeetingDiff]:
        """Get latest diffs across all bodies."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM meeting_diffs ORDER BY date_after DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_diff(r) for r in rows]

    def get_body_timeline(self, meeting_body: str, limit: int = 50) -> list[MeetingDiff]:
        """Get diff timeline for a specific meeting body."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM meeting_diffs WHERE meeting_body = ? ORDER BY date_after DESC LIMIT ?",
                (meeting_body, limit),
            ).fetchall()
            return [self._row_to_diff(r) for r in rows]

    def _row_to_diff(self, row) -> MeetingDiff:
        changes_raw = json.loads(row["changes_json"])
        changes = [
            Change(
                type=c["type"],
                description=c["description"],
                significance=c["significance"],
                details=c.get("details", {}),
            )
            for c in changes_raw
        ]
        return MeetingDiff(
            clip_id_before=row["clip_id_before"],
            clip_id_after=row["clip_id_after"],
            meeting_body=row["meeting_body"],
            date_before=row["date_before"],
            date_after=row["date_after"],
            changes=changes,
            whats_new_summary=row["whats_new_summary"],
            generated_at=row["generated_at"],
        )


# ---------------------------------------------------------------------------
# Diff computation engine
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase and strip whitespace for comparison."""
    return " ".join(text.lower().split()) if text else ""


def _similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """Check if two strings are similar enough to be the same item."""
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio() >= threshold


class MeetingDiffEngine:
    """Computes diffs between two meetings' extracted facts."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        db_path = os.path.join(output_dir, "diffs.db")
        self.store = DiffStore(db_path)

    def load_facts(self, clip_id: int) -> Optional[dict]:
        """Load extracted_facts.json for a clip."""
        facts_path = os.path.join(self.output_dir, "clips", str(clip_id), "extracted_facts.json")
        if not os.path.exists(facts_path):
            return None
        try:
            with open(facts_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def load_metadata(self, clip_id: int) -> Optional[dict]:
        """Load metadata.json for a clip."""
        meta_path = os.path.join(self.output_dir, "clips", str(clip_id), "metadata.json")
        if not os.path.exists(meta_path):
            return None
        try:
            with open(meta_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def find_previous_meeting(self, clip_id: int, meeting_body: str) -> Optional[int]:
        """Find the most recent earlier clip of the same meeting body.

        Scans all clip directories for metadata matching the same body
        with an earlier date than the given clip.
        """
        target_meta = self.load_metadata(clip_id)
        if not target_meta:
            return None

        target_date = target_meta.get("date", "")
        clips_dir = os.path.join(self.output_dir, "clips")
        if not os.path.isdir(clips_dir):
            return None

        candidates = []
        for name in os.listdir(clips_dir):
            if name == str(clip_id):
                continue
            meta_path = os.path.join(clips_dir, name, "metadata.json")
            if not os.path.exists(meta_path):
                continue
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                if meta.get("meeting_body") == meeting_body and meta.get("date", "") < target_date:
                    candidates.append((meta["clip_id"], meta["date"]))
            except (json.JSONDecodeError, IOError, KeyError):
                continue

        if not candidates:
            return None

        # Return the most recent earlier meeting
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    def compute_diff(self, clip_id_before: int, clip_id_after: int) -> MeetingDiff:
        """Compute a full diff between two meetings."""
        facts_before = self.load_facts(clip_id_before) or {}
        facts_after = self.load_facts(clip_id_after) or {}
        meta_before = self.load_metadata(clip_id_before) or {}
        meta_after = self.load_metadata(clip_id_after) or {}

        meeting_body = meta_after.get("meeting_body", meta_before.get("meeting_body", "Unknown"))
        date_before = meta_before.get("date", "")
        date_after = meta_after.get("date", "")

        changes = []

        # 1. Attendance changes
        changes.extend(self._diff_attendance(facts_before, facts_after))

        # 2. Agenda items: new vs carried over
        changes.extend(self._diff_agenda_items(facts_before, facts_after))

        # 3. Ordinance lifecycle
        changes.extend(self._diff_ordinances(facts_before, facts_after))

        # 4. Financial / budget changes
        changes.extend(self._diff_financial(facts_before, facts_after))

        # 5. New public comment speakers
        changes.extend(self._diff_public_comments(facts_before, facts_after))

        # 6. Appointments
        changes.extend(self._diff_appointments(facts_before, facts_after))

        # Generate natural language summary
        summary = self._generate_whats_new(changes, meeting_body, date_before, date_after)

        diff = MeetingDiff(
            clip_id_before=clip_id_before,
            clip_id_after=clip_id_after,
            meeting_body=meeting_body,
            date_before=date_before,
            date_after=date_after,
            changes=changes,
            whats_new_summary=summary,
            generated_at=datetime.utcnow().isoformat() + "Z",
        )

        return diff

    def compute_and_store(self, clip_id_before: int, clip_id_after: int) -> MeetingDiff:
        """Compute diff and persist to SQLite."""
        diff = self.compute_diff(clip_id_before, clip_id_after)
        self.store.save(diff)
        return diff

    def auto_diff_for_clip(self, clip_id: int) -> Optional[MeetingDiff]:
        """Auto-find previous meeting of same body and compute diff.

        Called after processing a new clip.
        """
        meta = self.load_metadata(clip_id)
        if not meta:
            logger.warning("No metadata for clip %s, skipping diff", clip_id)
            return None

        meeting_body = meta.get("meeting_body")
        if not meeting_body:
            logger.warning("No meeting_body in metadata for clip %s", clip_id)
            return None

        prev_clip = self.find_previous_meeting(clip_id, meeting_body)
        if prev_clip is None:
            logger.info("No previous %s meeting found for clip %s", meeting_body, clip_id)
            return None

        logger.info("Computing diff: clip %s -> %s (%s)", prev_clip, clip_id, meeting_body)
        return self.compute_and_store(prev_clip, clip_id)

    # ------------------------------------------------------------------
    # Diff sub-routines
    # ------------------------------------------------------------------

    def _diff_attendance(self, before: dict, after: dict) -> list[Change]:
        changes = []
        att_before = before.get("attendance", {})
        att_after = after.get("attendance", {})

        present_before = set(att_before.get("present", []))
        present_after = set(att_after.get("present", []))
        absent_before = set(att_before.get("absent", []))
        absent_after = set(att_after.get("absent", []))

        # Members now absent who were present last time
        newly_absent = (present_before & absent_after)
        for name in sorted(newly_absent):
            changes.append(Change(
                type="attendance",
                description=f"{name} was absent (was present at the previous meeting)",
                significance=SIGNIFICANCE_MEDIUM,
                details={"member": name, "status": "newly_absent"},
            ))

        # Members now present who were absent last time
        returned = (absent_before & present_after)
        for name in sorted(returned):
            changes.append(Change(
                type="attendance",
                description=f"{name} returned (was absent at the previous meeting)",
                significance=SIGNIFICANCE_LOW,
                details={"member": name, "status": "returned"},
            ))

        # Completely new members (in present_after but not in any previous list)
        all_before = present_before | absent_before | set(att_before.get("late", []))
        new_members = present_after - all_before
        for name in sorted(new_members):
            if name and all_before:  # Only flag if we had data from before
                changes.append(Change(
                    type="attendance",
                    description=f"{name} appears for the first time",
                    significance=SIGNIFICANCE_MEDIUM,
                    details={"member": name, "status": "new_member"},
                ))

        return changes

    def _diff_agenda_items(self, before: dict, after: dict) -> list[Change]:
        changes = []
        items_before = before.get("agenda_items", [])
        items_after = after.get("agenda_items", [])

        # Build lookup sets using titles and identifiers
        before_titles = {_normalize(i.get("title", "")): i for i in items_before if i.get("title")}
        before_ids = {_normalize(i.get("identifier", "")): i for i in items_before if i.get("identifier")}

        for item in items_after:
            title = item.get("title", "")
            identifier = item.get("identifier", "")
            norm_title = _normalize(title)
            norm_id = _normalize(identifier)

            # Check if this item appeared in the previous meeting
            carried = False
            if norm_id and norm_id in before_ids:
                carried = True
            elif norm_title and any(_similar(norm_title, bt) for bt in before_titles):
                carried = True

            if carried:
                prev_item = before_ids.get(norm_id) or next(
                    (v for k, v in before_titles.items() if _similar(norm_title, k)), {}
                )
                prev_outcome = prev_item.get("outcome", "")
                curr_outcome = item.get("outcome", "")

                if prev_outcome and curr_outcome and prev_outcome != curr_outcome:
                    changes.append(Change(
                        type="agenda_carried",
                        description=f"'{title}' carried over from previous meeting: {prev_outcome} -> {curr_outcome}",
                        significance=SIGNIFICANCE_HIGH,
                        details={
                            "identifier": identifier,
                            "title": title,
                            "previous_outcome": prev_outcome,
                            "current_outcome": curr_outcome,
                        },
                    ))
                else:
                    changes.append(Change(
                        type="agenda_carried",
                        description=f"'{title}' carried over from previous meeting",
                        significance=SIGNIFICANCE_LOW,
                        details={"identifier": identifier, "title": title},
                    ))
            else:
                sig = SIGNIFICANCE_HIGH if item.get("type") in ("ordinance", "resolution") else SIGNIFICANCE_MEDIUM
                changes.append(Change(
                    type="agenda_new",
                    description=f"New agenda item: '{title}'",
                    significance=sig,
                    details={
                        "identifier": identifier,
                        "title": title,
                        "type": item.get("type", ""),
                        "outcome": item.get("outcome", ""),
                    },
                ))

        return changes

    def _diff_ordinances(self, before: dict, after: dict) -> list[Change]:
        """Track ordinance lifecycle across meetings."""
        changes = []
        votes_before = before.get("motions_and_votes", [])
        votes_after = after.get("motions_and_votes", [])

        before_by_id = {}
        for v in votes_before:
            vid = _normalize(v.get("identifier", ""))
            if vid:
                before_by_id[vid] = v

        for vote in votes_after:
            vid = _normalize(vote.get("identifier", ""))
            outcome = vote.get("outcome", "")
            desc = vote.get("description", "")
            identifier = vote.get("identifier", "")

            if vid and vid in before_by_id:
                prev_outcome = before_by_id[vid].get("outcome", "")
                if prev_outcome != outcome:
                    changes.append(Change(
                        type="ordinance",
                        description=f"{identifier}: {prev_outcome} -> {outcome}",
                        significance=SIGNIFICANCE_HIGH,
                        details={
                            "identifier": identifier,
                            "description": desc,
                            "previous_outcome": prev_outcome,
                            "current_outcome": outcome,
                        },
                    ))
            elif vid:
                changes.append(Change(
                    type="ordinance",
                    description=f"New vote: {identifier} ({outcome}) - {desc[:100]}",
                    significance=SIGNIFICANCE_HIGH if outcome in ("passed", "failed") else SIGNIFICANCE_MEDIUM,
                    details={
                        "identifier": identifier,
                        "description": desc,
                        "outcome": outcome,
                        "ayes": vote.get("ayes"),
                        "nays": vote.get("nays"),
                    },
                ))

        return changes

    def _diff_financial(self, before: dict, after: dict) -> list[Change]:
        changes = []
        fin_before = before.get("financial_items", [])
        fin_after = after.get("financial_items", [])

        before_descs = {_normalize(f.get("description", "")): f for f in fin_before if f.get("description")}

        for item in fin_after:
            desc = item.get("description", "")
            amount = item.get("amount", "")
            norm_desc = _normalize(desc)

            matched = False
            for bkey, bitem in before_descs.items():
                if _similar(norm_desc, bkey):
                    matched = True
                    prev_amount = bitem.get("amount", "")
                    if prev_amount and amount and prev_amount != amount:
                        changes.append(Change(
                            type="budget",
                            description=f"Budget change for '{desc}': {prev_amount} -> {amount}",
                            significance=SIGNIFICANCE_HIGH,
                            details={
                                "description": desc,
                                "previous_amount": prev_amount,
                                "current_amount": amount,
                                "type": item.get("type", ""),
                            },
                        ))
                    break

            if not matched and amount:
                changes.append(Change(
                    type="budget",
                    description=f"New financial item: {desc} ({amount})",
                    significance=SIGNIFICANCE_MEDIUM,
                    details={
                        "description": desc,
                        "amount": amount,
                        "type": item.get("type", ""),
                        "vendor_or_recipient": item.get("vendor_or_recipient", ""),
                    },
                ))

        return changes

    def _diff_public_comments(self, before: dict, after: dict) -> list[Change]:
        changes = []
        comments_before = before.get("public_comments", [])
        comments_after = after.get("public_comments", [])

        speakers_before = {_normalize(c.get("speaker", "")) for c in comments_before if c.get("speaker")}

        for comment in comments_after:
            speaker = comment.get("speaker", "")
            topic = comment.get("topic", "")
            norm_speaker = _normalize(speaker)

            if norm_speaker and norm_speaker not in speakers_before:
                changes.append(Change(
                    type="speaker_new",
                    description=f"New public commenter: {speaker}" + (f" on '{topic}'" if topic else ""),
                    significance=SIGNIFICANCE_LOW,
                    details={
                        "speaker": speaker,
                        "topic": topic,
                        "summary": comment.get("summary", ""),
                    },
                ))

        return changes

    def _diff_appointments(self, before: dict, after: dict) -> list[Change]:
        changes = []
        appts_after = after.get("appointments", [])

        for appt in appts_after:
            person = appt.get("person", "")
            action = appt.get("action", "")
            body_role = appt.get("body_or_role", "")
            if person:
                changes.append(Change(
                    type="other",
                    description=f"Appointment: {person} {action} to {body_role}",
                    significance=SIGNIFICANCE_MEDIUM,
                    details={"person": person, "action": action, "body_or_role": body_role},
                ))

        return changes

    # ------------------------------------------------------------------
    # Natural-language summary
    # ------------------------------------------------------------------

    def _generate_whats_new(self, changes: list[Change], body: str,
                            date_before: str, date_after: str) -> str:
        """Generate a plain-English 'What's New' summary from the change list."""
        if not changes:
            return f"No significant changes detected between the {body} meetings on {date_before} and {date_after}."

        high = [c for c in changes if c.significance == SIGNIFICANCE_HIGH]
        medium = [c for c in changes if c.significance == SIGNIFICANCE_MEDIUM]
        low = [c for c in changes if c.significance == SIGNIFICANCE_LOW]

        parts = [f"Changes between {body} meetings ({date_before} to {date_after}):"]

        if high:
            parts.append("")
            parts.append(f"Key developments ({len(high)}):")
            for c in high:
                parts.append(f"  - {c.description}")

        if medium:
            parts.append("")
            parts.append(f"Notable updates ({len(medium)}):")
            for c in medium:
                parts.append(f"  - {c.description}")

        if low:
            parts.append("")
            parts.append(f"Minor changes ({len(low)}):")
            for c in low[:5]:  # Cap at 5 for readability
                parts.append(f"  - {c.description}")
            if len(low) > 5:
                parts.append(f"  ... and {len(low) - 5} more")

        stats = {
            "high": len(high),
            "medium": len(medium),
            "low": len(low),
            "total": len(changes),
        }
        parts.append("")
        parts.append(f"Total: {stats['total']} changes ({stats['high']} high, {stats['medium']} medium, {stats['low']} low priority)")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Module-level init helper
# ---------------------------------------------------------------------------

_engine: Optional[MeetingDiffEngine] = None


def init_diff(output_dir: str) -> MeetingDiffEngine:
    global _engine
    _engine = MeetingDiffEngine(output_dir)
    return _engine


def get_diff_engine() -> MeetingDiffEngine:
    if _engine is None:
        raise RuntimeError("Diff engine not initialized -- call init_diff() first")
    return _engine
