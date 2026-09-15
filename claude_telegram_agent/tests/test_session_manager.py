"""session_manager.py's `decision_answer` handling — resuming a parked tool call,
whether or not the session was still live in `self.sessions`."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from claude_telegram_agent import events, permissions, session_manager


class _FakeClaudeSession:
    def __init__(self) -> None:
        self.connect_calls: list[dict[str, Any]] = []
        self.disconnected = False
        self.modes_set: list[str] = []
        self.interrupted = False

    async def connect(
        self, cwd: str, mode: str, resume: str | None = None, pre_tool_use_hook: Any = None
    ) -> None:
        self.connect_calls.append({"cwd": cwd, "mode": mode, "resume": resume})

    async def disconnect(self) -> None:
        self.disconnected = True

    async def set_permission_mode(self, mode: str) -> None:
        self.modes_set.append(mode)

    async def interrupt(self) -> None:
        self.interrupted = True

    def __aiter__(self):
        async def _empty():
            return
            yield  # pragma: no cover - never reached, makes this an async generator

        return _empty()


async def _emit(_session_id, _data) -> None:
    return None


def _manager() -> session_manager.SessionManager:
    return session_manager.SessionManager(project_roots=["/root"], emit=_emit)


async def test_decision_answer_resumes_a_still_live_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager()
    live = _FakeClaudeSession()
    manager.sessions["sess1"] = live  # type: ignore[assignment]  # duck-typed fake
    registered: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        permissions, "register_answer", lambda token, answer: registered.append((token, answer))
    )

    await manager._decision_answer(
        "sess1",
        {
            "defer_token": "tu1",
            "cwd": "/proj",
            "claude_session_id": "claude-abc",
            "permission_mode": "default",
            "answer": {"kind": "permission", "choice": "allow"},
        },
    )
    await asyncio.sleep(0)  # let the spawned _pump task run to completion

    assert registered == [("tu1", {"kind": "permission", "choice": "allow"})]
    assert live.disconnected is True
    assert live.connect_calls == [{"cwd": "/proj", "mode": "default", "resume": "claude-abc"}]
    assert manager.sessions["sess1"] is live  # same session object, just reconnected


async def test_decision_answer_reconnects_a_dead_session(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager()
    created: list[_FakeClaudeSession] = []

    def _factory() -> _FakeClaudeSession:
        session = _FakeClaudeSession()
        created.append(session)
        return session

    monkeypatch.setattr(session_manager, "ClaudeSession", _factory)
    monkeypatch.setattr(permissions, "register_answer", lambda *_a, **_k: None)
    reset_calls: list[str] = []
    monkeypatch.setattr(events, "reset", lambda sid: reset_calls.append(sid))

    await manager._decision_answer(
        "sess2",
        {
            "defer_token": "tu2",
            "cwd": "/proj2",
            "claude_session_id": "claude-def",
            "permission_mode": "plan",
            "answer": {"kind": "plan_approval", "choice": "approve"},
        },
    )
    await asyncio.sleep(0)

    assert reset_calls == ["sess2"]
    assert len(created) == 1
    assert created[0].connect_calls == [{"cwd": "/proj2", "mode": "plan", "resume": "claude-def"}]
    assert manager.sessions["sess2"] is created[0]


async def test_dispatch_routes_decision_answer_by_type(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager()
    called: list[tuple[str, dict]] = []

    async def _fake_decision_answer(session_id: str, data: dict) -> None:
        called.append((session_id, data))

    monkeypatch.setattr(manager, "_decision_answer", _fake_decision_answer)

    await manager.dispatch(
        {
            "session_id": "sess3",
            "data": {"type": "decision_answer", "answer": {"kind": "permission"}},
        }
    )

    assert called == [("sess3", {"type": "decision_answer", "answer": {"kind": "permission"}})]


async def test_set_mode_dispatches_to_the_live_session() -> None:
    manager = _manager()
    live = _FakeClaudeSession()
    manager.sessions["sess1"] = live  # type: ignore[assignment]

    await manager.dispatch({"session_id": "sess1", "data": {"type": "set_mode", "mode": "plan"}})

    assert live.modes_set == ["plan"]


async def test_set_mode_with_no_active_session_emits_an_error() -> None:
    manager = _manager()
    emitted: list[tuple[str | None, dict]] = []

    async def _capture(session_id, data):
        emitted.append((session_id, data))

    manager._emit = _capture  # type: ignore[assignment]

    await manager.dispatch({"session_id": "sess1", "data": {"type": "set_mode", "mode": "plan"}})

    assert emitted == [("sess1", {"type": "error", "where": "set_mode",
                                   "message": "no active session", "recoverable": True})]


async def test_cancel_interrupts_the_live_session() -> None:
    manager = _manager()
    live = _FakeClaudeSession()
    manager.sessions["sess1"] = live  # type: ignore[assignment]

    await manager.dispatch({"session_id": "sess1", "data": {"type": "cancel"}})

    assert live.interrupted is True


async def test_end_session_disconnects_resets_and_drops_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager()
    live = _FakeClaudeSession()
    manager.sessions["sess1"] = live  # type: ignore[assignment]
    reset_calls: list[str] = []
    monkeypatch.setattr(events, "reset", lambda sid: reset_calls.append(sid))
    emitted: list[tuple[str | None, dict]] = []

    async def _capture(session_id, data):
        emitted.append((session_id, data))

    manager._emit = _capture  # type: ignore[assignment]

    await manager.dispatch({"session_id": "sess1", "data": {"type": "end_session"}})

    assert live.disconnected is True
    assert "sess1" not in manager.sessions
    assert reset_calls == ["sess1"]
    assert emitted == [("sess1", {"type": "session_status", "status": "ended"})]


async def test_clear_session_reconnects_fresh_without_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager()
    live = _FakeClaudeSession()
    manager.sessions["sess1"] = live  # type: ignore[assignment]
    reset_calls: list[str] = []
    monkeypatch.setattr(events, "reset", lambda sid: reset_calls.append(sid))

    await manager.dispatch(
        {
            "session_id": "sess1",
            "data": {"type": "clear_session", "cwd": "/proj", "permission_mode": "default"},
        }
    )
    await asyncio.sleep(0)

    assert live.disconnected is True
    assert reset_calls == ["sess1"]
    assert live.connect_calls == [{"cwd": "/proj", "mode": "default", "resume": None}]
    assert manager.sessions["sess1"] is live  # same session_id, same object, reconnected


async def test_new_session_with_claude_session_id_resumes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The broker sets `claude_session_id` on `new_session` only when resuming after a
    laptop reconnect/restart (`ws_hub.py::_handle_hello`) — `_new_session` is the same
    handler either way, so this must reconnect with history intact rather than opening
    blank. `_manager()`'s project root is `/root`, so `cwd` must live under it or the
    allowlist check (unrelated to this test) rejects it first."""
    manager = _manager()
    created: list[_FakeClaudeSession] = []

    def _factory() -> _FakeClaudeSession:
        session = _FakeClaudeSession()
        created.append(session)
        return session

    monkeypatch.setattr(session_manager, "ClaudeSession", _factory)

    await manager.dispatch(
        {
            "session_id": "sess-resumed",
            "data": {
                "type": "new_session",
                "cwd": "/root/proj",
                "permission_mode": "plan",
                "claude_session_id": "claude-old",
            },
        }
    )

    assert created[0].connect_calls == [
        {"cwd": "/root/proj", "mode": "plan", "resume": "claude-old"}
    ]
    assert manager.sessions["sess-resumed"] is created[0]


async def test_new_session_without_claude_session_id_does_not_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal `/new` never sets `claude_session_id` — must not accidentally resume."""
    manager = _manager()
    created: list[_FakeClaudeSession] = []

    def _factory() -> _FakeClaudeSession:
        session = _FakeClaudeSession()
        created.append(session)
        return session

    monkeypatch.setattr(session_manager, "ClaudeSession", _factory)

    await manager.dispatch(
        {"session_id": "sess-fresh", "data": {"type": "new_session", "cwd": "/root/proj"}}
    )

    assert created[0].connect_calls == [{"cwd": "/root/proj", "mode": "default", "resume": None}]


async def test_shutdown_disconnects_every_live_session_and_clears_the_dict() -> None:
    manager = _manager()
    live1, live2 = _FakeClaudeSession(), _FakeClaudeSession()
    manager.sessions["sess1"] = live1  # type: ignore[assignment]
    manager.sessions["sess2"] = live2  # type: ignore[assignment]

    await manager.shutdown()

    assert live1.disconnected is True
    assert live2.disconnected is True
    assert manager.sessions == {}
