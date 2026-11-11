"""Fill in the README's sample answers: the same model with no tools and no system prompt, next to
the agent's answer.

    uv run sample_answers.py          # all three
    uv run sample_answers.py 2        # only query 2

Needs the same setup as the app (.env with VERTEXAI_PROJECT, gcloud application-default login).
Writes the answers between the <!-- answer:qN:... --> markers in README.md.
"""

import asyncio
import re
import sys
from pathlib import Path

import litellm

import app

README = Path(__file__).parent / "README.md"

# question, and the answer to the Lead's clarifying questions if it asks
QUERIES = {
    "1": ("How did Hurricane Sandy affect home values in Long Beach, NY (11561)?", ""),
    "2": ("Which ZIP codes in Harris County, TX have flooded the most since 2000?", ""),
    "3": ("Is it safe to buy a beach house in Florida?", "Fort Myers Beach (33931), after Hurricane Ian. Buying."),
}


async def baseline(question: str) -> str:
    """The same model as the Lead and Writer: no tools, no system prompt, just the question."""
    response = await litellm.acompletion(
        model=app.MODEL.model, messages=[{"role": "user", "content": question}],
        vertex_location="global", timeout=120,
    )
    return response.choices[0].message.content.strip()


async def agent(question: str, clarification: str) -> str:
    hooks = app.ChatHooks(app.AgentContexts())
    result = await app.deep_research(question, "", hooks)
    if result["type"] == "clarify":
        asked = "\n".join(f"> - {q}" for q in result["questions"])
        result = await app.deep_research(question, clarification, app.ChatHooks(app.AgentContexts()))
        intro = f"*The Lead asked first:*\n{asked}\n\n*Answered with:* \"{clarification}\"\n\n"
        return intro + result["markdown"]
    return result["markdown"]


def fill(text: str, n: str, kind: str, label: str, answer: str) -> str:
    body = f"**{label}:**\n\n<details><summary>Show the answer</summary>\n\n{answer}\n\n</details>"
    pattern = re.compile(rf"(<!-- answer:q{n}:{kind} -->\n).*?(\n<!-- /answer:q{n}:{kind} -->)", re.S)
    return pattern.sub(lambda m: m.group(1) + body + m.group(2), text)


async def main(which: list[str]) -> None:
    for n in which:
        question, clarification = QUERIES[n]
        print(f"Query {n}: {question}", flush=True)
        no_tools, with_tools = await asyncio.gather(baseline(question), agent(question, clarification))
        text = README.read_text()
        text = fill(text, n, "baseline", "No tools (Gemini 3.5 Flash, no system prompt)", no_tools)
        text = fill(text, n, "agent", "Agent", with_tools)
        README.write_text(text)
        print(f"  written to {README.name}", flush=True)


if __name__ == "__main__":
    print(f"Vertex AI bills project: {app.billed_project()}")
    asyncio.run(main(sys.argv[1:] or list(QUERIES)))
