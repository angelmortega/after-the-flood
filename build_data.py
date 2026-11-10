"""Offline build: download the four sources and write clean Parquet tables to data/.

    uv run build_data.py              # everything
    uv run build_data.py nfip zhvi    # only some steps

Downloads are cached in data/raw/, so a re-run only rebuilds the Parquet files. Delete a file in
data/raw/ to download it again.

Tables written (all filtered to STATES):
    nfip_claims.parquet       one row per NFIP flood insurance claim (OpenFEMA NfipClaims v3)
    declarations.parquet      one row per (disaster, county) federal declaration (OpenFEMA)
    storm_events.parquet      one row per NOAA Storm Event, damage parsed to dollars
    storm_event_counties.parquet  event_id -> county_fips (zone events map to several counties)
    zhvi.parquet              (zip, month, value): Zillow typical home value, long format
    zips.parquet              one row per ZIP: city, metro, county, primary county FIPS
    zip_county.parquet        Census ZCTA -> county overlap, with each county's share of land area
    manifest.json             row counts, source URLs and build date, for citations
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import httpx

STATES = {"NY": "36", "WA": "53", "CA": "06", "TX": "48", "MA": "25", "IL": "17", "FL": "12"}
STATE_NAMES = {"NY": "NEW YORK", "WA": "WASHINGTON", "CA": "CALIFORNIA", "TX": "TEXAS", "MA": "MASSACHUSETTS", "IL": "ILLINOIS", "FL": "FLORIDA"}
# NOAA recorded only tornado, wind and hail before 1996: earlier years would skew every trend.
NOAA_FIRST_YEAR = 1996

HERE = Path(__file__).parent
DATA = HERE / "data"
RAW = DATA / "raw"

NFIP_URL = "https://www.fema.gov/api/open/v3/NfipClaims"
DECLARATIONS_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries.parquet"
NOAA_DIR = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
ZHVI_URL = "https://files.zillowstatic.com/research/public_csvs/zhvi/Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"
ZCTA_COUNTY_URL = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt"
NWS_ZONES_PAGE = "https://www.weather.gov/gis/ZoneCounty"

NFIP_COLUMNS = [
    "id", "dateOfLoss", "yearOfLoss", "state", "countyCode", "reportedZipCode", "censusGeoid",
    "latitude", "longitude", "nfipCommunityName", "floodEvent", "causeOfDamage",
    "ratedFloodZone", "floodZoneCurrent", "occupancyType", "primaryResidenceIndicator",
    "rentalPropertyIndicator", "elevatedBuildingIndicator", "postFIRMConstructionIndicator",
    "amountPaidOnBuildingClaim", "amountPaidOnContentsClaim", "amountPaidOnIncreasedCostOfComplianceClaim",
    "buildingDamageAmount", "contentsDamageAmount", "buildingPropertyValue", "waterDepth",
]
NFIP_PAGE = 10_000  # the API's maximum $top

client = httpx.Client(timeout=300, follow_redirects=True, headers={"User-Agent": "after-the-flood-build/0.1"})
manifest: dict = {}


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def download(url: str, dest: Path) -> Path:
    """Stream url to dest, unless dest is already there. Writes to .part first so an interrupted
    download is never mistaken for a finished one."""
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    log(f"downloading {url}")
    with client.stream("GET", url) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    part.rename(dest)
    return dest


def get_json(url: str, params: dict, tries: int = 4) -> dict:
    for attempt in range(tries):
        try:
            r = client.get(url, params=params)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            if attempt == tries - 1:
                raise
            log(f"  retry {attempt + 1} after {type(e).__name__}: {e}")
            time.sleep(2 ** attempt * 3)
    raise AssertionError("unreachable")


def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    c.execute("SET preserve_insertion_order = false")
    return c


def write(c: duckdb.DuckDBPyConnection, sql: str, name: str, source: str, note: str = "") -> None:
    out = DATA / f"{name}.parquet"
    c.execute(f"COPY ({sql}) TO '{out}' (FORMAT parquet, COMPRESSION zstd)")
    rows = c.execute(f"SELECT count(*) FROM '{out}'").fetchone()[0]
    manifest[name] = {"file": out.name, "rows": rows, "mb": round(out.stat().st_size / 1e6, 1), "source": source}
    if note:
        manifest[name]["note"] = note
    log(f"wrote {out.name}: {rows:,} rows, {manifest[name]['mb']} MB")


# --- NFIP claims: paged through the OpenFEMA API, one cached JSON file per page ---


def _nfip_page(state: str, skip: int) -> Path:
    dest = RAW / "nfip" / f"{state}_{skip:07d}.json"
    if dest.exists():
        return dest
    body = get_json(NFIP_URL, {
        "$filter": f"state eq '{state}'", "$select": ",".join(NFIP_COLUMNS),
        "$orderby": "id", "$top": NFIP_PAGE, "$skip": skip, "$metadata": "off",
    })
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_text(json.dumps(body["NfipClaims"]))
    tmp.rename(dest)
    return dest


def build_nfip() -> None:
    pages = []
    for state in STATES:
        meta = get_json(NFIP_URL, {"$filter": f"state eq '{state}'", "$select": "id", "$top": 1, "$count": "true"})
        total = meta["metadata"]["count"]
        log(f"NFIP {state}: {total:,} claims, {-(-total // NFIP_PAGE)} pages")
        pages += [(state, skip) for skip in range(0, total, NFIP_PAGE)]
    # A few pages at a time: fast enough, and polite to a free API.
    with ThreadPoolExecutor(max_workers=4) as pool:
        files = list(pool.map(lambda p: _nfip_page(*p), pages))
    log(f"NFIP: {len(files)} pages cached")

    c = con()
    pattern = str(RAW / "nfip" / "*.json")
    write(c, f"""
        SELECT DISTINCT ON (id)
            id,
            CAST(dateOfLoss AS DATE) AS date_of_loss,
            yearOfLoss AS year_of_loss,
            state,
            countyCode AS county_fips,
            -- ZIPs come through as text; keep only well-formed 5-digit ones
            CASE WHEN regexp_matches(reportedZipCode, '^[0-9]{{5}}$') THEN reportedZipCode END AS zip,
            censusGeoid AS census_block_group,
            latitude, longitude,
            nfipCommunityName AS community,
            NULLIF(trim(floodEvent), '') AS flood_event,
            causeOfDamage AS cause_of_damage,
            ratedFloodZone AS rated_flood_zone,
            floodZoneCurrent AS flood_zone_current,
            occupancyType AS occupancy_type,
            primaryResidenceIndicator AS primary_residence,
            rentalPropertyIndicator AS rental_property,
            elevatedBuildingIndicator AS elevated_building,
            postFIRMConstructionIndicator AS post_firm,
            coalesce(amountPaidOnBuildingClaim, 0) AS paid_building,
            coalesce(amountPaidOnContentsClaim, 0) AS paid_contents,
            coalesce(amountPaidOnIncreasedCostOfComplianceClaim, 0) AS paid_icc,
            coalesce(amountPaidOnBuildingClaim, 0) + coalesce(amountPaidOnContentsClaim, 0)
                + coalesce(amountPaidOnIncreasedCostOfComplianceClaim, 0) AS paid_total,
            buildingDamageAmount AS building_damage,
            contentsDamageAmount AS contents_damage,
            buildingPropertyValue AS building_value,
            waterDepth AS water_depth_in
        FROM read_json('{pattern}', format='array', columns={{
            {", ".join(f"'{col}': '{_nfip_type(col)}'" for col in NFIP_COLUMNS)}
        }})
        ORDER BY date_of_loss, id
    """, "nfip_claims", NFIP_URL + " (OpenFEMA NFIP Redacted Claims v3)",
        "Anonymized: ZIP, county and census block group, no addresses. Only insured properties.")


def _nfip_type(col: str) -> str:
    if col == "id":
        return "BIGINT"
    if col == "yearOfLoss":
        return "INTEGER"
    if col in ("latitude", "longitude", "waterDepth") or col.startswith(("amount", "building", "contents")):
        return "DOUBLE"
    if col.endswith("Indicator"):
        return "BOOLEAN"
    return "VARCHAR"


# --- FEMA disaster declarations: one small Parquet file ---


def build_declarations() -> None:
    raw = download(DECLARATIONS_URL, RAW / "DisasterDeclarationsSummaries.parquet")
    c = con()
    states = ", ".join(f"'{s}'" for s in STATES)
    write(c, f"""
        SELECT
            femaDeclarationString AS declaration_id,
            disasterNumber AS disaster_number,
            declarationType AS declaration_type,  -- DR major disaster, EM emergency, FM fire
            CAST(declarationDate AS DATE) AS declaration_date,
            incidentType AS incident_type,
            declarationTitle AS title,
            CAST(incidentBeginDate AS DATE) AS incident_begin,
            CAST(incidentEndDate AS DATE) AS incident_end,
            state,
            fipsStateCode || fipsCountyCode AS county_fips,  -- '000' county = statewide
            designatedArea AS designated_area,
            ihProgramDeclared AS individual_housing,
            iaProgramDeclared AS individual_assistance,
            paProgramDeclared AS public_assistance,
            hmProgramDeclared AS hazard_mitigation
        FROM '{raw}'
        WHERE state IN ({states})
        ORDER BY incident_begin, county_fips
    """, "declarations", DECLARATIONS_URL)


# --- NOAA Storm Events: yearly CSVs, damage strings parsed, zones mapped to counties ---


def build_noaa() -> None:
    listing = client.get(NOAA_DIR).text
    files = {}  # year -> newest file for that year
    for match in re.finditer(r"(StormEvents_details-ftp_v1\.0_d(\d{4})_c(\d+)\.csv\.gz)", listing):
        fname, year, _ = match.groups()
        if int(year) >= NOAA_FIRST_YEAR:
            files[year] = max(files.get(year, fname), fname)
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda f: download(NOAA_DIR + f, RAW / "noaa" / f), sorted(files.values())))
    log(f"NOAA: {len(paths)} yearly files ({min(files)}-{max(files)})")

    zones = _nws_zone_file()
    c = con()
    names = ", ".join(f"'{n}'" for n in STATE_NAMES.values())
    file_list = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
    # Old files carry a few undecodable bytes in the narratives, and unescaped quotes inside
    # quoted fields ("S OF "7 MILE" BRIDGE"): read leniently.
    c.execute(f"""
        CREATE TABLE ev AS
        SELECT * FROM read_csv({file_list}, all_varchar=true, encoding='latin-1', union_by_name=true,
                               strict_mode=false, quote='"', escape='"', header=true)
        WHERE STATE IN ({names})
    """)
    damage = lambda col: f"""
        CASE
            WHEN {col} IS NULL OR trim({col}) = '' THEN NULL
            ELSE TRY_CAST(regexp_extract(upper({col}), '^([0-9.]+)', 1) AS DOUBLE) *
                CASE regexp_extract(upper({col}), '([KMBT])$', 1)
                    WHEN 'K' THEN 1e3 WHEN 'M' THEN 1e6 WHEN 'B' THEN 1e9 WHEN 'T' THEN 1e12 ELSE 1 END
        END"""
    write(c, f"""
        SELECT
            CAST(EVENT_ID AS BIGINT) AS event_id,
            CAST(EPISODE_ID AS BIGINT) AS episode_id,
            make_date(CAST(BEGIN_YEARMONTH AS INT) // 100, CAST(BEGIN_YEARMONTH AS INT) % 100, CAST(BEGIN_DAY AS INT)) AS begin_date,
            make_date(CAST(END_YEARMONTH AS INT) // 100, CAST(END_YEARMONTH AS INT) % 100, CAST(END_DAY AS INT)) AS end_date,
            STATE AS state_name,
            lpad(STATE_FIPS, 2, '0') AS state_fips,
            EVENT_TYPE AS event_type,
            CZ_TYPE AS cz_type,  -- C county, Z forecast zone, M marine
            lpad(CZ_FIPS, 3, '0') AS cz_fips,
            CZ_NAME AS cz_name,
            CAST(INJURIES_DIRECT AS INT) + CAST(INJURIES_INDIRECT AS INT) AS injuries,
            CAST(DEATHS_DIRECT AS INT) + CAST(DEATHS_INDIRECT AS INT) AS deaths,
            {damage("DAMAGE_PROPERTY")} AS damage_property,
            {damage("DAMAGE_CROPS")} AS damage_crops,
            FLOOD_CAUSE AS flood_cause,
            EPISODE_NARRATIVE AS episode_narrative,
            EVENT_NARRATIVE AS event_narrative
        FROM ev
        WHERE CZ_TYPE IN ('C', 'Z')
        ORDER BY begin_date, event_id
    """, "storm_events", NOAA_DIR,
        f"From {NOAA_FIRST_YEAR}. Damage is the NWS estimate at the time, not adjusted for inflation; blank means not estimated, not zero.")

    # County events map to one county. Zone events map to every county the zone overlaps (NWS
    # zone-county correlation file). This file reflects today's zones: a few old zones have changed.
    c.execute(f"""
        CREATE TABLE zones AS SELECT * FROM read_csv('{zones}', delim='|', header=false, all_varchar=true,
            columns={{'state':'VARCHAR','zone':'VARCHAR','cwa':'VARCHAR','name':'VARCHAR','state_zone':'VARCHAR',
                      'county':'VARCHAR','fips':'VARCHAR','time_zone':'VARCHAR','fe_area':'VARCHAR','lat':'VARCHAR','lon':'VARCHAR'}})
    """)
    write(c, f"""
        SELECT DISTINCT e.event_id, county_fips, e.cz_type = 'Z' AS from_zone FROM (
            SELECT event_id, cz_type, state_fips || cz_fips AS county_fips FROM '{DATA / "storm_events.parquet"}' WHERE cz_type = 'C'
            UNION ALL
            SELECT s.event_id, s.cz_type, z.fips FROM '{DATA / "storm_events.parquet"}' s
            JOIN zones z ON z.fips[1:2] = s.state_fips AND lpad(z.zone, 3, '0') = s.cz_fips
            WHERE s.cz_type = 'Z'
        ) e
        ORDER BY event_id
    """, "storm_event_counties", NWS_ZONES_PAGE, "Zone events are counted in every county the zone overlaps.")
    unmapped = c.execute(f"""
        SELECT count(*) FROM '{DATA / "storm_events.parquet"}' s
        ANTI JOIN '{DATA / "storm_event_counties.parquet"}' m USING (event_id)
    """).fetchone()[0]
    log(f"NOAA: {unmapped:,} events could not be mapped to a county")
    manifest["storm_event_counties"]["unmapped_events"] = unmapped


def _nws_zone_file() -> Path:
    page = client.get(NWS_ZONES_PAGE).text
    links = re.findall(r'href="(/source/gis/Shapefiles/County/bp\w+\.dbx)"', page)
    if not links:
        raise RuntimeError(f"No zone-county correlation file linked from {NWS_ZONES_PAGE}")
    # Newest is listed last
    url = "https://www.weather.gov" + links[-1]
    return download(url, RAW / Path(url).name)


# --- Zillow ZHVI: wide CSV (a column per month) reshaped to (zip, month, value) ---


def build_zhvi() -> None:
    raw = download(ZHVI_URL, RAW / "zhvi_zip.csv")
    c = con()
    states = ", ".join(f"'{s}'" for s in STATES)
    c.execute(f"""
        CREATE TABLE wide AS SELECT * FROM read_csv('{raw}', header=true, types={{'RegionName': 'VARCHAR'}})
        WHERE State IN ({states})
    """)
    write(c, """
        SELECT lpad(RegionName, 5, '0') AS zip, CAST(month AS DATE) AS month, value
        FROM (UNPIVOT wide ON COLUMNS('^\\d{4}-\\d{2}-\\d{2}$') INTO NAME month VALUE value)
        WHERE value IS NOT NULL
        ORDER BY zip, month
    """, "zhvi", ZHVI_URL, "ZHVI all homes (SFR + condo), middle tier, smoothed, seasonally adjusted. Month = last day of month.")

    # ZIP -> county crosswalk from the Census: a ZCTA can straddle counties, so keep each one's share.
    zcta = download(ZCTA_COUNTY_URL, RAW / "zcta_county.txt")
    fips = ", ".join(f"'{f}'" for f in STATES.values())
    write(c, f"""
        WITH x AS (
            SELECT GEOID_ZCTA5_20 AS zip, GEOID_COUNTY_20 AS county_fips, NAMELSAD_COUNTY_20 AS county_name,
                   CAST(AREALAND_PART AS DOUBLE) AS land
            FROM read_csv('{zcta}', delim='|', header=true, all_varchar=true)
            WHERE GEOID_ZCTA5_20 IS NOT NULL AND GEOID_ZCTA5_20 <> '' AND GEOID_COUNTY_20[1:2] IN ({fips})
        )
        SELECT zip, county_fips, county_name, round(land / sum(land) OVER (PARTITION BY zip), 4) AS land_share
        FROM x
        ORDER BY zip, land_share DESC
    """, "zip_county", ZCTA_COUNTY_URL, "2020 ZCTAs approximate USPS ZIPs.")

    # One row per Zillow ZIP, with its main county's FIPS for joining to NOAA and FEMA.
    write(c, f"""
        WITH primary_county AS (
            SELECT DISTINCT ON (zip) zip, county_fips FROM '{DATA / "zip_county.parquet"}' ORDER BY zip, land_share DESC
        )
        SELECT lpad(w.RegionName, 5, '0') AS zip, w.City AS city, w.State AS state, w.Metro AS metro,
               w.CountyName AS county_name, p.county_fips, w.SizeRank AS size_rank
        FROM wide w LEFT JOIN primary_county p ON p.zip = lpad(w.RegionName, 5, '0')
        ORDER BY zip
    """, "zips", ZHVI_URL + " + " + ZCTA_COUNTY_URL)


STEPS = {"nfip": build_nfip, "declarations": build_declarations, "noaa": build_noaa, "zhvi": build_zhvi}

if __name__ == "__main__":
    wanted = sys.argv[1:] or list(STEPS)
    unknown = set(wanted) - set(STEPS)
    if unknown:
        sys.exit(f"Unknown step(s): {', '.join(unknown)}. Choose from: {', '.join(STEPS)}")
    DATA.mkdir(exist_ok=True)
    path = DATA / "manifest.json"
    if path.exists():
        manifest.update(json.loads(path.read_text()).get("tables", {}))
    for step in wanted:
        t = time.time()
        log(f"--- {step}")
        STEPS[step]()
        log(f"--- {step} done in {time.time() - t:.0f}s")
    path.write_text(json.dumps({
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "states": list(STATES), "tables": manifest,
    }, indent=2))
    log(f"manifest: {path}")
