"""PDF-ready report generation system for CivicLens.

Generates print-optimized HTML reports that render beautifully when
printed via the browser's Print dialog or saved as PDF. Each report
uses tenant branding (colors, logo, display name) and includes
proper @media print CSS for clean page breaks and formatting.

Report types:
- Meeting Summary: single meeting with all extracted facts
- Council Member Report Card: voting record, attendance, positions
- Financial Summary: all financial items for a date range
- Policy Tracking: alert matches and vote outcomes for tracked topics
- Monthly/Quarterly Digest: summary of all meetings in a period
"""

import json
import logging
import os
from datetime import datetime, timezone
from html import escape
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CSS for print-optimized reports
# ---------------------------------------------------------------------------

REPORT_CSS = """
/* Reset */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  color: #1a1a2e;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  background: #fff;
}

.report {
  max-width: 900px;
  margin: 0 auto;
  padding: 40px 48px;
}

/* Branded header */
.report-header {
  border-bottom: 3px solid var(--brand-primary, #1a56db);
  padding-bottom: 20px;
  margin-bottom: 32px;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.report-header-left h1 {
  font-size: 24px;
  font-weight: 700;
  color: var(--brand-secondary, #1e293b);
  margin-bottom: 4px;
}

.report-header-left .report-subtitle {
  font-size: 14px;
  color: #6b7280;
}

.report-header-right {
  text-align: right;
}

.report-header-right .brand-name {
  font-size: 16px;
  font-weight: 600;
  color: var(--brand-primary, #1a56db);
}

.report-header-right .report-meta {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 2px;
}

.report-logo {
  height: 36px;
  margin-bottom: 6px;
}

/* Section headings */
.report h2 {
  font-size: 18px;
  font-weight: 700;
  color: var(--brand-secondary, #1e293b);
  margin: 28px 0 12px;
  padding-bottom: 6px;
  border-bottom: 1px solid #e5e5ea;
}

.report h3 {
  font-size: 15px;
  font-weight: 600;
  color: #374151;
  margin: 20px 0 8px;
}

/* Tables */
.report table {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0 20px;
  font-size: 13px;
}

.report thead th {
  background: #f8f9fa;
  color: #374151;
  font-weight: 600;
  text-align: left;
  padding: 8px 12px;
  border-bottom: 2px solid #e5e5ea;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.report tbody td {
  padding: 8px 12px;
  border-bottom: 1px solid #f0f0f5;
  vertical-align: top;
}

.report tbody tr:last-child td {
  border-bottom: none;
}

/* Badges */
.badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.badge-passed { background: #dcfce7; color: #166534; }
.badge-failed { background: #fef2f2; color: #991b1b; }
.badge-tabled { background: #fef9c3; color: #854d0e; }

/* Stat cards */
.stat-row {
  display: flex;
  gap: 16px;
  margin: 16px 0 24px;
}

.stat-card {
  flex: 1;
  background: #f8f9fa;
  border-radius: 8px;
  padding: 16px;
  text-align: center;
  border: 1px solid #e5e5ea;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--brand-primary, #1a56db);
}

.stat-label {
  font-size: 11px;
  color: #6b7280;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-top: 2px;
}

/* Lists */
.report ul {
  margin: 8px 0 16px 20px;
  font-size: 13px;
}

.report li {
  margin-bottom: 6px;
}

/* Amount highlight */
.amount {
  font-weight: 600;
  color: #111827;
  white-space: nowrap;
}

/* Timestamp */
.timestamp {
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 11px;
  color: #6b7280;
  background: #f3f4f6;
  padding: 1px 6px;
  border-radius: 4px;
}

/* Footer */
.report-footer {
  margin-top: 40px;
  padding-top: 16px;
  border-top: 1px solid #e5e5ea;
  font-size: 11px;
  color: #9ca3af;
  display: flex;
  justify-content: space-between;
}

/* Citation note */
.data-source {
  background: #eff6ff;
  border-left: 3px solid var(--brand-primary, #1a56db);
  padding: 10px 14px;
  font-size: 12px;
  color: #374151;
  margin: 16px 0;
  border-radius: 0 6px 6px 0;
}

/* Empty state */
.empty-note {
  color: #9ca3af;
  font-style: italic;
  font-size: 13px;
  padding: 12px 0;
}

/* --- Print styles --- */
@media print {
  body { background: #fff; }

  .report {
    max-width: 100%;
    padding: 0;
    margin: 0;
  }

  .report-header {
    border-bottom-color: #333 !important;
  }

  .stat-card {
    border: 1px solid #ccc;
    background: #f9f9f9 !important;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }

  .badge {
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }

  table { page-break-inside: auto; }
  tr { page-break-inside: avoid; }
  thead { display: table-header-group; }

  h2, h3 { page-break-after: avoid; }

  .report-footer {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    padding: 8px 48px;
    background: #fff;
    border-top: 1px solid #ddd;
  }

  @page {
    margin: 0.75in;
    size: letter;
  }
}
"""


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _esc(value) -> str:
    """Escape a value for HTML. Returns empty string for None."""
    if value is None:
        return ""
    return escape(str(value))


def _outcome_badge(outcome: str) -> str:
    outcome_lower = (outcome or "").lower()
    if outcome_lower == "passed":
        cls = "badge-passed"
    elif outcome_lower == "failed":
        cls = "badge-failed"
    else:
        cls = "badge-tabled"
    return f'<span class="badge {cls}">{_esc(outcome or "Unknown")}</span>'


def _format_amount(amount_str: str) -> str:
    if not amount_str:
        return "N/A"
    return f'<span class="amount">{_esc(amount_str)}</span>'


def _wrap_html(title: str, body: str, branding: dict, report_type: str) -> str:
    """Wrap report body in full HTML document with branding and print CSS."""
    primary = _esc(branding.get("primary_color", "#1a56db"))
    secondary = _esc(branding.get("secondary_color", "#1e293b"))
    accent = _esc(branding.get("accent_color", "#f59e0b"))
    display_name = _esc(branding.get("display_name", "CivicLens"))
    logo_url = _esc(branding.get("logo_url", ""))

    now = datetime.now(timezone.utc).strftime("%B %d, %Y at %I:%M %p UTC")

    logo_html = ""
    if logo_url:
        logo_html = f'<img src="{logo_url}" alt="{display_name}" class="report-logo">'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)} - {display_name}</title>
<style>
:root {{
  --brand-primary: {primary};
  --brand-secondary: {secondary};
  --brand-accent: {accent};
}}
{REPORT_CSS}
</style>
</head>
<body>
<div class="report">

  <div class="report-header">
    <div class="report-header-left">
      <h1>{_esc(title)}</h1>
      <div class="report-subtitle">{_esc(report_type)}</div>
    </div>
    <div class="report-header-right">
      {logo_html}
      <div class="brand-name">{display_name}</div>
      <div class="report-meta">Generated {now}</div>
    </div>
  </div>

  {body}

  <div class="report-footer">
    <span>Data source: {display_name} Meeting Archive</span>
    <span>Generated {now}</span>
  </div>

</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# ReportGenerator
# ---------------------------------------------------------------------------

class ReportGenerator:
    """Generates print-ready HTML reports from meeting data.

    Uses tenant branding for colors/logo and produces HTML optimized
    for browser Print / Save-as-PDF via @media print CSS.
    """

    def __init__(self, output_dir: str):
        self._output_dir = output_dir
        self._clips_dir = os.path.join(output_dir, "clips")

    # ------------------------------------------------------------------
    # Data loading helpers
    # ------------------------------------------------------------------

    def _load_clip_facts(self, clip_id: str) -> Optional[dict]:
        path = os.path.join(self._clips_dir, str(clip_id), "extracted_facts.json")
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            return json.load(f)

    def _load_clip_metadata(self, clip_id: str) -> Optional[dict]:
        path = os.path.join(self._clips_dir, str(clip_id), "metadata.json")
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            return json.load(f)

    def _load_clip_summary(self, clip_id: str) -> str:
        path = os.path.join(self._clips_dir, str(clip_id), "summary.txt")
        if not os.path.isfile(path):
            return ""
        with open(path) as f:
            return f.read()

    def _all_clip_ids(self) -> list[str]:
        if not os.path.isdir(self._clips_dir):
            return []
        return sorted(os.listdir(self._clips_dir))

    def _load_clips_in_range(self, start: str, end: str) -> list[dict]:
        """Load all clips whose meeting date falls in [start, end]."""
        results = []
        for clip_id in self._all_clip_ids():
            meta = self._load_clip_metadata(clip_id)
            if not meta:
                continue
            date = meta.get("date", "")
            if date and start <= date <= end:
                facts = self._load_clip_facts(clip_id) or {}
                results.append({
                    "clip_id": clip_id,
                    "meta": meta,
                    "facts": facts,
                })
        results.sort(key=lambda c: c["meta"].get("date", ""), reverse=True)
        return results

    # ------------------------------------------------------------------
    # 1. Meeting Summary Report
    # ------------------------------------------------------------------

    def meeting_summary(self, clip_id: str, branding: dict) -> str:
        """Generate a full meeting summary report for a single clip."""
        facts = self._load_clip_facts(clip_id)
        meta = self._load_clip_metadata(clip_id)
        summary_text = self._load_clip_summary(clip_id)

        if not meta:
            return _wrap_html("Meeting Not Found", '<p class="empty-note">No data found for this meeting.</p>', branding, "Meeting Summary Report")

        meeting_info = (facts or {}).get("meeting_info", {})
        title = meta.get("title", f"Meeting {clip_id}")
        date = meta.get("date", "Unknown date")
        body_name = meeting_info.get("body", meta.get("meeting_body", ""))
        presiding = meeting_info.get("presiding_officer", "")
        location = meeting_info.get("location", "")

        # Meeting info header
        html = f"""
        <div class="data-source">
          Clip ID: {_esc(clip_id)} | Meeting Date: {_esc(date)}
          {f' | Body: {_esc(body_name)}' if body_name else ''}
          {f' | Presiding: {_esc(presiding)}' if presiding else ''}
          {f' | Location: {_esc(location)}' if location else ''}
        </div>
        """

        if not facts:
            html += '<p class="empty-note">No extracted facts available for this meeting.</p>'
            if summary_text:
                html += f"<h2>Summary</h2><div style='white-space:pre-wrap;font-size:13px;'>{_esc(summary_text)}</div>"
            return _wrap_html(title, html, branding, "Meeting Summary Report")

        # Attendance
        attendance = facts.get("attendance", {})
        present = attendance.get("present", [])
        absent = attendance.get("absent", [])
        late = attendance.get("late", [])

        if present or absent or late:
            html += "<h2>Attendance</h2>"
            html += '<div class="stat-row">'
            html += f'<div class="stat-card"><div class="stat-value">{len(present)}</div><div class="stat-label">Present</div></div>'
            html += f'<div class="stat-card"><div class="stat-value">{len(absent)}</div><div class="stat-label">Absent</div></div>'
            html += f'<div class="stat-card"><div class="stat-value">{len(late)}</div><div class="stat-label">Late</div></div>'
            html += "</div>"
            if present:
                html += f"<p style='font-size:13px;'><strong>Present:</strong> {_esc(', '.join(present))}</p>"
            if absent:
                html += f"<p style='font-size:13px;'><strong>Absent:</strong> {_esc(', '.join(absent))}</p>"
            if late:
                html += f"<p style='font-size:13px;'><strong>Late:</strong> {_esc(', '.join(late))}</p>"

        # Motions and Votes
        votes = facts.get("motions_and_votes", [])
        if votes:
            html += f"<h2>Motions &amp; Votes ({len(votes)})</h2>"
            html += """<table>
            <thead><tr>
              <th>Item</th><th>Motion By</th><th>Second</th>
              <th>Vote</th><th>Outcome</th>
            </tr></thead><tbody>"""
            for v in votes:
                identifier = v.get("identifier", "")
                desc = v.get("description", "")
                label = f"{identifier}: {desc}" if identifier else desc
                ayes = v.get("ayes", 0)
                nays = v.get("nays", 0)
                vote_str = f"{ayes}-{nays}"
                time_str = ""
                if v.get("transcript_approx_time"):
                    time_str = f' <span class="timestamp">{_esc(v["transcript_approx_time"])}</span>'
                html += f"""<tr>
                  <td>{_esc(label[:80])}{time_str}</td>
                  <td>{_esc(v.get('motion_by', ''))}</td>
                  <td>{_esc(v.get('second_by', ''))}</td>
                  <td>{vote_str}</td>
                  <td>{_outcome_badge(v.get('outcome', ''))}</td>
                </tr>"""
            html += "</tbody></table>"

            # Roll call details for contested votes
            contested = [v for v in votes if (v.get("nays") or 0) > 0]
            if contested:
                html += "<h3>Contested Vote Details</h3>"
                for v in contested:
                    identifier = v.get("identifier", v.get("description", "Unknown"))
                    html += f"<p style='font-size:13px;margin:8px 0 4px;'><strong>{_esc(identifier[:60])}</strong></p>"
                    vfor = v.get("votes_for", [])
                    vagainst = v.get("votes_against", [])
                    if vfor:
                        html += f"<p style='font-size:12px;color:#166534;'>Ayes: {_esc(', '.join(vfor))}</p>"
                    if vagainst:
                        html += f"<p style='font-size:12px;color:#991b1b;'>Nays: {_esc(', '.join(vagainst))}</p>"

        # Financial Items
        financial = facts.get("financial_items", [])
        if financial:
            html += f"<h2>Financial Items ({len(financial)})</h2>"
            html += """<table>
            <thead><tr>
              <th>Description</th><th>Type</th><th>Vendor/Recipient</th><th style="text-align:right">Amount</th>
            </tr></thead><tbody>"""
            for fi in financial:
                html += f"""<tr>
                  <td>{_esc(fi.get('description', ''))}</td>
                  <td>{_esc(fi.get('type', ''))}</td>
                  <td>{_esc(fi.get('vendor_or_recipient', ''))}</td>
                  <td style="text-align:right">{_format_amount(fi.get('amount', ''))}</td>
                </tr>"""
            html += "</tbody></table>"

        # Agenda Items
        agenda_items = facts.get("agenda_items", [])
        if agenda_items:
            html += f"<h2>Agenda Items ({len(agenda_items)})</h2>"
            html += """<table>
            <thead><tr>
              <th>Item</th><th>Type</th><th>Outcome</th>
            </tr></thead><tbody>"""
            for ai in agenda_items:
                identifier = ai.get("identifier", "")
                agenda_title = ai.get("title", "")
                label = f"{identifier}: {agenda_title}" if identifier else agenda_title
                time_str = ""
                if ai.get("transcript_approx_time"):
                    time_str = f' <span class="timestamp">{_esc(ai["transcript_approx_time"])}</span>'
                html += f"""<tr>
                  <td>{_esc(label)}{time_str}</td>
                  <td>{_esc(ai.get('type', ''))}</td>
                  <td>{_esc(ai.get('outcome', ''))}</td>
                </tr>"""
                if ai.get("summary"):
                    html += f'<tr><td colspan="3" style="font-size:12px;color:#6b7280;padding-top:0;">{_esc(ai["summary"])}</td></tr>'
            html += "</tbody></table>"

        # Public Comments
        comments = facts.get("public_comments", [])
        if comments:
            html += f"<h2>Public Comments ({len(comments)})</h2>"
            html += "<ul>"
            for c in comments:
                speaker = c.get("speaker", "Unknown")
                topic = c.get("topic", "")
                summary_c = c.get("summary", "")
                time_str = ""
                if c.get("transcript_approx_time"):
                    time_str = f' <span class="timestamp">{_esc(c["transcript_approx_time"])}</span>'
                html += f"<li><strong>{_esc(speaker)}</strong>"
                if topic:
                    html += f" on <em>{_esc(topic)}</em>"
                html += f"{time_str}: {_esc(summary_c)}</li>"
            html += "</ul>"

        # Appointments
        appointments = facts.get("appointments", [])
        if appointments:
            html += f"<h2>Appointments ({len(appointments)})</h2>"
            html += "<ul>"
            for a in appointments:
                if isinstance(a, dict):
                    html += f"<li>{_esc(a.get('name', ''))} - {_esc(a.get('position', ''))}</li>"
                else:
                    html += f"<li>{_esc(str(a))}</li>"
            html += "</ul>"

        # Contentious Items
        contentious = facts.get("contentious_items", [])
        if contentious:
            html += f"<h2>Contentious Items ({len(contentious)})</h2>"
            html += "<ul>"
            for ci in contentious:
                if isinstance(ci, dict):
                    html += f"<li>{_esc(ci.get('description', ci.get('title', str(ci))))}</li>"
                else:
                    html += f"<li>{_esc(str(ci))}</li>"
            html += "</ul>"

        return _wrap_html(title, html, branding, "Meeting Summary Report")

    # ------------------------------------------------------------------
    # 2. Council Member Report Card
    # ------------------------------------------------------------------

    def member_report_card(self, name: str, branding: dict,
                           start: str = "", end: str = "") -> str:
        """Generate a voting record and activity report card for a council member."""
        name_lower = name.lower()
        clips = self._all_clip_ids()

        all_votes = []
        attendance_present = 0
        attendance_absent = 0
        meetings_participated = set()
        financial_positions = []

        for clip_id in clips:
            meta = self._load_clip_metadata(clip_id)
            if not meta:
                continue
            date = meta.get("date", "")
            if start and date < start:
                continue
            if end and date > end:
                continue

            facts = self._load_clip_facts(clip_id)
            if not facts:
                continue

            # Attendance
            att = facts.get("attendance", {})
            present_list = [p.lower() for p in att.get("present", [])]
            absent_list = [p.lower() for p in att.get("absent", [])]
            if name_lower in present_list:
                attendance_present += 1
                meetings_participated.add(clip_id)
            if name_lower in absent_list:
                attendance_absent += 1

            # Votes
            for v in facts.get("motions_and_votes", []):
                vfor = [x.lower() for x in v.get("votes_for", [])]
                vagainst = [x.lower() for x in v.get("votes_against", [])]
                motion_by = (v.get("motion_by") or "").lower()
                second_by = (v.get("second_by") or "").lower()

                participated = (
                    name_lower in vfor
                    or name_lower in vagainst
                    or name_lower == motion_by
                    or name_lower == second_by
                )
                if participated:
                    role = []
                    if name_lower == motion_by:
                        role.append("Motioned")
                    if name_lower == second_by:
                        role.append("Seconded")
                    if name_lower in vfor:
                        role.append("Voted Yes")
                    if name_lower in vagainst:
                        role.append("Voted No")

                    all_votes.append({
                        "clip_id": clip_id,
                        "date": date,
                        "identifier": v.get("identifier", ""),
                        "description": v.get("description", ""),
                        "outcome": v.get("outcome", ""),
                        "role": ", ".join(role),
                        "ayes": v.get("ayes", 0),
                        "nays": v.get("nays", 0),
                    })
                    meetings_participated.add(clip_id)

            # Financial items where member is key speaker on related agenda items
            for ai in facts.get("agenda_items", []):
                speakers = [s.lower() for s in ai.get("key_speakers", [])]
                if name_lower in speakers:
                    for fi in facts.get("financial_items", []):
                        if fi.get("identifier") and ai.get("identifier") and fi["identifier"] == ai["identifier"]:
                            financial_positions.append({
                                "date": date,
                                "description": fi.get("description", ""),
                                "amount": fi.get("amount", ""),
                                "clip_id": clip_id,
                            })

        # Calculate stats
        total_votes = len(all_votes)
        voted_yes = sum(1 for v in all_votes if "Voted Yes" in v["role"])
        voted_no = sum(1 for v in all_votes if "Voted No" in v["role"])
        motioned = sum(1 for v in all_votes if "Motioned" in v["role"])
        seconded = sum(1 for v in all_votes if "Seconded" in v["role"])
        total_meetings = attendance_present + attendance_absent
        attend_pct = round(attendance_present / total_meetings * 100, 1) if total_meetings > 0 else 0

        date_range = ""
        if start or end:
            date_range = f"{start or 'Start'} to {end or 'Present'}"

        title = f"Report Card: {name}"
        html = ""

        if date_range:
            html += f'<div class="data-source">Period: {_esc(date_range)}</div>'

        # Stats
        html += '<div class="stat-row">'
        html += f'<div class="stat-card"><div class="stat-value">{attend_pct}%</div><div class="stat-label">Attendance</div></div>'
        html += f'<div class="stat-card"><div class="stat-value">{total_votes}</div><div class="stat-label">Total Votes</div></div>'
        html += f'<div class="stat-card"><div class="stat-value">{motioned}</div><div class="stat-label">Motions Made</div></div>'
        html += f'<div class="stat-card"><div class="stat-value">{len(meetings_participated)}</div><div class="stat-label">Meetings</div></div>'
        html += "</div>"

        # Attendance
        html += "<h2>Attendance Record</h2>"
        html += f"<p style='font-size:13px;'>Present: <strong>{attendance_present}</strong> | Absent: <strong>{attendance_absent}</strong> | Rate: <strong>{attend_pct}%</strong></p>"

        # Voting summary
        html += "<h2>Voting Summary</h2>"
        html += f"""<p style='font-size:13px;'>
          Voted Yes: <strong>{voted_yes}</strong> |
          Voted No: <strong>{voted_no}</strong> |
          Motioned: <strong>{motioned}</strong> |
          Seconded: <strong>{seconded}</strong>
        </p>"""

        # Detailed vote table
        if all_votes:
            html += "<h2>Voting Record</h2>"
            html += """<table>
            <thead><tr>
              <th>Date</th><th>Item</th><th>Role</th><th>Vote</th><th>Outcome</th>
            </tr></thead><tbody>"""
            for v in sorted(all_votes, key=lambda x: x["date"], reverse=True):
                identifier = v["identifier"]
                desc = v["description"]
                label = f"{identifier}: {desc}" if identifier else desc
                html += f"""<tr>
                  <td>{_esc(v['date'])}</td>
                  <td>{_esc(label[:60])}</td>
                  <td>{_esc(v['role'])}</td>
                  <td>{v['ayes']}-{v['nays']}</td>
                  <td>{_outcome_badge(v['outcome'])}</td>
                </tr>"""
            html += "</tbody></table>"
        else:
            html += '<p class="empty-note">No voting records found for this member in the selected period.</p>'

        # Financial positions
        if financial_positions:
            html += "<h2>Related Financial Items</h2>"
            html += """<table>
            <thead><tr><th>Date</th><th>Description</th><th style="text-align:right">Amount</th></tr></thead><tbody>"""
            for fp in financial_positions:
                html += f"""<tr>
                  <td>{_esc(fp['date'])}</td>
                  <td>{_esc(fp['description'])}</td>
                  <td style="text-align:right">{_format_amount(fp['amount'])}</td>
                </tr>"""
            html += "</tbody></table>"

        return _wrap_html(title, html, branding, "Council Member Report Card")

    # ------------------------------------------------------------------
    # 3. Financial Summary Report
    # ------------------------------------------------------------------

    def financial_summary(self, branding: dict, start: str = "", end: str = "") -> str:
        """Generate a financial summary report for a date range."""
        clips = self._load_clips_in_range(start or "0000-01-01", end or "9999-12-31")

        all_items = []
        by_type: dict[str, list] = {}
        total_cents = 0

        for clip_data in clips:
            facts = clip_data["facts"]
            meta = clip_data["meta"]
            for fi in facts.get("financial_items", []):
                item = {
                    **fi,
                    "clip_id": clip_data["clip_id"],
                    "meeting_date": meta.get("date", ""),
                    "meeting_body": meta.get("meeting_body", ""),
                }
                all_items.append(item)

                fi_type = fi.get("type", "Other") or "Other"
                by_type.setdefault(fi_type, []).append(item)

                # Parse amount
                amount_str = fi.get("amount", "")
                if amount_str:
                    import re
                    cleaned = re.sub(r"[^\d.]", "", amount_str)
                    try:
                        total_cents += int(float(cleaned) * 100)
                    except (ValueError, TypeError):
                        pass

        date_range = f"{start or 'All time'} to {end or 'Present'}"
        title = "Financial Summary Report"

        html = f'<div class="data-source">Period: {_esc(date_range)} | Meetings analyzed: {len(clips)}</div>'

        # Summary stats
        total_display = f"${total_cents / 100:,.2f}" if total_cents else "$0.00"
        html += '<div class="stat-row">'
        html += f'<div class="stat-card"><div class="stat-value">{len(all_items)}</div><div class="stat-label">Total Items</div></div>'
        html += f'<div class="stat-card"><div class="stat-value">{total_display}</div><div class="stat-label">Total Amount</div></div>'
        html += f'<div class="stat-card"><div class="stat-value">{len(by_type)}</div><div class="stat-label">Categories</div></div>'
        html += f'<div class="stat-card"><div class="stat-value">{len(clips)}</div><div class="stat-label">Meetings</div></div>'
        html += "</div>"

        # By category
        if by_type:
            html += "<h2>Totals by Category</h2>"
            html += """<table>
            <thead><tr><th>Category</th><th>Items</th><th style="text-align:right">Total</th></tr></thead><tbody>"""
            for fi_type, items in sorted(by_type.items()):
                cat_cents = 0
                for it in items:
                    amount_str = it.get("amount", "")
                    if amount_str:
                        import re
                        cleaned = re.sub(r"[^\d.]", "", amount_str)
                        try:
                            cat_cents += int(float(cleaned) * 100)
                        except (ValueError, TypeError):
                            pass
                cat_display = f"${cat_cents / 100:,.2f}" if cat_cents else "N/A"
                html += f"""<tr>
                  <td>{_esc(fi_type.capitalize())}</td>
                  <td>{len(items)}</td>
                  <td style="text-align:right"><span class="amount">{cat_display}</span></td>
                </tr>"""
            html += "</tbody></table>"

        # All items detail table
        if all_items:
            html += f"<h2>All Financial Items ({len(all_items)})</h2>"
            html += """<table>
            <thead><tr>
              <th>Date</th><th>Description</th><th>Type</th><th>Vendor/Recipient</th><th style="text-align:right">Amount</th>
            </tr></thead><tbody>"""
            for it in sorted(all_items, key=lambda x: x.get("meeting_date", ""), reverse=True):
                html += f"""<tr>
                  <td style="white-space:nowrap">{_esc(it.get('meeting_date', ''))}</td>
                  <td>{_esc(it.get('description', ''))}</td>
                  <td>{_esc(it.get('type', ''))}</td>
                  <td>{_esc(it.get('vendor_or_recipient', ''))}</td>
                  <td style="text-align:right">{_format_amount(it.get('amount', ''))}</td>
                </tr>"""
            html += "</tbody></table>"
        else:
            html += '<p class="empty-note">No financial items found in the selected period.</p>'

        return _wrap_html(title, html, branding, "Financial Summary Report")

    # ------------------------------------------------------------------
    # 4. Policy Tracking Report
    # ------------------------------------------------------------------

    def policy_tracking(self, alert_ids: list[str], branding: dict,
                        tracker=None) -> str:
        """Generate a report of policy alert matches and outcomes.

        Requires a PolicyTracker instance to query alert matches.
        """
        if tracker is None:
            from api.tracker import get_tracker
            tracker = get_tracker()

        title = "Policy Tracking Report"
        html = ""

        if not alert_ids:
            html += '<p class="empty-note">No alert IDs specified.</p>'
            return _wrap_html(title, html, branding, "Policy Tracking Report")

        total_matches = 0

        for alert_id in alert_ids:
            # Try to fetch with a generic tenant; routes will filter by tenant
            # For the report we query directly
            conn = tracker._conn()
            try:
                alert_row = conn.execute(
                    "SELECT * FROM alerts WHERE id = ?", (alert_id,)
                ).fetchone()
                if not alert_row:
                    html += f'<p class="empty-note">Alert {_esc(alert_id)} not found.</p>'
                    continue

                alert_name = alert_row["name"]
                alert_type = alert_row["type"]
                config = json.loads(alert_row["config"])

                match_rows = conn.execute(
                    "SELECT * FROM alert_matches WHERE alert_id = ? ORDER BY created_at DESC LIMIT 100",
                    (alert_id,),
                ).fetchall()
            finally:
                conn.close()

            match_count = len(match_rows)
            total_matches += match_count

            html += f"<h2>{_esc(alert_name)}</h2>"
            html += f'<div class="data-source">Alert type: {_esc(alert_type)} | Matches: {match_count} | Config: {_esc(json.dumps(config))}</div>'

            if match_rows:
                html += """<table>
                <thead><tr><th>Date</th><th>Clip</th><th>Match</th><th>Details</th></tr></thead><tbody>"""
                for m in match_rows:
                    context = json.loads(m["context"]) if m["context"] else {}
                    meeting_date = context.get("meeting_date", "")
                    details = ""
                    if "vote" in context:
                        v = context["vote"]
                        details = f"{_esc(v.get('description', '')[:50])} {_outcome_badge(v.get('outcome', ''))}"
                    elif "item" in context:
                        details = _esc(context["item"].get("description", "")[:60])
                    elif "matched_sections" in context:
                        secs = context["matched_sections"]
                        details = f"{len(secs)} section(s) matched"

                    html += f"""<tr>
                      <td style="white-space:nowrap">{_esc(meeting_date)}</td>
                      <td>{_esc(m['clip_id'])}</td>
                      <td>{_esc(m['matched_text'][:40])}</td>
                      <td>{details}</td>
                    </tr>"""
                html += "</tbody></table>"
            else:
                html += '<p class="empty-note">No matches for this alert.</p>'

        # Summary stat at top
        summary_html = '<div class="stat-row">'
        summary_html += f'<div class="stat-card"><div class="stat-value">{len(alert_ids)}</div><div class="stat-label">Alerts Tracked</div></div>'
        summary_html += f'<div class="stat-card"><div class="stat-value">{total_matches}</div><div class="stat-label">Total Matches</div></div>'
        summary_html += "</div>"

        html = summary_html + html

        return _wrap_html(title, html, branding, "Policy Tracking Report")

    # ------------------------------------------------------------------
    # 5. Monthly / Quarterly Digest Report
    # ------------------------------------------------------------------

    def period_digest(self, period: str, month: str, branding: dict) -> str:
        """Generate a digest report for a month or quarter.

        Args:
            period: "monthly" or "quarterly"
            month: YYYY-MM format (for monthly, the month; for quarterly, the first month)
            branding: tenant branding config dict
        """
        try:
            year, mon = month.split("-")
            year, mon = int(year), int(mon)
        except (ValueError, AttributeError):
            return _wrap_html("Invalid Period", '<p class="empty-note">Invalid month format. Use YYYY-MM.</p>', branding, "Digest Report")

        if period == "quarterly":
            # Quarter starts at the given month, spans 3 months
            start = f"{year}-{mon:02d}-01"
            end_mon = mon + 2
            end_year = year
            if end_mon > 12:
                end_mon -= 12
                end_year += 1
            # Last day of end month
            if end_mon in (1, 3, 5, 7, 8, 10, 12):
                end_day = 31
            elif end_mon == 2:
                end_day = 29
            else:
                end_day = 30
            end = f"{end_year}-{end_mon:02d}-{end_day}"
            period_label = f"Q{((mon - 1) // 3) + 1} {year}"
        else:
            start = f"{year}-{mon:02d}-01"
            if mon in (1, 3, 5, 7, 8, 10, 12):
                end_day = 31
            elif mon == 2:
                end_day = 29
            else:
                end_day = 30
            end = f"{year}-{mon:02d}-{end_day}"
            from datetime import date as date_cls
            period_label = date_cls(year, mon, 1).strftime("%B %Y")

        clips = self._load_clips_in_range(start, end)
        title = f"{period.capitalize()} Digest: {period_label}"

        # Aggregate stats
        total_votes = 0
        total_financial = 0
        total_comments = 0
        total_financial_cents = 0
        bodies_seen: dict[str, int] = {}
        all_topics: dict[str, int] = {}

        for clip_data in clips:
            facts = clip_data["facts"]
            meta = clip_data["meta"]
            body = meta.get("meeting_body", "Other")
            bodies_seen[body] = bodies_seen.get(body, 0) + 1

            for t in meta.get("topics", []):
                all_topics[t] = all_topics.get(t, 0) + 1

            total_votes += len(facts.get("motions_and_votes", []))
            total_comments += len(facts.get("public_comments", []))

            for fi in facts.get("financial_items", []):
                total_financial += 1
                amount_str = fi.get("amount", "")
                if amount_str:
                    import re
                    cleaned = re.sub(r"[^\d.]", "", amount_str)
                    try:
                        total_financial_cents += int(float(cleaned) * 100)
                    except (ValueError, TypeError):
                        pass

        html = f'<div class="data-source">Period: {_esc(start)} to {_esc(end)}</div>'

        # Summary stats
        fin_display = f"${total_financial_cents / 100:,.2f}" if total_financial_cents else "$0.00"
        html += '<div class="stat-row">'
        html += f'<div class="stat-card"><div class="stat-value">{len(clips)}</div><div class="stat-label">Meetings</div></div>'
        html += f'<div class="stat-card"><div class="stat-value">{total_votes}</div><div class="stat-label">Votes</div></div>'
        html += f'<div class="stat-card"><div class="stat-value">{fin_display}</div><div class="stat-label">Financial Total</div></div>'
        html += f'<div class="stat-card"><div class="stat-value">{total_comments}</div><div class="stat-label">Public Comments</div></div>'
        html += "</div>"

        # By meeting body
        if bodies_seen:
            html += "<h2>Meetings by Body</h2>"
            html += "<table><thead><tr><th>Meeting Body</th><th>Count</th></tr></thead><tbody>"
            for body, count in sorted(bodies_seen.items(), key=lambda x: -x[1]):
                html += f"<tr><td>{_esc(body)}</td><td>{count}</td></tr>"
            html += "</tbody></table>"

        # Top topics
        if all_topics:
            html += "<h2>Top Topics</h2>"
            html += "<table><thead><tr><th>Topic</th><th>Appearances</th></tr></thead><tbody>"
            for topic, count in sorted(all_topics.items(), key=lambda x: -x[1])[:15]:
                html += f"<tr><td>{_esc(topic)}</td><td>{count}</td></tr>"
            html += "</tbody></table>"

        # Individual meeting summaries
        if clips:
            html += f"<h2>Meeting Summaries ({len(clips)})</h2>"
            for clip_data in clips:
                meta = clip_data["meta"]
                facts = clip_data["facts"]
                clip_title = meta.get("title", f"Clip {clip_data['clip_id']}")
                clip_date = meta.get("date", "")
                clip_body = meta.get("meeting_body", "")
                n_votes = len(facts.get("motions_and_votes", []))
                n_fin = len(facts.get("financial_items", []))
                topics = meta.get("topics", [])

                html += f"""<h3>{_esc(clip_date)} - {_esc(clip_title)}</h3>
                <p style="font-size:12px;color:#6b7280;margin-bottom:8px;">
                  {_esc(clip_body)} | {n_votes} votes | {n_fin} financial items
                  {f' | Topics: {_esc(", ".join(topics[:5]))}' if topics else ''}
                </p>"""

                # Key votes for this meeting
                contested = [v for v in facts.get("motions_and_votes", []) if (v.get("nays") or 0) > 0]
                if contested:
                    html += "<ul>"
                    for v in contested[:3]:
                        desc = v.get("description", v.get("identifier", ""))
                        html += f"<li style='font-size:12px;'>{_esc(desc[:80])} {_outcome_badge(v.get('outcome', ''))} ({v.get('ayes', 0)}-{v.get('nays', 0)})</li>"
                    html += "</ul>"
        else:
            html += '<p class="empty-note">No meetings found in this period.</p>'

        return _wrap_html(title, html, branding, f"{period.capitalize()} Digest Report")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_report_generator: Optional[ReportGenerator] = None


def init_reports(output_dir: str) -> ReportGenerator:
    """Initialize the global ReportGenerator singleton."""
    global _report_generator
    _report_generator = ReportGenerator(output_dir)
    return _report_generator


def get_report_generator() -> ReportGenerator:
    """Return the global ReportGenerator singleton."""
    if _report_generator is None:
        raise RuntimeError("ReportGenerator not initialized. Call init_reports() at startup.")
    return _report_generator
