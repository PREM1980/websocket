"""
Agent WebSocket Client

Connects to server_agent.py, sends a prompt, handles tool-approval
requests interactively, and pretty-prints agent output.

Usage:
    python client_agent.py                              # prompted for input
    python client_agent.py -p "List Python files here"
    python client_agent.py --auto-approve               # approve all tools
"""

import argparse
import asyncio
import json
import sys

import websockets

SERVER_URI = "ws://localhost:8766/ws"

# ANSI colour helpers
RESET  = "\033[0m"
BOLD   = "\033[1m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
RED    = "\033[31m"
BLUE   = "\033[34m"
DIM    = "\033[2m"


def render_event(data: dict) -> str | None:
    """Return a formatted string for the event, or None to suppress it."""
    event = data.get("event", "UNKNOWN")

    if event == "WELCOME":
        return (
            f"\n{BOLD}{CYAN}=== Agent Server ==={RESET}\n"
            f"  {data.get('message', '')}"
        )

    if event == "AGENT_STARTED":
        return f"\n{BOLD}{BLUE}▶ Agent started{RESET}  {DIM}{data.get('timestamp', '')}{RESET}"

    if event == "TOOL_APPROVAL_REQUEST":
        tool  = data.get("tool_name", "?")
        inp   = json.dumps(data.get("tool_input", {}), indent=4)
        indented = "\n".join(f"    {line}" for line in inp.splitlines())
        return (
            f"\n{BOLD}{YELLOW}⚙  Tool request:{RESET} {BOLD}{tool}{RESET}\n"
            f"{DIM}{indented}{RESET}"
        )

    if event == "TOOL_APPROVED":
        return f"  {GREEN}✔ Approved:{RESET} {data.get('tool_name', '')}"

    if event == "TOOL_REJECTED":
        return f"  {RED}✘ Rejected:{RESET} {data.get('tool_name', '')}  — {data.get('reason', '')}"

    if event == "AGENT_MESSAGE":
        text = data.get("text", "").rstrip()
        return f"\n{BOLD}Agent:{RESET}\n{text}"

    if event == "AGENT_MESSAGE_DELTA":
        # Streaming: print token immediately without header
        text = data.get("text", "")
        sys.stdout.write(text)
        sys.stdout.flush()
        return None  # Already printed, no extra output

    if event == "AGENT_DONE":
        result     = data.get("result", "")
        stop       = data.get("stop_reason", "")
        timestamp  = data.get("timestamp", "")
        return (
            f"\n{BOLD}{GREEN}✓ Done{RESET}  {DIM}stop={stop}  {timestamp}{RESET}\n"
            f"{result}"
        )

    if event == "AGENT_ERROR":
        return f"\n{BOLD}{RED}✗ Agent error:{RESET} {data.get('message', '')}"

    if event == "ERROR":
        return f"\n{RED}Error:{RESET} {data.get('message', '')}"

    # Unknown — dump raw
    return f"\n{DIM}{json.dumps(data, indent=2)}{RESET}"


def prompt_approval(tool_name: str) -> str:
    """Ask the user to approve or reject a tool call. Returns 'APPROVE' or 'REJECT'."""
    while True:
        try:
            answer = input(f"\n  Approve '{tool_name}'? [y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "REJECT"
        if answer in ("y", "yes"):
            return "APPROVE"
        if answer in ("n", "no"):
            return "REJECT"
        print("  Please enter y or n.")


async def run(prompt: str, auto_approve: bool) -> None:
    try:
        async with websockets.connect(SERVER_URI) as ws:
            # Start the agent
            await ws.send(json.dumps({"action": "RUN_AGENT", "prompt": prompt}))

            streaming_started = False  # Track if we've printed the "Agent:" header

            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"[non-JSON] {raw}")
                    continue

                event = data.get("event")

                # Print "Agent:" header once when streaming starts
                if event == "AGENT_MESSAGE_DELTA" and not streaming_started:
                    print(f"\n{BOLD}Agent:{RESET}")
                    streaming_started = True

                rendered = render_event(data)
                if rendered is not None:
                    print(rendered)

                if event == "TOOL_APPROVAL_REQUEST":
                    tool_name = data.get("tool_name", "unknown")
                    if auto_approve:
                        print(f"  {GREEN}✔ Auto-approving '{tool_name}'{RESET}")
                        action = "APPROVE"
                    else:
                        action = prompt_approval(tool_name)
                    await ws.send(json.dumps({"action": action}))

                elif event in ("AGENT_DONE", "AGENT_ERROR"):
                    break

    except ConnectionRefusedError:
        print(f"{RED}Could not connect to {SERVER_URI} — is server_agent.py running?{RESET}",
              file=sys.stderr)
        sys.exit(1)
    except websockets.exceptions.ConnectionClosedError as exc:
        print(f"{RED}Connection closed unexpectedly: {exc}{RESET}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive client for the Agent WebSocket Server (server_agent.py)"
    )
    parser.add_argument(
        "--prompt", "-p",
        metavar="TEXT",
        help="Prompt to send to the agent (asked interactively if omitted)",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Automatically approve all tool calls without prompting",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    prompt = args.prompt
    if not prompt:
        try:
            prompt = input("Prompt: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)
    if not prompt:
        print("No prompt provided.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run(prompt, args.auto_approve))
