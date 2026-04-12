// CivicLens Demo Sandbox - Sample Data
// Realistic meeting data based on actual extracted_facts.json structure

const DEMO_DATA = {

  // ─────────────────────────────────────────────────
  // Sample meeting: full extracted_facts structure
  // ─────────────────────────────────────────────────
  meeting: {
    clip_id: 6742,
    date: "2026-03-24",
    title: "March 24, 2026 Urban County Council Regular Session",
    meeting_body: "Urban County Council",
    topics: ["Budget", "Zoning", "Infrastructure", "Public Safety", "Parks"],
    transcript_words: 14820,

    extracted_facts: {
      meeting_info: {
        date: "2026-03-24",
        time: "6:00 PM",
        body: "Urban County Council",
        presiding_officer: "Mayor Linda Gorton",
        location: "Council Chambers, 200 E. Main St."
      },
      attendance: {
        present: [
          "Beasley", "Boone", "Brown", "Curtis", "Ellinger",
          "Evans", "Farmer", "Hollander", "Kay", "McCann",
          "Moloney", "Nicholson", "Plomin", "Swanson", "Thompson"
        ],
        absent: [],
        late: ["Swanson"]
      },
      motions_and_votes: [
        {
          identifier: "Ordinance 0089-26",
          description: "Rezoning of 42 acres on Tates Creek Road from Agricultural-Rural (A-R) to Planned Neighborhood Residential (R-1N) for mixed-use development with 180 single-family homes and 12 acres of preserved greenspace",
          motion_by: "Brown",
          second_by: "Curtis",
          outcome: "passed",
          vote_type: "roll_call",
          ayes: 11,
          nays: 4,
          abstentions: 0,
          votes_for: ["Beasley", "Boone", "Brown", "Curtis", "Evans", "Farmer", "Hollander", "Kay", "McCann", "Nicholson", "Thompson"],
          votes_against: ["Ellinger", "Moloney", "Plomin", "Swanson"],
          conditions: "Subject to developer committing to 15% affordable housing units",
          transcript_approx_time: "25:15"
        },
        {
          identifier: "Resolution 0212-26",
          description: "Approval of $4.2M supplemental appropriation for Veteran's Park Phase II improvements including splash pad, ADA-accessible playground, and walking trails",
          motion_by: "Ellinger",
          second_by: "Moloney",
          outcome: "passed",
          vote_type: "roll_call",
          ayes: 15,
          nays: 0,
          abstentions: 0,
          votes_for: ["Beasley", "Boone", "Brown", "Curtis", "Ellinger", "Evans", "Farmer", "Hollander", "Kay", "McCann", "Moloney", "Nicholson", "Plomin", "Swanson", "Thompson"],
          votes_against: [],
          conditions: null,
          transcript_approx_time: "52:30"
        },
        {
          identifier: "Ordinance 0091-26",
          description: "First reading of short-term rental regulation ordinance requiring annual permits, 300-foot notification radius, and occupancy limits in residential zones",
          motion_by: "Plomin",
          second_by: "Ellinger",
          outcome: "passed",
          vote_type: "roll_call",
          ayes: 9,
          nays: 6,
          abstentions: 0,
          votes_for: ["Beasley", "Brown", "Curtis", "Ellinger", "Hollander", "Moloney", "Nicholson", "Plomin", "Thompson"],
          votes_against: ["Boone", "Evans", "Farmer", "Kay", "McCann", "Swanson"],
          conditions: "Second reading scheduled for April 14, 2026",
          transcript_approx_time: "1:18:45"
        },
        {
          identifier: "Resolution 0215-26",
          description: "Authorization of general obligation bonds not to exceed $18,040,000 for Town Branch Commons infrastructure improvements",
          motion_by: "Farmer",
          second_by: "Kay",
          outcome: "passed",
          vote_type: "roll_call",
          ayes: 13,
          nays: 2,
          abstentions: 0,
          votes_for: ["Beasley", "Boone", "Brown", "Curtis", "Ellinger", "Evans", "Farmer", "Hollander", "Kay", "McCann", "Nicholson", "Swanson", "Thompson"],
          votes_against: ["Moloney", "Plomin"],
          conditions: null,
          transcript_approx_time: "1:42:10"
        }
      ],
      financial_items: [
        {
          description: "Veteran's Park Phase II improvements",
          amount: "$4,200,000",
          type: "appropriation",
          identifier: "Resolution 0212-26",
          vendor_or_recipient: "Parks & Recreation Department"
        },
        {
          description: "Town Branch Commons infrastructure bonds",
          amount: "$18,040,000",
          type: "bond_issuance",
          identifier: "Resolution 0215-26",
          vendor_or_recipient: null
        },
        {
          description: "Fire Station #22 apparatus replacement",
          amount: "$1,850,000",
          type: "contract",
          identifier: "Contract 2026-0145",
          vendor_or_recipient: "Pierce Manufacturing"
        },
        {
          description: "Sidewalk repair program FY2026 Phase 2",
          amount: "$3,100,000",
          type: "appropriation",
          identifier: "Resolution 0218-26",
          vendor_or_recipient: "Public Works"
        },
        {
          description: "Body camera system upgrade and data storage",
          amount: "$890,000",
          type: "contract",
          identifier: "Contract 2026-0151",
          vendor_or_recipient: "Axon Enterprise Inc."
        }
      ],
      public_comments: [
        {
          speaker: "James Whitfield",
          topic: "Tates Creek rezoning opposition",
          summary: "Expressed concerns about traffic congestion on Tates Creek Road and impact on Stonewall Elementary School enrollment. Requested a traffic impact study before final approval.",
          transcript_approx_time: "15:30"
        },
        {
          speaker: "Maria Santos",
          topic: "Short-term rental support",
          summary: "Spoke in favor of reasonable short-term rental regulations. Operates two STR properties and noted they bring tourism revenue. Requested the 300-foot notification radius be reduced to 200 feet.",
          transcript_approx_time: "1:05:20"
        },
        {
          speaker: "Dr. Robert Chen",
          topic: "Veteran's Park accessibility",
          summary: "Thanked council for including ADA-accessible playground equipment in the park plan. Recommended consulting with disability advocacy groups during the design phase.",
          transcript_approx_time: "48:15"
        },
        {
          speaker: "Patricia O'Brien",
          topic: "Town Branch Commons fiscal concerns",
          summary: "Questioned whether $18M in bonds was prudent given current interest rates. Requested a breakdown of debt service costs over the bond term.",
          transcript_approx_time: "1:35:40"
        }
      ],
      agenda_items: [
        {
          identifier: "Ordinance 0089-26",
          title: "Tates Creek Road Rezoning - A-R to R-1N",
          type: "ordinance",
          summary: "Rezoned 42 acres for planned neighborhood development. Required 15% affordable housing commitment. Passed 11-4.",
          key_speakers: ["Brown", "Curtis", "Ellinger"],
          outcome: "approved",
          transcript_approx_time: "25:15"
        },
        {
          identifier: "Resolution 0212-26",
          title: "Veteran's Park Phase II Appropriation",
          type: "resolution",
          summary: "Approved $4.2M for park improvements including splash pad and ADA playground. Unanimous approval.",
          key_speakers: ["Ellinger", "Moloney"],
          outcome: "approved",
          transcript_approx_time: "52:30"
        },
        {
          identifier: "Ordinance 0091-26",
          title: "Short-Term Rental Regulation",
          type: "ordinance",
          summary: "First reading of STR permit requirements. Passed 9-6 with second reading scheduled April 14.",
          key_speakers: ["Plomin", "Ellinger", "Boone"],
          outcome: "approved_first_reading",
          transcript_approx_time: "1:18:45"
        },
        {
          identifier: "Resolution 0215-26",
          title: "Town Branch Commons GO Bonds",
          type: "resolution",
          summary: "Authorized $18.04M general obligation bonds for infrastructure improvements. Passed 13-2.",
          key_speakers: ["Farmer", "Kay", "Moloney"],
          outcome: "approved",
          transcript_approx_time: "1:42:10"
        }
      ],
      appointments: [
        {
          name: "Dr. Angela Foster",
          position: "Board of Health, District 4 seat",
          appointed_by: "Mayor Gorton",
          confirmed: true
        }
      ],
      contentious_items: [
        {
          identifier: "Ordinance 0091-26",
          title: "Short-Term Rental Regulation",
          reason: "Split 9-6 vote with significant public comment. Property rights concerns from opponents, neighborhood quality concerns from supporters.",
          key_opposition: ["Boone", "Evans", "Farmer"],
          key_support: ["Plomin", "Ellinger", "Moloney"]
        }
      ]
    }
  },

  // ─────────────────────────────────────────────────
  // Sample Q&A responses with sources
  // ─────────────────────────────────────────────────
  qaResponses: [
    {
      question: "What has the city spent on parks and recreation this year?",
      answer: "Based on council meeting records from January through March 2026, the city has approved approximately **$6.8M in parks and recreation spending** across multiple appropriations:\n\n- **$4,200,000** for Veteran's Park Phase II improvements including a splash pad, ADA-accessible playground, and walking trails (Resolution 0212-26, approved unanimously 15-0 on March 24)\n- **$1,450,000** for Jacobson Park trail extension and parking expansion (Resolution 0178-26, approved 14-1 on February 10)\n- **$850,000** for community center HVAC replacement at three facilities (Contract 2026-0098, approved January 27)\n- **$310,000** for seasonal staffing and programming at Shillito Park aquatic center\n\nCouncilmember Ellinger has been the primary champion of parks funding, sponsoring 3 of the 4 appropriations.",
      sources: [
        { clip_id: 6742, title: "March 24 Council Regular Session", timestamp: "52:30", type: "summary" },
        { clip_id: 6718, title: "February 10 Council Regular Session", timestamp: "38:15", type: "facts" },
        { clip_id: 6695, title: "January 27 Council Regular Session", timestamp: "1:12:00", type: "minutes" },
        { clip_id: 6708, title: "February 3 Budget Committee", timestamp: "22:45", type: "transcript" }
      ],
      processing_time: "2.1s",
      chunks_searched: 847
    },
    {
      question: "How did council members vote on short-term rental regulations?",
      answer: "The short-term rental regulation ordinance (Ordinance 0091-26) passed its **first reading on March 24, 2026 with a 9-6 vote**. The ordinance requires annual permits, a 300-foot neighbor notification radius, and occupancy limits in residential zones.\n\n**Voted FOR (9):** Beasley, Brown, Curtis, Ellinger, Hollander, Moloney, Nicholson, Plomin, Thompson\n\n**Voted AGAINST (6):** Boone, Evans, Farmer, Kay, McCann, Swanson\n\nThis was one of the most contentious items of the session, with significant public comment on both sides. Maria Santos, who operates two STR properties, spoke in favor but requested reducing the notification radius from 300 to 200 feet.\n\nThe **second reading is scheduled for April 14, 2026**. Council members opposed cited property rights concerns, while supporters emphasized neighborhood quality of life.",
      sources: [
        { clip_id: 6742, title: "March 24 Council Regular Session", timestamp: "1:18:45", type: "facts" },
        { clip_id: 6742, title: "March 24 Council Regular Session", timestamp: "1:05:20", type: "transcript" },
        { clip_id: 6735, title: "March 10 Planning Committee", timestamp: "15:00", type: "summary" }
      ],
      processing_time: "1.8s",
      chunks_searched: 623
    },
    {
      question: "What infrastructure bonds has the city approved recently?",
      answer: "The city has authorized **$26.5M in infrastructure bond issuances** in Q1 2026:\n\n1. **$18,040,000** - Town Branch Commons infrastructure improvements (Resolution 0215-26, passed 13-2 on March 24). This was the largest single bond authorization of the year. Council members Moloney and Plomin voted against, with concerns about debt service costs at current interest rates.\n\n2. **$8,500,000** - Sanitary sewer rehabilitation in the Idle Hour and Chevy Chase neighborhoods (Resolution 0185-26, passed unanimously on February 24). This addresses aging infrastructure dating to the 1940s.\n\nPublic commenter Patricia O'Brien questioned the prudence of the Town Branch bonds given current interest rates and requested a full debt service breakdown, which the Finance Commissioner committed to providing before second reading.",
      sources: [
        { clip_id: 6742, title: "March 24 Council Regular Session", timestamp: "1:42:10", type: "facts" },
        { clip_id: 6742, title: "March 24 Council Regular Session", timestamp: "1:35:40", type: "transcript" },
        { clip_id: 6725, title: "February 24 Council Regular Session", timestamp: "55:20", type: "summary" },
        { clip_id: 6710, title: "February 5 Public Works Committee", timestamp: "8:30", type: "minutes" }
      ],
      processing_time: "2.4s",
      chunks_searched: 912
    }
  ],

  // ─────────────────────────────────────────────────
  // Additional sample vote records for vote tracker
  // ─────────────────────────────────────────────────
  councilMembers: [
    { name: "Beasley",   votes: { total: 42, yea: 38, nay: 4,  pct_yea: 90 } },
    { name: "Boone",     votes: { total: 42, yea: 34, nay: 8,  pct_yea: 81 } },
    { name: "Brown",     votes: { total: 42, yea: 40, nay: 2,  pct_yea: 95 } },
    { name: "Curtis",    votes: { total: 41, yea: 37, nay: 4,  pct_yea: 90 } },
    { name: "Ellinger",  votes: { total: 42, yea: 39, nay: 3,  pct_yea: 93 } },
    { name: "Evans",     votes: { total: 40, yea: 33, nay: 7,  pct_yea: 83 } },
    { name: "Farmer",    votes: { total: 42, yea: 36, nay: 6,  pct_yea: 86 } },
    { name: "Hollander", votes: { total: 42, yea: 41, nay: 1,  pct_yea: 98 } },
    { name: "Kay",       votes: { total: 42, yea: 35, nay: 7,  pct_yea: 83 } },
    { name: "McCann",    votes: { total: 41, yea: 34, nay: 7,  pct_yea: 83 } },
    { name: "Moloney",   votes: { total: 42, yea: 31, nay: 11, pct_yea: 74 } },
    { name: "Nicholson", votes: { total: 42, yea: 39, nay: 3,  pct_yea: 93 } },
    { name: "Plomin",    votes: { total: 42, yea: 32, nay: 10, pct_yea: 76 } },
    { name: "Swanson",   votes: { total: 39, yea: 30, nay: 9,  pct_yea: 77 } },
    { name: "Thompson",  votes: { total: 42, yea: 40, nay: 2,  pct_yea: 95 } }
  ],

  // ─────────────────────────────────────────────────
  // Financial aggregates for dashboard
  // ─────────────────────────────────────────────────
  financialSummary: {
    period: "Q1 2026 (Jan - Mar)",
    totalApproved: 42870000,
    categories: [
      { name: "Infrastructure", amount: 21540000, color: "#e8a838", items: 8 },
      { name: "Parks & Recreation", amount: 6800000, color: "#34d399", items: 5 },
      { name: "Public Safety", amount: 5740000, color: "#818cf8", items: 7 },
      { name: "Technology", amount: 3290000, color: "#f472b6", items: 4 },
      { name: "Housing & Community Dev", amount: 2950000, color: "#22d3ee", items: 3 },
      { name: "General Government", amount: 2550000, color: "#a78bfa", items: 6 }
    ],
    topContracts: [
      { vendor: "Town Branch Commons LLC", amount: 18040000, type: "Bond Authorization" },
      { vendor: "Parks & Recreation Dept", amount: 4200000, type: "Appropriation" },
      { vendor: "Metro Sewer District", amount: 3100000, type: "Appropriation" },
      { vendor: "Pierce Manufacturing", amount: 1850000, type: "Contract" },
      { vendor: "Axon Enterprise Inc.", amount: 890000, type: "Contract" }
    ]
  },

  // ─────────────────────────────────────────────────
  // Before/after comparison data
  // ─────────────────────────────────────────────────
  comparison: {
    without: [
      { task: "Watch 3-hour meeting recording", time: "3 hours" },
      { task: "Take manual notes on votes", time: "45 min" },
      { task: "Cross-reference with agenda PDF", time: "30 min" },
      { task: "Search for financial details in minutes", time: "1 hour" },
      { task: "Track how a member voted across meetings", time: "2+ hours" },
      { task: "Compile weekly digest for stakeholders", time: "4 hours" }
    ],
    with: [
      { task: "Full meeting summary with timestamps", time: "Instant" },
      { task: "Structured vote data with roll calls", time: "Instant" },
      { task: "Agenda items cross-referenced automatically", time: "Instant" },
      { task: "Financial items extracted and categorized", time: "Instant" },
      { task: "Query: 'How did [member] vote on [topic]?'", time: "2.3 sec" },
      { task: "Auto-generated digest with citations", time: "5 sec" }
    ]
  },

  // ─────────────────────────────────────────────────
  // Widget preview Q&A pairs
  // ─────────────────────────────────────────────────
  widgetConversation: [
    {
      role: "user",
      text: "When is the next reading of the short-term rental ordinance?"
    },
    {
      role: "assistant",
      text: "The second reading of Ordinance 0091-26 (Short-Term Rental Regulation) is scheduled for **April 14, 2026**. The first reading passed 9-6 on March 24. [Clip 6742, 1:18:45]"
    },
    {
      role: "user",
      text: "Who voted against it?"
    },
    {
      role: "assistant",
      text: "Six council members voted against the first reading: **Boone, Evans, Farmer, Kay, McCann, and Swanson**. Their primary concern was property rights — Boone stated the 300-foot notification radius was 'overly burdensome.' [Clip 6742, 1:22:30]"
    }
  ]
};
