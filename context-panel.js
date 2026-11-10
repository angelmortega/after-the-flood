// Context panel: shows the message list the server sends to the model on every turn.
// Include with <script src="/context-panel.js"></script>, then call showContext(sessionId).
//
// Several agents: store an AgentContexts on the server (see context_panel.py) and call
// showContext(sessionId) on every change. The panel draws a timeline: a column per agent and a row
// per message, at the moment it was written. A sub-agent's fresh context starts where its parent
// called it; a handoff's copied conversation lines up with the original, dimmed. A dashed line marks
// an agent waiting on its own tool call. Columns share the panel's width; past about three on a
// laptop screen, the panel scrolls sideways. resetContext() empties the panel for a new run.

const contextStyle = document.createElement("style");
contextStyle.textContent = `
    :root { --context-width: min(40vw, 32rem); }
    body { margin-right: var(--context-width); }
    #context-panel {
        position: fixed; top: 0; right: 0; bottom: 0;
        width: var(--context-width);
        overflow-y: auto;
        padding: 1.5rem;
        background: #fafafa;
        border-left: 1px solid #e0e0e0;
        font-family: ui-monospace, monospace;
        font-size: 0.75rem;
    }
    #context-panel h2, .ctx-head {
        font-family: system-ui, sans-serif;
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #999;
    }
    #context-panel h2 { margin-bottom: 1rem; }
    /* Several agents: a timeline grid, one column per agent */
    #context-panel.timeline { overflow-x: auto; padding: 0 1rem 1rem; scroll-padding: 0 1rem; }
    /* Columns share the width, down to 16rem each; only then does the panel scroll sideways */
    .ctx-grid { display: grid; grid-template-columns: repeat(var(--ctx-cols, 1), minmax(16rem, 1fr)); column-gap: 1rem; }
    .ctx-head {
        position: sticky; top: 0; z-index: 1;
        background: #fafafa;
        padding: 1rem 0 0.6rem;
        border-top: 3px solid transparent;
        margin-bottom: 0.5rem;
    }
    .ctx-head.active { color: #000; border-top-color: #000; }
    .ctx-wait { border-left: 2px dashed #ccc; margin-left: 1px; }
    .ctx-note { font-family: system-ui, sans-serif; color: #999; padding: 0.2rem 0.75rem; margin-bottom: 0.5rem; }
    .ctx-note.divider { border-top: 1px solid #ccc; padding-top: 0.5rem; }
    .context-msg {
        border-left: 3px solid #999;
        padding: 0.4rem 0.75rem;
        margin-bottom: 0.5rem;
        white-space: pre-wrap;
        word-break: break-word;
    }
    .context-msg .role { font-weight: bold; }
    .context-msg.system { border-color: #dc2626; }
    .context-msg.user { border-color: #16a34a; }
    .context-msg.assistant { border-color: #2563eb; }
    .context-msg.tool { border-color: #f59e0b; }
    .context-msg.new { animation: context-flash 1.5s; }
    .context-msg.thinking { border-color: #ccc; color: #aaa; }
    .context-msg summary { cursor: pointer; color: #666; }
    .context-msg.copied { opacity: 0.55; }
    @keyframes context-flash { from { background: #fef3c7; } }
    @media (max-width: 800px) {
        body { margin-right: 0; height: auto !important; flex-direction: column; }
        #context-panel { position: static; width: 100%; border-left: none; }
        #context-panel.timeline { height: 70vh; }
    }
`;
document.head.appendChild(contextStyle);

const contextPanel = document.createElement("aside");
contextPanel.id = "context-panel";
document.body.appendChild(contextPanel);

const CONTEXT_FOLD = 1500; // longer messages (a fetched article, say) fold to their start
const TIMELINE_FOLD = 700; // tighter in the timeline: a tall cell stretches its whole row

let contextShown = 0; // single agent: how many messages were already shown
let contextQueue = Promise.resolve(); // one fetch at a time, so timeline events arrive in order
let timeline = null; // several agents: built on the first timeline response

function showContext(sessionId) {
    contextQueue = contextQueue.then(() => loadContext(sessionId)).catch((e) => console.error(e));
    return contextQueue;
}

function resetContext() {
    contextShown = 0;
    timeline = null;
    contextPanel.className = "";
    contextPanel.innerHTML = "";
    document.documentElement.style.removeProperty("--context-width");
}

async function loadContext(sessionId) {
    const after = timeline ? timeline.seq : 0;
    const res = await fetch("/context?session_id=" + encodeURIComponent(sessionId) + "&after=" + after);
    const data = await res.json();
    if (Array.isArray(data)) renderSingle(data);
    else renderTimeline(data.events);
}

// --- One agent: the message list ---

function renderSingle(messages) {
    contextPanel.innerHTML = "<h2>Context (" + messages.length + " messages)</h2>";
    messages.forEach((m, i) => {
        const isNew = i >= contextShown; // highlight what this turn added
        // Hidden thinking gets its own grayed-out block: it isn't sent back to the model
        if (m.thinking) contextPanel.appendChild(contextBlock("thinking", m.thinking, isNew));
        // Tool-call turns have no text, so show the calls instead
        contextPanel.appendChild(contextBlock(m.role, m.content ?? formatToolCalls(m.tool_calls), isNew));
    });
    contextShown = messages.length;
    contextPanel.scrollTop = contextPanel.scrollHeight;
}

// --- Several agents: the timeline ---

function renderTimeline(events) {
    if (!timeline) {
        const grid = document.createElement("div");
        grid.className = "ctx-grid";
        contextPanel.innerHTML = "";
        contextPanel.className = "timeline";
        contextPanel.appendChild(grid);
        timeline = { grid, seq: 0, row: 1, agents: [], heads: {}, rows: {}, waiting: {}, rowOf: {}, taken: new Set() };
    }
    const atBottom = contextPanel.scrollTop + contextPanel.clientHeight >= contextPanel.scrollHeight - 40;
    let last = null;
    for (const e of events) {
        if (e.seq < timeline.seq) continue; // already drawn
        timeline.seq = e.seq + 1;
        if (!timeline.agents.includes(e.agent)) addTimelineAgent(e.agent);
        last = e.agent;

        const cell = timelineCell(e);
        if (cell) {
            let row = timeline.rowOf[e.at];
            // An earlier row, unless this column already has something there
            if (row !== undefined && timeline.taken.has(e.agent + "|" + row)) row = undefined;
            if (row === undefined) {
                // A new row, now. Every other agent waiting on a tool call gets a dashed line through it.
                row = ++timeline.row;
                for (const other of timeline.agents) {
                    if (other !== e.agent && timeline.waiting[other]) {
                        placeCell(Object.assign(document.createElement("div"), { className: "ctx-wait" }), other, row);
                    }
                }
            }
            timeline.rowOf[e.seq] = row;
            timeline.taken.add(e.agent + "|" + row);
            placeCell(cell, e.agent, row);
            timeline.rows[e.agent] = (timeline.rows[e.agent] ?? 0) + 1;
        }
        if (e.type === "context") timeline.waiting[e.agent] = false;
        // Waiting on a tool call: only this agent's own calls count, not one copied from another agent
        if (e.type === "message" && !e.from) timeline.waiting[e.agent] = e.message.role === "assistant" && !!e.message.tool_calls;
    }
    if (!last) return;
    // Mark the agent that changed last, and bring its column into view
    for (const [agent, head] of Object.entries(timeline.heads)) head.classList.toggle("active", agent === last);
    timeline.heads[last].scrollIntoView({ inline: "nearest", block: "nearest" });
    if (atBottom) contextPanel.scrollTop = contextPanel.scrollHeight;
}

function addTimelineAgent(agent) {
    timeline.agents.push(agent);
    const n = timeline.agents.length;
    const head = document.createElement("div");
    head.className = "ctx-head";
    head.textContent = agent;
    timeline.heads[agent] = head;
    placeCell(head, agent, 1);
    timeline.grid.style.setProperty("--ctx-cols", n);
    // Room for every column up to about two-thirds of the window; past that, scroll sideways
    document.documentElement.style.setProperty("--context-width", `min(64vw, ${n * 24 + (n - 1) + 2.25}rem)`);
}

function timelineCell(e) {
    if (e.type === "message") {
        const m = e.message;
        const block = contextBlock(m.role, m.content ?? formatToolCalls(m.tool_calls), true, TIMELINE_FOLD);
        if (e.from) {
            // Another agent wrote this: this context shares that agent's conversation
            block.classList.add("copied");
            block.querySelector(".role").textContent += " (from " + e.from + ")";
        }
        return block;
    }
    // A fresh context: an agent's first needs no divider
    if (e.type !== "context" || !timeline.rows[e.agent]) return null;
    const note = document.createElement("div");
    note.className = "ctx-note divider";
    note.textContent = "new context (" + e.total + " messages)";
    return note;
}

function placeCell(cell, agent, row) {
    cell.style.gridColumn = timeline.agents.indexOf(agent) + 1;
    cell.style.gridRow = row;
    timeline.grid.appendChild(cell);
}

// --- Shared ---

// name(key: value, ...), with string values printed as text so a markdown argument stays readable.
// Takes LiteLLM's {function: {name, arguments}} calls or a flat {name, arguments}.
function formatToolCalls(calls) {
    return (calls ?? [])
        .map((call) => {
            const name = call.name ?? call.function?.name;
            let args = call.arguments ?? call.function?.arguments;
            if (typeof args === "string") {
                try {
                    args = JSON.parse(args);
                } catch {}
            }
            const lines =
                args && typeof args === "object"
                    ? Object.entries(args).map(([k, v]) => k + ": " + (typeof v === "string" ? v : JSON.stringify(v, null, 2)))
                    : [String(args ?? "")].filter(Boolean);
            if (!lines.length) return name + "()";
            return name + "(\n" + lines.map((l) => "  " + l.replaceAll("\n", "\n  ")).join("\n") + "\n)";
        })
        .join("\n");
}

function contextBlock(label, text, isNew, fold = CONTEXT_FOLD) {
    const div = document.createElement("div");
    div.className = "context-msg " + label + (isNew ? " new" : "");
    const role = document.createElement("div");
    role.className = "role";
    role.textContent = label;
    div.appendChild(role);
    if (text.length > fold) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = text.slice(0, 300) + "... (" + text.length + " characters)";
        details.append(summary, text);
        div.appendChild(details);
    } else {
        div.append(text);
    }
    return div;
}
