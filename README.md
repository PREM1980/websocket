# Flight Notification WebSocket Demo

Real-time flight status notification system modelled on the Etihad Airways rescheduled itinerary (IAD → AUH → MAA).

## Architecture

```
┌────────────────────────────────────────────────────────┐
│                    server.py                           │
│                                                        │
│  Mock flight data (EY-6, EY-212)                       │
│  ┌──────────────────────────────────────────────────┐  │
│  │  flight_update_producer()                        │  │
│  │  Pushes STATUS_SEQUENCE every 5 s → broadcast() │  │
│  └──────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────┐  │
│  │  handle_client()                                 │  │
│  │  • Sends WELCOME snapshot on connect             │  │
│  │  • Accepts SUBSCRIBE {"action","flight_number"}  │  │
│  └──────────────────────────────────────────────────┘  │
│                    ws://localhost:8765                  │
└──────────────────────┬─────────────────────────────────┘
                       │  WebSocket (JSON frames)
          ┌────────────┴──────────────┐
          │                           │
   ┌──────▼──────┐             ┌──────▼──────┐
   │  client.py  │             │  client.py  │
   │  (terminal) │   …         │  (terminal) │
   └─────────────┘             └─────────────┘
```

## Events

| Event | Direction | Description |
|---|---|---|
| `WELCOME` | server → client | Snapshot of all tracked flights on connect |
| `FLIGHT_UPDATE` | server → client | Status change broadcast to all clients |
| `HEARTBEAT` | server → client | Periodic keep-alive after all updates are sent |
| `SUBSCRIBE` | client → server | Request status for a specific flight |
| `ERROR` | server → client | Invalid action or unknown flight number |

## Flight Status Sequence (mock)

```
EY-6:   ON_TIME → BOARDING → DEPARTED → IN_FLIGHT → DELAYED → RESCHEDULED → LANDED → ARRIVED
EY-212: BOARDING → DEPARTED → LANDED
```

## Authentication

The agent server authenticates to Claude via **Amazon Bedrock**. Before starting the server, create a `.env` file in the project root (see `.env.example`):

```dotenv
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
```

If any variable is missing the server exits immediately with a descriptive error. The `.env` file is git-ignored — never commit real credentials.

## Quickstart

```bash
cd /Users/premlakshmanan/duplo-projects/agents/websocket

# Install dependency
pip install -r requirements.txt

# Terminal 1 — start server
python server.py

# Terminal 2 — start client (receive all broadcasts)
python client.py

# Terminal 3 — subscribe to a specific flight
python client.py --flight EY-6
python client.py --flight EY-212
```

## Client flags

| Flag | Description |
|---|---|
| `--flight` / `-f` | Subscribe to a specific flight number on connect |

---

## Agent Server (`server_agent.py` + `client_agent.py`)

Runs a Claude agent per client session with interactive tool-call approval.

### Usage

```bash
# Terminal 1 — start the agent server (requires .env with AWS credentials)
python server_agent.py

# Terminal 2 — connect the agent client
python client_agent.py                                    # prompted for input
python client_agent.py -p "List the Python files here"   # pass prompt directly
python client_agent.py -p "Summarise server.py" --auto-approve
```

### `client_agent.py` flags

| Flag | Description |
| --- | --- |
| `--prompt` / `-p` | Prompt to send to the agent (asked interactively if omitted) |
| `--auto-approve` | Approve all tool calls automatically without prompting |
