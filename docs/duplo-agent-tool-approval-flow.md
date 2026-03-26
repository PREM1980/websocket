# Agent Tool Approval Flow

Complete reference for how tool approval works across the three systems:
- **websocket** — Python WebSocket server (`server_agent.py`)
- **claude-code-generic-ai-agent** — Python agent with HTTP callback (`agent.py`)
- **duplo-ai-helpdesk** — .NET platform backend + SignalR
- **duplo-ui** — Angular frontend

---

## 1. WebSocket Server (`server_agent.py`)

### Architecture

```
Browser / Client
      │
      │  WebSocket ws://localhost:8766/ws
      │  (single persistent connection for everything)
      ▼
server_agent.py
      │
      └── Claude Agent SDK (runs agent in background asyncio task)
```

### How the Agent is Triggered

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

### Tool Approval Flow

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

### Cancel Session Flow

```
Client sends: { "action": "CANCEL_SESSION" }
          │
          ▼
handle_client() receive loop:
  agent_task.cancel()         ← cancels the background asyncio task
          │
          ▼
Server sends: { "event": "SESSION_CANCELLED" }
          │
          ▼
UI resets: isRunning = false
```

### Session State

```python
class ClientSession:
    websocket        # the WebSocket connection — acts as session identity
    approval_event   # asyncio.Event — blocks hook until client responds
    approval_result  # True = approved, False = rejected

# Stored in module-level dict:
SESSIONS: dict[WebSocket, ClientSession] = {}
# Cleaned up only on WebSocket disconnect (not on AGENT_DONE or CANCEL)
```

### Event Reference

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

### Key Design: Single Connection for Everything

```
Same WebSocket connection handles:
  - Sending prompt          (RUN_AGENT)
  - Receiving stream deltas (AGENT_MESSAGE_DELTA)
  - Approving tools         (APPROVE / REJECT)
  - Cancelling session      (CANCEL_SESSION)

This works because asyncio.create_task() keeps the receive loop
unblocked while the agent runs in the background.
```

---

## 2. Generic AI Agent with HTTP Callback (`agent.py`)

### Architecture

```
duplo-ui (Angular)
      │
      │  POST /api/sendMessage  (NDJSON stream)
      ▼
cca_server.py  →  _stream_agent()  →  agent.py
                                           │
                                    kubectl/helm/aws
                                    command intercepted
                                           │
                                           │  POST /agentCallback  (long-poll)
                                           ▼
                                  duplo-ai-helpdesk (.NET)
                                           │
                                           │  SignalR push
                                           ▼
                                      duplo-ui (Angular)
                                      user clicks Approve/Reject
                                           │
                                           │  SignalR response
                                           ▼
                                  duplo-ai-helpdesk (.NET)
                                  resolves the blocked POST
                                           │
                                           ▼
                                      agent.py unblocks
                                  PermissionResultAllow/Deny
```

### How the Agent is Triggered

```
duplo-ui sends:
  POST /v1/aiservicedesk/tickets/{tenantId}/{ticketName}/sendmessageStreaming
  { content: "scale my deployment", data: { ... } }
          │
          ▼
TicketController.SendTicketMessageStreamingAsync()
  Sets SSE response headers:
    Content-Type: text/event-stream
    Cache-Control: no-cache
    X-Accel-Buffering: no
          │
          ▼
Calls cca_server.py  →  POST /api/sendMessage
          │
          ▼
_stream_agent(prompt, session_id, thread_id, engineer_id, platform_context)
          │
          ▼
Claude Agent SDK streams NDJSON events back
          │
          ▼
duplo-ui reads stream with fetch() + response.body.getReader()
  renders token deltas in real time (chat-message.service.ts)
```

### Tool Approval Flow

```
Agent intercepts kubectl/helm/aws/gcloud/az Bash command
  (can_use_tool_handler in agent.py:1115)
          │
          ▼
async with _approval_sem:   ← semaphore ensures only 1 approval in-flight
  POST {callback_url}/agentCallback
  {
    "ticket_name": thread_id,
    "message": {
      "role": "assistant",
      "content": "Approve this command?",
      "data": { "cmds": [{ "command": "kubectl get pods", "execute": false }] }
    }
  }
  (blocks here, timeout = 45s)
          │
          ▼
TicketController.AgentCallbackAsync() receives POST
          │
          ▼
EvaluateCommandsForAutoApprovalAsync()
  ├─ matches RejectedCmdRegEx? → auto-deny  (returns immediately, no human needed)
  ├─ all match ApprovedCmdRegEx? → auto-approve (returns immediately, no human needed)
  └─ neither → needs human decision
          │
          ▼ (human decision path)
SignalR push to browser:
  _agentCallbackHub.Clients.Group(ticketName)
    .SendAsync("AgentCallbackEvent", message)
          │
          ▼
duplo-ui receives "AgentCallbackEvent"
  shows approval popup with command
          │
          ▼
_agentCallbackService.WaitForClientResponseAsync(ticketName)
  TaskCompletionSource blocks the HTTP request (up to 30 min)
          │
          │  User clicks Approve or Reject in UI
          │
          ▼
duplo-ui calls SignalR:
  hubConnection.invoke("RespondToAgentCallback", ticketName, {
    cmds: [{ command: "...", execute: true/false, rejection_reason?: "..." }]
  })
          │
          ▼
AgentCallbackHub.RespondToAgentCallback()
  _callbackService.ResolveCallback(ticketName, responseData)
  tcs.TrySetResult(responseData)   ← unblocks the HTTP request
          │
          ▼
agentCallback POST returns:
  { cmds: [{ execute: true/false, rejection_reason?: "..." }] }
          │
          ├── execute: true  → PermissionResultAllow → command runs
          └── execute: false → PermissionResultDeny  → command blocked, reason shown to agent
```

### The Semaphore — Why It's Needed

```
Without semaphore (problem):
  Agent fires 3 tool hooks concurrently in one turn
    ├── POST /agentCallback (kubectl get pods)    → waiting...
    ├── POST /agentCallback (kubectl get nodes)   → waiting...
    └── POST /agentCallback (helm list)           → waiting...
  One timeout cascades → "Stream closed" kills all three

With semaphore (solution):
  _approval_sem = asyncio.Semaphore(1)

  async with _approval_sem:        ← only 1 can enter at a time
    POST /agentCallback            → user responds → releases
  async with _approval_sem:        ← next one enters
    POST /agentCallback            → user responds → releases
  async with _approval_sem:
    POST /agentCallback            → ...
```

### Where `callback_url` Comes From

```python
# Built inside _stream_agent() from the incoming request's platform_context:
callback_url = (
    f"{agent_callback_base_url}"
    f"/v1/aiservicedesk/tickets/{engineer_id}/{thread_id}/agentCallback"
)

# Requires:
#   AGENT_CALLBACK_BASE_URL env var (or duplo_base_url from platform_context)
#   duplo_token from platform_context  (used as Bearer token)
#   engineer_id from HTTP request
#   thread_id from HTTP request
```

### Session Identity

Unlike the WebSocket server where the connection itself is the session,
here the session is identified by `thread_id` — it survives page refreshes
and connection drops.

```python
# WebSocket server — session tied to connection:
SESSIONS: dict[WebSocket, ClientSession] = {}

# This agent — session identified by thread_id:
# thread_id flows through every call and into the callback URL
```

---

## 3. Comparison: WebSocket vs HTTP + SSE + SignalR

| Concern | WebSocket (`server_agent.py`) | HTTP + SSE + SignalR (`agent.py` + Duplo) |
|---------|-------------------------------|-------------------------------------------|
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

---

## 4. Key Pattern: Block Until Human Responds

Both systems use the same fundamental pattern — hold a task open until the
human responds, then resume. The implementation differs by language:

**Python (`server_agent.py`):**
```python
# Block
session.approval_event.clear()
await asyncio.wait_for(session.approval_event.wait(), timeout=30)

# Resume (from receive loop)
session.approval_event.set()
```

**Python (`agent.py`):**
```python
# Block (HTTP request hangs)
data = await _call_agentcallback(callback_url, ...)   # timeout=45s

# Resume (when Duplo responds to the POST)
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

## 5. File Reference

### websocket repo
| File | Purpose |
|------|---------|
| `server_agent.py` | WebSocket server, agent runner, tool approval gate |
| `client_agent.py` | CLI client for testing |
| `websocket-ui/src/app/services/websocket.service.ts` | Angular WebSocket service |
| `websocket-ui/src/app/components/chat/chat.component.ts` | Chat UI, approve/reject/cancel logic |
| `websocket-ui/src/app/components/message-log/message-log.component.ts` | Event log, delta accumulation |

### claude-code-generic-ai-agent repo
| File | Purpose |
|------|---------|
| `agent.py:1033` | `_call_agentcallback()` — HTTP long-poll to Duplo |
| `agent.py:1113` | `_approval_sem` — semaphore for concurrent approvals |
| `agent.py:1115` | `can_use_tool_handler()` — intercepts kubectl/helm/aws |
| `cca_server.py:75` | `/api/sendMessage` — NDJSON streaming endpoint |

### duplo-ai-helpdesk repo (.NET)
| File | Purpose |
|------|---------|
| `TicketController.cs:255` | SSE streaming endpoint |
| `TicketController.cs:495` | `agentCallback` endpoint — receives agent POST |
| `AgentCallbackHub.cs` | SignalR hub — `SubscribeToTicket`, `RespondToAgentCallback` |
| `AgentCallbackService.cs` | `TaskCompletionSource` wait/resolve pattern |
| `TicketService.RequestApproval.cs:254` | Auto-approval regex evaluation |

### duplo-ui repo (Angular)
| File | Purpose |
|------|---------|
| `messages.datasource.ts:133` | `fetch()` + SSE stream reader |
| `chat-message.service.ts:175` | Token delta rendering |
| `chat-message.service.ts:316` | Sends approve/reject via SignalR |
| `chat-cmd-terminal.component.ts:110` | Approval popup UI |
