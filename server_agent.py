"""
Agent WebSocket Server

Runs a Claude agent (via Agent SDK) per client session.
Before executing any tool, the server pauses and asks the connected
WebSocket client to APPROVE or REJECT the tool call.

Architecture:
    Client ──► RUN_AGENT  ──► server starts Claude agent
    Server ──► TOOL_APPROVAL_REQUEST ──► client sees tool + input
    Client ──► APPROVE / REJECT  ──► server resumes or aborts the tool
    Server ──► AGENT_MESSAGE_DELTA (streaming) / AGENT_DONE ──► client sees agent output in real-time

Usage:
    pip install -r requirements.txt
    python server_agent.py
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    StreamEvent,
    TextBlock,
)

load_dotenv(override=True)  # Load .env into process environment before anything else

_REQUIRED_AWS_VARS = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"]


def validate_aws_env() -> None:
    """Exit with code 1 if any required AWS environment variable is missing."""
    missing = [v for v in _REQUIRED_AWS_VARS if not os.environ.get(v)]
    if missing:
        print(
            f"[AGENT-SERVER] ERROR: Missing required environment variable(s): "
            f"{', '.join(missing)}. "
            f"Set them in a .env file or in the process environment.",
            file=sys.stderr,
        )
        sys.exit(1)


logging.basicConfig(level=logging.INFO, format="%(asctime)s [AGENT-SERVER] %(message)s")
logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT = 30  # seconds the server waits for the client to approve/reject


# ---------------------------------------------------------------------------
# Per-client session state
# ---------------------------------------------------------------------------

class ClientSession:
    """
    Holds everything that belongs to one connected WebSocket client.

    approval_event  — asyncio.Event that the receive loop sets when the
                      client replies APPROVE or REJECT.
    approval_result — True = approved, False = rejected.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.approval_event: asyncio.Event = asyncio.Event()
        self.approval_result: bool = False


SESSIONS: dict[WebSocket, ClientSession] = {}

app = FastAPI()


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

async def run_agent(session: ClientSession, prompt: str) -> None:
    """
    Start a Claude agent for the given prompt.

    A PreToolUse hook intercepts every tool call:
      1. Sends TOOL_APPROVAL_REQUEST to the client.
      2. Waits (up to APPROVAL_TIMEOUT s) for the client to reply.
      3. Returns {} to allow, or raises RuntimeError to abort.
    """

    async def pre_tool_use_hook(input_data: dict, tool_use_id: str, context: dict):
        tool_name  = input_data.get("tool_name", "unknown")
        tool_input = input_data.get("tool_input", {})

        # Ask the client
        await session.websocket.send_text(json.dumps({
            "event":       "TOOL_APPROVAL_REQUEST",
            "timestamp":   datetime.utcnow().isoformat() + "Z",
            "tool_use_id": tool_use_id,
            "tool_name":   tool_name,
            "tool_input":  tool_input,
            "message":     f"Agent wants to run '{tool_name}'. Reply APPROVE or REJECT.",
        }))
        logger.info("Approval requested: tool=%s id=%s", tool_name, tool_use_id)

        # Wait for the receive loop to set the event
        session.approval_event.clear()
        try:
            await asyncio.wait_for(session.approval_event.wait(), timeout=APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            await session.websocket.send_text(json.dumps({
                "event":     "TOOL_REJECTED",
                "tool_name": tool_name,
                "reason":    "Approval timed out.",
            }))
            raise RuntimeError(f"Tool approval timed out: {tool_name}")

        if not session.approval_result:
            await session.websocket.send_text(json.dumps({
                "event":     "TOOL_REJECTED",
                "tool_name": tool_name,
                "reason":    "Rejected by user.",
            }))
            raise RuntimeError(f"Tool '{tool_name}' rejected by user.")

        # Approved — let the agent proceed
        await session.websocket.send_text(json.dumps({
            "event":     "TOOL_APPROVED",
            "tool_name": tool_name,
        }))
        return {}

    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Glob", "Grep", "Bash"],
        permission_mode="bypassPermissions",  # approval is handled via WebSocket above
        include_partial_messages=True,  # Enable streaming for real-time token output
        hooks={
            # Match every tool (".*") and run the approval gate
            "PreToolUse": [HookMatcher(matcher=".*", hooks=[pre_tool_use_hook])]
        },
    )

    await session.websocket.send_text(json.dumps({
        "event":     "AGENT_STARTED",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "message":   f"Agent started. Prompt: {prompt}",
    }))

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)

            async for message in client.receive_response():
                if isinstance(message, StreamEvent):
                    # Streaming: send each token delta in real-time
                    event_data = message.event
                    if event_data.get("type") == "content_block_delta":
                        delta = event_data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                await session.websocket.send_text(json.dumps({
                                    "event":     "AGENT_MESSAGE_DELTA",
                                    "timestamp": datetime.utcnow().isoformat() + "Z",
                                    "text":      text,
                                }))

                elif isinstance(message, AssistantMessage):
                    # Fallback for non-streaming or complete messages
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            await session.websocket.send_text(json.dumps({
                                "event":     "AGENT_MESSAGE",
                                "timestamp": datetime.utcnow().isoformat() + "Z",
                                "text":      block.text,
                            }))

                elif isinstance(message, ResultMessage):
                    await session.websocket.send_text(json.dumps({
                        "event":       "AGENT_DONE",
                        "timestamp":   datetime.utcnow().isoformat() + "Z",
                        "result":      message.result,
                        "stop_reason": message.stop_reason,
                    }))

    except Exception as exc:
        await session.websocket.send_text(json.dumps({
            "event":   "AGENT_ERROR",
            "message": str(exc),
        }))
        logger.error("Agent error: %s", exc)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def handle_client(websocket: WebSocket) -> None:
    await websocket.accept()

    session = ClientSession(websocket)
    SESSIONS[websocket] = session
    logger.info("Client connected: %s", websocket.client)

    await websocket.send_text(json.dumps({
        "event":     "WELCOME",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "message":   (
            "Connected to Agent Server. "
            'Send {"action": "RUN_AGENT", "prompt": "..."} to start an agent. '
            'Reply {"action": "APPROVE"} or {"action": "REJECT"} for tool approvals.'
        ),
    }))

    agent_task: asyncio.Task | None = None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg    = json.loads(raw)
                action = msg.get("action")

                if action == "RUN_AGENT":
                    prompt = msg.get("prompt", "").strip()
                    if not prompt:
                        await websocket.send_text(json.dumps({
                            "event":   "ERROR",
                            "message": "prompt is required.",
                        }))
                        continue

                    if agent_task and not agent_task.done():
                        await websocket.send_text(json.dumps({
                            "event":   "ERROR",
                            "message": "An agent is already running. Wait for AGENT_DONE.",
                        }))
                        continue

                    # Launch the agent as a background task so the receive
                    # loop stays unblocked (needed to receive APPROVE/REJECT)
                    agent_task = asyncio.create_task(run_agent(session, prompt))

                elif action == "APPROVE":
                    session.approval_result = True
                    session.approval_event.set()

                elif action == "REJECT":
                    session.approval_result = False
                    session.approval_event.set()

                elif action == "CANCEL_SESSION":
                    if agent_task and not agent_task.done():
                        agent_task.cancel()
                        await websocket.send_text(json.dumps({
                            "event": "SESSION_CANCELLED",
                        }))
                    else:
                        await websocket.send_text(json.dumps({
                            "event": "ERROR",
                            "message": "No active session to cancel.",
                        }))

                else:
                    await websocket.send_text(json.dumps({
                        "event":   "ERROR",
                        "message": f"Unknown action: {action!r}",
                    }))

            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "event":   "ERROR",
                    "message": "Invalid JSON.",
                }))

    except WebSocketDisconnect:
        pass
    finally:
        if agent_task:
            agent_task.cancel()
        SESSIONS.pop(websocket, None)
        logger.info("Client disconnected: %s", websocket.client)


if __name__ == "__main__":
    validate_aws_env()
    uvicorn.run(app, host="localhost", port=8766)
