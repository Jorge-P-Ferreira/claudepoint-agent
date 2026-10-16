"""The `run` loop — WebSocket client to the broker. Contract:
../../docs/transport-protocol.md.

Phase 1: connect, `hello`, heartbeat, cmd dispatch + ack, answer `ping`, and a fixed
reconnect delay. Phase 3 (this file, now) replaces the fixed delay with a real backoff
curve (1->2->5->15->30s + jitter, `_backoff_delay`) and adds resume-from-restart:

- `SessionManager` (and its `emit` callback) is built once in `run()`, not per
  connection attempt — a bare WebSocket reconnect (WiFi blip, broker restart) no
  longer wipes it. `emit` reads the *current* socket through `_Connection` and drops
  an event rather than raising if nothing's connected right now; anything actually
  running (a live `ClaudeSession`/turn) just keeps going and resumes pushing events
  once the socket is back — nothing about an in-progress conversation is lost on a
  connection that never truly died.
- Only an actual **process** restart (crash, reboot, manual relaunch) empties
  `SessionManager.sessions` for real. `hello.data.resumable_sessions` reports exactly
  what's alive right now, so the broker (`../../broker/app/ws_hub.py::_handle_hello`)
  can tell "still running, nothing to do" from "gone, please resume" per session and
  push a `new_session` cmd (with a `claude_session_id` to resume) only for the latter
  — see `docs/session-model.md` § Resume.

Phase 7 (this file, now) closes the one-directional gap in at-least-once delivery: the
broker's own `_unacked`/`_flush_unacked` already retried `cmd` frames sent while briefly
disconnected, but an `event` frame `emit()` couldn't send just got dropped with a
`print`. `_Connection.unacked` mirrors the broker's structure on this side — `emit`
buffers every frame before attempting to send it, `_run_once`'s frame loop clears an
entry on the matching broker->agent `ack` (new: the broker now acks inbound `event`
frames too, see `ws_hub.py::_ack_event`), and anything still unacked is resent right
after the next `hello`, reusing the same frame `id`s (safe under the broker's own
`_seen_ids` dedupe).

Post-Phase-7 hardening (this file, now): `run()`'s reconnect loop no longer resets
`attempt` to 0 just because `_run_once` returned without raising. The `websockets`
client's `async for raw in ws:` loop exits that way on *any* normal server-side close,
not only a genuinely healthy long-lived connection — so a proxy or idle-timeout
repeatedly closing the socket "cleanly" was reconnecting instantly with zero backoff
every time, hammering the broker and re-triggering its offline/online notification on
every cycle. The reset now depends on `_STABLE_CONNECTION_SECONDS` of actual uptime,
judged the same way whether `_run_once` raised or returned cleanly.

Unpaired from the phone: the broker no longer knows this laptop's token, so it closes
the socket with `4401` before ever sending `welcome`. That's permanent, not a blip —
`_run_once` raises `LaptopUnpaired` instead of letting the loop retry forever, and
`run()` lets it propagate (`__main__._cmd_run` then removes the local config and the
background service). A `4401` *after* `welcome` (the broker's hello timeout) is still
just a retryable drop.

A broker that's restarting (a deploy) answers the WebSocket handshake with an HTTP error
— Caddy's 502 while the container is down. `websockets` raises that as
`InvalidHandshake` (`InvalidStatus`), which is neither `ConnectionClosed` nor `OSError`,
so it used to escape `run()` and kill the process. On Windows that's permanent until
the next logon (Task Scheduler's restart-on-failure doesn't fire for a process that
exits non-zero). It's now retried with backoff like any other drop.
"""

from __future__ import annotations

import asyncio
import json
import random
import socket
import time
import uuid
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from . import __version__, projects
from .session_manager import SessionManager

HEARTBEAT_INTERVAL_SECONDS = 20
_UNAUTHORIZED_CLOSE_CODE = 4401


class LaptopUnpaired(Exception):
    """The broker rejected this laptop's token — it was unpaired (or never paired)."""

# 1 -> 2 -> 5 -> 15 -> 30s, then holds at 30s. Real network drops (a laptop asleep,
# a flaky WiFi AP) can last anywhere from a second to tens of minutes; this backs off
# quickly enough to feel responsive on a short blip without hammering the broker
# during a long one.
_BACKOFF_SCHEDULE = (1.0, 2.0, 5.0, 15.0, 30.0)

# A connection alive at least this long counts as a genuine, working reconnect — not
# a flap — and resets the backoff schedule back to the top. Judged by wall-clock time
# connected, not by whether `_run_once` happened to raise: the `websockets` client's
# `async for raw in ws:` loop exits *without raising* on a normal server-side close
# (not just an abnormal drop), so a "clean" exit isn't on its own evidence the
# connection was healthy — see `run`'s loop below.
_STABLE_CONNECTION_SECONDS = 30.0


def _backoff_delay(attempt: int) -> float:
    """`attempt` is 0-indexed (0 = first retry). ±20% jitter per
    `docs/transport-protocol.md` § Reconnect, so a whole fleet of laptops reconnecting
    after a broker restart doesn't all retry in lockstep."""
    base = _BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)]
    return base * random.uniform(0.8, 1.2)


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


class _Connection:
    """Holds the currently-live websocket, if any. `emit` closes over this (not a
    specific `ws` object) so it keeps working correctly across reconnects — `_run_once`
    updates `.ws` on connect and clears it in its `finally`. `unacked` (Phase 7) is the
    outbound-`event` counterpart to the broker's own `_unacked` — see module docstring."""

    def __init__(self) -> None:
        self.ws: ClientConnection | None = None
        self.unacked: dict[str, dict[str, Any]] = {}


async def run(config: dict[str, Any]) -> None:
    project_roots = config.get("project_roots", ["."])
    conn = _Connection()

    async def emit(session_id: str | None, data: dict[str, Any]) -> None:
        frame = _envelope("event", session_id, data)
        # Buffered before the send attempt, not after — the buffer *is* the retry
        # mechanism now, so a failed/impossible send just leaves it there for the next
        # connection's post-`hello` flush rather than needing a separate drop-and-log
        # path.
        conn.unacked[frame["id"]] = frame
        ws = conn.ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps(frame))
        except (websockets.exceptions.ConnectionClosed, OSError) as exc:
            print(f"failed to send event ({data.get('type')}): {exc!r} — buffered for retry")

    manager = SessionManager(project_roots, emit, laptop_name=socket.gethostname())
    attempt = 0
    try:
        while True:
            connected_at = time.monotonic()
            exc: Exception | None = None
            try:
                await _run_once(config, manager, conn)
            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.InvalidHandshake,
                OSError,
            ) as caught:
                exc = caught

            # Judge by how long the connection actually stayed up, not by whether
            # `_run_once` raised — a server-side "clean" close (e.g. an idle-timeout
            # proxy) exits the receive loop without an exception just as readily as a
            # real abnormal drop, and both deserve backoff if they keep happening
            # fast. Only a connection that was genuinely stable resets the schedule
            # and reconnects immediately.
            if time.monotonic() - connected_at >= _STABLE_CONNECTION_SECONDS:
                attempt = 0
                continue

            delay = _backoff_delay(attempt)
            reason = f"connection lost ({exc!r})" if exc is not None else "connection closed"
            print(f"{reason} — retrying in {delay:.1f}s")
            attempt += 1
            await asyncio.sleep(delay)
    finally:
        # Ctrl+C, a real process shutdown, or a test simulating one (cancelling this
        # task) — either way, don't leave any `claude` subprocess resident. See
        # SessionManager.shutdown's docstring for why this matters for resume.
        await manager.shutdown()


async def _run_once(config: dict[str, Any], manager: SessionManager, conn: _Connection) -> None:
    broker_url = config["broker_url"]
    laptop_token = config["laptop_token"]
    project_roots = config.get("project_roots", ["."])

    async with websockets.connect(
        broker_url,
        additional_headers={"Authorization": f"Bearer {laptop_token}"},
        subprotocols=[websockets.Subprotocol("cta.v1")],
    ) as ws:
        conn.ws = ws
        seen_ids: set[str] = set()

        try:
            await ws.recv()  # `welcome` — nothing to act on in Phase 1
        except websockets.exceptions.ConnectionClosed as exc:
            if exc.rcvd is not None and exc.rcvd.code == _UNAUTHORIZED_CLOSE_CODE:
                raise LaptopUnpaired from exc
            raise

        await ws.send(
            json.dumps(
                _envelope(
                    "hello",
                    None,
                    {
                        "agent_version": __version__,
                        "laptop_name": socket.gethostname(),
                        "projects": projects.discover(project_roots),
                        # What this process still has alive — empty on a fresh start,
                        # everything if this is just a reconnect after a blip. See
                        # this module's docstring / docs/session-model.md § Resume.
                        "resumable_sessions": list(manager.sessions.keys()),
                    },
                )
            )
        )

        # Anything still unacked from a previous, briefly-broken connection — resend
        # now, same frame `id`s, mirroring the broker's own post-hello `_flush_unacked`.
        for frame in list(conn.unacked.values()):
            await ws.send(json.dumps(frame))

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
                    await _dispatch_cmd(manager, frame, ws)
                elif kind == "ping":
                    await ws.send(json.dumps(_envelope("heartbeat", None, {})))
                elif kind == "ack":
                    conn.unacked.pop(frame.get("data", {}).get("ref"), None)
        finally:
            heartbeat_task.cancel()
            conn.ws = None


async def _dispatch_cmd(
    manager: SessionManager, frame: dict[str, Any], ws: ClientConnection
) -> None:
    session_id = frame.get("session_id")
    try:
        await manager.dispatch(frame)
    except Exception as exc:  # noqa: BLE001 - isolate; one bad cmd shouldn't kill the socket
        # A resumed session's `cwd` could be gone, permissions could have changed —
        # anything's possible after time offline. One failure here must not crash the
        # whole connection and orphan every *other* live session on this laptop.
        print(f"cmd dispatch failed ({frame.get('data', {}).get('type')}): {exc!r}")
        try:
            await manager.emit(
                session_id,
                {"type": "error", "where": "dispatch", "message": str(exc), "recoverable": True},
            )
        except Exception:  # noqa: BLE001 - emit itself is already best-effort; never re-raise
            pass
    await ws.send(json.dumps(_envelope("ack", session_id, {"ref": frame.get("id")})))


async def _heartbeat_loop(ws: ClientConnection) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        await ws.send(json.dumps(_envelope("heartbeat", None, {})))
