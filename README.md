# Claude Agent Workflow over WebSockets

A Socket.IO-based system for running Claude agents with interactive tool-call approval. The client sends a prompt, the agent streams responses back, and any tool calls are held for human approval via an HTTP callback before executing.

## Architecture

```
websocket-ui (Angular, port 4200)
      │
      │  Socket.IO  http://localhost:8766
      ▼
server_agent_socketio.py  (port 8766)
      │
      ├── Claude Agent SDK (runs agent in background asyncio task)
      │
      └── POST /dummy-callback  ← tool approval requests block here
                │
                │  approve / reject  (Socket.IO events from UI)
                ▼
            agent resumes
```

Tool approval uses an HTTP callback pattern: the server POSTs to a callback URL and blocks until it responds. For local testing, a built-in `/dummy-callback` endpoint is included — the UI's approve/reject buttons resolve it.

## Authentication

The server authenticates to Claude via **Amazon Bedrock**. Create a `.env` file before starting (see `.env.example`):

```dotenv
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
```

The `.env` file is git-ignored — never commit real credentials.

## Quickstart

### Terminal 1 — Python server

```bash
# Install dependencies
pip install -r requirements.txt

# Start the Socket.IO agent server (port 8766)
python server_agent_socketio.py
```

### Terminal 2 — Angular UI

```bash
cd ../websocket-ui

# Install dependencies (first time only)
npm install

# Start the dev server (port 4200)
npm start
```

Open `http://localhost:4200` in your browser.

### Using the UI

1. Enter a prompt in the chat input.
2. Set **Callback URL** to `http://localhost:8766/dummy-callback` (pre-filled by default).
3. **Callback Token** is pre-filled as `dummy` (the dummy endpoint accepts any value).
4. Click **Send** — the agent starts and streams output in real time.
5. When a tool call is intercepted, an approval card appears — click **Approve** or **Reject**.

---

## How a Session Works

```
Client sends:
  { "action": "RUN_AGENT", "prompt": "..." }
          │
          ▼
handle_client() receive loop picks it up
          │
          ▼
asyncio.create_task(run_agent(session, prompt))
  └── agent runs in background so receive loop stays unblocked
      (needed to receive APPROVE/REJECT/CANCEL while agent runs)
```

## Tool Approval Flow

```
Agent decides to run a tool (e.g. Bash)
          │
          ▼
pre_tool_use_hook fires (PreToolUse hook registered in ClaudeAgentOptions)
          │
          ▼
Server sends over WebSocket:
  {
    "event": "TOOL_APPROVAL_REQUEST",
    "tool_name": "Bash",
    "tool_input": { "command": "ls -la" },
    "tool_use_id": "...",
    "message": "Agent wants to run 'Bash'. Reply APPROVE or REJECT."
  }
          │
          ▼
server waits (blocks hook):
  await asyncio.wait_for(session.approval_event.wait(), timeout=30s)
          │
          │  Client sends: { "action": "APPROVE" } or { "action": "REJECT" }
          │
          ▼
receive loop sets the event:
  session.approval_result = True / False
  session.approval_event.set()
          │
          ├── APPROVE → return {}              → agent runs the tool
          └── REJECT  → raise RuntimeError     → agent is told tool was rejected
```

## Cancel Session Flow

```
Client sends: { "action": "CANCEL_SESSION" }
          │
          ▼
handle_client() receive loop:
  agent_task.cancel()         ← cancels the background asyncio task
          │
          ▼
Server sends: { "event": "SESSION_CANCELLED" }
```

## Session State

```python
class ClientSession:
    websocket        # the WebSocket connection — acts as session identity
    approval_event   # asyncio.Event — blocks hook until client responds
    approval_result  # True = approved, False = rejected

# Stored in module-level dict:
SESSIONS: dict[WebSocket, ClientSession] = {}
# Cleaned up only on WebSocket disconnect (not on AGENT_DONE or CANCEL)
```

## Event Reference

| Direction | Event | Meaning |
|-----------|-------|---------|
| Client → Server | `RUN_AGENT` | Start agent with prompt |
| Client → Server | `APPROVE` | Approve pending tool call |
| Client → Server | `REJECT` | Reject pending tool call |
| Client → Server | `CANCEL_SESSION` | Cancel running agent |
| Server → Client | `WELCOME` | Connection established |
| Server → Client | `AGENT_STARTED` | Agent task launched |
| Server → Client | `TOOL_APPROVAL_REQUEST` | Agent wants to run a tool |
| Server → Client | `TOOL_APPROVED` | Tool was approved |
| Server → Client | `TOOL_REJECTED` | Tool was rejected |
| Server → Client | `AGENT_MESSAGE_DELTA` | Streaming token from agent |
| Server → Client | `AGENT_MESSAGE` | Complete message from agent |
| Server → Client | `AGENT_DONE` | Agent finished |
| Server → Client | `AGENT_ERROR` | Agent encountered an error |
| Server → Client | `SESSION_CANCELLED` | Session was cancelled |

---

## Comparison: WebSocket vs HTTP + SSE + SignalR

For context, this repo implements the WebSocket approach. The table below compares it to an alternative HTTP + SSE + SignalR architecture (used in separate `claude-code-generic-ai-agent` + `ai-helpdesk` + `ui` repos).

| Concern | WebSocket (`server_agent.py`) | HTTP + SSE + SignalR (`agent.py` + ai-helpdesk) |
|---------|-------------------------------|--------------------------------------------------|
| Agent output stream | WebSocket messages | SSE (`text/event-stream`) |
| Tool approval request | WebSocket message to client | SignalR push to browser |
| User approve/reject | WebSocket message to server | SignalR `RespondToAgentCallback` |
| Cancel session | WebSocket message `CANCEL_SESSION` | HTTP POST `/cancel` (not implemented yet) |
| Block until approval | `asyncio.Event.wait()` | `TaskCompletionSource` (.NET) |
| Concurrent approvals | Not possible — SDK sequential | Possible — semaphore serialises |
| Session identity | WebSocket connection object | `thread_id` string |
| Survives page refresh | No — connection drops = session lost | Yes — `thread_id` persists |
| Auto-approval rules | None | Regex patterns on ticket config |
| Testability | Needs WS client | Plain `curl` for REST endpoints |

### Key Pattern: Block Until Human Responds

Both systems hold a task open until the human responds, then resume. The implementation differs by language:

**Python (`server_agent.py`):**

```python
# Block
session.approval_event.clear()
await asyncio.wait_for(session.approval_event.wait(), timeout=30)

# Resume (from receive loop)
session.approval_event.set()
```

**Python (`agent.py` — HTTP variant):**

```python
# Block (HTTP request hangs)
data = await _call_agentcallback(callback_url, ...)   # timeout=45s

# Resume (when ai-helpdesk responds to the POST)
# happens automatically when TaskCompletionSource resolves on .NET side
```

**.NET (`AgentCallbackService.cs`):**

```csharp
// Block
var tcs = new TaskCompletionSource<JsonElement>();
_pending[ticketName] = tcs;
return tcs.Task;        // HTTP request hangs here

// Resume (from SignalR hub when browser responds)
tcs.TrySetResult(responseData);
```

---

## File Reference

### This repo

| File | Purpose |
|------|---------|
| `server_agent.py` | WebSocket server, agent runner, tool approval gate |
| `client_agent.py` | CLI client for testing |
| `websocket-ui/src/app/services/websocket.service.ts` | Angular WebSocket service |
| `websocket-ui/src/app/components/chat/chat.component.ts` | Chat UI, approve/reject/cancel logic |
| `websocket-ui/src/app/components/message-log/message-log.component.ts` | Event log, delta accumulation |

### claude-code-generic-ai-agent repo (HTTP variant)

| File | Purpose |
|------|---------|
| `agent.py:1033` | `_call_agentcallback()` — HTTP long-poll to ai-helpdesk |
| `agent.py:1113` | `_approval_sem` — semaphore for concurrent approvals |
| `agent.py:1115` | `can_use_tool_handler()` — intercepts kubectl/helm/aws |
| `cca_server.py:75` | `/api/sendMessage` — NDJSON streaming endpoint |

### ai-helpdesk repo (.NET)

| File | Purpose |
|------|---------|
| `TicketController.cs:255` | SSE streaming endpoint |
| `TicketController.cs:495` | `agentCallback` endpoint — receives agent POST |
| `AgentCallbackHub.cs` | SignalR hub — `SubscribeToTicket`, `RespondToAgentCallback` |
| `AgentCallbackService.cs` | `TaskCompletionSource` wait/resolve pattern |
| `TicketService.RequestApproval.cs:254` | Auto-approval regex evaluation |

### ui repo (Angular)

| File | Purpose |
|------|---------|
| `messages.datasource.ts:133` | `fetch()` + SSE stream reader |
| `chat-message.service.ts:175` | Token delta rendering |
| `chat-message.service.ts:316` | Sends approve/reject via SignalR |
| `chat-cmd-terminal.component.ts:110` | Approval popup UI |
