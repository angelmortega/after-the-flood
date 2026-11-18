# After the Flood

**Live app:** https://after-the-flood-859932602393.us-central1.run.app (sign in with your Columbia account)

A deep research agent for flood-prone places. Name a ZIP code, city or county, and optionally a storm, and it finds out:

- **What happened:** the storms and floods that hit, and the federal disaster declarations that followed.
- **What it cost:** flood insurance (NFIP) claims and payouts, and how often the place floods again.
- **What came after:** whether home values fell behind similar ZIP codes the storm barely touched, and whether they caught up.

It returns a short cited report with a verdict, a claims map and a home value chart, and it says what the data can't tell you. You can ask about one storm ("How did Ian affect Fort Myers Beach?") or about a place in general ("Which parts of Palm Bay flood?").

**Who it's for:** people buying or renting in a flood-prone area, local journalists, and city-planning students.

## How it works

Deep Research path: a **Lead** plans, three **Researchers** work in parallel, and a **Writer** reports.

1. **Lead:** asks clarifying questions if the question is vague (e.g. a whole state), resolves the place to ZIP codes and counties, and finds the floods that mattered.
2. **Researchers**, run in parallel in fresh contexts, in one turn:
   - **Hazard:** NOAA storm events and FEMA declarations
   - **Insurance:** NFIP claims and repeat flooding
   - **Market:** Zillow home values
3. **Second round:** if the findings leave a gap, the Lead sends one more round, capped at 6 researchers in total.
4. **Writer:** writes the report. Every number cites a source id like `[S4]`, and the report ends with the tool call and exact SQL behind each source.

**Similar-ZIP comparison:** to judge recovery, the Market Researcher compares the ZIP with up to 15 ZIPs in the same metro that had similar home values before the storm but almost no flood claims in it. It flags anything that muddies the comparison, such as later floods in the window, the 2007-2011 housing crash, or comparison ZIPs from other metros.

## Data

Four official, free sources, one per team member, joined by ZIP and county with Census tables. Coverage: NY, WA, CA, TX, MA, IL and FL.

| Source | What it gives | Where it came from |
|---|---|---|
| OpenFEMA NFIP Redacted Claims v3 (`nfip_claims`) | 1.18M flood insurance claims since 1978 | `fema.gov/api/open/v3/NfipClaims` |
| OpenFEMA Disaster Declarations Summaries v2 (`declarations`) | Federal disaster declarations by county | `fema.gov/api/open/v2/DisasterDeclarationsSummaries` |
| NOAA NCEI Storm Events (`storm_events`) | Storm events since 1996: deaths, damage, narratives | `ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/` |
| Zillow Home Value Index (`zhvi`) | Typical home value by ZIP and month since 2000 | `zillow.com/research/data` |

**Loading it:** the built Parquet files are committed in `data/`, about 64 MB with every file under 100 MB, so the app runs as is. `uv run build_data.py` rebuilds them from the sources. Row counts and source URLs are in [data/manifest.json](data/manifest.json).

## Run it locally

```bash
cp .env.example .env            # set VERTEXAI_PROJECT to a GCP project with Vertex AI
gcloud auth application-default login
uv run app.py                   # http://localhost:8000
```

`POST /chat` with `{"message": "..."}` returns Project 1's shape: `reply` plus every tool call from the Lead and each Researcher in `tool_calls`, each with an `agent` field.

## Sample queries

Each query shows the expected answer from the tables, then the same model (Gemini 3.5 Flash) with no tools and no system prompt, next to the agent's answer. The answers were generated with `uv run sample_answers.py`.

### 1. "How did Hurricane Sandy affect home values in Long Beach, NY (11561)?"

**Expected:**
- **Claims:** 6,505 NFIP claims in 11561 after Sandy, $470M paid (`nfip_claims`).
- **Declarations:** EM-3351-NY and DR-4085-NY covered Nassau County (`declarations`).
- **Repeat flooding:** 28 years with 10 or more claims, 14 of them since 2000 (`nfip_claims`).
- **Home values vs. 15 similar ZIPs in the New York metro (`zhvi`):** even at 6 months (+0.2 pts), then 5.7 pts behind at 1 year and 15.3 pts behind at 5 years.
- **Flag:** a February 2013 flood that overlaps the window.

<!-- answer:q1:baseline -->
**No tools (Gemini 3.5 Flash, no system prompt):**

<details><summary>Show the answer</summary>

Hurricane Sandy, which struck on October 29, 2012, was a defining event for the real estate market in Long Beach, NY (11561). As a barrier island, Long Beach took a direct hit from a massive storm surge, leaving nearly every home on the island with some degree of damage. 

The impact of Hurricane Sandy on home values in Long Beach followed a distinct **"V-shaped" trajectory**: a sharp immediate decline, followed by a prolonged, regulatory-driven transition, and ultimately a significant long-term increase in property values, albeit with a changed demographic and architectural landscape.

Here is a detailed breakdown of how Hurricane Sandy affected home values in Long Beach (11561) over time:

---

### 1. The Immediate Aftermath (2012–2014): The Shock and Decline
In the first 12 to 24 months after the storm, the Long Beach real estate market experienced severe disruption.
* **Price Drops for Damaged Homes:** Homes that suffered major flooding (particularly the historic "walk-in" basements and ground-floor bungalows in the West End) saw their values plummet. "As-is" damaged properties were frequently sold at discounts of **20% to 40%** compared to their pre-storm values.
* **The Rise of Cash Investors:** Traditional buyers vanished because banks refused to write mortgages on homes without certificates of occupancy or those requiring massive remediation. This opened the door for cash-rich investors and flippers who bought flooded homes cheaply, gutted them, and sold them for a profit.
* **Inventory Spike:** A wave of inventory hit the market from seniors and middle-class families who could not afford to rebuild or wait for insurance/FEMA payouts (which were notoriously delayed).

### 2. The Rebuilding Phase (2014–2018): The "Two-Tier" Market
As federal aid (such as NY Rising) and flood insurance payouts slowly trickled in, the market bifurcated into two distinct categories: **Elevated vs. Un-elevated homes.**

* **The Elevation Premium:** FEMA’s revised flood maps required homes in high-risk zones to be elevated above the Base Flood Elevation (BFE) to avoid astronomical flood insurance rates. Homes that were raised and modernized began to sell at a premium. 
* **The Un-elevated Discount:** Homes that remained at ground level—either because the owners couldn't afford the $100,000+ cost to raise them or because the home's structure didn't allow for it—suffered a severe value penalty. Buyers factored the looming cost of elevation or high insurance premiums directly into their purchase offers.
* **Increased Cost of Construction:** The cost of building or renovating in Long Beach skyrocketed due to high demand for contractors, engineers, and specialized elevation equipment. This drove up the baseline cost of housing.

### 3. Long-Term Impact (2018–Present): Modernization and Gentrification
Nearly a decade after the storm, the Long Beach housing market had not only recovered but surpassed its pre-Sandy peaks. 

* **The "New" Long Beach:** Sandy accelerated the gentrification of Long Beach. Many of the small, ground-level 1920s bungalows—once the hallmark of the West End—were demolished. They were replaced by large, multi-story, FEMA-compliant luxury homes built on pilings. 
* **Dramatic Value Growth:** By 2019, the median home value in the 11561 zip code had surpassed pre-Sandy levels. According to MLS data, the median sale price in Long Beach went from roughly $450,000–$500,000 in the years surrounding Sandy to over $700,000 by 2021 (further accelerated by the COVID-19 suburban boom).
* **The Boardwalk Effect:** The rebuilding of the iconic 2.2-mile Long Beach Boardwalk (reopened in late 2013 with a $44 million stronger concrete-and-hardwood structure) restored confidence in the city's long-term viability, reassuring buyers that the city was resilient.

---

### Key Drivers That Permanent Changed Home Values in 11561

#### A. Flood Insurance (The FEMA Factor)
Before Sandy, many Long Beach homeowners paid nominal flood insurance premiums. Post-Sandy, the implementation of the Biggert-Waters Flood Insurance Reform Act meant that homes not elevated to FEMA standards faced insurance premiums that could rise to **$5,000 to $10,000+ per year**. This ongoing carrying cost acts as a drag on the value of non-compliant homes, while FEMA-compliant homes enjoy low rates (often under $1,000/year), bolstering their resale value.

#### B. Demographic Shift
The high cost of rebuilding forced many working-class, lifelong residents to sell and relocate. They were replaced by higher-income buyers, many commuting to New York City, who could afford the high mortgage, construction, and insurance costs. This shift in demographics naturally pushed average home values upward.

#### C. The Resiliency Premium
Today, buyers in Long Beach actively look for "post-Sandy" construction. Homes built or fully renovated/elevated after 2012 command a massive premium because they offer peace of mind. Conversely, homes that survived Sandy without being elevated are viewed with skepticism, and their values reflect the perceived risk.

### Summary
Hurricane Sandy did not permanently depress the Long Beach real estate market; instead, it acted as a **brutal catalyst for modernization**. While the storm caused a temporary and painful drop in equity for existing homeowners between 2012 and 2015, the long-term result was a highly resilient, heavily gentrified housing market with home values that are now significantly higher than they were before the storm.

</details>
<!-- /answer:q1:baseline -->

<!-- answer:q1:agent -->
**Agent:**

<details><summary>Show the answer</summary>

### Verdict

Home values in Long Beach fell 11.8% behind similar ZIP codes after Hurricane Sandy and had not caught up 61 months later [S7]. We compared Long Beach with similar ZIP codes in the New York metro area [S7].

**Confidence: medium**

The compared ZIPs did not move alike before Sandy, with Long Beach falling 3.4 points further behind [S7]. Also, a second flood on February 2, 2013, brought 50 claims and overlaps the window [S7].

### What happened

* October 28, 2012: FEMA declared an emergency in Nassau County for Hurricane Sandy [S3].
* October 29, 2012: NOAA recorded two high wind events in the county, with gusts up to 82 mph in Syosset [S4].
* October 30, 2012: FEMA issued a major disaster declaration for the county [S3].
* October 28 to November 8, 2012: NOAA recorded three coastal flood events exceeding 100-year base flood elevations in Southern Nassau County [S4].

### What it cost

* Hurricane Sandy caused 6,505 NFIP flood claims and $469,978,531 in payouts in ZIP 11561 [S5].
* Long Beach has experienced 28 flood years with at least 10 claims on record, with 14 of those occurring since 2000 [S9].
* The median gap between flood years in this ZIP is 1 year [S9].
* An unnamed event peaking on October 27, 2012, generated 14 claims and $449,914 paid in this ZIP [S5].

### What came after

We compare Long Beach (ZIP 11561) with 15 similar ZIP codes in the New York metro area that had similar home values before Sandy but almost no flood claims [S7]. Long Beach home values averaged $470,646 before the storm [S7].

| When | This ZIP | Similar ZIPs | Gap |
| :--- | :---: | :---: | :---: |
| 2 years before → event | −4.5% | −1.1% | −3.4 pts |
| 6 months after | +1.6% | +1.4% | +0.2 pts |
| 1 year after | +0.3% | +6.0% | −5.7 pts |
| 2 years after | +0.4% | +12.0% | −11.6 pts |
| 3 years after | +3.4% | +15.6% | −12.2 pts |
| 5 years after | +14.6% | +29.9% | −15.3 pts |

Source: [S7]

The earliest row is the closest to the event itself; later rows mix in more of everything else that moved the market.

Keep in mind:
* Another flood hit this ZIP on February 2, 2013, resulting in 50 claims [S7].
* The lowest post-event home value in Long Beach was $467,096 in March 2014, a decrease of 0.8% from baseline [S6].

### What the data can't tell you

* Whether any individual home flooded, as claims are anonymized to the ZIP code and block group level [S5].
* Total losses from uninsured properties or how many homes lacked flood policies [S5].
* The separate financial impacts of Hurricane Sandy versus subsequent flooding [S7].

## Sources

- **[S3]** `declarations(county_fips='36059', start='2012-10-01', end='2012-11-30')`: FEMA disaster declarations (OpenFEMA Disaster Declarations Summaries v2)
  ```sql
  SELECT declaration_id, declaration_type, title, incident_type, incident_begin, incident_end, declaration_date, designated_area, individual_assistance, public_assistance, hazard_mitigation FROM declarations WHERE (county_fips = '36059' OR county_fips = '36059'[1:2] || '000') AND incident_begin BETWEEN '2012-10-01' AND '2012-11-30' ORDER BY incident_begin DESC
  ```
- **[S4]** `storm_events(county_fips='36059', start='2012-10-01', end='2012-11-30', flood_only=False)`: NOAA Storm Events Database; NOAA storm event -> county (NWS zone-county correlation)
  ```sql
  SELECT event_type, count(*) AS events, count(DISTINCT year(begin_date)) AS years_with_events, sum(deaths) AS deaths, sum(injuries) AS injuries, sum(damage_property) AS property_damage_usd, min(begin_date) AS first, max(begin_date) AS last FROM ( SELECT e.* FROM storm_events e JOIN storm_event_counties c USING (event_id) WHERE c.county_fips = '36059' AND e.begin_date BETWEEN '2012-10-01' AND '2012-11-30' ) GROUP BY event_type ORDER BY events DESC
  ```
  ```sql
  SELECT episode_id, min(begin_date) AS begin_date, max(end_date) AS end_date, string_agg(DISTINCT event_type, ', ') AS event_types, count(*) AS events, sum(deaths) AS deaths, sum(damage_property) AS property_damage_usd, left(any_value(episode_narrative), 700) AS episode_narrative, left(arg_max(event_narrative, coalesce(damage_property, 0)), 500) AS worst_event_narrative FROM ( SELECT e.* FROM storm_events e JOIN storm_event_counties c USING (event_id) WHERE c.county_fips = '36059' AND e.begin_date BETWEEN '2012-10-01' AND '2012-11-30' ) GROUP BY episode_id ORDER BY coalesce(sum(damage_property), 0) DESC, sum(deaths) DESC, events DESC LIMIT 10
  ```
- **[S5]** `claims_summary(zips=['11561'], start='2012-10-25', end='2012-11-15', group_by='event')`: NFIP flood insurance claims (OpenFEMA NFIP Redacted Claims v3)
  ```sql
  SELECT count(*) AS claims, sum(paid_total) AS paid_usd, median(paid_total) AS median_paid_usd, count(*) FILTER (WHERE paid_total > 0) AS claims_paid, count(DISTINCT year_of_loss) AS years_with_claims, min(date_of_loss) AS first_loss, max(date_of_loss) AS last_loss, round(100.0 * avg(CASE WHEN primary_residence THEN 1 ELSE 0 END), 1) AS pct_primary_residence, round(100.0 * avg(CASE WHEN left(flood_zone_current, 1) IN ('A', 'V') THEN 1 ELSE 0 END), 1) AS pct_in_high_risk_zone FROM nfip_claims WHERE zip IN (SELECT unnest(['11561'])) AND date_of_loss BETWEEN '2012-10-25' AND '2012-11-15'
  ```
  ```sql
  SELECT coalesce(flood_event, 'Unnamed (' || year_of_loss || ')') AS event, count(*) AS claims, sum(paid_total) AS paid_usd, median(paid_total) AS median_paid_usd, mode(date_of_loss) AS peak_loss_date, min(date_of_loss) AS first_loss, max(date_of_loss) AS last_loss FROM nfip_claims WHERE zip IN (SELECT unnest(['11561'])) AND date_of_loss BETWEEN '2012-10-25' AND '2012-11-15' GROUP BY 1 ORDER BY claims DESC LIMIT 50
  ```
- **[S6]** `price_path(zip_code='11561', event_date='2012-10-29')`: Zillow Home Value Index (ZHVI), typical home value by ZIP and month
  ```sql
  SELECT month, value FROM zhvi WHERE zip = '11561' AND month BETWEEN '2011-10-23' AND '2015-11-19' ORDER BY month
  ```
- **[S7]** `recovery_vs_controls(zip_code='11561', event_date='2012-10-29', event_name='Sandy')`: ZIP metadata: city, metro, county (Zillow + Census); Zillow Home Value Index (ZHVI), typical home value by ZIP and month; NFIP flood insurance claims (OpenFEMA NFIP Redacted Claims v3)
  ```sql
  WITH target AS (SELECT metro, state FROM zips WHERE zip = '11561'), base AS ( SELECT zip, avg(value) AS baseline FROM zhvi WHERE month >= '2012-07-26' AND month < '2012-10-29' GROUP BY zip ), hit AS ( SELECT zip, count(*) AS claims FROM nfip_claims WHERE CASE WHEN 'Sandy' IS NOT NULL THEN flood_event ILIKE '%' || 'Sandy' || '%' ELSE date_of_loss BETWEEN '2012-10-29' AND '2012-11-28' END GROUP BY zip ) SELECT z.zip, z.city, z.metro, b.baseline, coalesce(h.claims, 0) AS event_claims FROM zips z JOIN target t ON z.metro = t.metro JOIN base b USING (zip) LEFT JOIN hit h USING (zip) WHERE z.zip <> '11561' AND b.baseline BETWEEN 352984.5 AND 588307.5 AND coalesce(h.claims, 0) < 10 ORDER BY abs(b.baseline - (SELECT baseline FROM base WHERE zip = '11561')) LIMIT 15
  ```
  ```sql
  SELECT zip, month, value FROM zhvi WHERE zip IN (SELECT unnest(['11561', '11379', '11432', '11739', '11547', '11360', '10458', '10606', '11803', '10595', '11216', '10044', '10913', '11205', '10520', '11804'])) AND month BETWEEN '2009-10-29' AND '2017-12-02' ORDER BY month
  ```
  ```sql
  SELECT coalesce(flood_event, 'unnamed flooding') AS event, min(date_of_loss) AS first_loss, count(*) AS claims FROM nfip_claims WHERE zip = '11561' AND date_of_loss > '2013-01-27' AND date_of_loss <= '2017-11-30' AND ('Sandy' IS NULL OR flood_event IS NULL OR flood_event NOT ILIKE '%' || 'Sandy' || '%') GROUP BY 1 HAVING count(*) >= 25 ORDER BY first_loss
  ```
- **[S9]** `repeat_flooding(zips=['11561'], min_claims=10)`: NFIP flood insurance claims (OpenFEMA NFIP Redacted Claims v3)
  ```sql
  SELECT year_of_loss AS year, count(*) AS claims, sum(paid_total) AS paid_usd, string_agg(DISTINCT coalesce(flood_event, 'unnamed'), ', ') AS events FROM nfip_claims WHERE zip IN (SELECT unnest(['11561'])) GROUP BY 1 HAVING count(*) >= 10 ORDER BY 1
  ```

</details>
<!-- /answer:q1:agent -->

### 2. "Which ZIP codes in Harris County, TX have flooded the most since 2000?"

**Expected:**
- **Top ZIPs by NFIP claims since 2000 (`nfip_claims`):**
  1. 77096: 5,332 claims, $667M
  2. 77089: 4,224
  3. 77037: 3,558
  4. 77088: 3,445
  5. 77040: 3,220
- **For context:** most of those claims come from Hurricane Harvey (48,992 claims county-wide, $5.37B), Tropical Storm Allison and Hurricane Ike. The report ranks the ZIPs and covers the last 2 years of storms in detail.

<!-- answer:q2:baseline -->
**No tools (Gemini 3.5 Flash, no system prompt):**

<details><summary>Show the answer</summary>

Determining which ZIP codes in Harris County have flooded the most since 2000 depends on how "flooding" is measured—whether by the **number of FEMA National Flood Insurance Program (NFIP) claims**, the **number of "Repetitive Loss" properties**, or the **sheer volume of homes inundated** during catastrophic events like Tropical Storm Allison (2001), the Memorial Day Flood (2015), the Tax Day Flood (2016), Hurricane Harvey (2017), Tropical Storm Imelda (2019), and the storms of 2024.

According to data from the Federal Emergency Management Agency (FEMA), the Harris County Flood Control District (HCFCD), and post-Harvey housing analyses, the following ZIP codes in Harris County consistently rank as the most severely and frequently flooded.

---

### 1. **77096 (Meyerland / Southwest Houston)**
*   **Primary Source of Flooding:** Brays Bayou
*   **Why it ranks at the top:** Meyerland is historically known as one of the premier "repetitive loss" hotspots in the entire United States. It suffered catastrophic flooding three years in a row (2015 Memorial Day, 2016 Tax Day, and 2017 Hurricane Harvey). 
*   **Impact:** In Hurricane Harvey alone, over 9,000 homes in this ZIP code flooded. It has some of the highest concentrations of FEMA Severe Repetitive Loss (SRL) properties in the country. 

### 2. **77084 (Bear Creek / Addicks / West Houston)**
*   **Primary Source of Flooding:** Addicks Reservoir pool (upstream flooding)
*   **Why it ranks at the top:** Located right on the edge of the Addicks Reservoir, this ZIP code was the epicenter of upstream reservoir flooding during Hurricane Harvey. Because the reservoir filled to capacity, water backed up into thousands of homes that were built inside the reservoir's evacuation pool.
*   **Impact:** Over 10,000 homes flooded in 77084 during Harvey, making it one of the absolute highest ZIP codes for total volume of flooded structures in a single event.

### 7. **77079 & 77024 (Memorial / Energy Corridor / West Houston)**
*   **Primary Source of Flooding:** Buffalo Bayou and controlled reservoir releases
*   **Why they rank at the top:** These wealthy, densely populated neighborhoods sit downstream of the Addicks and Barker reservoirs. During Hurricane Harvey, the U.S. Army Corps of Engineers had to make controlled releases of water into Buffalo Bayou to prevent dam failure. This kept these ZIP codes underwater for nearly two weeks.
*   **Impact:** High-dollar NFIP payout areas with extensive, long-duration structural damage. 

### 4. **77025 (Braeswood Place / Woodside / South Main)**
*   **Primary Source of Flooding:** Brays Bayou
*   **Why it ranks at the top:** Neighboring Meyerland, 77025 is highly susceptible to the same Brays Bayou overflows. It features a massive concentration of repetitive-loss townhomes, apartment complexes, and single-family homes.
*   **Impact:** Heavily inundated during Allison, Memorial Day, Tax Day, and Harvey.

### 5. **77396 & 77346 (Humble / Atascocita)**
*   **Primary Source of Flooding:** West Fork of the San Jacinto River
*   **Why they rank at the top:** These ZIP codes in northeast Harris County are highly vulnerable to riverine flooding. When Lake Conroe releases water or when heavy rains gorge the San Jacinto River, these areas flood rapidly.
*   **Impact:** This area saw severe flooding during Harvey, Imelda (2019), and faced major evacuations and home inundations again during the river flooding in May 2024.

### 6. **77074 & 77036 (Sharpstown / Southwest Houston)**
*   **Primary Source of Flooding:** Brays Bayou and Keegans Bayou
*   **Why they rank at the top:** High-density, lower-income areas with older infrastructure that struggle with both bayou overflows and street ponding (local drainage failure).
*   **Impact:** Thousands of apartment units and single-family homes in these areas have flooded multiple times since 2000.

### 7. **77008 (The Heights / Greater Heights)**
*   **Primary Source of Flooding:** White Oak Bayou
*   **Why it ranks at the top:** While parts of the Heights sit on high ground, the southern and western portions near White Oak Bayou are historically flood-prone.
*   **Impact:** Significant claims during Tropical Storm Allison (2001) and the Tax Day Flood (2016).

---

### Summary of Flooding Types by ZIP Code:

| ZIP Code | Major Neighborhood | Primary Waterway / Cause | Notable Events |
| :--- | :--- | :--- | :--- |
| **77096** | Meyerland | Brays Bayou | 2015, 2016, 2017 (Harvey) |
| **77084** | Bear Creek / Katy | Addicks Reservoir backing up | 2016, 2017 (Harvey) |
| **77079** | Memorial / Energy Corridor | Buffalo Bayou / Reservoir releases | 2017 (Harvey) |
| **77025** | Braeswood Place | Brays Bayou | 2001, 2015, 2016, 2017 |
| **77396** | Humble | San Jacinto River | 2017, 2019, 2024 |
| **77450** | Katy (Cinco Ranch) | Barker Reservoir / Buffalo Bayou | 2017 (Harvey) |

### Current Mitigation Efforts
If you are looking at these ZIP codes for real estate or insurance purposes, it is worth noting that massive changes have occurred since 2017:
*   **Project Brays:** A massive, multi-year federal/county project has widened Brays Bayou and added major detention basins, significantly reducing the flood risk for **77096** and **77025**.
*   **Home Elevations and Buyouts:** Hundreds of the most frequently flooded homes in **77096**, **77025**, and **77396** have been structurally elevated above the 500-year floodplain or bought out by the Harris County Flood Control District and turned into green space.

</details>
<!-- /answer:q2:baseline -->

<!-- answer:q2:agent -->
**Agent:**

<details><summary>Show the answer</summary>

### In short

* ZIP 77096 flooded the most since 2000 with 5,332 claims, while ZIP 77046 was the least flooded of those analyzed with 13 claims [S6].
* Over the last two years, ZIP 77096 and ZIP 77089 had home values that grew above the Houston-The Woodlands-Sugar Land metro area median, while ZIPs 77037, 77088, and 77040 fell below it [S6].
* Because this report only covers select ZIP codes in Harris County, you should ask about specific neighborhoods for a more precise answer.

### The last 2 years

* June 15, 2026: Tropical storm Arthur-TS peaked, bringing 24 flood claims that paid out $302,874 [S7].
* February 14, 2026: A Valentine's Day severe storm hit Cloverland with 70 mph winds, causing $93,000 in property damage [S3].
* August 31, 2025: A flash flood caused over 10 water rescues in Houston and $75,000 in property damage [S3].
* January 20, 2025: A winter storm brought 2 to 6 inches of snowfall and 2 indirect deaths [S3].

### ZIP by ZIP

We rank Harris County ZIP codes by their total flood claims since 2000 and show how their home values grew compared to the Houston-The Woodlands-Sugar Land metro area median change of -3.5% [S6].

| ZIP (area) | Claims, last 2 yrs | Claims since 2000 | Home value | Change, 2 yrs | vs. metro |
|---|---:|---:|---:|---:|---:|
| 77096 (Houston) | 5 | 5,332 | $434,130 | +0.6% | +4.1 pts |
| 77089 (Houston) | 3 | 4,224 | $270,868 | -3.2% | +0.3 pts |
| 77037 (Houston) | 3 | 3,559 | $197,954 | -4.8% | -1.3 pts |
| 77088 (Houston) | 4 | 3,448 | $210,113 | -6.0% | -2.5 pts |
| 77040 (Houston) | 0 | 3,222 | $255,196 | -4.7% | -1.2 pts |

Source: [S6]

Least flooded since 2000:

| ZIP (area) | Claims, last 2 yrs | Claims since 2000 | Home value | Change, 2 yrs | vs. metro |
|---|---:|---:|---:|---:|---:|
| 77046 (Houston) | 0 | 13 | $312,717 | — | -4.0 pts |
| 77003 (Houston) | 1 | 38 | $345,225 | — | -5.2 pts |
| 77014 (Houston) | 0 | 74 | $244,543 | — | -0.5 pts |

Source: [S6]

### Before you decide

* Check the FEMA flood zone of the exact address you are considering.
* Obtain a personalized flood insurance quote for the property.
* Review the seller's written flood and claims disclosure.

### What the data can't tell you

* Whether a specific property flooded, as the data is anonymized to ZIP codes.
* The true extent of flooding, because properties without insurance cannot file claims.
* Future flood risks, as past weather and claims do not guarantee future patterns.

## Sources

- **[S3]** `storm_events(county_fips='48201', start='2024-09-01', end='2026-09-30', flood_only=False)`: NOAA Storm Events Database; NOAA storm event -> county (NWS zone-county correlation)
  ```sql
  SELECT event_type, count(*) AS events, count(DISTINCT year(begin_date)) AS years_with_events, sum(deaths) AS deaths, sum(injuries) AS injuries, sum(damage_property) AS property_damage_usd, min(begin_date) AS first, max(begin_date) AS last FROM ( SELECT e.* FROM storm_events e JOIN storm_event_counties c USING (event_id) WHERE c.county_fips = '48201' AND e.begin_date BETWEEN '2024-09-01' AND '2026-09-30' ) GROUP BY event_type ORDER BY events DESC
  ```
  ```sql
  SELECT episode_id, min(begin_date) AS begin_date, max(end_date) AS end_date, string_agg(DISTINCT event_type, ', ') AS event_types, count(*) AS events, sum(deaths) AS deaths, sum(damage_property) AS property_damage_usd, left(any_value(episode_narrative), 700) AS episode_narrative, left(arg_max(event_narrative, coalesce(damage_property, 0)), 500) AS worst_event_narrative FROM ( SELECT e.* FROM storm_events e JOIN storm_event_counties c USING (event_id) WHERE c.county_fips = '48201' AND e.begin_date BETWEEN '2024-09-01' AND '2026-09-30' ) GROUP BY episode_id ORDER BY coalesce(sum(damage_property), 0) DESC, sum(deaths) DESC, events DESC LIMIT 10
  ```
- **[S6]** `compare_zips(years=2, county_fips='48201', rank_by='since_2000')`: NFIP flood insurance claims (OpenFEMA NFIP Redacted Claims v3); Zillow Home Value Index (ZHVI), typical home value by ZIP and month; ZIP metadata: city, metro, county (Zillow + Census)
  ```sql
  SELECT z.zip, any_value(m.city) AS city, any_value(m.county_name) AS county, any_value(m.metro) AS metro, count(c.id) FILTER (WHERE c.date_of_loss >= '2024-09-01') AS claims_recent, coalesce(sum(c.paid_total) FILTER (WHERE c.date_of_loss >= '2024-09-01'), 0) AS paid_recent_usd, string_agg(DISTINCT coalesce(c.flood_event, 'unnamed flooding'), '; ') FILTER (WHERE c.date_of_loss >= '2024-09-01') AS recent_events, count(c.id) FILTER (WHERE c.year_of_loss >= 2000) AS claims_since_2000, count(DISTINCT c.year_of_loss) FILTER (WHERE c.year_of_loss >= 2000) AS years_with_claims_since_2000 FROM (SELECT unnest(['77002', '77003', '77004', '77005', '77006', '77007', '77008', '77009', '77010', '77011', '77012', '77013', '77014', '77015', '77016', '77017', '77018', '77019', '77020', '77021', '77022', '77023', '77024', '77025', '77026', '77027', '77028', '77029', '77030', '77031', '77032', '77033', '77034', '77035', '77036', '77037', '77038', '77039', '77040', '77041', '77042', '77043', '77044', '77045', '77046', '77047', '77048', '77049', '77050', '77051', '77054', '77055', '77056', '77057', '77058', '77059', '77060', '77061', '77062', '77063', '77064', '77065', '77066', '77067', '77068', '77069', '77070', '77071', '77072', '77073', '77074', '77075', '77076', '77077', '77078', '77079', '77080', '77081', '77082', '77083', '77084', '77085', '77086', '77087', '77088', '77089', '77090', '77091', '77092', '77093', '77094', '77095', '77096', '77098', '77099', '77204', '77336', '77338', '77339', '77345', '77346', '77373', '77375', '77377', '77379', '77388', '77389', '77396', '77401', '77429', '77433', '77447', '77449', '77450', '77493', '77502', '77503', '77504', '77505', '77506', '77507', '77520', '77521', '77530', '77532', '77536', '77547', '77562', '77571', '77586', '77587', '77598']) AS zip) z LEFT JOIN nfip_claims c USING (zip) LEFT JOIN zips m USING (zip) GROUP BY z.zip
  ```
  ```sql
  SELECT zip, strftime(month, '%Y-%m') AS month, value FROM zhvi WHERE zip IN (SELECT unnest(['77002', '77003', '77004', '77005', '77006', '77007', '77008', '77009', '77010', '77011', '77012', '77013', '77014', '77015', '77016', '77017', '77018', '77019', '77020', '77021', '77022', '77023', '77024', '77025', '77026', '77027', '77028', '77029', '77030', '77031', '77032', '77033', '77034', '77035', '77036', '77037', '77038', '77039', '77040', '77041', '77042', '77043', '77044', '77045', '77046', '77047', '77048', '77049', '77050', '77051', '77054', '77055', '77056', '77057', '77058', '77059', '77060', '77061', '77062', '77063', '77064', '77065', '77066', '77067', '77068', '77069', '77070', '77071', '77072', '77073', '77074', '77075', '77076', '77077', '77078', '77079', '77080', '77081', '77082', '77083', '77084', '77085', '77086', '77087', '77088', '77089', '77090', '77091', '77092', '77093', '77094', '77095', '77096', '77098', '77099', '77204', '77336', '77338', '77339', '77345', '77346', '77373', '77375', '77377', '77379', '77388', '77389', '77396', '77401', '77429', '77433', '77447', '77449', '77450', '77493', '77502', '77503', '77504', '77505', '77506', '77507', '77520', '77521', '77530', '77532', '77536', '77547', '77562', '77571', '77586', '77587', '77598'])) AND month >= '2023-09-01' ORDER BY month
  ```
  ```sql
  WITH a AS (SELECT zip, value FROM zhvi WHERE strftime(month, '%Y-%m') = '2024-09'), b AS (SELECT zip, value FROM zhvi WHERE strftime(month, '%Y-%m') = '2026-09') SELECT z.metro, round(100 * (median(b.value / a.value) - 1), 1) AS change_pct, count(*) AS zips FROM zips z JOIN a USING (zip) JOIN b USING (zip) WHERE z.metro IN (SELECT unnest(['Houston-The Woodlands-Sugar Land, TX'])) GROUP BY 1
  ```
  ```sql
  WITH a AS (SELECT zip, value FROM zhvi WHERE strftime(month, '%Y-%m') = '2024-09') SELECT strftime(v.month, '%Y-%m') AS month, 10000 * median(v.value / a.value) AS idx_x10000 FROM zhvi v JOIN a USING (zip) JOIN zips z USING (zip) WHERE z.metro = 'Houston-The Woodlands-Sugar Land, TX' AND v.month >= '2023-09-01' GROUP BY 1 ORDER BY 1
  ```
- **[S7]** `claims_summary(county_fips='48201', start='2024-09-01', end='2026-09-30', group_by='event')`: NFIP flood insurance claims (OpenFEMA NFIP Redacted Claims v3)
  ```sql
  SELECT count(*) AS claims, sum(paid_total) AS paid_usd, median(paid_total) AS median_paid_usd, count(*) FILTER (WHERE paid_total > 0) AS claims_paid, count(DISTINCT year_of_loss) AS years_with_claims, min(date_of_loss) AS first_loss, max(date_of_loss) AS last_loss, round(100.0 * avg(CASE WHEN primary_residence THEN 1 ELSE 0 END), 1) AS pct_primary_residence, round(100.0 * avg(CASE WHEN left(flood_zone_current, 1) IN ('A', 'V') THEN 1 ELSE 0 END), 1) AS pct_in_high_risk_zone FROM nfip_claims WHERE county_fips = '48201' AND date_of_loss BETWEEN '2024-09-01' AND '2026-09-30'
  ```
  ```sql
  SELECT coalesce(flood_event, 'Unnamed (' || year_of_loss || ')') AS event, count(*) AS claims, sum(paid_total) AS paid_usd, median(paid_total) AS median_paid_usd, mode(date_of_loss) AS peak_loss_date, min(date_of_loss) AS first_loss, max(date_of_loss) AS last_loss FROM nfip_claims WHERE county_fips = '48201' AND date_of_loss BETWEEN '2024-09-01' AND '2026-09-30' GROUP BY 1 ORDER BY claims DESC LIMIT 50
  ```

</details>
<!-- /answer:q2:agent -->

### 3. "Is it safe to buy a beach house in Florida?" (vague, so it tests clarification)

**Expected:** the Lead asks which place first. Answered with "Fort Myers Beach (33931), after Hurricane Ian. Buying.", the report should cover:
- **Claims:** 2,611 NFIP claims after Ian, $576M paid, 86% in high-risk flood zones (`nfip_claims`).
- **Declarations:** EM-3584-FL and DR-4673-FL (`declarations`).
- **Home values (`zhvi`):** 6.7 pts behind similar ZIPs at 6 months and 27 pts behind at 3 years.
- **Lower confidence:** Helene and Milton (2024) fall in the window, and the comparison ZIPs come from other Florida metros.

<!-- answer:q3:baseline -->
**No tools (Gemini 3.5 Flash, no system prompt):**

<details><summary>Show the answer</summary>

Buying a beach house in Florida can be highly rewarding, but in the current market, **it is a high-risk, high-reward proposition.** 

While the lifestyle, warm weather, and lack of state income tax remain incredibly attractive, the physical and financial risks of owning oceanfront property in Florida have increased significantly over the last decade.

To determine if it is "safe" for you, you need to look at safety through three lenses: **environmental safety, financial safety, and structural safety.**

---

### 1. Environmental Safety (The Physical Risks)
The most obvious risks to a Florida beach house come from nature.
* **Hurricanes and Storm Surge:** Florida is the most hurricane-prone state in the U.S. While wind damage is a major threat, **storm surge** (water pushed inland by the storm) is the leading cause of total property destruction.
* **Sea Level Rise and Erosion:** Florida's coastline is eroding. Many beaches require constant "renourishment" (pumping in sand), which is paid for by taxpayers or local assessments. High tides (known as "King Tides") increasingly flood coastal streets even on sunny days.

### 2. Financial Safety (The Costs of Ownership)
This is currently the biggest risk for Florida buyers. The cost of owning a beach house has skyrocketed, even if you pay in cash.
* **The Insurance Crisis:** This is the #1 hurdle. Many major national home insurance companies have pulled out of Florida. Those that remain have doubled or tripled premiums. Windstorm and flood insurance (through FEMA’s NFIP) are often sold separately and are incredibly expensive. It is not uncommon for insurance on a beach house to cost $10,000 to $30,000+ per year.
* **Property Taxes:** Florida property taxes are based on the home's assessed value. While primary residents enjoy the "Save Our Homes" cap (which limits assessment increases to 3% per year), **second-home buyers and investors do not.** Your property taxes can rise up to 10% per year.
* **High Maintenance Costs:** The salt air, high humidity, and intense sun are brutal on buildings. Air conditioning units, roofs, paint, and metal fixtures degrade twice as fast near the ocean.

### 3. Structural and Legal Safety (The Condo Factor)
If you are buying a beachside **condominium** rather than a single-family home, there is a new set of risks.
* **Post-Surfside Regulations (SB 4-D):** Following the tragic collapse of the Champlain Towers South condo in Surfside in 2021, Florida passed strict new laws. Coastal condos over three stories must undergo regular structural integrity inspections. 
* **Special Assessments:** Condo associations are now legally required to fully fund their financial reserves for structural repairs. As a result, many condo owners are being hit with **"special assessments" of $50,000 to $150,000+** to repair concrete, balconies, and roofs, causing HOA fees to double or triple.

---

### How to Make Buying a Florida Beach House "Safer"
If you still want to buy, you can drastically reduce your risk by following these guidelines:

1. **Buy Post-2002 Construction:** In 2002, Florida implemented a strict statewide building code. Homes built after 2002 are engineered to withstand major hurricanes (with impact-rated glass, reinforced roofs, and tie-downs). Homes built after 2020 are even safer.
2. **Look at the Elevation:** Check the FEMA flood maps. Buy a home that is elevated on pilings (stilt homes) or sits on a high dune well above the base flood elevation. Avoid "Zone VE" (high risk of velocity wave action) if you want lower insurance rates.
3. **Analyze the HOA/Condo Financials:** If buying a condo, hire an attorney to review the HOA’s "Structural Integrity Reserve Study" (SIRS). If the building hasn't funded its reserves, walk away.
4. **Get Quotes *Before* You Put in an Offer:** Never make an offer on a Florida beach house without a contingency clause for insurance. Get binding insurance quotes for homeowners, wind, and flood insurance during your inspection period.
5. **Consider the Gulf vs. Atlantic:** While both experience hurricanes, the Gulf Coast (e.g., Tampa, Naples, Destin) generally experiences warmer, calmer waters but is highly vulnerable to shallow-water storm surge. The Atlantic Coast (e.g., Miami, Daytona, Jacksonville) has deeper offshore waters, which can slightly mitigate storm surge heights but faces stronger wave action.

### The Verdict
* **It is NOT safe** if you are on a tight budget, looking for a low-maintenance retirement home, or stretching your finances to make the purchase. Unforeseen insurance hikes or storm repairs could ruin you financially.
* **It IS relatively safe** if you are financially liquid, view the property as a luxury lifestyle choice rather than a guaranteed investment, purchase a modern (post-2002) elevated home, and have budgeted for high ongoing insurance and maintenance costs.

</details>
<!-- /answer:q3:baseline -->

<!-- answer:q3:agent -->
**Agent:**

<details><summary>Show the answer</summary>

*The Lead asked first:*
> - Which specific Florida city, county, or ZIP code are you considering?
> - Are you looking to buy or rent?
> - Are you interested in a specific time period or recent storm event?

*Answered with:* "Fort Myers Beach (33931), after Hurricane Ian. Buying."

### Verdict
Fort Myers Beach home values fell behind similar Florida ZIP codes after Hurricane Ian and remained behind three years later [S3, S4]. We compared Fort Myers Beach (33931) with 15 other coastal areas in Florida, such as Destin and Palm Beach Gardens, that had similar pre-storm home values but almost no flood claims [S4]. 

**Confidence: medium**
Before the storm, the areas moved differently as ZIP 33931 grew 62.9% while the similar ZIPs grew 53.1% [S4]. Additionally, two subsequent storms, Helene (September 24, 2024, with 447 claims) and Milton (October 8, 2024, with 770 claims), overlap the comparison window [S4].

### What happened
* September 24, 2022: FEMA issued an Emergency Declaration for Lee County ahead of Hurricane Ian [S5].
* September 28, 2022: Hurricane Ian made landfall with 150 mph maximum sustained winds, causing 60 deaths and $7,000,000,000 in property damage in Lee County [S6].
* September 28, 2022: Fort Myers Beach experienced a maximum storm surge near 15 feet with widespread 10 to 12 feet of inundation [S6].
* September 29, 2022: FEMA issued a Major Disaster Declaration for Lee County [S5].

### What it cost
* Property owners filed 2,611 NFIP flood claims for Hurricane Ian in ZIP 33931, resulting in a total payout of $576,206,418 and a median payout of $124,148 [S2].
* During the event window, 2,396 of the 2,627 total claims recorded were paid, representing a 91.2% claim success rate [S2].
* High-risk flood zones (FEMA zones A and V) accounted for 86.1% of these claims [S2].
* ZIP 33931 has experienced 20 flood years since 1979 (13 since 2000), with a 2-year median gap between years with at least 10 claims [S8].

### What came after
We compare Fort Myers Beach with 15 ZIP codes in other Florida metros (e.g. Destin, Palm Beach Gardens, Fort Lauderdale, Bradenton, and Palm Harbor) that had similar home values before the storm but almost no flood claims [S4].

| When | This ZIP | Similar ZIPs | Gap |
|---|---|---|---|
| 2 years before → event | +62.9% | +53.1% | +9.7 pts |
| 6 months after | −7.1% | −0.4% | −6.7 pts |
| 1 year after | −14.5% | +1.0% | −15.5 pts |
| 2 years after | −18.9% | +2.9% | −21.8 pts |
| 3 years after | −30.2% | −3.2% | −27.0 pts |

Source: [S3, S4]

The 6-month row is closest to the event; later rows mix in other market dynamics [S4].

Keep in mind:
* Hurricane Helene (September 24, 2024) and Hurricane Milton (October 8, 2024) struck the ZIP during this window, mixing their effects into the data [S4].
* The comparison ZIPs belong to other metros, so the gap also reflects other regional market trends [S4].

### What the data can't tell you
* Whether a specific home flooded, or if it is built to survive storm surges [S2].
* Damage to uninsured properties, which is not captured by NFIP claim counts [S2].
* If future storms will match the historical 2-year median flood interval [S8].

## Sources

- **[S2]** `claims_summary(zips=['33931'], start='2022-09-01', end='2022-10-31', group_by='event')`: NFIP flood insurance claims (OpenFEMA NFIP Redacted Claims v3)
  ```sql
  SELECT count(*) AS claims, sum(paid_total) AS paid_usd, median(paid_total) AS median_paid_usd, count(*) FILTER (WHERE paid_total > 0) AS claims_paid, count(DISTINCT year_of_loss) AS years_with_claims, min(date_of_loss) AS first_loss, max(date_of_loss) AS last_loss, round(100.0 * avg(CASE WHEN primary_residence THEN 1 ELSE 0 END), 1) AS pct_primary_residence, round(100.0 * avg(CASE WHEN left(flood_zone_current, 1) IN ('A', 'V') THEN 1 ELSE 0 END), 1) AS pct_in_high_risk_zone FROM nfip_claims WHERE zip IN (SELECT unnest(['33931'])) AND date_of_loss BETWEEN '2022-09-01' AND '2022-10-31'
  ```
  ```sql
  SELECT coalesce(flood_event, 'Unnamed (' || year_of_loss || ')') AS event, count(*) AS claims, sum(paid_total) AS paid_usd, median(paid_total) AS median_paid_usd, mode(date_of_loss) AS peak_loss_date, min(date_of_loss) AS first_loss, max(date_of_loss) AS last_loss FROM nfip_claims WHERE zip IN (SELECT unnest(['33931'])) AND date_of_loss BETWEEN '2022-09-01' AND '2022-10-31' GROUP BY 1 ORDER BY claims DESC LIMIT 50
  ```
- **[S3]** `price_path(zip_code='33931', event_date='2022-09-28')`: Zillow Home Value Index (ZHVI), typical home value by ZIP and month
  ```sql
  SELECT month, value FROM zhvi WHERE zip = '33931' AND month BETWEEN '2021-09-21' AND '2025-10-18' ORDER BY month
  ```
- **[S4]** `recovery_vs_controls(zip_code='33931', event_date='2022-09-28', event_name='Ian')`: ZIP metadata: city, metro, county (Zillow + Census); Zillow Home Value Index (ZHVI), typical home value by ZIP and month; NFIP flood insurance claims (OpenFEMA NFIP Redacted Claims v3)
  ```sql
  WITH target AS (SELECT metro, state FROM zips WHERE zip = '33931'), base AS ( SELECT zip, avg(value) AS baseline FROM zhvi WHERE month >= '2022-06-25' AND month < '2022-09-28' GROUP BY zip ), hit AS ( SELECT zip, count(*) AS claims FROM nfip_claims WHERE CASE WHEN 'Ian' IS NOT NULL THEN flood_event ILIKE '%' || 'Ian' || '%' ELSE date_of_loss BETWEEN '2022-09-28' AND '2022-10-28' END GROUP BY zip ) SELECT z.zip, z.city, z.metro, b.baseline, coalesce(h.claims, 0) AS event_claims FROM zips z JOIN target t ON z.state = t.state AND z.metro IS DISTINCT FROM t.metro JOIN base b USING (zip) LEFT JOIN hit h USING (zip) WHERE z.zip <> '33931' AND b.baseline BETWEEN 519496.5 AND 865827.5 AND coalesce(h.claims, 0) < 10 ORDER BY abs(b.baseline - (SELECT baseline FROM base WHERE zip = '33931')) LIMIT 15
  ```
  ```sql
  SELECT zip, month, value FROM zhvi WHERE zip IN (SELECT unnest(['33931', '32541', '33418', '33305', '34201', '34681', '33137', '34786', '33487', '33140', '33556', '33477', '32814', '34211', '32561', '33029'])) AND month BETWEEN '2019-09-28' AND '2027-11-01' ORDER BY month
  ```
  ```sql
  SELECT coalesce(flood_event, 'unnamed flooding') AS event, min(date_of_loss) AS first_loss, count(*) AS claims FROM nfip_claims WHERE zip = '33931' AND date_of_loss > '2022-12-27' AND date_of_loss <= '2026-09-30' AND ('Ian' IS NULL OR flood_event IS NULL OR flood_event NOT ILIKE '%' || 'Ian' || '%') GROUP BY 1 HAVING count(*) >= 25 ORDER BY first_loss
  ```
- **[S5]** `declarations(county_fips='12071', start='2022-09-01', end='2022-10-15')`: FEMA disaster declarations (OpenFEMA Disaster Declarations Summaries v2)
  ```sql
  SELECT declaration_id, declaration_type, title, incident_type, incident_begin, incident_end, declaration_date, designated_area, individual_assistance, public_assistance, hazard_mitigation FROM declarations WHERE (county_fips = '12071' OR county_fips = '12071'[1:2] || '000') AND incident_begin BETWEEN '2022-09-01' AND '2022-10-15' ORDER BY incident_begin DESC
  ```
- **[S6]** `storm_events(county_fips='12071', start='2022-09-01', end='2022-10-15', flood_only=False)`: NOAA Storm Events Database; NOAA storm event -> county (NWS zone-county correlation)
  ```sql
  SELECT event_type, count(*) AS events, count(DISTINCT year(begin_date)) AS years_with_events, sum(deaths) AS deaths, sum(injuries) AS injuries, sum(damage_property) AS property_damage_usd, min(begin_date) AS first, max(begin_date) AS last FROM ( SELECT e.* FROM storm_events e JOIN storm_event_counties c USING (event_id) WHERE c.county_fips = '12071' AND e.begin_date BETWEEN '2022-09-01' AND '2022-10-15' ) GROUP BY event_type ORDER BY events DESC
  ```
  ```sql
  SELECT episode_id, min(begin_date) AS begin_date, max(end_date) AS end_date, string_agg(DISTINCT event_type, ', ') AS event_types, count(*) AS events, sum(deaths) AS deaths, sum(damage_property) AS property_damage_usd, left(any_value(episode_narrative), 700) AS episode_narrative, left(arg_max(event_narrative, coalesce(damage_property, 0)), 500) AS worst_event_narrative FROM ( SELECT e.* FROM storm_events e JOIN storm_event_counties c USING (event_id) WHERE c.county_fips = '12071' AND e.begin_date BETWEEN '2022-09-01' AND '2022-10-15' ) GROUP BY episode_id ORDER BY coalesce(sum(damage_property), 0) DESC, sum(deaths) DESC, events DESC LIMIT 10
  ```
- **[S8]** `repeat_flooding(zips=['33931'], min_claims=10)`: NFIP flood insurance claims (OpenFEMA NFIP Redacted Claims v3)
  ```sql
  SELECT year_of_loss AS year, count(*) AS claims, sum(paid_total) AS paid_usd, string_agg(DISTINCT coalesce(flood_event, 'unnamed'), ', ') AS events FROM nfip_claims WHERE zip IN (SELECT unnest(['33931'])) GROUP BY 1 HAVING count(*) >= 10 ORDER BY 1
  ```

</details>
<!-- /answer:q3:agent -->

## What the data can't answer

The agent says so when these come up:
- **Whether a specific house flooded:** claims are anonymized to ZIP and block group.
- **Uninsured damage:** few claims can mean few policies, not little flooding.
- **Why prices moved:** the similar-ZIP comparison is correlation, not causation.
- **The future:** these are past records, not forecasts.
- **Places outside the 7 states, and NOAA events before 1996.**

## Team

amo2189, jts2199, bif2111
