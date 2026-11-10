"""Research tools: DuckDB queries over the Parquet tables in data/.

Plain functions, so they can be tested without a model: `uv run tools.py` runs the README's sample
queries. app.py wraps them as agent tools.

Every function returns a dict with the answer and a "source": the tables it read and the SQL it
ran, with the parameters filled in. That is what the Writer cites.
"""

import re
import statistics
from datetime import date, timedelta
from pathlib import Path

import duckdb

DATA = Path(__file__).parent / "data"
TABLES = {
    "nfip_claims": "NFIP flood insurance claims (OpenFEMA NFIP Redacted Claims v3)",
    "declarations": "FEMA disaster declarations (OpenFEMA Disaster Declarations Summaries v2)",
    "storm_events": "NOAA Storm Events Database",
    "storm_event_counties": "NOAA storm event -> county (NWS zone-county correlation)",
    "zhvi": "Zillow Home Value Index (ZHVI), typical home value by ZIP and month",
    "zips": "ZIP metadata: city, metro, county (Zillow + Census)",
    "zip_county": "Census ZCTA -> county overlap",
}
# NOAA event types that are floods, or storms that bring floods.
FLOOD_EVENT_TYPES = [
    "Flash Flood", "Flood", "Coastal Flood", "Storm Surge/Tide", "Lakeshore Flood", "Heavy Rain",
    "Hurricane (Typhoon)", "Hurricane", "Tropical Storm", "Tropical Depression", "Seiche", "Tsunami",
]
MAX_ROWS = 50  # rows any tool hands the model

_con = duckdb.connect()
for _name in TABLES:
    _con.execute(f"CREATE VIEW {_name} AS SELECT * FROM '{DATA / _name}.parquet'")


# --- Helpers ---


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_literal(v) for v in value) + "]"
    return "'" + str(value).replace("'", "''") + "'"


def _query(sql: str, params: dict | None = None) -> tuple[list[dict], str]:
    """Run sql with $name parameters. Returns the rows as dicts, and the SQL with the parameters
    written in, for citing."""
    # DuckDB rejects parameters the query doesn't use: pass only the ones it names.
    used = set(re.findall(r"\$(\w+)", sql))
    params = {k: v for k, v in (params or {}).items() if k in used}
    # A cursor per call: the researchers run in parallel.
    cur = _con.cursor()
    rel = cur.execute(sql, params)
    columns = [d[0] for d in rel.description]
    rows = [dict(zip(columns, r)) for r in rel.fetchall()]
    shown = re.sub(r"\$(\w+)", lambda m: _literal(params[m.group(1)]) if m.group(1) in params else m.group(0), sql)
    return [_clean(r) for r in rows], " ".join(shown.split())


def _clean(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, float):
            v = round(v, 2) if abs(v) < 100 else round(v)
        elif isinstance(v, date):
            v = v.isoformat()
        out[k] = v
    return out


def _source(tables: list[str], *queries: str) -> dict:
    return {"tables": {t: TABLES[t] for t in tables}, "queries": list(queries)}


def _zips(zips: list[str] | str) -> list[str]:
    if isinstance(zips, str):
        zips = re.findall(r"\d{5}", zips)
    return sorted({z.strip().zfill(5) for z in zips if z.strip()})


def _date(value: str | date | None, default: str) -> str:
    return (value.isoformat() if isinstance(value, date) else value) or default


# --- Place ---


def resolve_place(place: str, state: str | None = None) -> dict:
    """Turn a ZIP, city or county name into ZIPs, county FIPS codes and metro areas.

    Neighborhoods ("Rockaway") are not in the data: pass their ZIPs instead.
    """
    state = state.upper() if state else None
    zips = re.findall(r"\b\d{5}\b", place)
    params: dict = {"state": state}
    if zips:
        params["zips"] = zips
        where = "c.zip IN (SELECT unnest($zips))"
        how = "zip"
    elif re.search(r"\bcounty\b", place, re.I):
        params["name"] = re.sub(r"(?i),?\s*\b[A-Z]{2}$", "", place).strip()
        where = "c.county_name ILIKE $name || '%'"
        how = "county"
    else:
        params["name"] = re.sub(r"(?i),\s*[A-Z]{2}$", "", place).strip()
        where = "z.city ILIKE $name"
        how = "city"
    rows, sql = _query(f"""
        SELECT c.zip, z.city, coalesce(z.state, s.state) AS state, z.metro, c.county_fips, c.county_name,
               c.land_share, z.zip IS NOT NULL AS has_home_values
        FROM zip_county c
        LEFT JOIN zips z USING (zip)
        LEFT JOIN (SELECT DISTINCT state, county_fips[1:2] AS st FROM declarations) s ON s.st = c.county_fips[1:2]
        WHERE {where} AND ($state IS NULL OR coalesce(z.state, s.state) = $state)
        ORDER BY c.zip, c.land_share DESC
    """, params)
    if not rows:
        return {"error": f"No {how} matching {place!r} in NY, WA, CA, TX, MA, IL or FL. "
                         "Try a ZIP code, a city name, or '<name> County'."}
    counties = {}
    for r in rows:
        counties.setdefault(r["county_fips"], {"county_fips": r["county_fips"], "county_name": r["county_name"], "state": r["state"]})
    return {
        "matched_by": how,
        "zips": sorted({r["zip"] for r in rows}),
        "zips_without_home_values": sorted({r["zip"] for r in rows if not r["has_home_values"]}),
        "counties": list(counties.values()),
        "metros": sorted({r["metro"] for r in rows if r["metro"]}),
        "source": _source(["zip_county", "zips"], sql),
    }


# --- Hazard researcher: NOAA storm events + FEMA declarations ---


def storm_events(county_fips: str, start: str | None = None, end: str | None = None,
                 flood_only: bool = True, limit: int = 10) -> dict:
    """NOAA storm events in a county: totals by event type, and the most damaging episodes with
    their written narratives."""
    params = {"county": county_fips, "start": _date(start, "1996-01-01"), "end": _date(end, "2100-01-01"),
              "types": FLOOD_EVENT_TYPES if flood_only else None, "limit": min(limit, 20)}
    base = """
        SELECT e.* FROM storm_events e JOIN storm_event_counties c USING (event_id)
        WHERE c.county_fips = $county AND e.begin_date BETWEEN $start AND $end
    """ + ("AND e.event_type IN (SELECT unnest($types))" if flood_only else "")
    by_type, sql1 = _query(f"""
        SELECT event_type, count(*) AS events, count(DISTINCT year(begin_date)) AS years_with_events,
               sum(deaths) AS deaths, sum(injuries) AS injuries, sum(damage_property) AS property_damage_usd,
               min(begin_date) AS first, max(begin_date) AS last
        FROM ({base}) GROUP BY event_type ORDER BY events DESC
    """, params)
    episodes, sql2 = _query(f"""
        SELECT episode_id, min(begin_date) AS begin_date, max(end_date) AS end_date,
               string_agg(DISTINCT event_type, ', ') AS event_types, count(*) AS events,
               sum(deaths) AS deaths, sum(damage_property) AS property_damage_usd,
               left(any_value(episode_narrative), 700) AS episode_narrative,
               left(arg_max(event_narrative, coalesce(damage_property, 0)), 500) AS worst_event_narrative
        FROM ({base}) GROUP BY episode_id
        ORDER BY coalesce(sum(damage_property), 0) DESC, sum(deaths) DESC, events DESC
        LIMIT $limit
    """, params)
    return {
        "county_fips": county_fips,
        "by_event_type": by_type,
        "top_episodes": episodes,
        "caveats": [
            "Damage is the NWS estimate at the time, in nominal dollars; blank means not estimated, not zero.",
            "Events reported for a forecast zone are counted in every county the zone overlaps.",
            "Records start in 1996.",
        ],
        "source": _source(["storm_events", "storm_event_counties"], sql1, sql2),
    }


def declarations(county_fips: str, start: str | None = None, end: str | None = None) -> dict:
    """Federal disaster declarations for a county (including statewide ones), newest first."""
    params = {"county": county_fips, "start": _date(start, "1950-01-01"), "end": _date(end, "2100-01-01")}
    rows, sql = _query("""
        SELECT declaration_id, declaration_type, title, incident_type, incident_begin, incident_end,
               declaration_date, designated_area, individual_assistance, public_assistance, hazard_mitigation
        FROM declarations
        WHERE (county_fips = $county OR county_fips = $county[1:2] || '000')
          AND incident_begin BETWEEN $start AND $end
        ORDER BY incident_begin DESC
    """, params)
    by_type = {}
    for r in rows:
        by_type[r["incident_type"]] = by_type.get(r["incident_type"], 0) + 1
    return {
        "county_fips": county_fips,
        "count": len(rows),
        "by_incident_type": by_type,
        "declarations": rows[:MAX_ROWS],
        "caveats": ["DR = major disaster, EM = emergency, FM = fire management. A county '000' row is statewide."],
        "source": _source(["declarations"], sql),
    }


# --- Insurance researcher: NFIP claims ---


def claims_summary(zips: list[str] | str | None = None, county_fips: str | None = None,
                   start: str | None = None, end: str | None = None, group_by: str = "event") -> dict:
    """NFIP claims and payouts for some ZIPs, or a county, grouped by flood event, year or ZIP."""
    if not zips and not county_fips:
        return {"error": "Pass zips or county_fips."}
    groups = {
        "event": "coalesce(flood_event, 'Unnamed (' || year_of_loss || ')')",
        "year": "year_of_loss",
        "zip": "zip",
        "flood_zone": "coalesce(left(flood_zone_current, 1), '?')",
    }
    if group_by not in groups:
        return {"error": f"group_by must be one of {', '.join(groups)}"}
    params = {"zips": _zips(zips) if zips else None, "county": county_fips,
              "start": _date(start, "1970-01-01"), "end": _date(end, "2100-01-01")}
    # Only the filters that apply, so the cited SQL reads cleanly.
    where = " AND ".join(
        (["zip IN (SELECT unnest($zips))"] if params["zips"] else [])
        + (["county_fips = $county"] if county_fips else [])
        + ["date_of_loss BETWEEN $start AND $end"]
    )
    total, sql1 = _query(f"""
        SELECT count(*) AS claims, sum(paid_total) AS paid_usd, median(paid_total) AS median_paid_usd,
               count(*) FILTER (WHERE paid_total > 0) AS claims_paid,
               count(DISTINCT year_of_loss) AS years_with_claims,
               min(date_of_loss) AS first_loss, max(date_of_loss) AS last_loss,
               round(100.0 * avg(CASE WHEN primary_residence THEN 1 ELSE 0 END), 1) AS pct_primary_residence,
               round(100.0 * avg(CASE WHEN left(flood_zone_current, 1) IN ('A', 'V') THEN 1 ELSE 0 END), 1)
                   AS pct_in_high_risk_zone
        FROM nfip_claims WHERE {where}
    """, params)
    rows, sql2 = _query(f"""
        SELECT {groups[group_by]} AS {group_by}, count(*) AS claims, sum(paid_total) AS paid_usd,
               median(paid_total) AS median_paid_usd, mode(date_of_loss) AS peak_loss_date,
               min(date_of_loss) AS first_loss, max(date_of_loss) AS last_loss
        FROM nfip_claims WHERE {where}
        GROUP BY 1 ORDER BY {"1" if group_by == "year" else "claims DESC"}
        LIMIT {MAX_ROWS}
    """, params)
    return {
        "total": total[0],
        f"by_{group_by}": rows,
        "caveats": [
            "Only insured properties file claims: few claims can mean few policies, not little flooding.",
            "Anonymized to ZIP and census block group; no addresses.",
            "Amounts are nominal dollars. High-risk zone = FEMA zones A and V.",
        ],
        "source": _source(["nfip_claims"], sql1, sql2),
    }


def repeat_flooding(zips: list[str] | str, min_claims: int = 10) -> dict:
    """How often these ZIPs flood again: the years with at least min_claims claims, and the gaps
    between them."""
    params = {"zips": _zips(zips), "min": min_claims}
    rows, sql = _query("""
        SELECT year_of_loss AS year, count(*) AS claims, sum(paid_total) AS paid_usd,
               string_agg(DISTINCT coalesce(flood_event, 'unnamed'), ', ') AS events
        FROM nfip_claims WHERE zip IN (SELECT unnest($zips))
        GROUP BY 1 HAVING count(*) >= $min ORDER BY 1
    """, params)
    years = [r["year"] for r in rows]
    gaps = [b - a for a, b in zip(years, years[1:])]
    return {
        "zips": params["zips"],
        "threshold_claims_per_year": min_claims,
        "flood_years": len(years),
        "flood_years_since_2000": sum(1 for y in years if y >= 2000),
        "median_years_between_floods": statistics.median(gaps) if gaps else None,
        "years": rows,
        "caveats": ["A 'flood year' is a year with at least the threshold number of claims in these ZIPs combined."],
        "source": _source(["nfip_claims"], sql),
    }


# --- Market researcher: Zillow home values ---


def _baseline(zip_code: str, event: date) -> float | None:
    """Mean ZHVI over the 3 months before the event."""
    rows, _ = _query("""
        SELECT avg(value) AS v FROM zhvi
        WHERE zip = $zip AND month >= $from AND month < $event
    """, {"zip": zip_code, "from": (event - timedelta(days=95)).isoformat(), "event": event.isoformat()})
    return rows[0]["v"] if rows and rows[0]["v"] else None


def price_path(zip_code: str, event_date: str, months_before: int = 12, months_after: int = 36) -> dict:
    """A ZIP's typical home value around an event: baseline, low point, and change at 1, 2 and 3 years."""
    event = date.fromisoformat(event_date)
    params = {"zip": zip_code, "event": event.isoformat(),
              "from": (event - timedelta(days=31 * months_before)).isoformat(),
              "to": (event + timedelta(days=31 * months_after)).isoformat()}
    rows, sql = _query("""
        SELECT month, value FROM zhvi WHERE zip = $zip AND month BETWEEN $from AND $to ORDER BY month
    """, params)
    if not rows:
        return {"error": f"No Zillow home values for ZIP {zip_code} around {event_date}."}
    base = _baseline(zip_code, event)
    after = [r for r in rows if r["month"] >= event_date]
    low = min(after, key=lambda r: r["value"]) if after else None

    def at(months: int):
        target = (event + timedelta(days=round(30.44 * months))).isoformat()[:7]
        hit = next((r for r in rows if r["month"][:7] == target), None)
        return {"month": hit["month"], "value": hit["value"], "pct_vs_baseline": round(100 * (hit["value"] / base - 1), 1)} if hit and base else None

    return {
        "zip": zip_code,
        "event_date": event_date,
        "baseline_value": round(base) if base else None,
        "low_after_event": {**low, "pct_vs_baseline": round(100 * (low["value"] / base - 1), 1)} if low and base else None,
        "after_1_year": at(12), "after_2_years": at(24), "after_3_years": at(36),
        "series": rows,
        "caveats": ["ZHVI is smoothed and seasonally adjusted: a sudden drop shows up spread over several months.",
                    "Baseline = mean of the 3 months before the event."],
        "source": _source(["zhvi"], sql),
    }


def recovery_vs_controls(zip_code: str, event_date: str, event_name: str | None = None,
                         months_after: int = 60, max_controls: int = 15) -> dict:
    """Compare a ZIP's home values after an event with similar ZIPs nearby that the event barely hit.

    Control ZIPs: same metro, baseline home value within 25% of the ZIP's, and fewer than 10 NFIP
    claims in the event (by event name if given, else in the 30 days after the date). The closest
    max_controls by baseline value are used. If the event hit the whole metro and fewer than 5 ZIPs
    qualify, controls come from the rest of the state instead.

    Relative index = (ZIP value / ZIP baseline) / median over controls of (control value / its baseline).
    1.0 means the ZIP moved exactly like its controls. Recovered = first month after the low point
    when the relative index is back to 0.99 or more.
    """
    event = date.fromisoformat(event_date)
    base = _baseline(zip_code, event)
    if not base:
        return {"error": f"No Zillow home values for ZIP {zip_code} before {event_date}."}
    params = {
        "zip": zip_code, "event": event.isoformat(), "name": event_name,
        "from": (event - timedelta(days=95)).isoformat(),
        "loss_to": (event + timedelta(days=30)).isoformat(),
        "lo": base * 0.75, "hi": base * 1.25, "n": max_controls,
    }
    control_sql = """
        WITH target AS (SELECT metro, state FROM zips WHERE zip = $zip),
        base AS (
            SELECT zip, avg(value) AS baseline FROM zhvi
            WHERE month >= $from AND month < $event GROUP BY zip
        ),
        hit AS (
            SELECT zip, count(*) AS claims FROM nfip_claims
            WHERE CASE WHEN $name IS NOT NULL THEN flood_event ILIKE '%' || $name || '%'
                       ELSE date_of_loss BETWEEN $event AND $loss_to END
            GROUP BY zip
        )
        SELECT z.zip, z.city, b.baseline, coalesce(h.claims, 0) AS event_claims
        FROM zips z JOIN target t ON {scope}
        JOIN base b USING (zip) LEFT JOIN hit h USING (zip)
        WHERE z.zip <> $zip AND b.baseline BETWEEN $lo AND $hi AND coalesce(h.claims, 0) < 10
        ORDER BY abs(b.baseline - (SELECT baseline FROM base WHERE zip = $zip))
        LIMIT $n
    """
    control_scope = "same metro"
    controls, sql1 = _query(control_sql.format(scope="z.metro = t.metro"), params)
    if len(controls) < 5:
        # The event hit the whole metro: compare with similar ZIPs elsewhere in the state.
        control_scope = "same state, other metros (too few unaffected ZIPs in the metro)"
        controls, sql1 = _query(control_sql.format(scope="z.state = t.state AND z.metro IS DISTINCT FROM t.metro"), params)
    if len(controls) < 3:
        return {"error": f"Only {len(controls)} control ZIPs found for {zip_code}: too few to compare.",
                "controls": controls, "source": _source(["zips", "zhvi", "nfip_claims"], sql1)}

    params2 = {"zips": [zip_code] + [c["zip"] for c in controls], "from": params["from"],
               "to": (event + timedelta(days=31 * months_after)).isoformat()}
    series, sql2 = _query("""
        SELECT zip, month, value FROM zhvi
        WHERE zip IN (SELECT unnest($zips)) AND month BETWEEN $from AND $to ORDER BY month
    """, params2)
    baselines = {c["zip"]: c["baseline"] for c in controls} | {zip_code: base}
    by_month: dict[str, dict[str, float]] = {}
    for r in series:
        by_month.setdefault(r["month"], {})[r["zip"]] = r["value"] / baselines[r["zip"]]

    path = []
    for month, idx in sorted(by_month.items()):
        if month < event_date or zip_code not in idx:
            continue
        ctrl = [v for z, v in idx.items() if z != zip_code]
        if not ctrl:
            continue
        ctrl_median = statistics.median(ctrl)
        path.append({"month": month, "zip_index": round(idx[zip_code], 3),
                     "controls_index": round(ctrl_median, 3), "relative": round(idx[zip_code] / ctrl_median, 3)})
    if not path:
        return {"error": "No home values after the event.", "source": _source(["zhvi"], sql1, sql2)}

    low = min(path, key=lambda p: p["relative"])
    recovered = next((p for p in path if p["month"] > low["month"] and p["relative"] >= 0.99), None)
    months_to = lambda p: round((date.fromisoformat(p["month"]) - event).days / 30.44)
    if low["relative"] >= 0.99:
        verdict = "No measurable dip relative to the control ZIPs."
    elif recovered:
        verdict = f"Fell {round(100 * (1 - low['relative']), 1)}% behind the controls, caught up after {months_to(recovered)} months."
    else:
        verdict = (f"Fell {round(100 * (1 - low['relative']), 1)}% behind the controls and had not caught up "
                   f"{months_to(path[-1])} months later ({round(100 * (path[-1]['relative'] - 1), 1)}% at the end).")
    return {
        "zip": zip_code,
        "event_date": event_date,
        "baseline_value": round(base),
        "control_scope": control_scope,
        "controls": controls,
        "low_point": {**low, "months_after_event": months_to(low)},
        "recovered": {**recovered, "months_after_event": months_to(recovered)} if recovered else None,
        "verdict": verdict,
        "path_yearly": [p for i, p in enumerate(path) if i % 12 == 0] + ([path[-1]] if (len(path) - 1) % 12 else []),
        "method": recovery_vs_controls.__doc__.split("\n\n", 1)[1].strip(),
        "caveats": ["This shows correlation, not causation: the controls rule out market-wide moves, not everything.",
                    "ZHVI is smoothed: short dips are spread out and look smaller."],
        "source": _source(["zips", "zhvi", "nfip_claims"], sql1, sql2),
    }


# --- Escape hatch: read-only SQL ---

_FORBIDDEN = re.compile(r"\b(copy|attach|detach|install|load|pragma|set|export|import|create|insert|update|delete|drop|alter|call)\b|read_\w+\s*\(|[/'\"]\s*\.\w|;", re.I)


def sql(query: str) -> dict:
    """Run a read-only SELECT over the tables. At most 50 rows come back."""
    q = query.strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|with)\b", q) or _FORBIDDEN.search(q) or re.search(r"'[^']*\.(parquet|csv|json)'", q, re.I):
        return {"error": "Only a single SELECT (or WITH ... SELECT) over the named tables is allowed."}
    try:
        rows, shown = _query(f"SELECT * FROM ({q}) LIMIT {MAX_ROWS + 1}")
    except duckdb.Error as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS, "source": {"queries": [shown]}}


def describe_tables() -> dict:
    """The tables and their columns, for writing sql()."""
    out = {}
    for name, desc in TABLES.items():
        cols, _ = _query(f"SELECT column_name, column_type FROM (DESCRIBE {name})")
        out[name] = {"description": desc, "columns": {c["column_name"]: c["column_type"] for c in cols}}
    return out


if __name__ == "__main__":
    import json
    import sys

    def show(label, result):
        print(f"\n=== {label}")
        text = json.dumps({k: v for k, v in result.items() if k not in ("source", "series")}, indent=1, default=str)
        print(text[:2500] + ("\n..." if len(text) > 2500 else ""))

    which = sys.argv[1:] or ["rockaway", "harris", "fortmyers"]
    if "rockaway" in which:
        show("resolve 11691 11692 11694", resolve_place("11691 11692 11693 11694"))
        show("Sandy claims, Rockaway", claims_summary(["11691", "11692", "11693", "11694"], start="2012-10-01", end="2012-12-31"))
        show("Repeat flooding, Rockaway", repeat_flooding(["11691", "11692", "11693", "11694"]))
        show("Queens storm events around Sandy", storm_events("36081", "2012-10-01", "2012-11-30", limit=3))
        show("Recovery 11694 after Sandy", recovery_vs_controls("11694", "2012-10-29", "Sandy"))
    if "harris" in which:
        show("resolve Harris County", {k: v for k, v in resolve_place("Harris County", "TX").items() if k != "zips"})
        show("Harris claims by ZIP since 2000", claims_summary(county_fips="48201", start="2000-01-01", group_by="zip"))
        show("Harris declarations", declarations("48201", "2000-01-01"))
    if "fortmyers" in which:
        show("resolve Fort Myers Beach", resolve_place("Fort Myers Beach", "FL"))
        show("Ian claims 33931", claims_summary(["33931"], start="2022-09-01", end="2022-12-31"))
        show("Price path 33931 after Ian", price_path("33931", "2022-09-28"))
        show("Recovery 33931 after Ian", recovery_vs_controls("33931", "2022-09-28", "Ian"))
