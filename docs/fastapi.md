# WebSocket Flight Notification Server — FastAPI (`server_fastapi.py`)

FastAPI-based WebSocket server with the same flight notification logic as `server.py`, plus a server-initiated **interest confirmation** handshake: every 5th update the server asks each client if it is still listening, and disconnects any client that does not reply within 10 seconds.

## Quickstart

```bash
python server_fastapi.py
```

Listens on `ws://localhost:8765`.

## Architecture

```
lifespan() starts flight_update_producer() as background task
                │
                │  every 5 s: broadcast update
                │
         handle_client()  ◄──────────────────────────────┐
                │                                         │
                │  starts confirm_interest_checker()      │
                │      │                                  │
                │      │  every 5 updates: send           │
                │      │  CONFIRM_INTEREST, wait 10 s     │
                │      │  for CONTINUE — else disconnect  │
                │      └──────────────────────────────────┘
                │
                │  receive loop: SUBSCRIBE / CONTINUE / STOP
```

## Events

### Server → Client

| Event | When | Payload fields |
|---|---|---|
| `WELCOME` | On connect | `flights[]` — snapshot of all tracked flights |
| `FLIGHT_UPDATE` | Every 5 s (status sequence) | `flight{}`, `update{status, message}` |
| `CONFIRM_INTEREST` | Every 5th update sent to client | `message` — reply `CONTINUE` or `STOP` |
| `HEARTBEAT` | Every 15 s after sequence ends | `message` |
| `INFO` | After `STOP` action | `message` |
| `ERROR` | Bad action or unknown flight | `message` |

### Client → Server

| Action | Description | Required field |
|---|---|---|
| `SUBSCRIBE` | Request current status for a flight | `flight_number` |
| `CONTINUE` | Confirm still listening (response to `CONFIRM_INTEREST`) | — |
| `STOP` | Unsubscribe and close connection | — |

## Confirmation Handshake

After every `CONFIRM_EVERY` (default: 5) updates sent to a client:

1. Server sends `CONFIRM_INTEREST`
2. Client must reply `{"action": "CONTINUE"}` within `CONFIRM_TIMEOUT` (default: 10 s)
3. If no reply → server closes the connection

This prevents idle clients from accumulating silently.

## Differences from `server.py`

| Feature | `server.py` | `server_fastapi.py` |
|---|---|---|
| Library | `websockets` | FastAPI + uvicorn |
| Port | 8765 | 8765 |
| Confirmation handshake | No | Yes — every 5 updates |
| `STOP` action | No | Yes |
| `CONTINUE` action | No | Yes |
| Producer startup | `asyncio.run(main())` | FastAPI lifespan |

## Message Examples

**CONFIRM_INTEREST**
```json
{
  "event": "CONFIRM_INTEREST",
  "message": "[Server] Are you still listening? You've received 5 updates. Reply CONTINUE or STOP."
}
```

**CONTINUE request**
```json
{ "action": "CONTINUE" }
```

**STOP request**
```json
{ "action": "STOP" }
```
