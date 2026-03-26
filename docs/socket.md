# WebSocket Flight Notification Server (`server.py`)

Pure-Python WebSocket server using the `websockets` library. Pushes mock real-time flight status updates to all connected clients.

## Quickstart

```bash
python server.py
```

Listens on `ws://localhost:8765`.

## Architecture

```
flight_update_producer()          handle_client()
  │                                    │
  │  every 5 s: push STATUS_SEQUENCE   │  on connect: send WELCOME snapshot
  │  then: heartbeat every 15 s        │  on message: handle SUBSCRIBE
  │                                    │
  └────────── broadcast() ─────────────┘
                  │
         all connected CLIENTS
```

The producer and each client handler run as concurrent async tasks sharing the `CLIENTS` set.

## Events

### Server → Client

| Event | When | Payload fields |
|---|---|---|
| `WELCOME` | On connect | `flights[]` — snapshot of all tracked flights |
| `FLIGHT_UPDATE` | Every 5 s (status sequence) | `flight{}`, `update{status, message}` |
| `HEARTBEAT` | Every 15 s after sequence ends | `message` |
| `ERROR` | Bad action or unknown flight | `message` |

### Client → Server

| Action | Description | Required field |
|---|---|---|
| `SUBSCRIBE` | Request current status for a flight | `flight_number` |

## Tracked Flights

| Flight | Route | Status sequence |
|---|---|---|
| EY-6 | IAD → AUH | ON_TIME → BOARDING → DEPARTED → IN_FLIGHT → DELAYED → RESCHEDULED → LANDED → ARRIVED |
| EY-212 | AUH → MAA | ON_TIME → BOARDING → DEPARTED → LANDED |

## Message Examples

**WELCOME**
```json
{
  "event": "WELCOME",
  "timestamp": "2026-03-21T10:00:00Z",
  "message": "Connected to Flight Notification Service.",
  "flights": [...]
}
```

**FLIGHT_UPDATE**
```json
{
  "event": "FLIGHT_UPDATE",
  "timestamp": "2026-03-21T10:00:05Z",
  "flight": {
    "flight_number": "EY-6",
    "airline": "Etihad Airways",
    "origin": "Washington-Dulles Apt (IAD)",
    "destination": "Abu Dhabi Intl (AUH)",
    "departure_date": "10 Apr",
    "scheduled_departure": "14:05",
    "terminal": "Abu Dhabi Intl - Terminal A"
  },
  "update": {
    "status": "BOARDING",
    "message": "Boarding has started at Gate B12."
  }
}
```

**SUBSCRIBE request**
```json
{ "action": "SUBSCRIBE", "flight_number": "EY-6" }
```
