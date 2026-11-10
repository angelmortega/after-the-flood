"""A side panel that shows the context: the exact message list sent to the model each turn.

To add it to a web agent that keeps a `sessions` dict:
    app.py:      add_context_panel(app, sessions)
    index.html:  <script src="/context-panel.js"></script>
                 then call showContext(sessionId) whenever the session changes
Delete those lines to remove it.

Several agents: store an AgentContexts in sessions instead of a message list, and record each
agent's context with set() and add(). showContext(sessionId) then draws a timeline: a column per
agent and a row per moment, so you can see who ran when and who waited on whom.

With the OpenAI Agents SDK, pass PanelHooks(contexts, emit) as Runner.run's hooks: it records every
agent's context for you, and calls emit with an event for each change.
"""

import json
from pathlib import Path

from fastapi.responses import FileResponse

try:  # Optional: five-questions-web uses the panel without the Agents SDK
    from agents import RunHooks
except ImportError:
    RunHooks = object


def add_context_panel(app, sessions):
    @app.get("/context")
    def context(session_id: str, after: int = 0):
        session = sessions.get(session_id, [])
        if isinstance(session, AgentContexts):
            return {"events": session.events[after:]}
        return session

    @app.get("/context-panel.js")
    def script():
        return FileResponse(Path(__file__).parent / "context-panel.js", headers={"Cache-Control": "no-cache"})


class AgentContexts:
    """Several agents' contexts, and when each message entered them.

    events is the timeline the panel draws, in the order things happened:
      {"type": "message", "agent", "message"}  a message entered the agent's context. A new
          message gets its own row. With "at", it belongs on that earlier event's row instead:
          "from" names the agent that wrote it (a shared conversation, like a handoff's), and
          without "from" it stands in for the message at the same position in the copy (a
          handoff's swapped instructions).
      {"type": "context", "agent", "total"}    the agent started a fresh context, not a grown one
    Every event carries its seq, which "at" refers to.
    """

    def __init__(self):
        self.contexts: dict[str, list[dict]] = {}
        self.events: list[dict] = []
        self._born: dict[str, tuple[str, int]] = {}  # message key -> (agent, seq) where it first appeared
        self._shown: dict[tuple[str, int], int] = {}  # (agent, position in its context) -> seq
        self._after: dict[int, int] = {}  # seq of a message -> the original of the message before it

    def set(self, agent: str, messages: list[dict], own: bool = False) -> None:
        """Record the whole context an agent sends to the model. own: the new messages are the
        agent's own, like its reply, so they're never copies of another agent's."""
        old = self.contexts.get(agent)
        grown = bool(old) and messages[: len(old)] == old
        start = len(old) if grown else 0
        self.contexts[agent] = list(messages)
        keys = self._keys(messages)
        born = [self._born.get(k) for k in keys]
        # Messages another agent wrote: this context shares (part of) that agent's conversation.
        # A system prompt is never shared: copies of one agent have the same one, but not a conversation.
        # A tool result goes with its call: when two agents make the same call, each result is its own.
        # But a result to this agent's call that another agent saw first (a handoff's) is a copy.
        copied = ["from" in self.events[self._shown[(agent, i)]] for i in range(start)]
        for i in range(start, len(messages)):
            role = messages[i].get("role")
            ours = i - 1 < start and born[i] and self._after.get(born[i][1]) == self._original(agent, i - 1)
            follows = role != "tool" or (i > 0 and (copied[i - 1] or ours))
            copied.append(bool(born[i] and born[i][0] != agent and not own and role != "system" and follows))
        copies = [i for i in range(start, len(messages)) if copied[i]]
        source = born[copies[0]][0] if copies else None
        if not grown:
            self._log(agent, "context", total=len(messages))
        for i in range(start, len(messages)):
            if copied[i]:
                fields = {"from": born[i][0], "at": born[i][1]}  # show it beside the original
            elif not old and copies and i < copies[-1] and (source, i) in self._shown:
                # An agent's first context, copied from another: line a swapped message up with the one it replaces
                fields = {"at": self._shown[(source, i)]}
            else:
                fields = {}  # new to the conversation: its own row, now
            seq = self._log(agent, "message", message=messages[i], **fields)
            self._born.setdefault(keys[i], (agent, seq))
            self._shown[(agent, i)] = seq
            if i > 0:
                self._after[seq] = self._original(agent, i - 1)

    def add(self, agent: str, messages: list[dict]) -> None:
        """Record the model's reply, appended to the agent's context."""
        self.set(agent, self.contexts.get(agent, []) + list(messages), own=True)

    def _original(self, agent: str, i: int) -> int | None:
        """The seq of the message at position i of the agent's context, or of the original it copies."""
        seq = self._shown.get((agent, i))
        event = self.events[seq] if seq is not None else {}
        return event["at"] if "from" in event else seq

    @staticmethod
    def _keys(messages: list[dict]) -> list[str]:
        """A message's identity: its content plus how many identical ones came before it, so a
        second, identical tool result is still new, but the same conversation copied elsewhere is not."""
        counts: dict[str, int] = {}
        keys = []
        for message in messages:
            text = json.dumps(message, sort_keys=True, default=str)
            counts[text] = counts.get(text, 0) + 1
            keys.append(f"{counts[text]}:{text}")
        return keys

    def _log(self, agent: str, kind: str, **fields) -> int:
        seq = len(self.events)
        self.events.append({"seq": seq, "agent": agent, "type": kind, **fields})
        return seq


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # [{"type": "output_text", "text": ...}, ...]
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return json.dumps(content)


def _arguments(raw):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def from_agents_sdk(system_prompt: str | None, items: list[dict]) -> list[dict]:
    """Agents SDK input or output items -> the panel's {role, content, tool_calls} messages."""
    messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
    for item in items:
        kind = item.get("type")
        if kind == "function_call":
            call = {"name": item.get("name"), "arguments": _arguments(item.get("arguments"))}
            if messages and messages[-1].get("tool_calls") is not None:
                messages[-1]["tool_calls"].append(call)  # parallel calls share one assistant turn
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif kind == "function_call_output":
            messages.append({"role": "tool", "content": _text(item.get("output"))})
        elif kind == "reasoning":
            continue
        elif "role" in item:
            messages.append({"role": item["role"], "content": _text(item.get("content"))})
    return messages


# --- OpenAI Agents SDK ---


def _plain(value):
    """Make SDK items (TypedDicts and pydantic models) JSON-safe."""
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return json.loads(json.dumps(value, default=str))


def _first_line(text, limit: int = 120) -> str:
    line = str(text).strip().splitlines()[0] if str(text).strip() else ""
    return line if len(line) <= limit else line[:limit] + "..."


class PanelHooks(RunHooks):
    """Records each agent's context in contexts, exactly as the model saw it: on_llm_start gets the
    system prompt and every input item, and on_llm_end gets the reply. emit gets an event per change:
        {"type": "context", "agent"}            a context changed: call showContext() on the page
        {"type": "activity", "agent", "text"}   one line per step: started, called a tool, handed off
    Subclass it to emit events of your own."""

    def __init__(self, contexts: AgentContexts, emit):
        self.contexts = contexts
        self.emit = emit

    def activity(self, agent: str, text: str) -> None:
        self.emit({"type": "activity", "agent": agent, "text": text})

    async def on_agent_start(self, context, agent):
        self.activity(agent.name, "started")

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        self.contexts.set(agent.name, from_agents_sdk(system_prompt, [_plain(i) for i in input_items]))
        self.emit({"type": "context", "agent": agent.name})

    async def on_llm_end(self, context, agent, response):
        self.contexts.add(agent.name, from_agents_sdk(None, [_plain(i) for i in response.output]))
        self.emit({"type": "context", "agent": agent.name})

    async def on_tool_start(self, context, agent, tool):
        self.activity(agent.name, f"called {tool.name}")

    async def on_handoff(self, context, from_agent, to_agent):
        self.activity(from_agent.name, f"handed off to {to_agent.name}")

    async def on_agent_end(self, context, agent, output):
        self.activity(agent.name, f"finished: {_first_line(output)}")


if __name__ == "__main__":
    # Self-check: uv run context_panel.py
    log = AgentContexts()
    sys_a, sys_b, url = {"role": "system", "content": "A"}, {"role": "system", "content": "B"}, {"role": "user", "content": "url"}
    call, ok = {"role": "assistant", "content": None, "tool_calls": [{"name": "t", "arguments": {}}]}, {"role": "tool", "content": "ok"}
    log.set("A", [sys_a, url])  # seq 0 context, 1 sys_a, 2 url
    log.add("A", [call])  # 3
    log.set("A", [sys_a, url, call, ok])  # 4
    log.set("B", [sys_b, url, call, ok, {"role": "user", "content": "x"}])  # a handoff: 5 context, 6..10
    by_seq = {e["seq"]: e for e in log.events}
    assert by_seq[6]["message"] == sys_b and by_seq[6]["at"] == 1 and "from" not in by_seq[6]  # swapped, aligned
    assert by_seq[7]["from"] == "A" and by_seq[7]["at"] == 2  # copied url sits beside A's
    assert "at" not in by_seq[10]  # x is new: a row of its own
    log.set("A", [sys_a, url, call, ok, {"role": "user", "content": "x"}])  # B hands back: B's x reaches A
    assert log.events[-1]["from"] == "B" and log.events[-1]["at"] == 10
    log.set("A", [sys_a, url, call, ok, {"role": "user", "content": "x"}, call, ok])  # the same call again: new
    assert "at" not in log.events[-1] and "at" not in log.events[-2]
    # A hands off: its transfer call's result enters B first, then reaches A when B hands back
    to_b, to_a = ({"role": "assistant", "content": None, "tool_calls": [{"name": n, "arguments": {}}]} for n in ("to_b", "to_a"))
    done = {"role": "tool", "content": "B"}
    a_ctx = [sys_a, url, call, ok, {"role": "user", "content": "x"}, call, ok]
    log.add("A", [to_b])
    log.set("B", [sys_b] + a_ctx[1:] + [to_b, done])
    log.add("B", [to_a])
    log.set("A", a_ctx + [to_b, done, to_a])
    assert [e.get("from") for e in log.events[-2:]] == ["B", "B"], log.events[-2:]  # in order, on B's rows
    log.set("C", [{"role": "system", "content": "C"}, {"role": "user", "content": "deck"}])  # a sub-agent: fresh
    assert all("at" not in e for e in log.events[-2:])
    # The sub-agent makes the same call A made, and gets the same result: both are C's own, not copies
    log.add("C", [call])
    log.set("C", [{"role": "system", "content": "C"}, {"role": "user", "content": "deck"}, call, ok])
    assert all("from" not in e and "at" not in e for e in log.events[-2:]), log.events[-2:]
    log.set("C 2", [{"role": "system", "content": "C"}, {"role": "user", "content": "deck 2"}])  # a copy of C
    assert all("from" not in e and "at" not in e for e in log.events[-2:]), log.events[-2:]
    # B hands back to A with a fresh context (a summary instead of the transcript): A's column already
    # has rows, so A's re-sent system prompt and the summary take new rows; only B's words line up.
    log.set("A", [sys_a, {"role": "user", "content": "(summary)"}, {"role": "user", "content": "x"}])
    last = log.events[-3:]
    assert "at" not in last[0] and "at" not in last[1] and last[2].get("from") == "B", last
    print("ok")
