# After the Flood: A Disaster Recovery Research Agent

**Course:** Agentic AI, Project 2 (Data Exploration Agent)
**Path:** Deep Research (`"path": "research"`)
**Team:** _add UNIs_

## The idea

You name a place (a ZIP code, a county or a city) and optionally an event (e.g. "Hurricane Ian", "Ida 2021"). The agent finds out:

1. **What happened:** which storms or floods hit the place, when, and how bad they were (damage, deaths, written NOAA descriptions).
2. **What it cost:** how many flood insurance claims there were, how much was paid, and whether the area floods again and again.
3. **What came after:** did home values dip and recover, and how long did it take compared with nearby areas that weren't hit?

Then it writes a cited report with a "recovery verdict" for the place.

**Target user:** People buying or renting a home in a flood-prone area, local journalists, and city-planning students. Example: someone thinking about buying in Rockaway, Queens wants to know how often it floods, how bad it gets, and whether prices bounce back.

**Why it fits the assignment:**
- The model knows big storms (Sandy, Katrina) in general, but not ZIP-level claim counts, payouts or price paths. A no-tools answer will be vague or made up, so the README baseline comparison is easy to show.
- All the data is free, official and mostly needs no API key.
- No team on the sign-up sheet uses FEMA, flood insurance or home-value data.

## Data sources (one per team member)

| Source | What it gives | Access | Size / limits |
|---|---|---|---|
| **OpenFEMA: NFIP Redacted Claims (v2)** | ~2.7M flood insurance claims since 1978: date of loss, ZIP, county, census tract, flood zone, amounts paid (building and contents), building type, elevation | REST API, **no key**: `https://www.fema.gov/api/open/v2/FimaNfipClaims` (OData filters such as `$filter=reportedZipCode eq '11693'`). Full CSV/Parquet download at fema.gov/openfema-data-page | Full file is a few GB, so pre-filter to your states and store as Parquet in GCS. API returns up to 10,000 rows per call (use `$skip`) |
| **OpenFEMA: Disaster Declarations Summaries (v2)** | Every federal disaster declaration: type, incident dates, state, county, program types | **No key**: `https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries` | Small (~65k rows), can commit to the repo |
| **NOAA NCEI Storm Events Database** | Every significant storm event by county/zone since 1950: event type, dates, deaths, injuries, property and crop damage, **written event and episode narratives** | Bulk yearly CSVs, **no key**: `https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/` (`StormEvents_details-ftp_v1.0_dYYYY_*.csv.gz`) | ~1.5M rows total. Damage is stored as text ("25K", "1.2M") and needs parsing |
| **Zillow Home Value Index (ZHVI)** | Monthly typical home value by ZIP, county and metro since 2000 | Free CSV at `https://www.zillow.com/research/data/` (ZHVI All Homes, smoothed, seasonally adjusted, by ZIP) | ~30k ZIPs × ~300 months; a wide CSV that needs reshaping to long format |

**Optional stretch:** A **Census ACS 5-year** API for income and renter share by ZIP (free key), to ask "do poorer ZIPs recover more slowly?" Or **FEMA National Risk Index** scores per census tract.

## Architecture

Recommended: the Deep Research path, with a small **DuckDB** layer under each Researcher's tools. Build Parquet files offline and load them from GCS at startup.

### Offline build step (`build_data.py`)
- Download NFIP claims, filter to the target states (or the whole US if it fits) and save as Parquet.
- Parse NOAA damage strings ("2.5M" → 2,500,000) and turn yearly files into one table.
- Reshape ZHVI from wide to long: `(zip, month, value)`.
- Build **join tables**: ZIP → county FIPS (HUD USPS ZIP–County crosswalk, free with a HUD account, or the Census ZCTA relationship file).
- Upload everything to a GCS bucket. The app downloads it at startup.

### Lead
- If the input is vague ("Is Florida safe?"), asks 1 to 3 clarifying questions: which place (ZIP/city), which time period, buying or renting.
- Resolves the place to ZIP(s) and a county FIPS code, and picks the events to look at.
- Sends work to the three Researchers in parallel in one turn.
- If findings leave a gap (e.g. NOAA shows a big event but there are no NFIP claims), runs one more round, e.g. "check the neighboring ZIPs" or "check whether the area was declared a disaster."
- Cap: 2 rounds.

### Researchers (x3, fresh contexts)
1. **Hazard Researcher (NOAA + FEMA declarations):** events in the county, their damage, written descriptions, and federal declarations.
2. **Insurance Researcher (NFIP):** claim counts, amounts paid, repeat-flooding patterns (same ZIP flooding in several years), and flood-zone mix.
3. **Market Researcher (Zillow):** home-value path before and after each event, recovery time, and a comparison with **control ZIPs** (nearby ZIPs in the same metro with few claims).

Each returns findings in a shared format: `claim, number, source table/endpoint, query used, caveat`.

### Writer
Writes the report:
1. **Verdict:** e.g. "Repeat flood zone; prices recovered within 18 months after Sandy" + confidence level
2. **Timeline of events** (with NOAA and FEMA citations)
3. **Insurance picture** (claims, payouts, how often)
4. **Price recovery** with a chart (before/after, compared with control ZIPs)
5. **What the data can't tell you** (see below)

## Frontend ideas
- Search box for a ZIP or address, plus an event dropdown
- A **map** (Leaflet + free OpenStreetMap tiles) with ZIPs colored by total claims
- A **"recovery curve"** chart: the ZIP's home value versus control ZIPs, with event dates marked by vertical lines
- A live panel showing the three Researchers working side by side
- An "About" section saying what the agent explores, who it's for and how to use it (required)

## README sample queries

1. **"How did Hurricane Sandy affect home values in Rockaway, Queens (11693/11694)?"** Combines NFIP claims in 2012, the NOAA Sandy description and the ZHVI recovery path.
2. **"Which ZIP codes in Harris County, TX have flooded the most since 2000?"** Mostly NFIP: claim counts by ZIP and year, cross-checked with NOAA flood events.
3. **"Is Fort Myers Beach safe to buy in?"** A vague question that triggers clarification. After that, the answer covers Hurricane Ian (2022) claims and the ZHVI path, and says what the data can't tell you.

For each one: the expected answer, the table or endpoint it comes from, and the no-tools answer side by side with the agent's.

## What the data can't answer (the agent must say so)
- **Whether a specific house flooded:** NFIP data is anonymized to ZIP or census tract level.
- **Uninsured damage:** NFIP only covers insured properties, and many flooded homes have no flood policy. Low claim counts can mean low coverage, not low risk.
- **Causation:** a price drop after a storm could also come from interest rates or the wider market. That's why control ZIPs matter, and even then the result is correlation.
- **Future risk:** these records are historical, not climate forecasts.
- **Rentals:** ZHVI tracks home values. Zillow's rent index (ZORI) only starts in 2015 and covers fewer ZIPs.

## Team split

| Person | Owns |
|---|---|
| Person 1 | NOAA Storm Events + FEMA declarations data, Hazard Researcher, Lead agent and `/chat` endpoint |
| Person 2 | NFIP claims data (biggest dataset), offline build step, GCS upload, Insurance Researcher |
| Person 3 | Zillow ZHVI + control-ZIP logic, Market Researcher, Writer, frontend (map + chart), Cloud Run deploy |

## Main development challenges

1. **Joining across geographies.** NFIP uses ZIP, census tract and county. NOAA uses county FIPS *or* forecast zones (a weather-service geography that is not a county). Zillow uses ZIP. You need a ZIP ↔ county crosswalk, and NOAA zone events (`CZ_TYPE = 'Z'`) need a zone → county mapping (NWS publishes one). Get this wrong and the agent joins the wrong places.
2. **NFIP data size and gaps.** The full claims file is several GB. Pre-filter, use Parquet, and query with DuckDB. Some ZIP fields are blank or misspelled, so fall back to county.
3. **Messy NOAA damage fields.** Values like `"10.00K"`, `"1.5M"`, `"0"` or blank need parsing. Before ~1996 only some event types were recorded, so trends over long periods are skewed. The agent should know this.
4. **Choosing control ZIPs fairly.** "Prices recovered" only means something compared with similar ZIPs that weren't hit. Use a simple rule (same metro, similar starting price, low claims) and explain it in the README. This is the most "research-y" part and good for the creativity grade.
5. **Matching event names to records.** Users say "Sandy", but NFIP has no storm-name column for most claims, only the date of loss. Map named storms to date windows and states (FEMA declarations help here).
6. **Response time.** Three DuckDB-backed Researchers are fast, but the LLM turns add up. Cap Researchers at ~5 tool calls, set the Cloud Run timeout to 300s, and run gunicorn with `--timeout 300` (or uvicorn).
7. **Startup time on Cloud Run.** Downloading a large Parquet file from GCS on every cold start is slow. Keep the deployed files small (e.g. filtered to coastal states), or set minimum instances to 1.
8. **Not overclaiming.** The Writer must not say "the flood caused prices to drop." Use a prompt rule plus a check that every number in the report comes from a tool result.

## Submission checklist
- [ ] Sign-up sheet entry (dataset + questions + path)
- [ ] `app.py`, `pyproject.toml`, `uv.lock` at repo root
- [ ] `README.md`: description, target user, data sources and how to load them, 3 sample queries with expected answers and no-tools baseline
- [ ] `submission.json` with `deploy_url`, all `authors`, `"path": "research"`
- [ ] Cloud Run with continuous deploy from GitHub; data in GCS if > 100 MB
- [ ] Graders added as collaborators if the repo is private: codeboi07, bhuvighosh3, nniishhh, x
