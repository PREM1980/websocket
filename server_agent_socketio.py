"""
Agent WebSocket Server — Socket.IO Edition

Tool approval via outbound HTTP POST to a callback URL (same pattern as agent.py).
The client supplies callback_url and callback_token in the run_agent payload.
The server POSTs to that URL when a tool needs approval and blocks until it responds.

Architecture:
    Client ──► run_agent { prompt, callback_url, callback_token } ──► server starts Claude agent
    Server ──► POST callback_url { tool_name, tool_input, ... }   ──► callback endpoint
    callback endpoint ──► { cmds: [{ execute: true/false }] }     ──► server resumes
    Server ──► tool_approved / tool_rejected                      ──► client (status only)
    Server ──► agent_message_delta (streaming) / agent_done       ──► real-time output

Usage:
    pip install -r requirements.txt
    python server_agent_socketio.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

import httpx
import socketio
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    StreamEvent,
    TextBlock,
)

load_dotenv(override=True)

_REQUIRED_AWS_VARS = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"]


def validate_aws_env() -> None:
    missing = [v for v in _REQUIRED_AWS_VARS if not os.environ.get(v)]
    if missing:
        print(
            f"[SOCKETIO-SERVER] ERROR: Missing required environment variable(s): "
            f"{', '.join(missing)}.",
            file=sys.stderr,
        )
        sys.exit(1)


logging.basicConfig(level=logging.INFO, format="%(asctime)s [SOCKETIO-SERVER] %(message)s")
logger = logging.getLogger(__name__)

APPROVAL_TIMEOUT = 45.0  # seconds


# ---------------------------------------------------------------------------
# Socket.IO setup
# ---------------------------------------------------------------------------

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
fastapi_app = FastAPI()
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)


# ---------------------------------------------------------------------------
# Per-client session state
# ---------------------------------------------------------------------------

class ClientSession:
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.callback_url: str | None = None
        self.callback_token: str | None = None
        self.approval_sem: asyncio.Semaphore = asyncio.Semaphore(1)
        self.agent_task: asyncio.Task | None = None


SESSIONS: dict[str, ClientSession] = {}


# ---------------------------------------------------------------------------
# HTTP callback helper (mirrors agent.py _call_agentcallback)
# ---------------------------------------------------------------------------

async def _call_agentcallback(
    callback_url: str,
    callback_token: str,
    ticket_name: str,
    message: dict,
    timeout: float = APPROVAL_TIMEOUT,
) -> dict:
    """POST to callback_url and block until the callback endpoint responds."""
    payload = {"ticket_name": ticket_name, "message": message}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            callback_url,
            json=payload,
            headers={"Authorization": f"Bearer {callback_token}"},
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

async def run_agent_task(session: ClientSession, prompt: str) -> None:
    """
    Start a Claude agent for the given prompt.

    A PreToolUse hook intercepts every tool call:
      1. Emits tool_approval_request to the client (status only).
      2. POSTs to session.callback_url and blocks until it responds.
      3. Returns {} to allow, or raises RuntimeError to abort.
    """

    async def pre_tool_use_hook(input_data: dict, tool_use_id: str, context: dict):
        tool_name  = input_data.get("tool_name", "unknown")
        tool_input = input_data.get("tool_input", {})

        # Inform the socket client (status only — no approve/reject expected)
        await sio.emit("tool_approval_request", {
            "event":       "TOOL_APPROVAL_REQUEST",
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "tool_use_id": tool_use_id,
            "tool_name":   tool_name,
            "tool_input":  tool_input,
            "message":     f"Waiting for callback approval of '{tool_name}'.",
        }, to=session.sid)
        logger.info("Approval requested via HTTP callback: tool=%s id=%s", tool_name, tool_use_id)

        if not session.callback_url:
            raise RuntimeError("No callback_url provided — cannot request tool approval.")

        async with session.approval_sem:
            msg = {
                "role":    "assistant",
                "content": f"Approve tool '{tool_name}'?",
                "data":    {"tool_name": tool_name, "tool_input": tool_input},
            }
            try:
                data = await _call_agentcallback(
                    session.callback_url,
                    session.callback_token or "",
                    session.sid,
                    msg,
                )
            except httpx.TimeoutException:
                await sio.emit("tool_rejected", {
                    "tool_name": tool_name,
                    "reason":    "Approval timed out.",
                }, to=session.sid)
                raise RuntimeError(f"Tool approval timed out: {tool_name}")
            except Exception as exc:
                await sio.emit("tool_rejected", {
                    "tool_name": tool_name,
                    "reason":    f"Callback error: {exc}",
                }, to=session.sid)
                raise RuntimeError(f"Callback error for '{tool_name}': {exc}")

        cmds = data.get("cmds", [{}])
        approved = cmds[0].get("execute", False)
        reason   = cmds[0].get("rejection_reason", "")

        if approved:
            await sio.emit("tool_approved", {"tool_name": tool_name}, to=session.sid)
            logger.info("Tool approved: %s", tool_name)
            return {}
        else:
            await sio.emit("tool_rejected", {
                "tool_name": tool_name,
                "reason":    reason or "Rejected by callback.",
            }, to=session.sid)
            raise RuntimeError(f"Tool '{tool_name}' rejected: {reason}")

    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Glob", "Grep", "Bash"],
        permission_mode="bypassPermissions",
        include_partial_messages=True,
        hooks={
            "PreToolUse": [HookMatcher(matcher=".*", hooks=[pre_tool_use_hook])]
        },
    )

    await sio.emit("agent_started", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message":   f"Agent started. Prompt: {prompt}",
    }, to=session.sid)

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)

            async for message in client.receive_response():
                if isinstance(message, StreamEvent):
                    event_data = message.event
                    if event_data.get("type") == "content_block_delta":
                        delta = event_data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                await sio.emit("agent_message_delta", {
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "text":      text,
                                }, to=session.sid)

                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            await sio.emit("agent_message", {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "text":      block.text,
                            }, to=session.sid)

                elif isinstance(message, ResultMessage):
                    await sio.emit("agent_done", {
                        "timestamp":   datetime.now(timezone.utc).isoformat(),
                        "result":      message.result,
                        "stop_reason": message.stop_reason,
                    }, to=session.sid)

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        await sio.emit("agent_error", {"message": str(exc)}, to=session.sid)
        logger.error("Agent error: %s", exc)
    finally:
        SESSIONS.pop(session.sid, None)


# ---------------------------------------------------------------------------
# Socket.IO event handlers
# ---------------------------------------------------------------------------

@sio.event
async def connect(sid, environ):
    session = ClientSession(sid)
    SESSIONS[sid] = session
    logger.info("Client connected: sid=%s", sid)
    await sio.emit("welcome", {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": (
            "Connected to Agent Server (Socket.IO). "
            "Emit 'run_agent' with { prompt, callback_url, callback_token } to start."
        ),
    }, to=sid)


@sio.event
async def disconnect(sid):
    session = SESSIONS.pop(sid, None)
    if session and session.agent_task and not session.agent_task.done():
        session.agent_task.cancel()
    logger.info("Client disconnected: sid=%s", sid)


@sio.event
async def run_agent(sid, data):
    if sid not in SESSIONS:
        SESSIONS[sid] = ClientSession(sid)

    session = SESSIONS[sid]

    if session.agent_task and not session.agent_task.done():
        await sio.emit("error", {"message": "An agent is already running. Wait for agent_done."}, to=sid)
        return

    prompt = (data or {}).get("prompt", "").strip()
    if not prompt:
        await sio.emit("error", {"message": "prompt is required."}, to=sid)
        return

    session.callback_url   = (data or {}).get("callback_url", "").strip() or None
    session.callback_token = (data or {}).get("callback_token", "").strip() or None

    session.agent_task = asyncio.create_task(run_agent_task(session, prompt))


@sio.event
async def cancel_session(sid, data):
    session = SESSIONS.get(sid)
    if not session or not session.agent_task or session.agent_task.done():
        await sio.emit("error", {"message": "No active session to cancel."}, to=sid)
        return
    session.agent_task.cancel()
    SESSIONS.pop(sid, None)
    await sio.emit("session_cancelled", {}, to=sid)
    logger.info("Session cancelled: sid=%s", sid)


# ---------------------------------------------------------------------------
# Dummy callback endpoint — waits for browser approve/reject via Socket.IO
# Use callback_url = http://localhost:8766/dummy-callback  (no token needed)
# ---------------------------------------------------------------------------

# sid -> Future[bool]  (True = approved, False = rejected)
PENDING_APPROVALS: dict[str, asyncio.Future] = {}


@fastapi_app.post("/dummy-callback")
async def dummy_callback(request: Request):
    body = await request.json()
    sid = body.get("ticket_name", "")
    tool_name = body.get("message", {}).get("data", {}).get("tool_name", "unknown")
    logger.info("Dummy callback: waiting for browser approval sid=%s tool=%s", sid, tool_name)

    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()
    PENDING_APPROVALS[sid] = future

    try:
        approved = await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT)
    except asyncio.TimeoutError:
        return {"cmds": [{"execute": False, "rejection_reason": "Approval timed out."}]}
    finally:
        PENDING_APPROVALS.pop(sid, None)

    return {"cmds": [{"execute": approved}]}


@sio.event
async def approve(sid, data):
    future = PENDING_APPROVALS.get(sid)
    if future and not future.done():
        future.set_result(True)


@sio.event
async def reject(sid, data):
    future = PENDING_APPROVALS.get(sid)
    if future and not future.done():
        future.set_result(False)


if __name__ == "__main__":
    validate_aws_env()
    uvicorn.run(app, host="localhost", port=8766)
