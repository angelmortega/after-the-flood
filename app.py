import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import uvicorn
from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse

import tools
from context_panel import AgentContexts, PanelHooks, add_context_panel

load_dotenv(Path(__file__).parent / ".env")

# Traces upload to OpenAI by default, which needs an OpenAI key. We're on Gemini.
set_tracing_disabled(True)

# LiteLLM routes the call to Gemini. api_key is unused: Vertex AI uses your gcloud credentials.
MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash", api_key="unused")
# Researchers call tools and summarize: the cheaper model is enough.
RESEARCHER_MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash-lite", api_key="unused")
# A stalled model call would hang the page: give up after 90 s, and retry twice.
SETTINGS = ModelSettings(extra_args={"vertex_location": "global", "timeout": 90, "num_retries": 2})

# Three researchers per round, two rounds at most. Prompts ask for restraint; code enforces it.
MAX_RESEARCHERS = 6


@dataclass
class Research:
    """One run's state, shared by every agent and tool in it. The model never sees it."""

    hooks: PanelHooks
    researchers: int = 0  # how many researchers have started, to number them
    findings: list[str] = field(default_factory=list)  # what each researcher reported, for the Writer
    sources: dict[str, dict] = field(default_factory=dict)  # "S1" -> the tool call, tables and SQL behind it
    charts: list[dict] = field(default_factory=list)  # recovery curves, for the page
    map: dict | None = None  # ZIPs around the place with their claims, for the page


def _answer(ctx: RunContextWrapper[Research], name: str, args: dict, fn, **kwargs) -> str:
    """Run a tool function and give the model its result under a source id it can cite, e.g. [S3].
    The tables and SQL stay here, for the report's source list: the model doesn't need to read them."""
    try:
        result = fn(**kwargs)
    except Exception as e:
        # The model cannot see an exception. Return something it can reason about.
        return json.dumps({"error": f"{name} failed: {type(e).__name__}: {e}"})
    if "error" in result:
        return json.dumps(result, default=str)
    source_id = f"S{len(ctx.context.sources) + 1}"
    ctx.context.sources[source_id] = {"tool": name, "args": args, **result.get("source", {})}
    if "chart" in result:
        # The recovery curve goes straight to the page; the model gets the yearly summary.
        chart = {**result["chart"], "source_id": source_id}
        ctx.context.charts.append(chart)
        ctx.context.hooks.emit({"type": "chart", **chart})
    body = {k: v for k, v in result.items() if k not in ("source", "chart")}
    return json.dumps({"source_id": source_id, **body}, default=str)


# --- Tools: thin wrappers over tools.py, so the model gets a docstring per argument ---


@function_tool
def resolve_place(ctx: RunContextWrapper[Research], place: str, state: str | None = None) -> str:
    """Find the ZIP codes, county FIPS codes and metro area for a place.

    Args:
        place: One or more 5-digit ZIP codes, a city ('Fort Myers Beach'), or a county ('Harris County').
            Neighborhoods are not in the data: pass their ZIP codes instead.
        state: Two-letter state code, to tell apart places with the same name. Optional.
    """
    answer = _answer(ctx, "resolve_place", {"place": place, "state": state}, tools.resolve_place, place=place, state=state)
    _send_map(ctx.context, json.loads(answer))
    return answer


def _send_map(run: "Research", place: dict) -> None:
    """Draw the place on the page's map as soon as it's resolved: every ZIP in its counties,
    sized by NFIP claims, with the place's own ZIPs highlighted."""
    if "error" in place or not place.get("counties"):
        return
    try:
        counties = [c["county_fips"] for c in place["counties"]][:3]
        data = tools.claims_map(counties, place["zips"])
    except Exception:
        return  # the map is a nicety: never fail the research over it
    run.map = {"zips": data["zips"], "counties": place["counties"]}
    run.hooks.emit({"type": "map", **run.map})


@function_tool
def storm_events(ctx: RunContextWrapper[Research], county_fips: str, start: str | None = None,
                 end: str | None = None, flood_only: bool = True) -> str:
    """NOAA storm events in a county: totals by event type (deaths, injuries, property damage), and
    the most damaging episodes with NOAA's written narratives. Records start in 1996.

    Args:
        county_fips: 5-digit county FIPS code, e.g. '36081' for Queens County, NY.
        start: First date to include, YYYY-MM-DD. Optional.
        end: Last date to include, YYYY-MM-DD. Optional.
        flood_only: True for floods, storm surge, heavy rain and tropical storms only.
    """
    args = {"county_fips": county_fips, "start": start, "end": end, "flood_only": flood_only}
    return _answer(ctx, "storm_events", args, tools.storm_events, **args)


@function_tool
def declarations(ctx: RunContextWrapper[Research], county_fips: str, start: str | None = None, end: str | None = None) -> str:
    """Federal disaster declarations (FEMA) for a county, including statewide ones, newest first.

    Args:
        county_fips: 5-digit county FIPS code.
        start: First incident date to include, YYYY-MM-DD. Optional.
        end: Last incident date to include, YYYY-MM-DD. Optional.
    """
    args = {"county_fips": county_fips, "start": start, "end": end}
    return _answer(ctx, "declarations", args, tools.declarations, **args)


@function_tool
def claims_summary(ctx: RunContextWrapper[Research], zips: list[str] | None = None, county_fips: str | None = None,
                   start: str | None = None, end: str | None = None,
                   group_by: Literal["event", "year", "zip", "flood_zone"] = "event") -> str:
    """NFIP flood insurance claims and payouts for some ZIP codes or a county, with totals and a
    breakdown. Grouped by event, each row has the event's peak date of loss.

    Args:
        zips: 5-digit ZIP codes. Pass these or county_fips.
        county_fips: 5-digit county FIPS code. Pass this or zips.
        start: First date of loss to include, YYYY-MM-DD. Optional.
        end: Last date of loss to include, YYYY-MM-DD. Optional.
        group_by: 'event' (named storms), 'year', 'zip' or 'flood_zone'.
    """
    args = {"zips": zips, "county_fips": county_fips, "start": start, "end": end, "group_by": group_by}
    return _answer(ctx, "claims_summary", args, tools.claims_summary, **args)


@function_tool
def repeat_flooding(ctx: RunContextWrapper[Research], zips: list[str], min_claims: int = 10) -> str:
    """How often these ZIP codes flood again: every year with at least min_claims NFIP claims, and
    the median gap between those years.

    Args:
        zips: 5-digit ZIP codes, counted together.
        min_claims: Claims in a year for it to count as a flood year.
    """
    args = {"zips": zips, "min_claims": min_claims}
    return _answer(ctx, "repeat_flooding", args, tools.repeat_flooding, **args)


@function_tool
def price_path(ctx: RunContextWrapper[Research], zip_code: str, event_date: str) -> str:
    """A ZIP code's typical home value (Zillow ZHVI) from a year before an event to three years
    after: baseline, low point, and change after 1, 2 and 3 years.

    Args:
        zip_code: One 5-digit ZIP code.
        event_date: The event's date, YYYY-MM-DD.
    """
    args = {"zip_code": zip_code, "event_date": event_date}
    return _answer(ctx, "price_path", args, tools.price_path, **args)


@function_tool
def recovery_vs_controls(ctx: RunContextWrapper[Research], zip_code: str, event_date: str, event_name: str | None = None) -> str:
    """Compare a ZIP code's home values after an event with similar ZIP codes nearby that the event
    barely hit (the control ZIPs), and say whether and when it caught up. Returns a verdict, the
    method, and the control ZIPs used.

    Args:
        zip_code: One 5-digit ZIP code.
        event_date: The event's date, YYYY-MM-DD.
        event_name: Storm name as NFIP records it, e.g. 'Sandy', 'Ian'. Optional: without it,
            control ZIPs are those with few claims in the 30 days after event_date.
    """
    args = {"zip_code": zip_code, "event_date": event_date, "event_name": event_name}
    return _answer(ctx, "recovery_vs_controls", args, tools.recovery_vs_controls, **args)


@function_tool
def sql(ctx: RunContextWrapper[Research], query: str) -> str:
    """Run one read-only SQL SELECT (DuckDB dialect) over the tables, when no other tool answers.
    At most 50 rows come back. Call describe_tables first for the columns.

    Args:
        query: A SELECT or WITH ... SELECT over the tables nfip_claims, declarations, storm_events,
            storm_event_counties, zhvi, zips and zip_county.
    """
    return _answer(ctx, "sql", {"query": query}, tools.sql, query=query)


@function_tool
def describe_tables() -> str:
    """List the tables and their columns, for writing sql()."""
    return json.dumps(tools.describe_tables())


# --- The researchers: one data source each, fresh context per task ---

RESEARCHER_RULES = """
Rules:
- Make at most 5 tool calls. Use sql only if the other tools can't answer.
- Reply with the findings that answer your task and nothing else: at most 10 bullets.
- Every bullet states one claim with its number, and ends with the source_id of the tool result it
  came from, in brackets, e.g. "2,611 NFIP claims during Hurricane Ian, $576.2M paid [S4]".
- Use only numbers from tool results. Never estimate or round a number into a different claim.
- End with a bullet "Caveats:" giving the tool's caveats that matter for your findings, and say
  plainly what the data could not answer."""

researcher_agents = {
    "hazard": Agent(
        name="Hazard Researcher",
        instructions="""\
You research what happened in a place: storms and floods from NOAA's Storm Events Database, and
federal disaster declarations from FEMA. You get a task with the county FIPS code(s) and dates.
Report the events, their dates, deaths, damage, and a short quote from NOAA's narrative for the
biggest ones, and the matching federal declarations.""" + RESEARCHER_RULES,
        model=RESEARCHER_MODEL, model_settings=SETTINGS,
        tools=[storm_events, declarations, sql, describe_tables],
    ),
    "insurance": Agent(
        name="Insurance Researcher",
        instructions="""\
You research what floods cost in a place, from NFIP flood insurance claims. You get a task with the
ZIP codes and/or county FIPS code and dates. Report claim counts and payouts by event, how often the
place floods again (repeat_flooding), and the flood-zone mix. Remember that few claims can mean few
policies, not little flooding.""" + RESEARCHER_RULES,
        model=RESEARCHER_MODEL, model_settings=SETTINGS,
        tools=[claims_summary, repeat_flooding, sql, describe_tables],
    ),
    "market": Agent(
        name="Market Researcher",
        instructions="""\
You research what happened to home values after an event, from the Zillow Home Value Index. You get
a task with the ZIP code(s) and the event name and date. For each ZIP (at most 3), call
recovery_vs_controls; call price_path when you need the plain before/after values. Report the
baseline value, the low point, whether and when the ZIP caught up with its control ZIPs, and which
controls were used (control_scope). Never say the event caused the change: say how the ZIP moved
compared with its controls.""" + RESEARCHER_RULES,
        model=RESEARCHER_MODEL, model_settings=SETTINGS,
        tools=[recovery_vs_controls, price_path, sql, describe_tables],
    ),
}


@function_tool
async def research(ctx: RunContextWrapper[Research], researcher: Literal["hazard", "insurance", "market"], task: str) -> str:
    """Send a researcher to answer one task from its data. Returns its findings, each with a source id.

    Args:
        researcher: 'hazard' (NOAA storm events + FEMA declarations, by county), 'insurance' (NFIP
            flood claims, by ZIP or county) or 'market' (Zillow home values and recovery, by ZIP).
        task: What to find out, with everything the researcher needs: ZIP codes, county FIPS codes,
            event names and dates (YYYY-MM-DD).
    """
    if ctx.context.researchers >= MAX_RESEARCHERS:
        return "No researchers left. Reply DONE."
    # What researcher.as_tool() does: run the researcher in a fresh context and return its answer.
    # Written out so each run gets a numbered copy, and its own column in the context panel.
    ctx.context.researchers += 1
    base = researcher_agents[researcher]
    agent = base.clone(name=f"{base.name} {ctx.context.researchers}")
    result = await Runner.run(agent, f"Task: {task}", context=ctx.context, hooks=ctx.context.hooks, max_turns=8)
    ctx.context.findings.append(f"## {agent.name}\nTask: {task}\n\n{result.final_output}")
    return str(result.final_output)


# --- The lead: clarifies, plans, fans out to researchers, and checks for gaps ---

lead = Agent(
    name="Lead",
    instructions="""\
You lead a team researching floods and recovery in a US place, for home buyers, renters, local
journalists and planning students. The data covers NY, WA, CA, TX, MA, IL and FL only.

1. If the question doesn't name a place you can look up (a ZIP code, city, or county), or it is too
   broad (a whole state), and the user hasn't already answered a clarifying question, reply with
   "CLARIFY:" followed by 1 to 3 short questions, one per line (which place, which period or event,
   buying or renting), and nothing else. Otherwise don't ask: work with what you have.
2. Call resolve_place. For a neighborhood, pass its ZIP codes. Then call claims_summary on its ZIPs
   (or county) grouped by event, to find the floods that mattered and their peak dates.
3. Call research three times in the same turn, one per researcher (hazard, insurance, market), so
   they work in parallel. Give each a self-contained task: the ZIP codes, county FIPS code(s), the
   events with their dates (YYYY-MM-DD), and what to find out. For market, pick at most 3 ZIPs that
   have home values, and the most important event (or the one the user asked about).
4. Read the findings. If there's a gap (e.g. NOAA shows a big event but there are no claims, or a
   ZIP has no home values), call research again for just that part. One extra round at most.
5. Reply DONE and nothing else: a writer writes the report.""",
    model=MODEL,
    model_settings=SETTINGS,
    tools=[resolve_place, claims_summary, research],
)

# --- The writer: one call, after the research ends ---

writer = Agent(
    name="Writer",
    instructions="""\
Write a report in markdown that answers the question, using only the findings you're given.

Sections, in this order:
1. **Verdict**: one or two sentences, e.g. "Repeat flood zone; home values fell behind similar ZIPs
   after Sandy and had not caught up five years later", and a confidence level (high, medium, low)
   with the reason.
2. **What happened**: a timeline of the main events (NOAA, FEMA).
3. **What it cost**: the insurance picture: claims, payouts, how often the place floods again.
4. **What came after**: home values compared with the control ZIPs, and how the controls were chosen.
5. **What the data can't tell you**: the caveats that matter for this place, including: NFIP data
   can't say whether a specific house flooded; it only covers insured homes; these are records of
   the past, not forecasts; and the home value comparison is correlation, not causation.

Rules:
- End every sentence that states a number with the source id(s) of its finding, e.g. [S4].
- Use only numbers that appear in the findings. Never compute new totals, counts or percentages,
  e.g. don't turn "44.2% primary residences" and "2,611 claims" into a count of homes.
- Write for a home buyer, not a programmer: never mention field names, "null", or tool names.
- Never say the flood caused a price change: say how the ZIP's values moved compared with similar ZIPs.
- Write plain, specific sentences. No "profoundly", "pivotal", "it is worth noting".""",
    model=MODEL,
    model_settings=SETTINGS,
)


def source_list(report: str, sources: dict[str, dict]) -> str:
    """The sources the report cites, with the tool call, tables and SQL behind each."""
    cited = sorted(set(re.findall(r"\bS(\d+)\b", report)), key=int)
    lines = ["\n\n## Sources\n"]
    for n in cited:
        s = sources.get(f"S{n}")
        if not s:
            continue
        args = ", ".join(f"{k}={v!r}" for k, v in s["args"].items() if v not in (None, ""))
        tables = "; ".join(s.get("tables", {}).values()) or "see query"
        lines.append(f"- **[S{n}]** `{s['tool']}({args})`: {tables}")
        for q in s.get("queries", []):
            lines.append(f"  ```sql\n  {q}\n  ```")
    return "\n".join(lines) if len(lines) > 1 else ""


async def deep_research(question: str, clarification: str, hooks: PanelHooks) -> dict:
    research_run = Research(hooks=hooks)

    # 1. Research: clarify or plan, fan out, check for gaps
    prompt = f"Question: {question}"
    if clarification:
        prompt += f"\n\nThe user answered your clarifying questions: {clarification}\nDon't ask again."
    lead_result = await Runner.run(lead, prompt, context=research_run, hooks=hooks, max_turns=12)
    reply = str(lead_result.final_output).strip()
    if reply.startswith("CLARIFY:") and not research_run.findings:
        questions = [q.strip(" -•") for q in reply.removeprefix("CLARIFY:").splitlines() if q.strip(" -•")]
        return {"type": "clarify", "questions": questions}

    # 2. Write: one call, outside the loop. The Writer sees only the question and the findings.
    findings = "\n\n---\n\n".join(research_run.findings)
    asked = question + (f"\n(Clarified: {clarification})" if clarification else "")
    report = await Runner.run(writer, f"Question: {asked}\n\nFindings:\n\n{findings}", hooks=hooks)
    markdown = str(report.final_output)
    return {"type": "done", "markdown": markdown + source_list(markdown, research_run.sources),
            "charts": research_run.charts, "map": research_run.map}


# --- FastAPI App ---

HERE = Path(__file__).parent

# run_id -> every agent's context, in order. In-memory, single process. The context panel reads it.
sessions: dict[str, AgentContexts] = {}

app = FastAPI()
add_context_panel(app, sessions)


@app.get("/")
def index():
    # no-cache: browsers otherwise keep serving an old page after a redeploy
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/run")
async def run(question: str, clarification: str = ""):
    """Research one question and stream what happens as Server-Sent Events."""
    run_id = str(uuid.uuid4())
    sessions[run_id] = AgentContexts()
    queue: asyncio.Queue = asyncio.Queue()
    hooks = PanelHooks(sessions[run_id], queue.put_nowait)

    async def work():
        try:
            queue.put_nowait(await deep_research(question, clarification, hooks))
        except Exception as e:
            # Auth, quota, a model that won't stop: show it on the page, not as a dropped stream.
            queue.put_nowait({"type": "error", "message": f"{type(e).__name__}: {str(e)[:500]}"})

    task = asyncio.create_task(work())

    async def events():
        try:
            yield f"data: {json.dumps({'type': 'start', 'run_id': run_id})}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event["type"] in ("done", "clarify", "error"):
                    break
        finally:
            task.cancel()  # the page closed the stream: stop spending tokens

    return StreamingResponse(events(), media_type="text/event-stream")


def billed_project() -> str:
    """The project Vertex AI bills, resolved the way LiteLLM does: VERTEXAI_PROJECT first, then
    Google's default credentials (GOOGLE_CLOUD_PROJECT, then `gcloud config`)."""
    if os.environ.get("VERTEXAI_PROJECT"):
        return f"{os.environ['VERTEXAI_PROJECT']} (from VERTEXAI_PROJECT)"
    try:
        import google.auth

        _, project = google.auth.default()
        return f"{project} (from gcloud defaults: set VERTEXAI_PROJECT in .env to pin it)"
    except Exception as e:
        return f"unknown ({type(e).__name__}: run gcloud auth application-default login)"


if __name__ == "__main__":
    print(f"Vertex AI bills project: {billed_project()}")
    uvicorn.run(app, host="127.0.0.1", port=8000)
