"""Public meeting calendar system with schedule detection and iCal generation."""

import hashlib
import json
import logging
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ORDINAL_NAMES = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}

# Default meeting time when not known (used for iCal events)
DEFAULT_MEETING_TIME = "18:00"
DEFAULT_MEETING_DURATION_HOURS = 2

# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    clip_id       TEXT PRIMARY KEY,
    date          TEXT NOT NULL,
    meeting_body  TEXT,
    title         TEXT,
    url           TEXT,
    time          TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_meetings_date ON meetings(date);
CREATE INDEX IF NOT EXISTS idx_meetings_body ON meetings(meeting_body);

CREATE TABLE IF NOT EXISTS schedule_profiles (
    meeting_body  TEXT PRIMARY KEY,
    weekday       INTEGER,
    ordinal_weeks TEXT,
    frequency     TEXT,
    typical_time  TEXT,
    confidence    REAL,
    description   TEXT,
    updated_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscribers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL,
    meeting_body  TEXT,
    days_before   INTEGER DEFAULT 1,
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(email, meeting_body)
);

CREATE TABLE IF NOT EXISTS predicted_meetings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,
    meeting_body  TEXT NOT NULL,
    source        TEXT DEFAULT 'predicted',
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(date, meeting_body)
);
"""


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_calendar_instance: Optional["MeetingCalendar"] = None


def init_calendar(output_dir: str) -> "MeetingCalendar":
    global _calendar_instance
    _calendar_instance = MeetingCalendar(output_dir)
    return _calendar_instance


def get_calendar() -> "MeetingCalendar":
    if _calendar_instance is None:
        raise RuntimeError("Calendar not initialized. Call init_calendar() first.")
    return _calendar_instance


# ---------------------------------------------------------------------------
# MeetingCalendar
# ---------------------------------------------------------------------------

class MeetingCalendar:
    """Manages meeting calendar with schedule detection and iCal generation."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.db_path = os.path.join(output_dir, "calendar.db")
        os.makedirs(output_dir, exist_ok=True)
        self._init_db()

    # -- DB setup ----------------------------------------------------------

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- Ingest from metadata ----------------------------------------------

    def ingest_from_clips(self, clips_dir: Optional[str] = None):
        """Scan all metadata.json files and load meetings into the database."""
        if clips_dir is None:
            clips_dir = os.path.join(self.output_dir, "clips")
        if not os.path.isdir(clips_dir):
            logger.warning("Clips directory not found: %s", clips_dir)
            return 0

        count = 0
        with self._conn() as conn:
            for clip_dir_name in os.listdir(clips_dir):
                meta_path = os.path.join(clips_dir, clip_dir_name, "metadata.json")
                if not os.path.isfile(meta_path):
                    continue
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)

                    clip_id = str(meta.get("clip_id", clip_dir_name))
                    date = meta.get("date")
                    if not date:
                        continue

                    meeting_body = meta.get("meeting_body")
                    title = meta.get("title", "")
                    url = meta.get("url", "")

                    # Try to extract time from extracted_facts.json
                    meeting_time = None
                    facts_path = os.path.join(clips_dir, clip_dir_name, "extracted_facts.json")
                    if os.path.isfile(facts_path):
                        try:
                            with open(facts_path) as f:
                                facts = json.load(f)
                            meeting_time = facts.get("meeting_info", {}).get("time")
                        except (json.JSONDecodeError, KeyError):
                            pass

                    conn.execute(
                        """INSERT OR REPLACE INTO meetings
                           (clip_id, date, meeting_body, title, url, time)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (clip_id, date, meeting_body, title, url, meeting_time),
                    )
                    count += 1
                except (json.JSONDecodeError, OSError) as e:
                    logger.debug("Skipping %s: %s", clip_dir_name, e)

        logger.info("Ingested %d meetings into calendar DB", count)
        return count

    # -- Query meetings ----------------------------------------------------

    def get_meetings(
        self,
        meeting_body: Optional[str] = None,
        date_after: Optional[str] = None,
        date_before: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return historical meetings, optionally filtered."""
        query = "SELECT * FROM meetings WHERE 1=1"
        params = []

        if meeting_body:
            query += " AND meeting_body = ?"
            params.append(meeting_body)
        if date_after:
            query += " AND date >= ?"
            params.append(date_after)
        if date_before:
            query += " AND date <= ?"
            params.append(date_before)

        query += " ORDER BY date DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()

        return [dict(r) for r in rows]

    def get_meeting_bodies(self) -> list[dict]:
        """Return all distinct meeting bodies with counts."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT meeting_body, COUNT(*) as count, MIN(date) as first_date, MAX(date) as last_date
                   FROM meetings
                   WHERE meeting_body IS NOT NULL
                   GROUP BY meeting_body
                   ORDER BY count DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    # -- Schedule detection ------------------------------------------------

    def detect_schedules(self) -> list[dict]:
        """Analyze meeting dates to detect recurring patterns per body."""
        bodies = self.get_meeting_bodies()
        profiles = []

        with self._conn() as conn:
            for body_info in bodies:
                body = body_info["meeting_body"]
                if not body:
                    continue

                rows = conn.execute(
                    "SELECT date FROM meetings WHERE meeting_body = ? ORDER BY date",
                    (body,),
                ).fetchall()

                dates = []
                for r in rows:
                    try:
                        dates.append(datetime.strptime(r["date"], "%Y-%m-%d"))
                    except (ValueError, TypeError):
                        continue

                if len(dates) < 3:
                    continue

                profile = self._analyze_schedule(body, dates)
                if profile:
                    conn.execute(
                        """INSERT OR REPLACE INTO schedule_profiles
                           (meeting_body, weekday, ordinal_weeks, frequency,
                            typical_time, confidence, description, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                        (
                            body,
                            profile["weekday"],
                            json.dumps(profile["ordinal_weeks"]),
                            profile["frequency"],
                            profile["typical_time"],
                            profile["confidence"],
                            profile["description"],
                        ),
                    )
                    profiles.append(profile)

        return profiles

    def _analyze_schedule(self, body: str, dates: list[datetime]) -> Optional[dict]:
        """Determine the recurring schedule pattern for a meeting body."""
        if len(dates) < 3:
            return None

        # Count weekdays
        weekday_counts = Counter(d.weekday() for d in dates)
        dominant_weekday, dominant_count = weekday_counts.most_common(1)[0]
        weekday_confidence = dominant_count / len(dates)

        # Filter to dominant weekday only
        weekday_dates = [d for d in dates if d.weekday() == dominant_weekday]

        # Determine which weeks of month the meetings fall on
        week_ordinals = Counter()
        for d in weekday_dates:
            ordinal = (d.day - 1) // 7 + 1
            week_ordinals[ordinal] += 1

        # Determine frequency pattern
        total_weekday = len(weekday_dates)
        ordinal_list = sorted(week_ordinals.keys())

        # Check for specific week patterns (e.g., 1st and 3rd, 2nd and 4th)
        frequency = "irregular"
        description_parts = []
        confident_ordinals = []

        for ordinal, count in week_ordinals.most_common():
            ratio = count / total_weekday if total_weekday else 0
            if ratio > 0.2:
                confident_ordinals.append(ordinal)

        confident_ordinals.sort()

        if not confident_ordinals:
            return None

        weekday_name = WEEKDAY_NAMES[dominant_weekday]

        # Try to extract typical meeting time from DB
        typical_time = self._get_typical_time(body)

        if len(confident_ordinals) == 1:
            frequency = "monthly"
            ordinal_word = ORDINAL_NAMES.get(confident_ordinals[0], str(confident_ordinals[0]))
            description = f"{body} meets the {ordinal_word} {weekday_name} of each month"
        elif len(confident_ordinals) == 2:
            frequency = "bimonthly"
            words = [ORDINAL_NAMES.get(o, str(o)) for o in confident_ordinals]
            description = f"{body} meets the {words[0]} and {words[1]} {weekday_name} of each month"
        elif len(confident_ordinals) >= 3:
            frequency = "weekly"
            description = f"{body} meets every {weekday_name}"
        else:
            description = f"{body} meets on {weekday_name}s"

        if typical_time:
            description += f" at {typical_time}"

        confidence = weekday_confidence * (dominant_count / max(len(dates), 1))
        confidence = min(confidence, 1.0)

        return {
            "meeting_body": body,
            "weekday": dominant_weekday,
            "weekday_name": weekday_name,
            "ordinal_weeks": confident_ordinals,
            "frequency": frequency,
            "typical_time": typical_time or DEFAULT_MEETING_TIME,
            "confidence": round(confidence, 3),
            "description": description,
            "meeting_count": len(dates),
        }

    def _get_typical_time(self, meeting_body: str) -> Optional[str]:
        """Get the most common meeting time for a body from stored data."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT time FROM meetings WHERE meeting_body = ? AND time IS NOT NULL",
                (meeting_body,),
            ).fetchall()

        if not rows:
            return None

        times = [r["time"] for r in rows if r["time"]]
        if not times:
            return None

        return Counter(times).most_common(1)[0][0]

    def get_schedule_profiles(self) -> list[dict]:
        """Return stored schedule profiles."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM schedule_profiles ORDER BY meeting_body").fetchall()

        results = []
        for r in rows:
            d = dict(r)
            d["ordinal_weeks"] = json.loads(d["ordinal_weeks"]) if d["ordinal_weeks"] else []
            d["weekday_name"] = WEEKDAY_NAMES[d["weekday"]] if d["weekday"] is not None else None
            results.append(d)
        return results

    # -- Predict upcoming meetings -----------------------------------------

    def predict_upcoming(self, months_ahead: int = 3) -> list[dict]:
        """Predict future meetings based on detected schedule patterns."""
        profiles = self.get_schedule_profiles()
        if not profiles:
            profiles = self.detect_schedules()

        predictions = []
        today = datetime.now().date()
        end_date = today + timedelta(days=months_ahead * 30)

        with self._conn() as conn:
            for profile in profiles:
                weekday = profile["weekday"]
                ordinals = profile.get("ordinal_weeks", [])
                if isinstance(ordinals, str):
                    ordinals = json.loads(ordinals)
                body = profile["meeting_body"]
                typical_time = profile.get("typical_time", DEFAULT_MEETING_TIME)

                # Generate dates
                current = today.replace(day=1)
                while current <= end_date:
                    year, month = current.year, current.month

                    for ordinal in ordinals:
                        predicted_date = self._nth_weekday(year, month, weekday, ordinal)
                        if predicted_date and today <= predicted_date <= end_date:
                            date_str = predicted_date.strftime("%Y-%m-%d")

                            # Check if a real meeting already exists on this date
                            existing = conn.execute(
                                "SELECT 1 FROM meetings WHERE date = ? AND meeting_body = ?",
                                (date_str, body),
                            ).fetchone()

                            predictions.append({
                                "date": date_str,
                                "meeting_body": body,
                                "time": typical_time,
                                "source": "historical" if existing else "predicted",
                                "confidence": profile.get("confidence", 0),
                            })

                            # Store prediction
                            if not existing:
                                try:
                                    conn.execute(
                                        """INSERT OR IGNORE INTO predicted_meetings
                                           (date, meeting_body, source)
                                           VALUES (?, ?, 'predicted')""",
                                        (date_str, body),
                                    )
                                except sqlite3.IntegrityError:
                                    pass

                    # Next month
                    if month == 12:
                        current = current.replace(year=year + 1, month=1)
                    else:
                        current = current.replace(month=month + 1)

        predictions.sort(key=lambda x: x["date"])
        return predictions

    @staticmethod
    def _nth_weekday(year: int, month: int, weekday: int, n: int) -> Optional[datetime]:
        """Get the nth occurrence of a weekday in a given month.

        weekday: 0=Monday, 6=Sunday
        n: 1-based (1=first, 2=second, etc.)
        """
        import calendar as cal

        first_day, num_days = cal.monthrange(year, month)
        # Find the first occurrence of the target weekday
        first_occurrence = 1 + (weekday - first_day) % 7
        # Calculate nth occurrence
        target_day = first_occurrence + (n - 1) * 7

        if target_day > num_days:
            return None

        try:
            return datetime(year, month, target_day).date()
        except ValueError:
            return None

    # -- Combined calendar view --------------------------------------------

    def get_calendar_view(
        self,
        meeting_body: Optional[str] = None,
        months_back: int = 1,
        months_ahead: int = 3,
    ) -> dict:
        """Return combined view of past and upcoming meetings."""
        today = datetime.now().date()
        date_after = (today - timedelta(days=months_back * 30)).strftime("%Y-%m-%d")
        date_before = (today + timedelta(days=months_ahead * 30)).strftime("%Y-%m-%d")

        # Historical meetings
        historical = self.get_meetings(
            meeting_body=meeting_body,
            date_after=date_after,
            date_before=today.strftime("%Y-%m-%d"),
            limit=500,
        )
        for m in historical:
            m["source"] = "historical"

        # Predicted upcoming
        predicted = self.predict_upcoming(months_ahead=months_ahead)
        if meeting_body:
            predicted = [p for p in predicted if p["meeting_body"] == meeting_body]

        # Merge: historical past + predicted future
        all_meetings = historical + predicted
        all_meetings.sort(key=lambda x: x["date"])

        # Group by date for calendar display
        by_date = defaultdict(list)
        for m in all_meetings:
            by_date[m["date"]].append(m)

        return {
            "meetings": all_meetings,
            "by_date": dict(by_date),
            "today": today.strftime("%Y-%m-%d"),
            "range_start": date_after,
            "range_end": date_before,
            "bodies": [b["meeting_body"] for b in self.get_meeting_bodies()],
        }

    # -- iCalendar generation ----------------------------------------------

    def generate_ical(self, meeting_body: Optional[str] = None) -> str:
        """Generate an iCalendar (.ics) feed."""
        # Get historical + predicted meetings
        historical = self.get_meetings(meeting_body=meeting_body, limit=500)
        predicted = self.predict_upcoming(months_ahead=6)
        if meeting_body:
            predicted = [p for p in predicted if p["meeting_body"] == meeting_body]

        cal_lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//CivicLens//Meeting Calendar//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            f"X-WR-CALNAME:CivicLens Meeting Calendar{' - ' + meeting_body if meeting_body else ''}",
            "X-WR-TIMEZONE:America/Los_Angeles",
        ]

        seen = set()

        for meeting in historical + predicted:
            date_str = meeting.get("date")
            body = meeting.get("meeting_body") or "Meeting"
            if not date_str:
                continue

            key = f"{date_str}:{body}"
            if key in seen:
                continue
            seen.add(key)

            # Parse date
            try:
                meeting_date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue

            # Parse time
            time_str = meeting.get("time") or DEFAULT_MEETING_TIME
            hour, minute = self._parse_time(time_str)
            start_dt = meeting_date.replace(hour=hour, minute=minute)
            end_dt = start_dt + timedelta(hours=DEFAULT_MEETING_DURATION_HOURS)

            title = meeting.get("title") or f"{body} Meeting"
            url = meeting.get("url", "")
            source = meeting.get("source", "historical")
            uid = hashlib.md5(key.encode()).hexdigest()

            summary = title
            if source == "predicted":
                summary = f"[Predicted] {body} Meeting"

            description = f"Meeting Body: {body}"
            if url:
                description += f"\\nVideo: {url}"
            if source == "predicted":
                description += "\\nNote: This is a predicted meeting based on historical patterns."

            cal_lines.extend([
                "BEGIN:VEVENT",
                f"UID:{uid}@civiclens",
                f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%S')}",
                f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}",
                f"SUMMARY:{self._ical_escape(summary)}",
                f"DESCRIPTION:{self._ical_escape(description)}",
                f"CATEGORIES:{self._ical_escape(body)}",
            ])
            if url:
                cal_lines.append(f"URL:{url}")
            if source == "predicted":
                cal_lines.append("STATUS:TENTATIVE")
            else:
                cal_lines.append("STATUS:CONFIRMED")

            cal_lines.append("END:VEVENT")

        cal_lines.append("END:VCALENDAR")
        return "\r\n".join(cal_lines)

    @staticmethod
    def _parse_time(time_str: str) -> tuple[int, int]:
        """Parse a time string into (hour, minute). Handles various formats."""
        if not time_str:
            return 18, 0

        time_str = time_str.strip().upper()

        # Handle "6:00 PM", "6:30PM", "18:00", etc.
        match = re.match(r"(\d{1,2}):?(\d{2})?\s*(AM|PM)?", time_str)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            ampm = match.group(3)

            if ampm == "PM" and hour < 12:
                hour += 12
            elif ampm == "AM" and hour == 12:
                hour = 0

            return hour, minute

        return 18, 0

    @staticmethod
    def _ical_escape(text: str) -> str:
        """Escape special characters for iCalendar."""
        if not text:
            return ""
        text = text.replace("\\", "\\\\")
        text = text.replace(",", "\\,")
        text = text.replace(";", "\\;")
        # Preserve intentional \\n for iCal line breaks
        return text

    # -- Google Calendar URL -----------------------------------------------

    def google_calendar_url(self, meeting_body: str) -> str:
        """Generate a Google Calendar URL to add recurring meeting events."""
        profiles = self.get_schedule_profiles()
        profile = next((p for p in profiles if p["meeting_body"] == meeting_body), None)

        # If no profile, try detecting
        if not profile:
            self.detect_schedules()
            profiles = self.get_schedule_profiles()
            profile = next((p for p in profiles if p["meeting_body"] == meeting_body), None)

        # Get next predicted meeting for the body
        predicted = self.predict_upcoming(months_ahead=3)
        body_meetings = [p for p in predicted if p["meeting_body"] == meeting_body]

        if not body_meetings:
            # Fallback: create a generic event for next month
            today = datetime.now()
            params = {
                "action": "TEMPLATE",
                "text": f"{meeting_body} Meeting",
                "details": f"Regular {meeting_body} meeting",
            }
            return "https://calendar.google.com/calendar/render?" + urlencode(params)

        next_meeting = body_meetings[0]
        date_str = next_meeting["date"]
        time_str = next_meeting.get("time", DEFAULT_MEETING_TIME)
        hour, minute = self._parse_time(time_str)

        start_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=hour, minute=minute)
        end_dt = start_dt + timedelta(hours=DEFAULT_MEETING_DURATION_HOURS)

        # Build recurrence rule
        recur = ""
        if profile:
            weekday_abbr = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
            wd = weekday_abbr[profile["weekday"]]
            ordinals = profile.get("ordinal_weeks", [])
            if isinstance(ordinals, str):
                ordinals = json.loads(ordinals)

            if ordinals:
                byday = ",".join(f"{o}{wd}" for o in ordinals)
                recur = f"RRULE:FREQ=MONTHLY;BYDAY={byday}"

        # Google Calendar URL format
        dates = f"{start_dt.strftime('%Y%m%dT%H%M%S')}/{end_dt.strftime('%Y%m%dT%H%M%S')}"

        desc = f"Regular {meeting_body} meeting"
        if profile:
            desc = profile.get("description", desc)

        params = {
            "action": "TEMPLATE",
            "text": f"{meeting_body} Meeting",
            "dates": dates,
            "details": desc,
        }
        if recur:
            params["recur"] = recur

        return "https://calendar.google.com/calendar/render?" + urlencode(params)

    # -- Subscriber management ---------------------------------------------

    def subscribe(self, email: str, meeting_body: Optional[str] = None, days_before: int = 1) -> dict:
        """Subscribe an email to meeting reminders."""
        with self._conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO subscribers (email, meeting_body, days_before)
                       VALUES (?, ?, ?)""",
                    (email, meeting_body, days_before),
                )
                return {"status": "subscribed", "email": email, "meeting_body": meeting_body or "all"}
            except sqlite3.IntegrityError:
                # Already subscribed, update days_before
                conn.execute(
                    """UPDATE subscribers SET days_before = ?
                       WHERE email = ? AND meeting_body IS ?""",
                    (days_before, email, meeting_body),
                )
                return {"status": "updated", "email": email, "meeting_body": meeting_body or "all"}

    def unsubscribe(self, email: str, meeting_body: Optional[str] = None) -> dict:
        """Remove a subscription."""
        with self._conn() as conn:
            if meeting_body:
                conn.execute(
                    "DELETE FROM subscribers WHERE email = ? AND meeting_body = ?",
                    (email, meeting_body),
                )
            else:
                conn.execute("DELETE FROM subscribers WHERE email = ?", (email,))
        return {"status": "unsubscribed", "email": email}

    def get_pending_reminders(self) -> list[dict]:
        """Get subscribers who should be reminded about upcoming meetings."""
        predicted = self.predict_upcoming(months_ahead=1)
        today = datetime.now().date()
        reminders = []

        with self._conn() as conn:
            subscribers = conn.execute("SELECT * FROM subscribers").fetchall()

            for sub in subscribers:
                sub_body = sub["meeting_body"]
                days_before = sub["days_before"] or 1

                for meeting in predicted:
                    try:
                        meeting_date = datetime.strptime(meeting["date"], "%Y-%m-%d").date()
                    except ValueError:
                        continue

                    delta = (meeting_date - today).days
                    if delta != days_before:
                        continue

                    if sub_body and sub_body != meeting["meeting_body"]:
                        continue

                    reminders.append({
                        "email": sub["email"],
                        "meeting_body": meeting["meeting_body"],
                        "meeting_date": meeting["date"],
                        "meeting_time": meeting.get("time", DEFAULT_MEETING_TIME),
                        "days_until": delta,
                    })

        return reminders
