"""
WebSocket Flight Notification Client

Connects to the server and pretty-prints incoming flight status events.
Optionally subscribes to a specific flight on launch.

Usage:
    pip install -r requirements.txt
    python client.py                        # receive all broadcasts
    python client.py --flight EY-6          # also subscribe to EY-6
    python client.py --flight EY-212        # also subscribe to EY-212
"""

import argparse
import asyncio
import json
import logging
from datetime import datetime

import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLIENT] %(message)s")
logger = logging.getLogger(__name__)

SERVER_URI = "ws://localhost:8765"

# ANSI colour helpers
RESET  = "\033[0m"
BOLD   = "\033[1m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
RED    = "\033[31m"
BLUE   = "\033[34m"

STATUS_COLOUR = {
    "ON_TIME":     GREEN,
    "BOARDING":    CYAN,
    "DEPARTED":    CYAN,
    "IN_FLIGHT":   BLUE,
    "DELAYED":     YELLOW,
    "RESCHEDULED": YELLOW,
    "LANDED":      GREEN,
    "ARRIVED":     GREEN,
}


def colour_status(status: str) -> str:
    c = STATUS_COLOUR.get(status, RESET)
    return f"{c}{BOLD}{status}{RESET}"


def render_event(data: dict) -> str:
    event_type = data.get("event", "UNKNOWN")
    ts = data.get("timestamp", "")

    if event_type == "WELCOME":
        lines = [
            f"\n{BOLD}{CYAN}=== Flight Notification Service ==={RESET}",
            f"  {data.get('message', '')}",
            f"  Timestamp : {ts}",
            f"  Tracking  :",
        ]
        for f in data.get("flights", []):
            lines.append(
                f"    • {BOLD}{f['flight_number']}{RESET}  "
                f"{f['origin']}  →  {f['destination']}  "
                f"(dep {f['scheduled_departure']} on {f['departure_date']})"
            )
        return "\n".join(lines)

    if event_type == "FLIGHT_UPDATE":
        flight  = data.get("flight", {})
        update  = data.get("update", {})
        status  = update.get("status", "")
        message = update.get("message", "")
        impacted = status in ("DELAYED", "RESCHEDULED")
        impact_tag = f" {RED}{BOLD}[IMPACTED]{RESET}" if impacted else ""
        return (
            f"\n{BOLD}[FLIGHT UPDATE]{RESET}{impact_tag}\n"
            f"  Flight    : {BOLD}{flight.get('flight_number')}{RESET}  {flight.get('airline')}\n"
            f"  Route     : {flight.get('origin')}  →  {flight.get('destination')}\n"
            f"  Departure : {flight.get('scheduled_departure')} on {flight.get('departure_date')}\n"
            f"  Terminal  : {flight.get('terminal')}\n"
            f"  Status    : {colour_status(status)}\n"
            f"  Info      : {message}\n"
            f"  Time      : {ts}"
        )

    if event_type == "HEARTBEAT":
        return f"  {BLUE}♥ heartbeat{RESET}  {data.get('message', '')}  [{ts}]"

    if event_type == "ERROR":
        return f"  {RED}ERROR:{RESET} {data.get('message', '')}"

    return json.dumps(data, indent=2)


async def run(flight_filter: str | None) -> None:
    logger.info("Connecting to %s …", SERVER_URI)
    try:
        async with websockets.connect(SERVER_URI) as ws:
            logger.info("Connected.")

            # Optionally subscribe to a specific flight
            if flight_filter:
                sub = json.dumps({"action": "SUBSCRIBE", "flight_number": flight_filter})
                await ws.send(sub)
                logger.info("Sent subscription request for %s", flight_filter)

            async for raw in ws:
                try:
                    data = json.loads(raw)
                    print(render_event(data))
                except json.JSONDecodeError:
                    logger.warning("Non-JSON message received: %s", raw)

    except ConnectionRefusedError:
        logger.error("Could not connect to %s — is the server running?", SERVER_URI)
    except websockets.exceptions.ConnectionClosedError as exc:
        logger.warning("Connection closed: %s", exc)
    except KeyboardInterrupt:
        logger.info("Client stopped by user.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Flight Notification WebSocket Client")
    parser.add_argument(
        "--flight", "-f",
        metavar="FLIGHT_NUMBER",
        help="Subscribe to a specific flight (e.g. EY-6 or EY-212)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(args.flight))
