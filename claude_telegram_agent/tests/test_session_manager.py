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

    async def connect(
        self, cwd: str, mode: str, resume: str | None = None, pre_tool_use_hook: Any = None
    ) -> None:
        self.connect_calls.append({"cwd": cwd, "mode": mode, "resume": resume})

    async def disconnect(self) -> None:
        self.disconnected = True

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
