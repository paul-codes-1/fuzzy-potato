"""Tests for table_of_motions.py — block extraction, motion parsing,
date handling, and clip resolution (with the ambiguity guard)."""

import pytest

from table_of_motions import (
    Motion,
    TableOfMotions,
    extract_tables,
    merge_table_into_facts,
    parse_date,
    parse_motion_line,
    resolve_target_clip,
)


# A trimmed but faithful Council Work Session table, including the packet
# header above the marker, the attendance preamble, unanimous + roll-call
# motions, an "(as amended)" condition, and the packet resuming afterward.
SAMPLE_AGENDA = """\
III. Approval of Summary - Yes
a. Table of Motions: Council Work Session, May 12, 2026, p. 1-2

1
URBAN COUNTY COUNCIL
WORK SESSION
TABLE OF MOTIONS
May 12, 2026
Mayor Gorton called the meeting to order at 3:01 p.m. Vice Mayor Wu and
Council Members Brown, Ellinger II, Morton, Hale, and Boone were present.
I. Public Comment - Issues on Agenda
II. Requested Rezonings/Docket Approval
Motion by Ellinger to approve the May 14, 2026 Council Meeting Docket.
Seconded by Wu. Motion passed without dissent.
III. Approval of Summary
Motion by Curtis to approve the May 5, 2026 Table of Motions. Seconded by
Baxter. Motion passed without dissent (as amended).
IV. Budget Amendments
Councilmember Sheehan provided a summary of the GGP Committee meeting.
Motion by Sheehan to send the text amendment to the Planning Commission.
Seconded by Lynch, the motion passed with a 11 - 4 vote (yes: Wu, Brown,
Morton, Lynch, Curtis, Sheehan, Higgins-Hord, Hale, Baxter, Sevigny; no:
Ellinger, Eblen, Reynolds, Boone).
XIII. Adjournment
Motion by Baxter to adjourn at 3:51 p.m. Seconded by Sevigny. Motion
passed without dissent.

3
BUDGET AMENDMENT REQUEST LIST
JOURNAL 163330-31 DIVISION Traffic Fund Name General Fund
"""

CLIPS = [
    {"clip_id": 6768, "date": "2026-05-12", "meeting_body": "Council",
     "title": "Council Work Session (1)"},
    {"clip_id": 6769, "date": "2026-05-12", "meeting_body": "Commission",
     "title": "Planning Commission Work Session (1)"},
    {"clip_id": 6779, "date": "2026-05-26", "meeting_body": "Council",
     "title": "Council Work Session (1)"},
]


@pytest.fixture
def table():
    tables = extract_tables(SAMPLE_AGENDA)
    assert len(tables) == 1
    return tables[0]


# --------------------------------------------------------------------------
# Date parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("May 12, 2026", "2026-05-12"),
    ("September 24th, 2013", "2013-09-24"),
    ("October 15Th, 2013", "2013-10-15"),
    ("April 7 2026", "2026-04-07"),
    ("no date here", None),
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


# --------------------------------------------------------------------------
# Block extraction
# --------------------------------------------------------------------------

def test_extract_one_block(table):
    assert table.body_class == "council_work_session"
    assert table.date == "2026-05-12"
    assert table.raw_date == "May 12, 2026"
    assert table.called_to_order == "3:01 p.m."


def test_attendance_includes_vice_mayor(table):
    # Vice Mayor Wu must survive the role-label stripping.
    assert "Wu" in table.attendees
    assert "Brown" in table.attendees
    assert "Ellinger II" in table.attendees
    # Role words are not names.
    assert not any(n.lower() in {"mayor", "vice mayor", "council members"}
                   for n in table.attendees)


def test_block_excludes_packet_items(table):
    # The "BUDGET AMENDMENT REQUEST LIST" / JOURNAL lines after the table
    # must not be parsed as motions or narrative.
    blob = " ".join(table.narrative)
    assert "JOURNAL" not in blob
    assert "BUDGET AMENDMENT" not in blob


def test_meeting_date_not_taken_from_motion_text(table):
    # A motion references "May 14, 2026"; the table date stays May 12.
    assert table.date == "2026-05-12"


def test_narrative_captured_separately(table):
    # The committee-report aside is narrative, not a motion.
    assert any("GGP Committee" in n for n in table.narrative)
    assert all(m.motion_by is not None for m in table.motions)


def test_two_blocks_in_one_agenda():
    doubled = SAMPLE_AGENDA + "\n" + SAMPLE_AGENDA.replace(
        "May 12, 2026", "May 26, 2026")
    assert len(extract_tables(doubled)) == 2


def test_control_bytes_stripped():
    dirty = SAMPLE_AGENDA.replace("Motion passed without dissent.",
                                  "Motion passed witho\x7fut dissent.")
    t = extract_tables(dirty)[0]
    assert t.motions[0].vote_type == "unanimous"


def test_empty_input():
    assert extract_tables("") == []
    assert extract_tables("no table here at all") == []


# --------------------------------------------------------------------------
# Motion parsing
# --------------------------------------------------------------------------

def test_unanimous_motion(table):
    m = table.motions[0]
    assert m.motion_by == "Ellinger"
    assert m.second_by == "Wu"
    assert m.outcome == "passed"
    assert m.vote_type == "unanimous"
    assert m.description == "approve the May 14, 2026 Council Meeting Docket"
    assert m.section.startswith("II.")


def test_condition_captured(table):
    m = [x for x in table.motions if x.motion_by == "Curtis"][0]
    assert m.conditions == "as amended"
    assert m.vote_type == "unanimous"


def test_roll_call_split_vote(table):
    m = [x for x in table.motions if x.motion_by == "Sheehan"][0]
    assert m.vote_type == "roll_call"
    assert m.outcome == "passed"
    # The printed tally is authoritative for the counts. This real example
    # (copied verbatim from the April-28 agenda) prints "11 - 4" but lists
    # only 10 yes-names — the source itself is inconsistent. We trust the
    # tally for ayes/nays and store the names exactly as printed.
    assert m.ayes == 11
    assert m.nays == 4
    assert len(m.votes_for) == 10
    assert len(m.votes_against) == 4
    assert "Ellinger" in m.votes_against
    assert "Wu" in m.votes_for


def test_ocr_without_dissent_typo():
    m = parse_motion_line(
        "Motion by Eblen to approve the budget. Seconded by Hale. "
        "Motion passed witho0ut dissent."
    )
    assert m.outcome == "passed"
    assert m.vote_type == "unanimous"


@pytest.mark.parametrize("text,outcome", [
    ("Motion by A to do X. Seconded by B. Motion failed.", "failed"),
    ("Motion by A to do X. Seconded by B. The motion was defeated.", "failed"),
    ("Motion by A to do X. Seconded by B. Motion tabled.", "tabled"),
    ("Motion by A to do X. Seconded by B. Motion carried.", "passed"),
])
def test_outcome_variants(text, outcome):
    assert parse_motion_line(text).outcome == outcome


def test_no_false_tally_from_identifier():
    # "Ordinance 0016-26" / "KRS 67A.921" must not be read as a vote tally.
    m = parse_motion_line(
        "Motion by Brown to adopt Ordinance 0016-26 pursuant to KRS 67A.921. "
        "Seconded by Wu. Motion passed without dissent."
    )
    assert m.ayes == 0 and m.nays == 0
    assert m.vote_type == "unanimous"


def test_to_fact_shape(table):
    fact = table.motions[0].to_fact()
    assert set(fact) >= {
        "identifier", "description", "motion_by", "second_by", "outcome",
        "vote_type", "ayes", "nays", "abstentions", "votes_for",
        "votes_against", "conditions", "transcript_approx_time",
    }
    assert fact["transcript_approx_time"] is None


# --------------------------------------------------------------------------
# Clip resolution
# --------------------------------------------------------------------------

def test_resolve_unambiguous(table):
    r = resolve_target_clip(table, CLIPS)
    assert r.status == "matched"
    assert r.clip_id == 6768  # not the Planning Commission session same day


def test_resolve_no_match(table):
    others = [c for c in CLIPS if c["date"] != "2026-05-12"]
    r = resolve_target_clip(table, others)
    assert r.status == "no_match"
    assert r.clip_id is None


def test_resolve_ambiguous_is_guarded(table):
    dupes = CLIPS + [{"clip_id": 9999, "date": "2026-05-12",
                      "meeting_body": "Council",
                      "title": "Council Work Session (1)"}]
    r = resolve_target_clip(table, dupes)
    assert r.status == "ambiguous"
    assert r.clip_id is None
    assert set(r.candidates) == {6768, 9999}


def test_resolve_no_date():
    t = TableOfMotions(body_header="X", body_class="council_work_session",
                       date=None, raw_date="", called_to_order=None,
                       attendees=[], motions=[], narrative=[])
    assert resolve_target_clip(t, CLIPS).status == "no_date"


# --------------------------------------------------------------------------
# Merge into extracted_facts.json
# --------------------------------------------------------------------------

def test_merge_replaces_motions(table):
    old = {
        "motions_and_votes": [
            {"description": "old garbled motion", "motion_by": "Savigny",
             "outcome": "passed", "transcript_approx_time": "10:00"},
        ],
        "attendance": {"present": []},
    }
    new, stats = merge_table_into_facts(old, table, source_clip_id=6779,
                                        source_page=1)
    assert stats["official_motions"] == len(table.motions)
    assert stats["displaced_motions"] == 1
    # Whisper motions are gone; official ones are in.
    descs = [m["description"] for m in new["motions_and_votes"]]
    assert "old garbled motion" not in descs
    assert new["motions_source"] == "table_of_motions"
    assert new["table_of_motions_ref"]["source_clip_id"] == 6779
    assert new["table_of_motions_ref"]["meeting_date"] == "2026-05-12"


def test_merge_fills_empty_attendance(table):
    new, _ = merge_table_into_facts({"attendance": {"present": []}}, table, 6779)
    assert "Wu" in new["attendance"]["present"]


def test_merge_does_not_clobber_attendance(table):
    new, _ = merge_table_into_facts(
        {"attendance": {"present": ["SomeoneElse"]}}, table, 6779)
    assert new["attendance"]["present"] == ["SomeoneElse"]


def test_merge_carries_over_timestamp(table):
    # A displaced Whisper motion describing the same docket approval should
    # lend its timestamp to the matching official motion.
    old = {"motions_and_votes": [
        {"description": "approve the May 14 council meeting docket",
         "motion_by": "Ellinger", "transcript_approx_time": "02:15"},
    ]}
    new, stats = merge_table_into_facts(old, table, 6779)
    assert stats["timestamps_carried"] == 1
    docket = [m for m in new["motions_and_votes"]
              if m["motion_by"] == "Ellinger"][0]
    assert docket["transcript_approx_time"] == "02:15"


def test_merge_handles_empty_facts(table):
    new, stats = merge_table_into_facts(None, table, 6779)
    assert len(new["motions_and_votes"]) == len(table.motions)
    assert stats["displaced_motions"] == 0
