"""
Flight Notification UI

A FastAPI web app that serves a browser UI connecting to the WebSocket
flight notification server and displays real-time flight status updates.

Usage:
    python ui.py              # serves on http://localhost:5001
    # Make sure server.py is also running: python server.py
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flight Monitor</title>
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
        font-family: 'Segoe UI', system-ui, sans-serif;
        background: #0a0f1e;
        color: #e2e8f0;
        min-height: 100vh;
        padding: 24px;
    }

    header {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 28px;
    }

    header h1 {
        font-size: 1.6rem;
        font-weight: 700;
        letter-spacing: 0.02em;
        color: #f8fafc;
    }

    #conn-badge {
        padding: 4px 12px;
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.05em;
        background: #1e293b;
        color: #94a3b8;
        transition: background 0.3s, color 0.3s;
    }

    #conn-badge.connected    { background: #14532d; color: #86efac; }
    #conn-badge.disconnected { background: #7f1d1d; color: #fca5a5; }

    #flights {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
        gap: 16px;
        margin-bottom: 32px;
    }

    .flight-card {
        background: #111827;
        border: 1px solid #1e293b;
        border-radius: 12px;
        padding: 20px;
        transition: border-color 0.3s;
    }

    .flight-card.flash { border-color: #3b82f6; }

    .card-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        margin-bottom: 14px;
    }

    .flight-num {
        font-size: 1.2rem;
        font-weight: 700;
        color: #f8fafc;
    }

    .airline {
        font-size: 0.8rem;
        color: #64748b;
        margin-top: 2px;
    }

    .status-badge {
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
    }

    .status-ON_TIME     { background:#14532d; color:#86efac; }
    .status-BOARDING    { background:#0c4a6e; color:#7dd3fc; }
    .status-DEPARTED    { background:#0c4a6e; color:#7dd3fc; }
    .status-IN_FLIGHT   { background:#1e3a5f; color:#93c5fd; }
    .status-DELAYED     { background:#78350f; color:#fcd34d; }
    .status-RESCHEDULED { background:#78350f; color:#fcd34d; }
    .status-LANDED      { background:#14532d; color:#86efac; }
    .status-ARRIVED     { background:#14532d; color:#86efac; }
    .status-UNKNOWN     { background:#1e293b; color:#94a3b8; }

    .route { font-size: 0.88rem; color: #cbd5e1; margin-bottom: 6px; }
    .meta  { font-size: 0.78rem; color: #475569; margin-bottom: 4px; }

    .last-msg {
        margin-top: 12px;
        padding: 10px;
        background: #0f172a;
        border-radius: 8px;
        font-size: 0.8rem;
        color: #94a3b8;
        line-height: 1.5;
        min-height: 40px;
    }

    #log-section h2 {
        font-size: 1rem;
        font-weight: 600;
        color: #94a3b8;
        margin-bottom: 12px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }

    #event-log {
        background: #111827;
        border: 1px solid #1e293b;
        border-radius: 12px;
        padding: 16px;
        height: 300px;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 6px;
    }

    .log-entry {
        font-size: 0.8rem;
        padding: 8px 10px;
        border-radius: 6px;
        border-left: 3px solid #1e293b;
        background: #0f172a;
        color: #94a3b8;
        line-height: 1.4;
    }

    .log-entry.update    { border-left-color: #3b82f6; }
    .log-entry.delayed   { border-left-color: #f59e0b; color: #fcd34d; }
    .log-entry.heartbeat { border-left-color: #10b981; color: #6ee7b7; }
    .log-entry.welcome   { border-left-color: #8b5cf6; color: #c4b5fd; }
    .log-entry.error     { border-left-color: #ef4444; color: #fca5a5; }

    .log-time { font-size: 0.7rem; color: #475569; margin-right: 8px; }

    #impact-banner {
        display: none;
        background: #78350f;
        border: 1px solid #92400e;
        border-radius: 10px;
        padding: 14px 18px;
        margin-bottom: 20px;
        font-size: 0.88rem;
        color: #fcd34d;
    }

    #impact-banner.show { display: block; }
</style>
</head>
<body>

<header>
    <h1>&#9992; Flight Monitor</h1>
    <span id="conn-badge">CONNECTING&hellip;</span>
</header>

<div id="impact-banner"></div>
<div id="flights"></div>

<div id="log-section">
    <h2>Live Event Log</h2>
    <div id="event-log"></div>
</div>

<script>
const WS_URL = "ws://localhost:8765";
const badge     = document.getElementById("conn-badge");
const flightsEl = document.getElementById("flights");
const logEl     = document.getElementById("event-log");
const banner    = document.getElementById("impact-banner");

function statusClass(s) {
    const known = ["ON_TIME","BOARDING","DEPARTED","IN_FLIGHT","DELAYED","RESCHEDULED","LANDED","ARRIVED"];
    return known.includes(s) ? "status-" + s : "status-UNKNOWN";
}

function renderCard(f, status, msg) {
    const id = "card-" + f.flight_number.replace("-", "_");
    let card = document.getElementById(id);
    if (!card) {
        card = document.createElement("div");
        card.id = id;
        card.className = "flight-card";
        flightsEl.appendChild(card);
    }

    card.innerHTML = `
        <div class="card-header">
            <div>
                <div class="flight-num">${f.flight_number}</div>
                <div class="airline">${f.airline || ""}</div>
            </div>
            <span class="status-badge ${statusClass(status)}">${status}</span>
        </div>
        <div class="route">${f.origin} &rarr; ${f.destination}</div>
        <div class="meta">Dep: ${f.scheduled_departure || "&#8212;"} on ${f.departure_date || "&#8212;"}</div>
        <div class="meta">Terminal: ${f.terminal || "&#8212;"}</div>
        ${msg ? `<div class="last-msg">${msg}</div>` : ""}
    `;

    card.classList.add("flash");
    setTimeout(() => card.classList.remove("flash"), 1200);
}

function addLog(text, cls) {
    const entry = document.createElement("div");
    entry.className = "log-entry " + cls;
    entry.innerHTML = `<span class="log-time">${new Date().toLocaleTimeString()}</span>${text}`;
    logEl.insertBefore(entry, logEl.firstChild);
    while (logEl.children.length > 200) logEl.removeChild(logEl.lastChild);
}

function handleMessage(raw) {
    let data;
    try { data = JSON.parse(raw); }
    catch { addLog("Non-JSON: " + raw, "error"); return; }

    const ev = data.event;

    if (ev === "WELCOME") {
        badge.textContent = "CONNECTED";
        badge.className = "connected";
        (data.flights || []).forEach(f => renderCard(f, f.status, ""));
        addLog(data.message, "welcome");
        return;
    }

    if (ev === "FLIGHT_UPDATE") {
        const f = data.flight || {};
        const u = data.update || {};
        const impacted = u.status === "DELAYED" || u.status === "RESCHEDULED";
        renderCard(f, u.status, u.message);
        if (impacted) {
            banner.textContent = "\u26a0 Itinerary impacted \u2014 " + u.message;
            banner.classList.add("show");
        }
        addLog(`<b>${f.flight_number}</b> &rarr; ${u.status}: ${u.message}`, impacted ? "delayed" : "update");
        return;
    }

    if (ev === "HEARTBEAT") {
        addLog("&#9829; " + data.message, "heartbeat");
        return;
    }

    if (ev === "ERROR") {
        addLog("ERROR: " + data.message, "error");
        return;
    }

    addLog(raw, "");
}

function connect() {
    const ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        badge.textContent = "CONNECTED";
        badge.className = "connected";
    };

    ws.onmessage = e => handleMessage(e.data);

    ws.onclose = () => {
        badge.textContent = "DISCONNECTED";
        badge.className = "disconnected";
        addLog("Connection closed \u2014 retrying in 3 s\u2026", "error");
        setTimeout(connect, 3000);
    };

    ws.onerror = () => {
        badge.textContent = "ERROR";
        badge.className = "disconnected";
    };
}

connect();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=5001)
