#!/usr/bin/env python3
"""Generate realistic demo data for CivicLens sales demos and marketing pages.

Usage:
    python scripts/generate_demo_data.py
    python scripts/generate_demo_data.py --output-dir meetings_output/demo --count 20
"""

import argparse
import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Seed for reproducibility across runs
# ---------------------------------------------------------------------------
RANDOM_SEED = 42

# ---------------------------------------------------------------------------
# Reference data -- realistic municipal content
# ---------------------------------------------------------------------------

MEETING_BODIES = {
    "City Council": {
        "topics_pool": [
            "Budget Approval", "Zoning Amendment", "Infrastructure",
            "Public Safety", "Housing Policy", "Economic Development",
            "Short-Term Rentals", "Annexation", "Tax Increment Financing",
            "Municipal Code Update", "Franchise Agreement", "General Plan",
            "Capital Improvement Program", "Revenue Bonds", "Intergovernmental Agreement",
            "Homelessness Services", "Climate Action Plan", "Downtown Revitalization",
        ],
        "ordinance_prefix": "ORD",
        "resolution_prefix": "RES",
    },
    "Planning Commission": {
        "topics_pool": [
            "Conditional Use Permit", "Site Plan Review", "Subdivision",
            "Environmental Review", "Variance Request", "General Plan Amendment",
            "Design Guidelines", "Mixed-Use Development", "Historic Preservation",
            "Parking Standards", "Setback Variance", "Density Bonus",
            "Planned Unit Development", "Sign Code Update", "Tree Preservation",
        ],
        "ordinance_prefix": "CUP",
        "resolution_prefix": "SPR",
    },
    "Budget Committee": {
        "topics_pool": [
            "Annual Budget Review", "Capital Projects Funding",
            "Revenue Projections", "Department Budgets", "Audit Report",
            "Reserve Fund Policy", "Grant Applications", "Pension Obligations",
            "Utility Rate Study", "Fee Schedule Update", "Debt Service",
            "Fund Balance Analysis", "Mid-Year Budget Adjustment",
        ],
        "ordinance_prefix": "BUD",
        "resolution_prefix": "FIN",
    },
    "Parks & Recreation Commission": {
        "topics_pool": [
            "Trail System Expansion", "Park Master Plan", "Recreation Programs",
            "Facility Maintenance", "Community Events", "Sports Field Renovation",
            "Playground Equipment", "Aquatic Center", "Senior Programs",
            "Youth Sports Leagues", "Open Space Acquisition", "Dog Park",
        ],
        "ordinance_prefix": "PRK",
        "resolution_prefix": "REC",
    },
    "Public Safety Committee": {
        "topics_pool": [
            "Fire Department Staffing", "Police Body Cameras",
            "Emergency Preparedness", "Traffic Calming", "Neighborhood Watch",
            "Code Enforcement", "Building Inspection", "Disaster Recovery Plan",
            "911 System Upgrade", "Community Policing", "Fire Station Relocation",
            "DUI Checkpoint Policy", "School Resource Officers",
        ],
        "ordinance_prefix": "PS",
        "resolution_prefix": "SAF",
    },
}

COUNCIL_MEMBERS = [
    "Mayor Patricia Henderson",
    "Vice Mayor David Chen",
    "Councilmember Maria Gutierrez",
    "Councilmember James Patterson",
    "Councilmember Sarah Okonkwo",
    "Councilmember Robert Kim",
    "Councilmember Linda Washington",
]

COMMISSION_MEMBERS = {
    "Planning Commission": [
        "Chair Thomas Blackwell",
        "Vice Chair Amy Nakamura",
        "Commissioner Frank DeLuca",
        "Commissioner Priya Sharma",
        "Commissioner Kevin O'Brien",
    ],
    "Budget Committee": [
        "Chair Lisa Morales",
        "Vice Chair Stephen Grant",
        "Member Janet Yee",
        "Member Carlos Rivera",
        "Member Diane Foster",
    ],
    "Parks & Recreation Commission": [
        "Chair Michael Torres",
        "Vice Chair Rachel Greenfield",
        "Commissioner Beth Swanson",
        "Commissioner Amir Hassan",
        "Commissioner Dorothy Yamamoto",
    ],
    "Public Safety Committee": [
        "Chair Councilmember James Patterson",
        "Vice Chair Councilmember Sarah Okonkwo",
        "Member Chief Brian Hawkins",
        "Member Captain Teresa Ruiz",
        "Member Deputy Chief Mark Larson",
    ],
}

PUBLIC_COMMENTERS = [
    "John Miller", "Susan Wright", "Karen Thompson", "Michael Davis",
    "Jennifer Garcia", "Robert Martinez", "Laura Anderson", "William Taylor",
    "Elizabeth Moore", "Richard Jackson", "Margaret White", "Charles Harris",
    "Dorothy Martin", "Christopher Lee", "Nancy Walker", "Daniel Hall",
    "Sandra Allen", "Matthew Young", "Betty King", "George Scott",
]

# Street names for realistic addresses
STREETS = [
    "Oak Avenue", "Main Street", "Elm Boulevard", "Cedar Lane",
    "Maple Drive", "Park Way", "First Street", "Washington Avenue",
    "Lincoln Road", "Heritage Boulevard", "Civic Center Drive",
    "Commerce Parkway", "Industrial Way", "Sunset Drive", "Valley Road",
]

# ---------------------------------------------------------------------------
# Summary templates per body type
# ---------------------------------------------------------------------------


def _generate_city_council_summary(meeting, facts):
    """Generate a realistic City Council meeting summary."""
    date_str = datetime.strptime(meeting["date"], "%Y-%m-%d").strftime("%B %d, %Y")
    votes = facts["motions_and_votes"]
    financials = facts["financial_items"]
    comments = facts["public_comments"]
    topics = meeting["topics"]

    sections = []
    sections.append("## Meeting Overview\n")
    sections.append(
        f"The Elk Grove City Council convened in regular session on {date_str} "
        f"with {facts['meeting_info']['presiding_officer']} presiding. "
        f"The Council addressed {len(facts['agenda_items'])} agenda items during "
        f"the session, taking {len(votes)} formal vote{'s' if len(votes) != 1 else ''} "
        f"on matters before them. "
        f"{'A total of ' + str(len(comments)) + ' members of the public offered testimony during the public comment period.' if comments else 'No public comments were received during this meeting.'}"
    )

    # Attendance
    sections.append("\n## Attendance\n")
    sections.append("The following Council members were present at the meeting:\n")
    for m in facts["attendance"]["present"]:
        sections.append(f"- {m}")
    if facts["attendance"]["absent"]:
        sections.append("\nAbsent:")
        for m in facts["attendance"]["absent"]:
            sections.append(f"- {m}")

    # Votes
    if votes:
        sections.append("\n## Votes and Decisions\n")
        for v in votes:
            ts = f" [timestamp: {v['transcript_approx_time']}]" if v.get("transcript_approx_time") else ""
            sections.append(f"**{v['description']}**{ts}")
            if v["vote_type"] == "roll_call":
                sections.append(
                    f"Roll call vote: {v['ayes']} ayes, {v['nays']} nays, "
                    f"{v['abstentions']} abstention{'s' if v['abstentions'] != 1 else ''}. "
                    f"Motion {'passed' if v['outcome'] == 'passed' else 'failed'}."
                )
                if v["votes_for"]:
                    sections.append(f"  Ayes: {', '.join(v['votes_for'])}")
                if v["votes_against"]:
                    sections.append(f"  Nays: {', '.join(v['votes_against'])}")
            else:
                sections.append(f"Voice vote: motion {v['outcome']}.")
            sections.append("")

    # Financial items
    if financials:
        sections.append("## Financial Actions\n")
        for fi in financials:
            sections.append(f"- **{fi['description']}**: {fi['amount']} ({fi['type']})")
        sections.append("")

    # Agenda item details
    for item in facts["agenda_items"]:
        ts = f"[timestamp: {item['transcript_approx_time']}]" if item.get("transcript_approx_time") else ""
        sections.append(f"\n## {item['title']}\n")
        if ts:
            sections.append(ts + "\n")
        sections.append(item["summary"])
        if item.get("key_speakers"):
            sections.append(f"\n**Key Speakers:** {', '.join(item['key_speakers'])}")
        sections.append(f"\n**Outcome:** {item['outcome'].replace('_', ' ').title()}")

    return "\n".join(sections)


def _generate_commission_summary(meeting, facts, body_name):
    """Generate a summary for commission/committee meetings."""
    date_str = datetime.strptime(meeting["date"], "%Y-%m-%d").strftime("%B %d, %Y")
    votes = facts["motions_and_votes"]

    sections = []
    sections.append("## Meeting Overview\n")
    sections.append(
        f"The {body_name} held a regular meeting on {date_str} "
        f"with {facts['meeting_info']['presiding_officer']} presiding. "
        f"The commission addressed {len(facts['agenda_items'])} agenda items "
        f"and took {len(votes)} vote{'s' if len(votes) != 1 else ''}."
    )

    sections.append("\n## Attendance\n")
    for m in facts["attendance"]["present"]:
        sections.append(f"- {m}")

    if votes:
        sections.append("\n## Actions Taken\n")
        for v in votes:
            ts = f" [timestamp: {v['transcript_approx_time']}]" if v.get("transcript_approx_time") else ""
            sections.append(f"**{v['description']}**{ts}")
            sections.append(f"Vote: {v['outcome']}.\n")

    for item in facts["agenda_items"]:
        ts = f"[timestamp: {item['transcript_approx_time']}]" if item.get("transcript_approx_time") else ""
        sections.append(f"\n## {item['title']}\n")
        if ts:
            sections.append(ts + "\n")
        sections.append(item["summary"])
        sections.append(f"\n**Outcome:** {item['outcome'].replace('_', ' ').title()}")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Data generation helpers
# ---------------------------------------------------------------------------

FINANCIAL_DESCRIPTIONS = [
    ("General Obligation Bonds for street improvements", "bond_issuance", (5_000_000, 25_000_000)),
    ("Annual contract for solid waste collection services", "contract", (1_200_000, 3_500_000)),
    ("Capital improvement fund allocation for FY {fy}", "appropriation", (2_000_000, 15_000_000)),
    ("Emergency vehicle fleet replacement", "purchase", (400_000, 1_800_000)),
    ("Water system infrastructure upgrades", "capital_project", (3_000_000, 12_000_000)),
    ("Community development block grant acceptance", "grant", (200_000, 800_000)),
    ("Professional services agreement for EIR preparation", "contract", (150_000, 500_000)),
    ("Park restroom facility construction", "capital_project", (250_000, 750_000)),
    ("Traffic signal modernization project", "capital_project", (500_000, 2_000_000)),
    ("Affordable housing trust fund contribution", "appropriation", (1_000_000, 5_000_000)),
    ("Storm drain rehabilitation Phase {phase}", "capital_project", (2_000_000, 8_000_000)),
    ("IT system modernization and cybersecurity upgrade", "purchase", (300_000, 1_200_000)),
    ("Library expansion and renovation project", "capital_project", (4_000_000, 10_000_000)),
    ("Salary and benefits adjustment for sworn personnel", "appropriation", (500_000, 3_000_000)),
    ("Developer impact fee refund for Elm Street project", "refund", (100_000, 400_000)),
]

PUBLIC_COMMENT_TOPICS = [
    ("traffic congestion on {street}", "Expressed concern about increasing traffic congestion and requested the Council consider traffic calming measures including speed bumps and signal timing adjustments."),
    ("proposed development at {addr}", "Spoke in opposition to the proposed mixed-use development, citing concerns about building height, parking adequacy, and impact on neighborhood character."),
    ("park maintenance in {neighborhood}", "Requested increased maintenance funding for neighborhood parks, noting deteriorating playground equipment and overgrown landscaping."),
    ("short-term rental enforcement", "Urged stricter enforcement of the short-term rental ordinance, describing noise and parking issues caused by vacation rentals on their street."),
    ("pedestrian safety near {school}", "Advocated for improved crosswalk markings, crossing guards, and reduced speed limits in school zones to protect student safety."),
    ("water rate increases", "Objected to the proposed water rate increase, suggesting the city explore alternative revenue sources before passing costs to ratepayers."),
    ("homeless encampment on {street}", "Described safety concerns related to a growing encampment and asked about the city's plans for outreach and relocation services."),
    ("tree removal permit process", "Criticized the tree removal permit process as overly burdensome and requested streamlined procedures for dead or hazardous trees."),
    ("community event funding", "Requested continued city sponsorship of the annual cultural festival, emphasizing its importance for community cohesion and local business support."),
    ("noise from commercial operations", "Filed a formal noise complaint about late-night deliveries at the distribution center on {street} and requested enforcement of the noise ordinance."),
]

NEIGHBORHOODS = [
    "Elk Grove", "Laguna", "Sheldon", "Franklin", "Vineyard",
    "Lakeside", "Camden", "Heritage", "East Elk Grove", "Old Town",
]

SCHOOLS = [
    "Elk Grove High School", "Franklin High School", "Laguna Creek High School",
    "Valley High School", "Pleasant Grove High School",
    "Joseph Kerr Middle School", "Samuel Jackman Middle School",
]

AGENDA_ITEM_TEMPLATES = {
    "City Council": [
        {
            "title": "Second Reading of Ordinance {num} -- Zoning Map Amendment for {street} Corridor",
            "type": "ordinance",
            "summary": "The Council conducted the second reading and public hearing on Ordinance {num}, which would rezone approximately {acres} acres along the {street} corridor from R-1 Single Family Residential to C-2 General Commercial. Staff presented findings from the environmental review and the Planning Commission's recommendation for approval. The amendment is intended to support the city's economic development goals by enabling mixed-use development along a key transit corridor.",
            "speakers": ["Planning Director", "City Attorney"],
        },
        {
            "title": "Resolution {num} -- Approval of FY {fy} Capital Improvement Program",
            "type": "resolution",
            "summary": "The Council considered Resolution {num} adopting the five-year Capital Improvement Program for fiscal year {fy}. The program includes {project_count} projects totaling an estimated ${total}M in infrastructure investments across transportation, water, sewer, parks, and public facilities. The Public Works Director presented highlights including the {street} widening project and the new community center in {neighborhood}.",
            "speakers": ["Public Works Director", "Finance Director"],
        },
        {
            "title": "Consent Calendar",
            "type": "consent",
            "summary": "The Council approved the consent calendar on a single motion. Items included approval of the previous meeting minutes, ratification of warrants and demands, acceptance of a Community Development Block Grant in the amount of ${grant_amount}, renewal of the city's property and liability insurance policy, and approval of a professional services agreement for the annual financial audit.",
            "speakers": [],
        },
        {
            "title": "Public Hearing -- Conditional Use Permit {num} for {business} at {address}",
            "type": "public_hearing",
            "summary": "The Council held a public hearing on Conditional Use Permit application {num} for {business} proposing to operate at {address}. The applicant presented plans for the facility including hours of operation, parking, and traffic mitigation. Staff recommended approval with {condition_count} conditions including enhanced landscaping, restricted delivery hours, and a six-month operational review. Several residents spoke during the public hearing both in support and opposition.",
            "speakers": ["Planning Director", "Applicant Representative"],
        },
        {
            "title": "Award of Contract for {project} Project",
            "type": "action",
            "summary": "The Council awarded a construction contract to {contractor} in the amount of ${amount} for the {project} project. The project was competitively bid with {bid_count} responsive bids received. The awarded bid was {pct}% below the engineer's estimate. Construction is expected to begin within 30 days with a completion timeline of approximately {months} months.",
            "speakers": ["Public Works Director", "City Engineer"],
        },
    ],
    "Planning Commission": [
        {
            "title": "Site Plan Review SPR-{num} -- {units}-Unit Residential Development at {address}",
            "type": "public_hearing",
            "summary": "The Commission reviewed the site plan for a proposed {units}-unit residential development at {address}. The project includes a mix of single-family homes and townhouses on {acres} acres with density of {density} units per acre. The applicant has provided {parking} parking spaces, exceeding the minimum requirement. Staff recommended approval subject to conditions addressing landscaping, drainage, and traffic mitigation.",
            "speakers": ["Senior Planner", "Project Architect"],
        },
        {
            "title": "Variance Request VAR-{num} -- Reduced Setback at {address}",
            "type": "public_hearing",
            "summary": "The Commission considered a variance request to reduce the required front setback from 25 feet to 18 feet for a residential addition at {address}. The applicant cited the irregular lot shape and existing mature oak trees as hardship factors. Staff analysis confirmed that strict application of the setback standard would create practical difficulties given the site constraints. Neighboring property owners were notified and no objections were received.",
            "speakers": ["Associate Planner", "Property Owner"],
        },
    ],
    "Budget Committee": [
        {
            "title": "Quarterly Financial Report -- Q{quarter} FY {fy}",
            "type": "report",
            "summary": "The Finance Director presented the quarterly financial report for the {quarter_name} quarter of fiscal year {fy}. General Fund revenues are tracking {rev_pct}% above projections, primarily due to stronger-than-expected sales tax receipts and development fee collections. Expenditures are {exp_pct}% below budget, with savings in personnel costs from unfilled positions. The General Fund balance stands at ${balance}M, representing {reserve_pct}% of operating expenditures.",
            "speakers": ["Finance Director", "Budget Analyst"],
        },
    ],
    "Parks & Recreation Commission": [
        {
            "title": "Presentation -- {park} Park Master Plan Update",
            "type": "presentation",
            "summary": "Staff presented an update on the {park} Park Master Plan, which has been in development for eight months with extensive community input. The plan proposes phased improvements including a renovated playground area, new walking trails, upgraded restroom facilities, a splash pad, and improved ADA accessibility throughout the park. An online survey received {survey_count} responses, with the splash pad and trail improvements receiving the highest support. Estimated cost for full build-out is ${cost}M over five years.",
            "speakers": ["Parks Director", "Landscape Architect"],
        },
    ],
    "Public Safety Committee": [
        {
            "title": "Report -- Annual Crime Statistics and Public Safety Trends",
            "type": "report",
            "summary": "The Police Chief presented the annual crime statistics report showing an overall {crime_change}% {crime_direction} in reported crimes compared to the previous year. Property crimes {prop_direction} by {prop_pct}%, while violent crimes remained largely stable. The department's community policing initiatives were credited with improved clearance rates. The report also highlighted the success of the neighborhood watch expansion program, which now covers {watch_count} neighborhoods citywide.",
            "speakers": ["Police Chief", "Crime Analyst"],
        },
    ],
}


def _rand_time(start_min=0, end_min=180):
    """Return a random timestamp string like '1:23:45'."""
    minutes = random.randint(start_min, end_min)
    h, m = divmod(minutes, 60)
    s = random.randint(0, 59)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _rand_amount(lo, hi):
    """Return a formatted dollar amount."""
    val = random.randint(lo // 1000, hi // 1000) * 1000
    if val >= 1_000_000:
        return f"${val:,.0f}"
    return f"${val:,.0f}"


def _pick_members(body_name, rng):
    """Return present and absent members for a given body."""
    if body_name == "City Council":
        members = list(COUNCIL_MEMBERS)
    else:
        members = list(COMMISSION_MEMBERS.get(body_name, COUNCIL_MEMBERS[:5]))
    # Randomly mark 0-1 member absent
    absent = []
    if rng.random() < 0.3 and len(members) > 3:
        absent_member = rng.choice(members[2:])  # never the chair/mayor
        members.remove(absent_member)
        absent.append(absent_member)
    return members, absent


def _generate_votes(body_name, topics, members, rng):
    """Generate realistic vote records."""
    body_cfg = MEETING_BODIES[body_name]
    votes = []
    num_votes = rng.randint(1, min(4, len(topics)))
    used_topics = rng.sample(topics, min(num_votes, len(topics)))

    for i, topic in enumerate(used_topics):
        is_roll_call = rng.random() < 0.6
        is_unanimous = rng.random() < 0.7

        if is_unanimous:
            ayes = len(members)
            nays = 0
            abstentions = 0
            votes_for = [m.split()[-1] for m in members]
            votes_against = []
        else:
            nays = rng.randint(1, max(1, len(members) // 3))
            abstentions = 1 if rng.random() < 0.15 else 0
            ayes = len(members) - nays - abstentions
            all_names = [m.split()[-1] for m in members]
            rng.shuffle(all_names)
            votes_against = all_names[:nays]
            votes_for = all_names[nays + abstentions:]

        passed = ayes > nays
        prefix = body_cfg["ordinance_prefix"] if rng.random() < 0.5 else body_cfg["resolution_prefix"]
        identifier = f"{prefix}-{rng.randint(2025, 2026)}-{rng.randint(1, 99):03d}"

        motion_by = rng.choice(members).split()[-1]
        remaining = [m.split()[-1] for m in members if m.split()[-1] != motion_by]
        second_by = rng.choice(remaining) if remaining else None

        votes.append({
            "identifier": identifier,
            "description": f"{'Approval' if rng.random() < 0.5 else 'Adoption'} of {topic.lower()} {'ordinance' if 'ORD' in prefix else 'resolution'}",
            "motion_by": motion_by,
            "second_by": second_by,
            "outcome": "passed" if passed else "failed",
            "vote_type": "roll_call" if is_roll_call else "voice",
            "ayes": ayes,
            "nays": nays,
            "abstentions": abstentions,
            "votes_for": votes_for if is_roll_call else [],
            "votes_against": votes_against if is_roll_call else [],
            "conditions": None,
            "transcript_approx_time": _rand_time(10 + i * 30, 30 + i * 30),
        })

    return votes


def _generate_financial_items(rng, count=None):
    """Generate financial items."""
    if count is None:
        count = rng.randint(0, 4)
    items = []
    templates = rng.sample(FINANCIAL_DESCRIPTIONS, min(count, len(FINANCIAL_DESCRIPTIONS)))
    for desc_tpl, item_type, (lo, hi) in templates:
        desc = desc_tpl.format(
            fy=f"{rng.randint(2025, 2027)}-{rng.randint(26, 28)}",
            phase=rng.choice(["I", "II", "III", "IV"]),
        )
        items.append({
            "description": desc,
            "amount": _rand_amount(lo, hi),
            "type": item_type,
        })
    return items


def _generate_public_comments(rng, count=None):
    """Generate public comment records."""
    if count is None:
        count = rng.randint(0, 5)
    comments = []
    used = set()
    for _ in range(count):
        speaker = rng.choice(PUBLIC_COMMENTERS)
        if speaker in used:
            continue
        used.add(speaker)
        topic_tpl, summary_tpl = rng.choice(PUBLIC_COMMENT_TOPICS)
        topic = topic_tpl.format(
            street=rng.choice(STREETS),
            addr=f"{rng.randint(100, 9999)} {rng.choice(STREETS)}",
            neighborhood=rng.choice(NEIGHBORHOODS),
            school=rng.choice(SCHOOLS),
        )
        comments.append({
            "speaker": speaker,
            "topic": topic.title(),
            "summary": summary_tpl,
            "transcript_approx_time": _rand_time(5, 25),
        })
    return comments


def _generate_agenda_items(body_name, topics, rng):
    """Generate agenda items from templates."""
    templates = AGENDA_ITEM_TEMPLATES.get(body_name, AGENDA_ITEM_TEMPLATES["City Council"])
    items = []
    num_items = rng.randint(3, min(6, len(templates) + 2))

    # Always include consent calendar for City Council
    used_templates = rng.sample(templates, min(num_items, len(templates)))

    for i, tpl in enumerate(used_templates):
        street = rng.choice(STREETS)
        neighborhood = rng.choice(NEIGHBORHOODS)
        # Build a shared kwargs dict so every template placeholder is always available
        fmt_kwargs = dict(
            num=rng.randint(1, 999),
            street=street,
            address=f"{rng.randint(100, 9999)} {street}",
            acres=f"{rng.uniform(2, 45):.1f}",
            neighborhood=neighborhood,
            fy=f"20{rng.randint(25, 27)}-{rng.randint(26, 28)}",
            project_count=rng.randint(15, 45),
            total=f"{rng.uniform(20, 120):.1f}",
            grant_amount=f"{rng.randint(200, 800):,}K",
            business=rng.choice(["Elk Grove Auto Spa", "Quick Lube Express"]),
            condition_count=rng.randint(5, 15),
            contractor=rng.choice(["Granite Construction", "Teichert Construction",
                                   "DeSilva Gates Construction", "Pacific Excavation Inc."]),
            amount=f"{rng.randint(1, 12):,.0f},{rng.randint(100, 999):03d}",
            bid_count=rng.randint(3, 9),
            pct=f"{rng.uniform(2, 18):.1f}",
            months=rng.randint(6, 24),
            units=rng.randint(24, 200),
            density=f"{rng.uniform(8, 25):.1f}",
            parking=rng.randint(50, 400),
            quarter=rng.randint(1, 4),
            quarter_name=rng.choice(["first", "second", "third", "fourth"]),
            rev_pct=f"{rng.uniform(1, 8):.1f}",
            exp_pct=f"{rng.uniform(1, 6):.1f}",
            balance=f"{rng.uniform(15, 45):.1f}",
            reserve_pct=rng.randint(18, 35),
            survey_count=rng.randint(200, 1500),
            cost=f"{rng.uniform(2, 12):.1f}",
            crime_change=f"{rng.uniform(1, 12):.1f}",
            crime_direction=rng.choice(["decrease", "increase"]),
            prop_direction=rng.choice(["decreased", "increased"]),
            prop_pct=f"{rng.uniform(2, 15):.1f}",
            watch_count=rng.randint(20, 60),
            park=rng.choice(["Bartholomew", "Morse", "Elk Grove", "Laguna", "Heritage"]),
            project=rng.choice(["Elk Grove Boulevard Widening", "Laguna Main Sewer Extension",
                                "Sheldon Road Bridge Replacement", "Heritage Park Restroom",
                                "Bond Road Interchange"]),
            school=rng.choice(SCHOOLS),
        )
        title = tpl["title"].format(**fmt_kwargs)
        summary = tpl["summary"].format(**fmt_kwargs)
        items.append({
            "identifier": str(i + 1),
            "title": title,
            "type": tpl["type"],
            "summary": summary,
            "key_speakers": tpl["speakers"],
            "outcome": rng.choice(["approved", "approved_with_conditions", "continued", "received_and_filed"]),
            "transcript_approx_time": _rand_time(10 + i * 20, 30 + i * 20),
        })

    return items


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate_meeting(clip_id, date, body_name, rng):
    """Generate a complete meeting record."""
    body_cfg = MEETING_BODIES[body_name]
    topics = rng.sample(body_cfg["topics_pool"], rng.randint(3, min(6, len(body_cfg["topics_pool"]))))
    members, absent = _pick_members(body_name, rng)
    presiding = members[0]

    # Extracted facts
    votes = _generate_votes(body_name, topics, members, rng)
    financial_items = _generate_financial_items(rng) if body_name in ("City Council", "Budget Committee") else []
    public_comments = _generate_public_comments(rng) if body_name == "City Council" else []
    agenda_items = _generate_agenda_items(body_name, topics, rng)
    late = []
    if rng.random() < 0.15 and len(members) > 3:
        late_member = rng.choice(members[2:])
        late.append(late_member)

    meeting_time = rng.choice(["6:00 PM", "6:30 PM", "7:00 PM", "2:00 PM", "5:00 PM"])
    facts = {
        "meeting_info": {
            "date": date,
            "time": meeting_time,
            "body": body_name,
            "presiding_officer": presiding,
            "location": "Council Chambers, 8400 Laguna Palms Way, Elk Grove, CA 95758",
        },
        "attendance": {
            "present": members,
            "absent": absent,
            "late": late,
        },
        "motions_and_votes": votes,
        "financial_items": financial_items,
        "public_comments": public_comments,
        "agenda_items": agenda_items,
        "appointments": [],
        "contentious_items": [
            {
                "description": votes[i]["description"],
                "reason": rng.choice([
                    "Split vote with significant public testimony",
                    "Extended debate among council members",
                    "Multiple public speakers in opposition",
                    "Fiscal impact concerns raised",
                ]),
            }
            for i, v in enumerate(votes)
            if v["outcome"] == "passed" and v["nays"] > 0
        ],
    }

    date_fmt = datetime.strptime(date, "%Y-%m-%d").strftime("%B %-d %Y")
    title_body = body_name.replace("&", "and")
    title = f"{date_fmt} {title_body} Meeting"

    meeting = {
        "clip_id": clip_id,
        "date": date,
        "meeting_body": body_name,
        "title": title,
        "topics": topics,
    }

    # Generate summary
    if body_name == "City Council":
        summary = _generate_city_council_summary(meeting, facts)
    else:
        summary = _generate_commission_summary(meeting, facts, body_name)

    transcript_words = rng.randint(3000, 25000)
    safe_title = title.replace(" ", "_").replace(",", "")

    metadata = {
        "clip_id": clip_id,
        "url": f"https://elkgrove.granicus.com/player/clip/{clip_id}?view_id=2&redirect=true",
        "date": date,
        "meeting_body": body_name,
        "title": title,
        "topics": topics,
        "files": {
            "audio": f"{safe_title}_audio.mp3",
            "transcript": f"transcript_{safe_title}_audio.txt",
            "transcript_segments": f"transcript_{safe_title}_audio_segments.json",
            "extracted_facts": "extracted_facts.json",
            "summary_txt": "summary.txt",
        },
        "processed_at": datetime.now().isoformat(),
        "processing_time_seconds": round(rng.uniform(60, 300), 2),
        "transcript_words": transcript_words,
        "audio_kept": True,
        "models": {
            "transcribe": "whisper-1",
            "summary": "gpt-4o+claude-sonnet",
            "topics": "gpt-4o-mini",
        },
    }

    # Index entry
    summary_lines = summary.split("\n")
    summary_preview = ""
    for line in summary_lines:
        if line.strip() and not line.startswith("#"):
            summary_preview = line.strip()[:500]
            break

    index_entry = {
        "clip_id": clip_id,
        "date": date,
        "meeting_body": body_name,
        "title": title,
        "transcript_words": transcript_words,
        "transcript_preview": "",
        "agenda_preview": "",
        "summary_preview": summary_preview,
        "processed_at": metadata["processed_at"],
        "files": metadata["files"],
    }

    return metadata, facts, summary, index_entry


def generate_analytics_data(meetings, rng):
    """Generate demo analytics data (query history and usage stats)."""
    queries = [
        "What has the city done about short-term rentals?",
        "How did the council vote on the budget?",
        "What are the plans for the new park?",
        "Show me all votes on zoning amendments",
        "What traffic calming measures were discussed?",
        "Who spoke during public comment about housing?",
        "What is the capital improvement program budget?",
        "Were there any contentious votes this quarter?",
        "What infrastructure projects are planned?",
        "How much was allocated for police body cameras?",
        "What did the planning commission say about the subdivision?",
        "Show financial items over $1 million",
        "What appointments were made to boards and commissions?",
        "Were there any split votes on development projects?",
        "What is the status of the climate action plan?",
        "How many public comments were received on the zoning change?",
        "What grants has the city applied for?",
        "Show me all meetings about public safety",
        "What are the upcoming capital projects?",
        "How did Councilmember Gutierrez vote on the bond measure?",
    ]

    base_date = datetime.now() - timedelta(days=90)
    query_log = []
    for i in range(200):
        ts = base_date + timedelta(
            days=rng.randint(0, 90),
            hours=rng.randint(8, 22),
            minutes=rng.randint(0, 59),
        )
        query_log.append({
            "id": str(uuid.uuid4()),
            "timestamp": ts.isoformat(),
            "query": rng.choice(queries),
            "model": rng.choice(["gpt-4o", "claude-sonnet", "gpt-4o-mini"]),
            "response_time_ms": rng.randint(800, 4500),
            "sources_cited": rng.randint(1, 6),
            "user_rating": rng.choice([None, None, None, 4, 5, 5, 5, 3]),
        })

    query_log.sort(key=lambda x: x["timestamp"])

    # Usage summary
    usage = {
        "period": "last_90_days",
        "total_queries": len(query_log),
        "unique_questions": len(set(q["query"] for q in query_log)),
        "avg_response_time_ms": round(
            sum(q["response_time_ms"] for q in query_log) / len(query_log)
        ),
        "total_meetings_processed": len(meetings),
        "total_votes_tracked": sum(
            1 for m in meetings for _ in range(rng.randint(1, 4))
        ),
        "queries_by_day": {},
        "popular_topics": [
            {"topic": "Zoning", "count": rng.randint(20, 45)},
            {"topic": "Budget", "count": rng.randint(15, 35)},
            {"topic": "Public Safety", "count": rng.randint(10, 30)},
            {"topic": "Infrastructure", "count": rng.randint(10, 25)},
            {"topic": "Housing", "count": rng.randint(8, 20)},
            {"topic": "Parks", "count": rng.randint(5, 15)},
        ],
    }

    # Queries by day
    for q in query_log:
        day = q["timestamp"][:10]
        usage["queries_by_day"][day] = usage["queries_by_day"].get(day, 0) + 1

    return {"query_log": query_log, "usage_summary": usage}


def main():
    parser = argparse.ArgumentParser(
        description="Generate realistic demo data for CivicLens sales demos."
    )
    parser.add_argument(
        "--output-dir",
        default="meetings_output/demo",
        help="Output directory (default: meetings_output/demo)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=20,
        help="Number of meetings to generate (default: 20)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=RANDOM_SEED,
        help=f"Random seed for reproducibility (default: {RANDOM_SEED})",
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    output_dir = Path(args.output_dir)

    # Generate dates spanning ~6 months
    end_date = datetime(2026, 4, 1)
    start_date = end_date - timedelta(days=180)

    # Distribute meetings across bodies with realistic frequency
    body_weights = {
        "City Council": 6,
        "Planning Commission": 5,
        "Budget Committee": 3,
        "Parks & Recreation Commission": 3,
        "Public Safety Committee": 3,
    }

    body_pool = []
    for body, weight in body_weights.items():
        body_pool.extend([body] * weight)

    meetings = []
    clip_base = 10001  # demo clip IDs start high to avoid collisions
    all_dates = []

    # Generate evenly-spaced dates with some jitter
    for i in range(args.count):
        day_offset = int((i / args.count) * 180)
        jitter = rng.randint(-3, 3)
        meeting_date = start_date + timedelta(days=day_offset + jitter)
        # Push to a weekday (Tue/Wed/Thu are typical)
        while meeting_date.weekday() > 4:  # skip weekends
            meeting_date += timedelta(days=1)
        all_dates.append(meeting_date.strftime("%Y-%m-%d"))

    all_dates.sort()

    index_entries = []
    for i in range(args.count):
        clip_id = clip_base + i
        date = all_dates[i]
        body = rng.choice(body_pool)

        metadata, facts, summary, index_entry = generate_meeting(clip_id, date, body, rng)
        meetings.append(metadata)
        index_entries.append(index_entry)

        # Write clip files
        clip_dir = output_dir / "clips" / str(clip_id)
        clip_dir.mkdir(parents=True, exist_ok=True)

        with open(clip_dir / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        with open(clip_dir / "extracted_facts.json", "w") as f:
            json.dump(facts, f, indent=2)

        with open(clip_dir / "summary.txt", "w") as f:
            f.write(summary)

        body_short = body.split()[0].lower()
        print(f"  [{clip_id}] {date} -- {body:<35s} ({len(facts['motions_and_votes'])} votes, {len(facts['financial_items'])} financial, {len(facts['public_comments'])} comments)")

    # Write index.json
    index_entries.sort(key=lambda x: x["date"], reverse=True)
    index = {
        "generated_at": datetime.now().isoformat(),
        "total_clips": len(index_entries),
        "clips": index_entries,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "index.json", "w") as f:
        json.dump(index, f, indent=2)

    # Generate analytics data
    analytics = generate_analytics_data(meetings, rng)
    with open(output_dir / "analytics_demo.json", "w") as f:
        json.dump(analytics, f, indent=2)

    print(f"\nGenerated {args.count} demo meetings in {output_dir}/")
    print(f"  index.json           -- {len(index_entries)} entries")
    print(f"  analytics_demo.json  -- {len(analytics['query_log'])} query log entries")
    print(f"  clips/               -- {args.count} clip directories")

    # Summary stats
    bodies_used = {}
    total_votes = 0
    total_financial = 0
    total_comments = 0
    for entry in index_entries:
        b = entry["meeting_body"]
        bodies_used[b] = bodies_used.get(b, 0) + 1
    for m in meetings:
        clip_dir = output_dir / "clips" / str(m["clip_id"])
        with open(clip_dir / "extracted_facts.json") as f:
            facts = json.load(f)
            total_votes += len(facts["motions_and_votes"])
            total_financial += len(facts["financial_items"])
            total_comments += len(facts["public_comments"])

    print("\n  Meetings by body:")
    for body, count in sorted(bodies_used.items(), key=lambda x: -x[1]):
        print(f"    {body:<35s} {count}")
    print(f"  Total votes:     {total_votes}")
    print(f"  Total financial: {total_financial}")
    print(f"  Total comments:  {total_comments}")


if __name__ == "__main__":
    main()
