"""daemon.py: the backoff curve, reconnect behavior, `SessionManager` persistence
across reconnects, and dispatch-failure isolation."""

from __future__ import annotations

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


async def test_emit_drops_events_while_disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    """`conn.ws is None` (no live socket right now) must not raise — the event is
    just dropped, matching this module's best-effort philosophy elsewhere."""
    captured_emit = {}

    class _FakeManager:
        sessions: dict = {}

        async def shutdown(self) -> None:
            pass

    def fake_session_manager(project_roots, emit):
        captured_emit["emit"] = emit
        return _FakeManager()

    monkeypatch.setattr(daemon, "SessionManager", fake_session_manager)

    async def fake_run_once(config, manager, conn) -> None:
        raise SystemExit

    monkeypatch.setattr(daemon, "_run_once", fake_run_once)

    with pytest.raises(SystemExit):
        await daemon.run({"broker_url": "ws://localhost", "laptop_token": "t"})

    # conn.ws was never set (no successful connect in this fake run) — emit must be a
    # silent no-op, not an exception.
    await captured_emit["emit"]("sess1", {"type": "assistant_text", "text": "hi"})


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

    monkeypatch.setattr(daemon, "SessionManager", lambda project_roots, emit: _FakeManager())

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
