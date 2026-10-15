"""daemon.py: the backoff curve, reconnect behavior, `SessionManager` persistence
across reconnects, and dispatch-failure isolation."""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from claude_telegram_agent import daemon


@pytest.mark.parametrize(
    ("attempt", "expected_base"),
    [
        (0, 1.0),
        (1, 2.0),
        (2, 5.0),
        (3, 15.0),
        (4, 30.0),
        (5, 30.0),  # past the schedule's end — holds at the cap, doesn't keep growing
        (100, 30.0),
    ],
)
def test_backoff_delay_follows_the_documented_schedule(attempt: int, expected_base: float) -> None:
    # ±20% jitter per docs/transport-protocol.md § Reconnect.
    delays = [daemon._backoff_delay(attempt) for _ in range(50)]
    assert all(expected_base * 0.8 <= d <= expected_base * 1.2 for d in delays)


async def test_run_reconnects_after_connection_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_run_once(config, manager, conn) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise websockets.exceptions.ConnectionClosed(None, None)
        raise SystemExit  # stop the loop once we've proven a retry happened

    monkeypatch.setattr(daemon, "_run_once", fake_run_once)
    monkeypatch.setattr(daemon, "_backoff_delay", lambda attempt: 0)

    with pytest.raises(SystemExit):
        await daemon.run({"broker_url": "ws://localhost", "laptop_token": "t"})

    assert calls == 2


async def test_run_reconnects_after_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_run_once(config, manager, conn) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionRefusedError("no route to host")
        raise SystemExit

    monkeypatch.setattr(daemon, "_run_once", fake_run_once)
    monkeypatch.setattr(daemon, "_backoff_delay", lambda attempt: 0)

    with pytest.raises(SystemExit):
        await daemon.run({"broker_url": "ws://localhost", "laptop_token": "t"})

    assert calls == 2


async def test_run_backs_off_on_a_fast_clean_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `_run_once` that returns *without raising* (the `websockets` client's receive
    loop exits this way on a normal server-side close, e.g. an idle-timeout proxy —
    not just on a real abnormal drop) must still back off if it keeps happening fast.
    Previously a clean exit reset `attempt` to 0 and reconnected with zero delay,
    turning a repeatedly-closing-cleanly connection into a tight reconnect loop."""
    calls = 0
    delays: list[int] = []

    async def fake_run_once(config, manager, conn) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise SystemExit
        return  # returns cleanly, near-instantly — no exception

    def fake_backoff_delay(attempt: int) -> float:
        delays.append(attempt)
        return 0

    monkeypatch.setattr(daemon, "_run_once", fake_run_once)
    monkeypatch.setattr(daemon, "_backoff_delay", fake_backoff_delay)
    # nothing in this test is ever "stable"
    monkeypatch.setattr(daemon, "_STABLE_CONNECTION_SECONDS", 3600.0)

    with pytest.raises(SystemExit):
        await daemon.run({"broker_url": "ws://localhost", "laptop_token": "t"})

    assert calls == 3
    assert delays == [0, 1]  # attempt increments on each fast clean exit, never resets


async def test_run_resets_backoff_after_a_stable_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection that stays up past `_STABLE_CONNECTION_SECONDS` resets the backoff
    schedule — whether it then closes cleanly or drops via an exception — and
    reconnects immediately, same as before this fix for a genuinely healthy
    connection."""
    calls = 0
    delays: list[int] = []

    async def fake_run_once(config, manager, conn) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise websockets.exceptions.ConnectionClosed(None, None)  # fails instantly
        if calls == 2:
            await asyncio.sleep(0.05)  # stays up past the (tiny) stability threshold
            return
        raise SystemExit

    def fake_backoff_delay(attempt: int) -> float:
        delays.append(attempt)
        return 0

    monkeypatch.setattr(daemon, "_run_once", fake_run_once)
    monkeypatch.setattr(daemon, "_backoff_delay", fake_backoff_delay)
    monkeypatch.setattr(daemon, "_STABLE_CONNECTION_SECONDS", 0.02)

    with pytest.raises(SystemExit):
        await daemon.run({"broker_url": "ws://localhost", "laptop_token": "t"})

    assert calls == 3
    assert delays == [0]  # only the first, near-instant failure backed off


async def test_run_passes_the_same_manager_across_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare reconnect must not lose in-memory session state — `run()` builds one
    `SessionManager` up front and reuses it across every `_run_once` attempt."""
    seen_managers = []

    async def fake_run_once(config, manager, conn) -> None:
        seen_managers.append(manager)
        if len(seen_managers) == 1:
            raise websockets.exceptions.ConnectionClosed(None, None)
        raise SystemExit

    monkeypatch.setattr(daemon, "_run_once", fake_run_once)
    monkeypatch.setattr(daemon, "_backoff_delay", lambda attempt: 0)

    with pytest.raises(SystemExit):
        await daemon.run({"broker_url": "ws://localhost", "laptop_token": "t"})

    assert len(seen_managers) == 2
    assert seen_managers[0] is seen_managers[1]


async def test_emit_buffers_events_while_disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    """`conn.ws is None` (no live socket right now) must not raise — and, since Phase 7,
    the event isn't just dropped either: it's buffered in `conn.unacked` for the next
    connection's post-`hello` flush (this module's docstring)."""
    captured_emit = {}
    captured_conn = {}

    class _FakeManager:
        sessions: dict = {}

        async def shutdown(self) -> None:
            pass

    def fake_session_manager(project_roots, emit, laptop_name=""):
        captured_emit["emit"] = emit
        return _FakeManager()

    monkeypatch.setattr(daemon, "SessionManager", fake_session_manager)

    async def fake_run_once(config, manager, conn) -> None:
        captured_conn["conn"] = conn
        raise SystemExit

    monkeypatch.setattr(daemon, "_run_once", fake_run_once)

    with pytest.raises(SystemExit):
        await daemon.run({"broker_url": "ws://localhost", "laptop_token": "t"})

    # conn.ws was never set (no successful connect in this fake run) — emit must be a
    # silent no-op, not an exception, and the event must land in unacked rather than
    # vanish.
    await captured_emit["emit"]("sess1", {"type": "assistant_text", "text": "hi"})

    buffered = list(captured_conn["conn"].unacked.values())
    assert len(buffered) == 1
    assert buffered[0]["data"] == {"type": "assistant_text", "text": "hi"}


class _FakeWebSocket:
    """Duck-typed stand-in for `websockets.asyncio.client.ClientConnection`, just
    enough of it for `_run_once`: an initial `welcome` on `.recv()`, then whatever
    frames `incoming` holds via async iteration, recording everything sent."""

    def __init__(self, incoming: list[dict] | None = None) -> None:
        self.sent: list[str] = []
        self._incoming = incoming or []

    async def recv(self) -> str:
        return json.dumps({"kind": "welcome"})

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    def __aiter__(self):
        async def _gen():
            for frame in self._incoming:
                yield json.dumps(frame)

        return _gen()


class _FakeConnectCtx:
    def __init__(self, ws: _FakeWebSocket) -> None:
        self._ws = ws

    async def __aenter__(self) -> _FakeWebSocket:
        return self._ws

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _patch_connect(monkeypatch: pytest.MonkeyPatch, ws: _FakeWebSocket) -> None:
    monkeypatch.setattr(daemon.websockets, "connect", lambda *a, **k: _FakeConnectCtx(ws))
    monkeypatch.setattr(daemon.projects, "discover", lambda roots: [])


class _FakeManagerNoOp:
    sessions: dict = {}

    async def dispatch(self, frame: dict) -> None:
        pass

    async def emit(self, session_id: str | None, data: dict) -> None:
        pass


async def test_run_once_flushes_unacked_events_after_hello(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame buffered while briefly disconnected (`conn.unacked`) must be resent
    right after the next connection's `hello`, mirroring the broker's own post-hello
    `_flush_unacked` — same frame id, safe under the broker's `_seen_ids` dedupe."""
    conn = daemon._Connection()
    stale_frame = {
        "v": 1,
        "id": "stale-1",
        "ts": 0,
        "session_id": "sess1",
        "kind": "event",
        "data": {"type": "assistant_text", "text": "missed"},
    }
    conn.unacked["stale-1"] = stale_frame

    fake_ws = _FakeWebSocket(incoming=[])
    _patch_connect(monkeypatch, fake_ws)

    await daemon._run_once(
        {"broker_url": "ws://x", "laptop_token": "t"}, _FakeManagerNoOp(), conn  # type: ignore[arg-type]
    )

    sent_frames = [json.loads(s) for s in fake_ws.sent]
    assert any(f.get("id") == "stale-1" for f in sent_frames)


async def test_ack_frame_clears_the_matching_unacked_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = daemon._Connection()
    conn.unacked["stale-1"] = {
        "v": 1,
        "id": "stale-1",
        "ts": 0,
        "session_id": "s1",
        "kind": "event",
        "data": {},
    }
    ack_frame = {
        "v": 1,
        "id": "ack-1",
        "ts": 0,
        "session_id": "s1",
        "kind": "ack",
        "data": {"ref": "stale-1"},
    }
    fake_ws = _FakeWebSocket(incoming=[ack_frame])
    _patch_connect(monkeypatch, fake_ws)

    await daemon._run_once(
        {"broker_url": "ws://x", "laptop_token": "t"}, _FakeManagerNoOp(), conn  # type: ignore[arg-type]
    )

    assert "stale-1" not in conn.unacked


async def test_duplicate_incoming_frame_id_is_processed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[dict] = []

    class _FakeManager:
        sessions: dict = {}

        async def dispatch(self, frame: dict) -> None:
            dispatched.append(frame)

        async def emit(self, session_id, data) -> None:
            pass

    cmd_frame = {
        "v": 1,
        "id": "dupe-1",
        "ts": 0,
        "session_id": "s1",
        "kind": "cmd",
        "data": {"type": "set_mode", "mode": "plan"},
    }
    # Identical frame id, twice — the second must be a no-op (dedup).
    fake_ws = _FakeWebSocket(incoming=[cmd_frame, dict(cmd_frame)])
    _patch_connect(monkeypatch, fake_ws)

    await daemon._run_once(
        {"broker_url": "ws://x", "laptop_token": "t"},
        _FakeManager(),  # type: ignore[arg-type]
        daemon._Connection(),
    )

    assert len(dispatched) == 1


async def test_run_shuts_down_the_manager_on_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever caused `run()` to exit (cancellation, or — as here — an unhandled
    exception), it must still disconnect every live session first. Without this, a
    `claude` subprocess outlives the process and blocks a later resume attempt on the
    same session (see `SessionManager.shutdown`'s docstring)."""
    shutdown_calls = 0

    class _FakeManager:
        sessions: dict = {}

        async def shutdown(self) -> None:
            nonlocal shutdown_calls
            shutdown_calls += 1

    monkeypatch.setattr(
        daemon, "SessionManager", lambda project_roots, emit, laptop_name="": _FakeManager()
    )

    async def fake_run_once(config, manager, conn) -> None:
        raise SystemExit

    monkeypatch.setattr(daemon, "_run_once", fake_run_once)

    with pytest.raises(SystemExit):
        await daemon.run({"broker_url": "ws://localhost", "laptop_token": "t"})

    assert shutdown_calls == 1


async def test_dispatch_cmd_isolates_a_failure_and_still_acks() -> None:
    """One bad cmd (e.g. a resumed session's `cwd` no longer exists) must not raise
    out of `_dispatch_cmd` — the caller (`_run_once`'s frame loop) would otherwise
    crash the whole connection over it."""

    class _FakeManager:
        async def dispatch(self, frame):
            raise RuntimeError("boom")

        async def emit(self, session_id, data):
            emitted.append((session_id, data))

    class _FakeWs:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, raw: str) -> None:
            self.sent.append(raw)

    emitted: list[tuple] = []
    ws = _FakeWs()
    frame = {"id": "f1", "session_id": "sess1", "kind": "cmd", "data": {"type": "new_session"}}

    await daemon._dispatch_cmd(_FakeManager(), frame, ws)  # type: ignore[arg-type]  # duck-typed fakes

    assert emitted == [
        ("sess1", {"type": "error", "where": "dispatch", "message": "boom", "recoverable": True})
    ]
    assert len(ws.sent) == 1  # the ack still went out despite the failure
    assert json.loads(ws.sent[0])["kind"] == "ack"
    assert json.loads(ws.sent[0])["data"] == {"ref": "f1"}


class _ClosingSocket:
    """A socket the broker closes before `welcome` — what an unpaired laptop gets."""

    def __init__(self, code: int) -> None:
        self.code = code

    async def __aenter__(self) -> _ClosingSocket:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def recv(self) -> str:
        from websockets.frames import Close

        raise websockets.exceptions.ConnectionClosedError(Close(self.code, ""), None)


@pytest.mark.parametrize(("code", "expected"), [(4401, daemon.LaptopUnpaired), (1011, None)])
async def test_run_once_flags_a_4401_before_welcome_as_unpaired(
    monkeypatch: pytest.MonkeyPatch, code: int, expected: type[Exception] | None
) -> None:
    monkeypatch.setattr(daemon.websockets, "connect", lambda *a, **kw: _ClosingSocket(code))
    config = {"broker_url": "ws://localhost", "laptop_token": "t"}

    if expected is None:  # any other close stays a retryable drop
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await daemon._run_once(config, None, daemon._Connection())  # type: ignore[arg-type]
    else:
        with pytest.raises(expected):
            await daemon._run_once(config, None, daemon._Connection())  # type: ignore[arg-type]


async def test_run_stops_retrying_once_unpaired(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_run_once(config, manager, conn) -> None:
        nonlocal calls
        calls += 1
        raise daemon.LaptopUnpaired

    monkeypatch.setattr(daemon, "_run_once", fake_run_once)

    with pytest.raises(daemon.LaptopUnpaired):
        await daemon.run({"broker_url": "ws://localhost", "laptop_token": "t"})
    assert calls == 1
