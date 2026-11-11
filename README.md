# After the Flood

A deep research agent for flood-prone places. Name a place (a ZIP code, city or county) and optionally a storm, and it finds out:

1. **What happened:** which storms and floods hit, when, and how bad they were (NOAA Storm Events, FEMA disaster declarations).
2. **What it cost:** National Flood Insurance Program (NFIP) claims and payouts, and how often the place floods again.
3. **What came after:** did typical home values fall behind similar ZIP codes that the storm barely hit, and did they catch up?

It writes a cited report with a verdict, a claims map and a recovery chart, and says what the data can't tell you.

**Who it's for:** people buying or renting a home in a flood-prone area, local journalists, and city-planning students. Example: someone thinking about buying in Long Beach, NY wants to know how often it floods, how bad Sandy was, and whether prices came back.

**Live app:** see `deploy_url` in [submission.json](submission.json). Columbia sign-in required.

## How it works

Deep Research path: a Lead plans, three Researchers explore in parallel, and a Writer reports.

```
question ─► Lead ──(vague?)──► clarifying questions back to the user
             │ resolve_place, claims_summary (finds the events that mattered and their peak dates)
             │
             ├─► Hazard Researcher     NOAA storm events + FEMA declarations, by county  ┐
             ├─► Insurance Researcher  NFIP claims, payouts, repeat flooding, by ZIP     ├ parallel, fresh contexts
             └─► Market Researcher     Zillow home values vs. control ZIPs               ┘
             │
             │ gap in the findings? one more round for just that part
             ▼
           Writer ─► report: verdict, timeline, insurance picture, price recovery, what the data can't tell you
```

| Requirement | Where |
|---|---|
| Three or more agent shapes | `lead`, `researcher_agents` (hazard, insurance, market) and `writer` in [app.py](app.py) |
| Lead fans out in one turn, researchers run in parallel in fresh contexts | The Lead calls `research` once per researcher in the same turn; each call runs a fresh clone of that researcher (`research()` in app.py) |
| Lead goes back for more when there's a gap, up to a cap | Step 4 of the Lead's prompt: one extra round. `MAX_RESEARCHERS = 6` is enforced in code; every agent has a `max_turns` |
| Vague questions get clarification | The Lead replies `CLARIFY:` with 1 to 3 questions; the page (and `/chat`) asks them before researching |
| Accurate citations | Every tool result gets a source id (`[S4]`); the report ends with each cited source's tool call, tables and exact SQL |
| Admits what the data can't answer | Every tool returns caveats; the market tool returns warnings (later floods, the 2007-2011 housing crash, controls from other metros); the report always ends with "What the data can't tell you" |

**Control ZIPs** (the most "research" part, in `recovery_vs_controls` in [tools.py](tools.py)): to say whether a ZIP's home values recovered, we compare it with ZIPs in the same metro whose typical value was within 25% of it before the event and that had fewer than 10 NFIP claims in the event. The 15 closest by value are used. If the storm hit the whole metro, controls come from other metros in the state, and the agent says so. The relative index is (ZIP value / its pre-event baseline) / median over controls of the same ratio; the ZIP has recovered when that's back to 0.99.

## Data

One source per team member, plus Census tables to join them. All free, official, no API keys.

| Source | Table(s) | What it gives | Where it came from |
|---|---|---|---|
| OpenFEMA NFIP Redacted Claims v3 | `nfip_claims` | 1.18M flood insurance claims since 1978: date of loss, ZIP, county, flood zone, amounts paid | `https://www.fema.gov/api/open/v3/NfipClaims` (paged, no key) |
| OpenFEMA Disaster Declarations Summaries v2 | `declarations` | Federal disaster declarations by county | `https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries.parquet` |
| NOAA NCEI Storm Events Database | `storm_events`, `storm_event_counties` | Storm events since 1996: type, deaths, injuries, damage, written narratives | Yearly CSVs at `https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/`; forecast zones mapped to counties with the NWS zone-county file |
| Zillow Home Value Index (ZHVI) | `zhvi`, `zips` | Typical home value by ZIP and month since 2000 (all homes, middle tier, smoothed, seasonally adjusted) | `https://files.zillowstatic.com/research/public_csvs/zhvi/` |
| Census ZCTA-county relationships and Gazetteer | `zip_county`, `zip_centroids` | ZIP to county joins, ZIP locations for the map | `https://www2.census.gov/geo/docs/maps-data/data/` |

Coverage: NY, WA, CA, TX, MA, IL and FL. Row counts, source URLs and the build date are in [data/manifest.json](data/manifest.json).

**Loading it:** the built Parquet files are committed in `data/` (about 64 MB, every file under GitHub's 100 MB limit), so the app runs as is. To rebuild them from the sources:

```bash
uv run build_data.py              # everything (downloads a few GB into data/raw/, cached)
uv run build_data.py nfip zhvi    # only some steps
```

To cover more states, edit `STATES` at the top of [build_data.py](build_data.py).

## Run it

```bash
cp .env.example .env        # set VERTEXAI_PROJECT to a GCP project with Vertex AI enabled
gcloud auth application-default login
uv run app.py               # http://localhost:8000
uv run tools.py             # the research tools on the sample queries, no model needed
```

Models: Gemini 3.5 Flash (Lead, Writer) and Gemini 3.5 Flash Lite (Researchers) on Vertex AI.

### API

`POST /chat` keeps Project 1's response shape. Every tool call in the run, from the Lead and from every Researcher, is in `tool_calls` with the `agent` that made it.

```bash
curl -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message": "How did Hurricane Sandy affect home values in Long Beach, NY (11561)?"}'
```

```json
{
  "type": "done",
  "reply": "**Verdict**: ... [S3] ...\n\n## Sources\n...",
  "questions": [],
  "tool_calls": [
    {"agent": "Lead", "name": "resolve_place", "arguments": {"place": "11561"}, "result": {"source_id": "S1", "zips": ["11561"], "...": "..."}},
    {"agent": "Insurance Researcher 2", "name": "claims_summary", "arguments": {"zips": ["11561"], "...": "..."}, "result": {"...": "..."}}
  ],
  "messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
  "run_id": "..."
}
```

If `type` is `"clarify"`, `reply` holds the Lead's questions. Answer by posting the returned `messages` with your answer as the new `message`. The page itself uses `GET /run`, which streams the same run as server-sent events.

## Sample queries

Expected answers come straight from the tables (run `uv run tools.py` to see them). The no-tools baseline is the same model (Gemini 3.5 Flash) with no tools and no system prompt. To regenerate the baseline and agent answers below, run `uv run sample_answers.py`.

### 1. "How did Hurricane Sandy affect home values in Long Beach, NY (11561)?"

**Expected answer:**
- **Sandy's claims:** 6,505 NFIP claims in 11561, $470M paid, median $49,482 per claim, peak date of loss 2012-10-29. *Table: `nfip_claims`*
- **Federal declarations:** emergency EM-3351-NY and major disaster DR-4085-NY covered Nassau County. *Table: `declarations`*
- **Repeat flooding:** 28 years since 1978 with 10 or more claims, 14 of them since 2000. *Table: `nfip_claims`*
- **Home values vs. 15 similar ZIPs in the New York metro:** the pre-storm baseline was $470,646. 11561 fell 11.8% behind the controls and had not caught up 61 months later (still 11.5% behind). The agent should also flag the 2013 flooding inside the comparison window. *Tables: `zhvi`, `zips`, `nfip_claims`*

<!-- answer:q1:baseline -->
**No tools (Gemini 3.5 Flash, no system prompt):** _Run `uv run sample_answers.py` to fill this in._
<!-- /answer:q1:baseline -->

<!-- answer:q1:agent -->
**Agent:** _Run `uv run sample_answers.py` to fill this in._
<!-- /answer:q1:agent -->

### 2. "Which ZIP codes in Harris County, TX have flooded the most since 2000?"

**Expected answer:**
- **Top ZIPs by NFIP claims since 2000:**
  1. 77096: 5,332 claims, $667M paid
  2. 77089: 4,224 claims, $233M
  3. 77037: 3,558 claims, $142M
  4. 77088: 3,445 claims
  5. 77040: 3,220 claims

  *Table: `nfip_claims`*
- **County-wide since 2000:** 126,536 claims, $8.2B paid. *Table: `nfip_claims`*
- **Biggest events:** Hurricane Harvey (48,992 claims, $5.37B), Tropical Storm Allison (22,394 claims) and Hurricane Ike (13,360 claims). *Table: `nfip_claims`*
- **Repeat flooding:** each of the top three ZIPs had claims in 26 to 27 of the years since 2000. *Table: `nfip_claims`*

<!-- answer:q2:baseline -->
**No tools (Gemini 3.5 Flash, no system prompt):** _Run `uv run sample_answers.py` to fill this in._
<!-- /answer:q2:baseline -->

<!-- answer:q2:agent -->
**Agent:** _Run `uv run sample_answers.py` to fill this in._
<!-- /answer:q2:agent -->

### 3. "Is it safe to buy a beach house in Florida?" (vague: tests clarification)

**Expected answer:** the Lead doesn't research a whole state. It asks which place (and possibly the period, or buying vs. renting). Answered with *"Fort Myers Beach (33931), after Hurricane Ian. Buying."*, the report should say:
- **Ian's claims:** 2,611 NFIP claims in 33931, $576M paid, median about $122,000 per claim, peak date 2022-09-28. 86% of claims were in FEMA high-risk zones, and 44% were primary residences. *Table: `nfip_claims`*
- **Federal declarations:** EM-3584-FL and major disaster DR-4673-FL covered Lee County. *Table: `declarations`*
- **Repeat flooding:** 20 years with 10 or more claims, 13 of them since 2000. *Table: `nfip_claims`*
- **Home values:** the pre-Ian baseline was $692,662. 33931 fell 30.1% behind its controls and had not caught up 48 months later. *Tables: `zhvi`, `zips`, `nfip_claims`*
- **Lower confidence, with three warnings:** Hurricanes Helene (447 claims) and Milton (770 claims) hit inside the window in 2024, and the controls came from other Florida metros.
- **What it can't answer:** the agent should say it can't tell whether a specific house is safe or predict future storms.

<!-- answer:q3:baseline -->
**No tools (Gemini 3.5 Flash, no system prompt):** _Run `uv run sample_answers.py` to fill this in._
<!-- /answer:q3:baseline -->

<!-- answer:q3:agent -->
**Agent:** _Run `uv run sample_answers.py` to fill this in._
<!-- /answer:q3:agent -->

## What the data can't answer

The agent says so when these come up:

- **Whether a specific house flooded:** NFIP claims are anonymized to ZIP and census block group.
- **Uninsured damage:** only insured homes file claims, so few claims can mean few policies, not little flooding.
- **Why prices moved:** the control-ZIP comparison rules out market-wide moves, but it's correlation, not causation. Later floods and the 2007-2011 housing crash are flagged when they fall in the window.
- **The future:** these are records of the past, not climate forecasts.
- **Places outside NY, WA, CA, TX, MA, IL and FL, and NOAA events before 1996** (earlier years only recorded tornadoes, wind and hail).
- **Rents:** ZHVI tracks home values, not rents.

## Team

amo2189, jts2199, bif2111
