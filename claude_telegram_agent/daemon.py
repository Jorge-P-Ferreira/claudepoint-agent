"""The `run` loop — WebSocket client to the broker. Contract:
../../docs/transport-protocol.md.

Phase 1 scope: connect, `hello`, heartbeat, cmd dispatch + ack, answer `ping`, and a
fixed reconnect delay. The real backoff curve (1->2->5->15->30s + jitter) and
resume-from-restart (re-`hello` with `resumable_sessions`) land in Phase 3.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from . import __version__, projects
from .session_manager import SessionManager

HEARTBEAT_INTERVAL_SECONDS = 20
RECONNECT_DELAY_SECONDS = 2  # Phase 3 replaces this with the real backoff curve


def _frame_id() -> str:
    return uuid.uuid4().hex


def _envelope(kind: str, session_id: str | None, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "v": 1,
        "id": _frame_id(),
        "ts": int(time.time() * 1000),
        "session_id": session_id,
        "kind": kind,
        "data": data,
    }


async def run(config: dict[str, Any]) -> None:
    while True:
        try:
            await _run_once(config)
        except (websockets.exceptions.ConnectionClosed, OSError) as exc:
            print(f"connection lost ({exc!r}) — reconnecting in {RECONNECT_DELAY_SECONDS}s")
        await asyncio.sleep(RECONNECT_DELAY_SECONDS)


async def _run_once(config: dict[str, Any]) -> None:
    broker_url = config["broker_url"]
    laptop_token = config["laptop_token"]
    project_roots = config.get("project_roots", ["."])

    async with websockets.connect(
        broker_url,
        additional_headers={"Authorization": f"Bearer {laptop_token}"},
        subprotocols=[websockets.Subprotocol("cta.v1")],
    ) as ws:
        seen_ids: set[str] = set()

        async def emit(session_id: str | None, data: dict[str, Any]) -> None:
            await ws.send(json.dumps(_envelope("event", session_id, data)))

        manager = SessionManager(project_roots, emit)

        await ws.recv()  # `welcome` — nothing to act on in Phase 1

        await ws.send(
            json.dumps(
                _envelope(
                    "hello",
                    None,
                    {
                        "agent_version": __version__,
                        "laptop_name": socket.gethostname(),
                        "projects": projects.discover(project_roots),
                        "resumable_sessions": [],  # Phase 3
                    },
                )
            )
        )

        heartbeat_task = asyncio.create_task(_heartbeat_loop(ws))
        try:
            async for raw in ws:
                frame = json.loads(raw)
                frame_id = frame.get("id")
                if frame_id in seen_ids:
                    continue
                seen_ids.add(frame_id)

                kind = frame.get("kind")
                if kind == "cmd":
                    await manager.dispatch(frame)
                    await ws.send(
                        json.dumps(
                            _envelope("ack", frame.get("session_id"), {"ref": frame_id})
                        )
                    )
                elif kind == "ping":
                    await ws.send(json.dumps(_envelope("heartbeat", None, {})))
        finally:
            heartbeat_task.cancel()


async def _heartbeat_loop(ws: ClientConnection) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        await ws.send(json.dumps(_envelope("heartbeat", None, {})))
