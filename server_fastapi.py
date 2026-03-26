"""
WebSocket Flight Notification Server — FastAPI version

Same logic as server.py but using FastAPI's built-in WebSocket support.
Every 5th update, the server asks each client if it still wants data.
The client must reply CONTINUE within 10 seconds, or it gets disconnected.

Usage:
    pip install -r requirements.txt
    python server_fastapi.py
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SERVER] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock flight data
# ---------------------------------------------------------------------------

FLIGHTS = {
    "EY-6": {
        "flight_number": "EY-6",
        "airline": "Etihad Airways",
        "origin": "Washington-Dulles Apt (IAD)",
        "destination": "Abu Dhabi Intl (AUH)",
        "terminal": "Abu Dhabi Intl - Terminal A",
        "scheduled_departure": "14:05",
        "scheduled_arrival": "12:10+1",
        "departure_date": "10 Apr",
        "arrival_date": "11 Apr",
        "status": "ON_TIME",
    },
    "EY-212": {
        "flight_number": "EY-212",
        "airline": "Etihad Airways",
        "origin": "Abu Dhabi Intl (AUH)",
        "destination": "Chennai (MAA)",
        "terminal": "Terminal 1",
        "scheduled_departure": "16:30",
        "scheduled_arrival": "22:45",
        "departure_date": "11 Apr",
        "arrival_date": "11 Apr",
        "status": "ON_TIME",
    },
}

STATUS_SEQUENCE = [
    ("EY-6",   {"status": "BOARDING",    "message": "Boarding has started at Gate B12."}),
    ("EY-6",   {"status": "DEPARTED",    "message": "Flight EY-6 has departed Washington-Dulles."}),
    ("EY-6",   {"status": "IN_FLIGHT",   "message": "En route to Abu Dhabi. ETA on schedule."}),
    ("EY-6",   {"status": "DELAYED",     "message": "Minor delay of 20 min due to air traffic control."}),
    ("EY-6",   {"status": "RESCHEDULED", "message": "Itinerary impacted. New departure rescheduled to 14:05, 10 Apr."}),
    ("EY-6",   {"status": "LANDED",      "message": "EY-6 has landed at Abu Dhabi Intl."}),
    ("EY-6",   {"status": "ARRIVED",     "message": "Aircraft at gate. Terminal A. Welcome to Abu Dhabi."}),
    ("EY-212", {"status": "BOARDING",    "message": "EY-212 boarding started at Gate D04."}),
    ("EY-212", {"status": "DEPARTED",    "message": "EY-212 has departed Abu Dhabi."}),
    ("EY-212", {"status": "LANDED",      "message": "EY-212 has landed in Chennai."}),
]

CONFIRM_TIMEOUT = 10   # seconds to wait for client to reply CONTINUE
CONFIRM_EVERY   = 5    # ask after every Nth broadcast

# Per-client state:
#   update_count  — how many flight updates sent to this client
#   confirm_event — asyncio.Event set by the receive loop when client replies CONTINUE
#   active        — False means stop sending (client replied STOP or timed out)
CLIENT_STATES: dict[WebSocket, dict] = {}


def build_event(flight_id: str, update: dict) -> str:
    flight = FLIGHTS.get(flight_id, {})
    return json.dumps({
        "event": "FLIGHT_UPDATE",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "flight": {
            "flight_number": flight.get("flight_number"),
            "airline": flight.get("airline"),
            "origin": flight.get("origin"),
            "destination": flight.get("destination"),
            "departure_date": flight.get("departure_date"),
            "scheduled_departure": flight.get("scheduled_departure"),
            "terminal": flight.get("terminal"),
        },
        "update": update,
    })


async def send_to_client(websocket: WebSocket, message: str) -> None:
    """Send one message to a client and increment its update counter."""
    state = CLIENT_STATES.get(websocket)
    if not state or not state["active"]:
        return
    await websocket.send_text(message)
    state["update_count"] += 1


async def confirm_interest_checker(websocket: WebSocket) -> None:
    """
    Independent server-initiated task: every CONFIRM_EVERY updates, the server
    asks the client 'are you still listening?' and waits for a CONTINUE reply.
    Runs alongside handle_client for the lifetime of the connection.
    """
    state = CLIENT_STATES[websocket]
    while state["active"]:
        # Wait until this client has received another CONFIRM_EVERY updates
        target = state["update_count"] + CONFIRM_EVERY
        while state["update_count"] < target and state["active"]:
            await asyncio.sleep(1)

        if not state["active"]:
            break

        confirm_req = json.dumps({
            "event": "CONFIRM_INTEREST",
            "message": f"[Server] Are you still listening? You've received {state['update_count']} updates. Reply CONTINUE or STOP.",
        })
        await websocket.send_text(confirm_req)
        logger.info("Server asked CONFIRM_INTEREST → %s (update #%d)", websocket.client, state["update_count"])

        try:
            await asyncio.wait_for(state["confirm_event"].wait(), timeout=CONFIRM_TIMEOUT)
            state["confirm_event"].clear()
            logger.info("Client %s confirmed CONTINUE", websocket.client)
        except asyncio.TimeoutError:
            logger.warning("Client %s did not respond in %ds — disconnecting", websocket.client, CONFIRM_TIMEOUT)
            state["active"] = False
            await websocket.close()
            break


async def broadcast(message: str) -> None:
    if not CLIENT_STATES:
        logger.info("No clients connected, skipping broadcast.")
        return
    await asyncio.gather(
        *(send_to_client(ws, message) for ws in CLIENT_STATES),
        return_exceptions=True,
    )


async def flight_update_producer() -> None:
    logger.info("Producer started — will push %d updates.", len(STATUS_SEQUENCE))
    for flight_id, update in STATUS_SEQUENCE:
        await asyncio.sleep(5)
        FLIGHTS[flight_id]["status"] = update["status"]
        payload = build_event(flight_id, update)
        logger.info("Broadcasting %-8s → %s", flight_id, update["status"])
        await broadcast(payload)

    while True:
        await asyncio.sleep(15)
        heartbeat = json.dumps({
            "event": "HEARTBEAT",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "message": "All systems operational.",
        })
        await broadcast(heartbeat)


# ---------------------------------------------------------------------------
# Lifespan: starts the producer as a background task when the app starts
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(flight_update_producer())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def handle_client(websocket: WebSocket) -> None:
    await websocket.accept()

    client_addr = websocket.client
    logger.info("Client connected: %s", client_addr)
    CLIENT_STATES[websocket] = {
        "update_count": 0,
        "confirm_event": asyncio.Event(),
        "active": True,
    }

    # Start the independent confirmation checker for this client
    checker_task = asyncio.create_task(confirm_interest_checker(websocket))

    welcome = json.dumps({
        "event": "WELCOME",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "message": "Connected to Flight Notification Service.",
        "flights": list(FLIGHTS.values()),
    })
    await websocket.send_text(welcome)

    try:
        while True:
            raw = await websocket.receive_text()
            logger.info("Raw message from %s: %s", client_addr, raw)
            try:
                msg = json.loads(raw)
                action = msg.get("action")

                if action == "CONTINUE":
                    # Unblock send_to_client which is waiting on confirm_event
                    CLIENT_STATES[websocket]["confirm_event"].set()

                elif action == "STOP":
                    CLIENT_STATES[websocket]["active"] = False
                    await websocket.send_text(json.dumps({"event": "INFO", "message": "Unsubscribed. Closing connection."}))
                    break

                elif action == "SUBSCRIBE":
                    fid = msg.get("flight_number", "EY-6")
                    if fid in FLIGHTS:
                        resp = build_event(fid, {
                            "status": FLIGHTS[fid]["status"],
                            "message": f"Subscribed to {fid}. Current status: {FLIGHTS[fid]['status']}",
                        })
                        await websocket.send_text(resp)
                    else:
                        await websocket.send_text(json.dumps({"event": "ERROR", "message": f"Unknown flight: {fid}"}))

                else:
                    await websocket.send_text(json.dumps({"event": "ERROR", "message": "Unknown action."}))

            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"event": "ERROR", "message": "Invalid JSON payload."}))

    except WebSocketDisconnect:
        pass
    finally:
        checker_task.cancel()
        CLIENT_STATES.pop(websocket, None)
        logger.info("Client disconnected: %s", client_addr)


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8765)
