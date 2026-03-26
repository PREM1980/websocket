# Socket.IO Migration Design

**Date:** 2026-03-22
**Status:** Approved

## Overview

Replace the plain WebSocket transport in `server_agent.py` with Socket.IO, implemented as a new file `server_agent_socketio.py`. Update the Angular UI (`websocket-ui`) to use `ngx-socket-io` instead of the manual `WebSocket` wrapper.

All existing functionality is preserved:

- Run agent with a prompt
- Stream token deltas in real time
- Tool approval (approve / reject)
- Cancel session

The `websocket-ui` Angular 13 project already exists at `/Users/premlakshmanan/duplo-projects/agents/websocket-ui`.

## Architecture

```text
Angular UI (websocket-ui)               server_agent_socketio.py
        │                                          │
        │  Socket.IO  http://localhost:8766         │
        │◄────────────────────────────────────────►│
        │                                          │
        │  emit('run_agent', { prompt })  ────────►│  @sio.on('run_agent')
        │  emit('approve')  ──────────────────────►│  @sio.on('approve')
        │  emit('reject')   ──────────────────────►│  @sio.on('reject')
        │  emit('cancel_session') ────────────────►│  @sio.on('cancel_session')
        │                                          │
        │◄──────────── emit('welcome')             │
        │◄──────────── emit('agent_started')       │
        │◄──────────── emit('tool_approval_request')
        │◄──────────── emit('agent_message_delta') │
        │◄──────────── emit('agent_done')          │
        │◄──────────── emit('agent_error')         │
        │◄──────────── emit('tool_approved')       │
        │◄──────────── emit('tool_rejected')       │
        │◄──────────── emit('session_cancelled')   │
        │◄──────────── emit('error')               │
```

## Backend — `server_agent_socketio.py`

### Dependencies

Add to `requirements.txt` (retain `uvicorn` — it is still the ASGI server):

```text
python-socketio>=5.11.0
```

### FastAPI + Socket.IO Mounting

`socketio.ASGIApp` is a full ASGI app. FastAPI is passed as `other_asgi_app` so both share one port. `uvicorn.run` targets the `ASGIApp` wrapper, not the FastAPI instance:

```python
import socketio
from fastapi import FastAPI
import uvicorn

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
fastapi_app = FastAPI()
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)

if __name__ == "__main__":
    validate_aws_env()
    uvicorn.run(app, host="localhost", port=8766)
```

### Session State

`sid` (Socket.IO session ID, auto-assigned per connection) replaces the `WebSocket` object as the session key. `agent_task` is moved onto `ClientSession` — fixing the current design gap where it floated as a local variable in `handle_client`.

`ClientSession` must define `__init__` with concrete assignments (not bare annotations) so instance attributes are created correctly:

```python
class ClientSession:
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.approval_event: asyncio.Event = asyncio.Event()
        self.approval_result: bool = False
        self.agent_task: asyncio.Task | None = None

SESSIONS: dict[str, ClientSession] = {}
```

### Event Handlers

One decorated function per event replaces the `action` switch block in `handle_client()`. All handlers are `async def`. Each includes a session guard and edge case behaviour:

| Event | Handler | Behaviour |
| --- | --- | --- |
| `connect` | `async def on_connect(sid, environ)` | Create `ClientSession(sid)`, store in `SESSIONS`, emit `welcome` |
| `disconnect` | `async def on_disconnect(sid)` | `SESSIONS.pop(sid, None)` — use `pop` not `del` since session may already be removed; cancel `agent_task` if still running |
| `run_agent` | `async def on_run_agent(sid, data)` | If `SESSIONS.get(sid)` is None (post-`AGENT_DONE` re-run) create a fresh `ClientSession(sid)` and store it. Guard: if `session.agent_task` exists and not done emit `error` "Agent already running". Guard: if prompt empty emit `error` "prompt is required". Otherwise create agent task and store on `session.agent_task` |
| `approve` | `async def on_approve(sid, data)` | Guard: if `SESSIONS.get(sid)` is None emit `error` "No active session". Otherwise set `approval_result=True`, set event |
| `reject` | `async def on_reject(sid, data)` | Guard: if `SESSIONS.get(sid)` is None emit `error` "No active session". Otherwise set `approval_result=False`, set event |
| `cancel_session` | `async def on_cancel_session(sid, data)` | Guard: if no agent task or already done emit `error` "No active session to cancel". Otherwise cancel task, emit `session_cancelled`, `SESSIONS.pop(sid, None)` |

### Pushing Events to Client

`sio.emit(..., to=sid)` replaces `websocket.send_text(...)`:

```python
await sio.emit('tool_approval_request', {
    'tool_name': tool_name,
    'tool_input': tool_input,
    'tool_use_id': tool_use_id,
    'message': f"Agent wants to run '{tool_name}'.",
}, to=sid)

await sio.emit('agent_message_delta', {'text': text}, to=sid)
```

### Agent Runner

`run_agent(session, prompt)` logic is unchanged. Only the emit calls change. The `pre_tool_use_hook` uses `sio.emit(..., to=session.sid)`.

`APPROVAL_TIMEOUT = 30` seconds is retained. On timeout, the hook emits `tool_rejected` with `reason: "Approval timed out."` then raises `RuntimeError` — same as current behaviour.

`asyncio.CancelledError` must be caught explicitly and suppressed (not re-raised), since it is a `BaseException` subclass in Python 3.8+ and will not be caught by a bare `except Exception`. `logger.error` is retained on the general exception path:

```python
except asyncio.CancelledError:
    pass  # cancelled by cancel_session or disconnect — no error event needed
except Exception as exc:
    await sio.emit('agent_error', {'message': str(exc)}, to=session.sid)
    logger.error("Agent error: %s", exc)
```

### Session Cleanup

Sessions are removed in all terminal states, fixing the current gap where only disconnect triggered cleanup. All paths use `pop` to prevent `KeyError` on double-removal:

| Trigger | Cleanup |
| --- | --- |
| `disconnect` | `SESSIONS.pop(sid, None)` in `on_disconnect` |
| `AGENT_DONE` (inside `run_agent`) | `SESSIONS.pop(session.sid, None)` |
| `AGENT_ERROR` (inside `run_agent`) | `SESSIONS.pop(session.sid, None)` |
| `cancel_session` | `SESSIONS.pop(sid, None)` in `on_cancel_session` |

**Post-`AGENT_DONE` re-run:** After `AGENT_DONE` removes the session, the socket remains connected. If the user sends `run_agent` again, `on_run_agent` creates a fresh `ClientSession(sid)` and inserts it into `SESSIONS` before proceeding. The `connect` event is not re-fired.

## Frontend — `websocket-ui`

### New Dependencies

`ngx-socket-io@4.2.0` requires `rxjs ^7.0.0`. The existing project is on `rxjs ~6.6.0`. Angular 13 supports `rxjs ^6.5.3 || ^7.4.0`, so upgrading RxJS is safe:

```bash
npm install ngx-socket-io@4.2.0 rxjs@^7.4.0
```

Add `rxjs` to the `Files Changed` table — it is a changed dependency.

### `app.module.ts`

Configure Socket.IO at module level:

```typescript
import { SocketIoModule, SocketIoConfig } from 'ngx-socket-io';

const config: SocketIoConfig = {
  url: environment.websocketUrl,
  options: {}
};

@NgModule({
  imports: [SocketIoModule.forRoot(config)]
})
```

### `environment.ts` and `environment.prod.ts`

URL changes from `ws://` to `http://` (Socket.IO uses HTTP for the initial handshake). Update both files:

```typescript
// environment.ts
export const environment = {
  production: false,
  websocketUrl: 'http://localhost:8766'
};

// environment.prod.ts — update to production Socket.IO server URL
export const environment = {
  production: true,
  websocketUrl: 'http://<prod-host>:8766'
};
```

### `websocket.service.ts`

Replace manual `WebSocket` with `ngx-socket-io` `Socket` injection. The `connect(url)` method is removed — connection is configured at module level via `SocketIoModule.forRoot`. `serverEvent$` is replaced with named event observables since Socket.IO has no generic `'message'` event. `disconnect$` replaces the synthetic `DISCONNECTED` event emitted by the old `onclose` handler:

```typescript
import { Injectable } from '@angular/core';
import { Socket } from 'ngx-socket-io';

@Injectable({ providedIn: 'root' })
export class WebsocketService {
  welcome$              = this.socket.fromEvent<any>('welcome');
  agentStarted$         = this.socket.fromEvent<any>('agent_started');
  agentMessageDelta$    = this.socket.fromEvent<any>('agent_message_delta');
  agentMessage$         = this.socket.fromEvent<any>('agent_message');
  agentDone$            = this.socket.fromEvent<any>('agent_done');
  agentError$           = this.socket.fromEvent<any>('agent_error');
  toolApprovalRequest$  = this.socket.fromEvent<any>('tool_approval_request');
  toolApproved$         = this.socket.fromEvent<any>('tool_approved');
  toolRejected$         = this.socket.fromEvent<any>('tool_rejected');
  sessionCancelled$     = this.socket.fromEvent<any>('session_cancelled');
  error$                = this.socket.fromEvent<any>('error');
  disconnect$           = this.socket.fromEvent<any>('disconnect');

  constructor(private socket: Socket) {}

  runAgent(prompt: string)  { this.socket.emit('run_agent', { prompt }); }
  approve()                 { this.socket.emit('approve'); }
  reject()                  { this.socket.emit('reject'); }
  cancelSession()           { this.socket.emit('cancel_session'); }
}
```

### `chat.component.ts`

- Remove `websocketService.connect()` call from `ngOnInit` — no longer needed
- All subscriptions must use `takeUntil(this.destroy$)` for teardown
- `sendMessage({ action: 'RUN_AGENT', prompt })` → `websocketService.runAgent(prompt)`
- `sendMessage({ action: 'CANCEL_SESSION' })` → `websocketService.cancelSession()`
- Replace `serverEvent$ DISCONNECTED` handler with `websocketService.disconnect$` subscription
- Replace `serverEvent$ SESSION_CANCELLED` handler with `websocketService.sessionCancelled$` subscription
- Subscribe to `agentStarted$`, `agentDone$`, `agentError$`, `error$`

### `tool-approval.component.ts`

- Subscribe to `websocketService.toolApprovalRequest$` to show the approval UI
- Subscribe to `websocketService.toolApproved$` and `websocketService.toolRejected$` to clear `pendingApproval` after the server responds — there is no longer a generic `serverEvent$` to catch these
- Call `websocketService.approve()` or `websocketService.reject()` on user action
- All subscriptions must use `takeUntil(this.destroy$)`

### `message-log.component.ts`

Subscribe to the following named observables to replace the single `serverEvent$` stream:
`agentStarted$`, `agentMessageDelta$`, `agentMessage$`, `agentDone$`, `agentError$`, `toolApprovalRequest$`, `toolApproved$`, `toolRejected$`, `sessionCancelled$`, `error$`, `disconnect$`.

Delta accumulation logic is unchanged — consecutive `agent_message_delta` entries are still merged into one log entry. All subscriptions must use `takeUntil(this.destroy$)`.

## Event Reference

### Client → Server

| Event | Payload | Description |
| --- | --- | --- |
| `run_agent` | `{ prompt: string }` | Start agent |
| `approve` | `{}` | Approve pending tool |
| `reject` | `{}` | Reject pending tool |
| `cancel_session` | `{}` | Cancel running agent |

### Server → Client

| Event | Payload | Description |
| --- | --- | --- |
| `welcome` | `{ message, timestamp }` | Connection established |
| `agent_started` | `{ timestamp, message }` | Agent task launched |
| `tool_approval_request` | `{ tool_name, tool_input, tool_use_id, message }` | Agent wants to run a tool |
| `tool_approved` | `{ tool_name }` | Tool was approved |
| `tool_rejected` | `{ tool_name, reason }` | Tool was rejected (user or timeout) |
| `agent_message_delta` | `{ text, timestamp }` | Streaming token |
| `agent_message` | `{ text, timestamp }` | Complete message (non-streaming fallback) |
| `agent_done` | `{ result, stop_reason, timestamp }` | Agent finished |
| `agent_error` | `{ message }` | Agent error |
| `session_cancelled` | `{}` | Session was cancelled |
| `error` | `{ message }` | Server-side validation error |

## Testing

Add to dev dependencies:

```text
pytest-asyncio>=0.23.0
```

Add `asyncio_mode = auto` to `pytest.ini` so async test functions run without needing `@pytest.mark.asyncio` on each one:

```ini
[pytest]
pythonpath = .
asyncio_mode = auto
```

Create `tests/test_socketio_server.py`. Use `uvicorn.Server` to spin up the ASGI app in-process on a random port — `python-socketio` has no `AsyncTestClient` in its public API:

```python
import asyncio
import pytest_asyncio
import uvicorn
import socketio as sio_lib
from server_agent_socketio import app, sio

@pytest_asyncio.fixture
async def client():
    config = uvicorn.Config(app, host="127.0.0.1", port=0)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.1)          # wait for bind
    port = server.servers[0].sockets[0].getsockname()[1]
    c = sio_lib.AsyncClient()
    await c.connect(f"http://127.0.0.1:{port}")
    yield c
    await c.disconnect()
    server.should_exit = True
    await task
```

Test cases:

- Connection emits `welcome` event with `message` and `timestamp`
- `run_agent` with empty prompt emits `error` event
- `run_agent` while agent already running emits `error` event
- `approve` with no active session emits `error` event
- `reject` with no active session emits `error` event
- `cancel_session` with no active agent emits `error` event
- `cancel_session` with active agent emits `session_cancelled`
- Disconnect while agent running does not raise exceptions
- Post-`AGENT_DONE` `run_agent` creates a new session successfully

## Files Changed

| File | Change |
| --- | --- |
| `websocket/server_agent_socketio.py` | New file — Socket.IO server |
| `websocket/requirements.txt` | Add `python-socketio>=5.11.0` (retain `uvicorn`) |
| `websocket/tests/test_socketio_server.py` | New test file |
| `websocket/pytest.ini` | Add `asyncio_mode = auto` |
| `websocket-ui/package.json` | Add `ngx-socket-io@4.2.0`, upgrade `rxjs` to `^7.4.0` |
| `websocket-ui/src/app/app.module.ts` | Add `SocketIoModule.forRoot(config)` |
| `websocket-ui/src/environments/environment.ts` | Change URL to `http://localhost:8766` |
| `websocket-ui/src/environments/environment.prod.ts` | Change URL to production Socket.IO URL |
| `websocket-ui/src/app/services/websocket.service.ts` | Replace with Socket.IO service, named event observables, `disconnect$` |
| `websocket-ui/src/app/components/chat/chat.component.ts` | Update to named observables, remove `connect()`, handle `disconnect$` |
| `websocket-ui/src/app/components/message-log/message-log.component.ts` | Update event subscriptions to named observables |
| `websocket-ui/src/app/components/tool-approval/tool-approval.component.ts` | Subscribe to `toolApprovalRequest$`, `toolApproved$`, `toolRejected$` |
