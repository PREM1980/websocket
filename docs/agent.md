# Agent WebSocket Server (`server_agent.py`)

FastAPI WebSocket server that runs a **Claude agent** (via the Agent SDK) per connected client. Every tool call the agent wants to make is paused and sent to the client for approval — the agent only proceeds if the client replies `APPROVE`.

## Authentication

Requires AWS Bedrock IAM credentials loaded from a `.env` file (see `.env.example`):

```dotenv
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
```

The server exits immediately with a descriptive error if any variable is missing.

## Quickstart

```bash
python server_agent.py
```

Listens on `ws://localhost:8766`.

## Architecture

```
Client ──► RUN_AGENT {"prompt": "..."}
              │
              ▼
         run_agent() — starts ClaudeSDKClient (streaming enabled)
              │
              ├── PreToolUse hook fires before every tool call
              │       │
              │       ▼
              │   Server ──► TOOL_APPROVAL_REQUEST ──► Client
              │   Server ◄── APPROVE / REJECT ◄────── Client
              │       │
              │       ├─ APPROVE → agent proceeds, sends TOOL_APPROVED
              │       └─ REJECT  → agent aborts tool, sends TOOL_REJECTED
              │
              ├── ContentBlockDelta → AGENT_MESSAGE_DELTA (streaming) ──► Client
              ├── AssistantMessage → AGENT_MESSAGE    (fallback)     ──► Client
              └── ResultMessage   → AGENT_DONE                       ──► Client
```

The receive loop and agent task run concurrently so `APPROVE`/`REJECT` messages are processed while the agent is paused waiting. **Streaming is enabled** — agent responses stream token-by-token to the client in real-time.

## Events

### Client → Server

| Action | Description | Required field |
|---|---|---|
| `RUN_AGENT` | Start a new agent with a prompt | `prompt` |
| `APPROVE` | Allow the pending tool call to proceed | — |
| `REJECT` | Abort the pending tool call | — |

### Server → Client

| Event | When | Payload fields |
|---|---|---|
| `WELCOME` | On connect | `message` |
| `AGENT_STARTED` | Agent task launched | `message`, `timestamp` |
| `TOOL_APPROVAL_REQUEST` | Before each tool call | `tool_use_id`, `tool_name`, `tool_input`, `message` |
| `TOOL_APPROVED` | Client sent APPROVE | `tool_name` |
| `TOOL_REJECTED` | Client sent REJECT or timeout | `tool_name`, `reason` |
| `AGENT_MESSAGE_DELTA` | Agent text streaming (token-by-token) | `text`, `timestamp` |
| `AGENT_MESSAGE` | Agent text output (fallback if not streaming) | `text`, `timestamp` |
| `AGENT_DONE` | Agent finished | `result`, `stop_reason`, `timestamp` |
| `AGENT_ERROR` | Unhandled exception | `message` |
| `ERROR` | Bad request (no prompt, unknown action, invalid JSON) | `message` |

## Tool Approval Flow

1. Client sends `RUN_AGENT` with a prompt.
2. Agent runs and hits a tool call.
3. Server sends `TOOL_APPROVAL_REQUEST` with the tool name and input.
4. Client has **30 seconds** to reply `APPROVE` or `REJECT`.
5. On timeout: tool is rejected automatically with reason `"Approval timed out."`.
6. Only one agent may run per client at a time — a second `RUN_AGENT` while one is active returns an error.

## Allowed Tools

The agent is limited to: `Read`, `Glob`, `Grep`, `Bash`.

## Message Examples

**Start an agent**
```json
{ "action": "RUN_AGENT", "prompt": "List all Python files in the current directory." }
```

**TOOL_APPROVAL_REQUEST**
```json
{
  "event": "TOOL_APPROVAL_REQUEST",
  "timestamp": "2026-03-21T10:00:01Z",
  "tool_use_id": "toolu_01ABC",
  "tool_name": "Bash",
  "tool_input": { "command": "find . -name '*.py'" },
  "message": "Agent wants to run 'Bash'. Reply APPROVE or REJECT."
}
```

**Approve**
```json
{ "action": "APPROVE" }
```

**AGENT_DONE**
```json
{
  "event": "AGENT_DONE",
  "timestamp": "2026-03-21T10:00:05Z",
  "result": "Found 6 Python files: ...",
  "stop_reason": "end_turn"
}
```
