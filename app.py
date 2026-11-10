import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse

import wikipedia
from context_panel import AgentContexts, PanelHooks, add_context_panel

# WIKIMEDIA_USERNAME and WIKIMEDIA_PASSWORD come from the .env file next to this one.
load_dotenv(Path(__file__).parent / ".env")

# Traces upload to OpenAI by default, which needs an OpenAI key. We're on Gemini.
set_tracing_disabled(True)

# LiteLLM routes the call to Gemini. api_key is unused: Vertex AI uses your gcloud credentials.
MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash", api_key="unused")
# Researchers only read and take notes: the cheaper model is enough.
RESEARCHER_MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash-lite", api_key="unused")
# A stalled model call would hang the page: give up after 90 s, and retry twice.
SETTINGS = ModelSettings(extra_args={"vertex_location": "global", "timeout": 90, "num_retries": 2})

# The Lead gets this many researchers in all. Prompts ask for restraint; code enforces it.
MAX_RESEARCHERS = 6


@dataclass
class Research:
    """One run's state, shared by every agent and tool in it. The model never sees it."""

    hooks: PanelHooks
    researchers: int = 0  # how many researchers have started, to number them
    findings: list[str] = field(default_factory=list)  # what each researcher reported, for the Writer


# --- Tools ---


@function_tool
def get_article(url: str) -> str:
    """Get the text of an English Wikipedia article, and the articles it links to.

    Args:
        url: The article's URL, e.g. 'https://en.wikipedia.org/wiki/Ada_Lovelace'.
    """
    try:
        article = wikipedia.fetch_article(url)
    except Exception as e:
        # The model cannot see an exception. Return something it can reason about.
        return json.dumps({"error": f"Could not fetch {url}: {type(e).__name__}: {e}"})
    links = "\n".join(f"- {link['text']}: {link['url']}" for link in article["links"])
    return f"# {article['title']}\n{article['description']}\nSource: {article['url']}\n\n{article['text']}\n\n## Links\n{links}"


# --- The researcher: one task, by walking Wikipedia's links ---

researcher = Agent(
    name="Researcher",
    instructions="""\
You research one task on English Wikipedia, starting from the article you're given.

1. Read the starting article with get_article.
2. Walk the links: when an article links to one that's more specific to your task, read that one.
   If the starting article doesn't answer your task, follow its links before giving up.
   Read at most 4 articles in all.
3. Reply with the facts that answer your task and nothing else, at most 8 bullets. End every
   bullet with a link to the article it came from, e.g.
   ([Harvard Mark I](https://en.wikipedia.org/wiki/Harvard_Mark_I)).
   If the articles don't answer part of the task, say so in a last bullet.""",
    model=RESEARCHER_MODEL,
    model_settings=SETTINGS,
    tools=[get_article],
)


@function_tool
async def research(ctx: RunContextWrapper[Research], task: str, start_url: str) -> str:
    """Send a researcher to answer one task by walking Wikipedia from a starting article.
    Returns its findings, each with the article it came from.

    Args:
        task: One specific thing to find out.
        start_url: The Wikipedia article to start from: one of the links you've seen.
    """
    if ctx.context.researchers >= MAX_RESEARCHERS:
        return "No researchers left. Reply DONE."
    # What researcher.as_tool() does: run the researcher in a fresh context and return its answer.
    # Written out so each run gets a numbered copy, and its own column in the context panel.
    ctx.context.researchers += 1
    agent = researcher.clone(name=f"Researcher {ctx.context.researchers}")
    prompt = f"Task: {task}\nStart at: {start_url}"
    result = await Runner.run(agent, prompt, context=ctx.context, hooks=ctx.context.hooks, max_turns=12)
    ctx.context.findings.append(f"{task}\n\n{result.final_output}")
    return str(result.final_output)


# --- The lead: plans, fans out to researchers, and checks for gaps ---

lead = Agent(
    name="Lead",
    instructions="""\
You lead a research team. The user gives you a question and a Wikipedia article to start from.

1. Read the starting article with get_article.
2. Split the question into 2 to 4 independent research tasks. Call research once per task, all in
   the same turn, so the researchers work in parallel. Give each a starting URL from the
   article's links: another article, never the starting one.
3. Read the findings. If part of the question is still unanswered, call research again for just
   that part. Do this at most twice.
4. When the question is covered, reply DONE and nothing else: a writer writes the report.""",
    model=MODEL,
    model_settings=SETTINGS,
    tools=[get_article, research],
)

# --- The writer: one call, after the research ends ---

writer = Agent(
    name="Writer",
    instructions="""\
Write a short report in markdown that answers the question, using only the findings you're given.

- Start with a one-paragraph answer, then a section per part of the question.
- Keep the links: end every sentence that states a fact with the link from its finding.
- If the findings don't answer part of the question, say so.
- Write plain, specific sentences. Don't claim more significance than the findings do: no
  "profoundly", "pivotal", "paving the way", or "it is worth noting".""",
    model=MODEL,
    model_settings=SETTINGS,
)


async def deep_research(question: str, start_url: str, hooks: PanelHooks) -> str:
    research_run = Research(hooks=hooks)

    # 1. Research: plan, fan out, check for gaps
    prompt = f"Question: {question}\nStart at: {start_url}"
    await Runner.run(lead, prompt, context=research_run, hooks=hooks, max_turns=12)

    # 2. Write: one call, outside the loop. The Writer sees only the question and the findings.
    findings = "\n\n---\n\n".join(research_run.findings)
    report = await Runner.run(writer, f"Question: {question}\n\nFindings:\n\n{findings}", hooks=hooks)
    return str(report.final_output)


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
async def run(question: str, url: str):
    """Research one question and stream what happens as Server-Sent Events."""
    run_id = str(uuid.uuid4())
    sessions[run_id] = AgentContexts()
    queue: asyncio.Queue = asyncio.Queue()
    hooks = PanelHooks(sessions[run_id], queue.put_nowait)

    async def work():
        try:
            report = await deep_research(question, url, hooks)
            queue.put_nowait({"type": "done", "markdown": report})
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
                if event["type"] in ("done", "error"):
                    break
        finally:
            task.cancel()  # the page closed the stream: stop spending tokens

    return StreamingResponse(events(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
