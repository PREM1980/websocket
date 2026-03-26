"""
WebSocket Flight Notification Server

Simulates a real-time flight status notification system.
Pushes flight updates (schedule changes, delays, gate info) to all connected clients.

Usage:
    pip install -r requirements.txt
    python server.py
"""

import asyncio
import json
import logging
from datetime import datetime

import websockets
from websockets.server import WebSocketServerProtocol

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SERVER] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock flight data  (based on the Etihad EY-6 rescheduled itinerary)
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

# Sequence of status updates pushed every 5 seconds
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

CLIENTS: set[WebSocketServerProtocol] = set()


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


async def broadcast(message: str) -> None:
    if not CLIENTS:
        logger.info("No clients connected, skipping broadcast.")
        return
    results = await asyncio.gather(
        *(client.send(message) for client in list(CLIENTS)),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Broadcast error: %s", r)


async def flight_update_producer() -> None:
    """Sequentially push mock flight status events, then send heartbeats."""
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


async def handle_client(websocket: WebSocketServerProtocol) -> None:
    client_addr = websocket.remote_address
    logger.info("Client connected: %s", client_addr)
    CLIENTS.add(websocket)

    # Welcome snapshot
    welcome = json.dumps({
        "event": "WELCOME",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "message": "Connected to Flight Notification Service.",
        "flights": list(FLIGHTS.values()),
    })
    await websocket.send(welcome)

    try:
        async for raw in websocket:
            logger.info("Raw message from %s: %s", client_addr, raw)
            try:
                msg = json.loads(raw)
                if msg.get("action") == "SUBSCRIBE":
                    fid = msg.get("flight_number", "EY-6")
                    if fid in FLIGHTS:
                        resp = build_event(fid, {
                            "status": FLIGHTS[fid]["status"],
                            "message": f"Subscribed to {fid}. Current status: {FLIGHTS[fid]['status']}",
                        })
                        await websocket.send(resp)
                    else:
                        await websocket.send(json.dumps({"event": "ERROR", "message": f"Unknown flight: {fid}"}))
                else:
                    await websocket.send(json.dumps({"event": "ERROR", "message": "Unknown action."}))
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"event": "ERROR", "message": "Invalid JSON payload."}))
    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError as exc:
        logger.warning("Client %s dropped: %s", client_addr, exc)
    finally:
        CLIENTS.discard(websocket)
        logger.info("Client disconnected: %s", client_addr)


async def main() -> None:
    host, port = "localhost", 8765
    logger.info("Flight Notification Server starting on ws://%s:%d", host, port)
    async with websockets.serve(handle_client, host, port):
        await flight_update_producer()


if __name__ == "__main__":
    asyncio.run(main())
