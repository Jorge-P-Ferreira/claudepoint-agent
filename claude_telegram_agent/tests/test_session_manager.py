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
        self.can_use_tool_calls: list[Any] = []
        self.disconnected = False
        self.modes_set: list[str] = []
        self.interrupted = False

    async def connect(
        self,
        cwd: str,
        mode: str,
        resume: str | None = None,
        pre_tool_use_hook: Any = None,
        can_use_tool: Any = None,
    ) -> None:
        self.connect_calls.append({"cwd": cwd, "mode": mode, "resume": resume})
        self.can_use_tool_calls.append(can_use_tool)

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


async def test_decision_answer_approving_a_plan_switches_out_of_plan_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per the Agent SDK docs, `plan` mode routes every file-edit/shell-write tool
    through the approval callback regardless of any earlier allow — approving
    `ExitPlanMode` itself doesn't exit plan mode. Confirmed live, 2026-09-16: without
    this, "Approve & run" loops Claude back into proposing another plan forever."""
    manager = _manager()
    live = _FakeClaudeSession()
    manager.sessions["sess1"] = live  # type: ignore[assignment]
    monkeypatch.setattr(permissions, "register_answer", lambda *_a, **_k: None)

    await manager._decision_answer(
        "sess1",
        {
            "defer_token": "tu1",
            "cwd": "/proj",
            "claude_session_id": "claude-abc",
            "permission_mode": "plan",
            "answer": {"kind": "plan_approval", "choice": "approve"},
        },
    )
    await asyncio.sleep(0)

    assert live.connect_calls == [{"cwd": "/proj", "mode": "default", "resume": "claude-abc"}]


async def test_decision_answer_keeping_a_plan_stays_in_plan_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Keep planning" must not switch modes — only an actual approval does."""
    manager = _manager()
    live = _FakeClaudeSession()
    manager.sessions["sess1"] = live  # type: ignore[assignment]
    monkeypatch.setattr(permissions, "register_answer", lambda *_a, **_k: None)

    await manager._decision_answer(
        "sess1",
        {
            "defer_token": "tu1",
            "cwd": "/proj",
            "claude_session_id": "claude-abc",
            "permission_mode": "plan",
            "answer": {"kind": "plan_approval", "choice": "keep", "message": "add detail"},
        },
    )
    await asyncio.sleep(0)

    assert live.connect_calls == [{"cwd": "/proj", "mode": "plan", "resume": "claude-abc"}]


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
    # mode is "default", not the deferred "plan" — approving a plan must exit plan mode
    # (test_decision_answer_approving_a_plan_switches_out_of_plan_mode covers this
    # directly; this test's real subject is the dead-session reconnect path).
    assert created[0].connect_calls == [
        {"cwd": "/proj2", "mode": "default", "resume": "claude-def"}
    ]
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


async def test_new_session_registers_can_use_tool_to_unlock_ask_user_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headless Claude Code sessions hide `AskUserQuestion`/`ExitPlanMode` from the tool
    catalog entirely unless a `can_use_tool` callback is registered (confirmed live,
    2026-09-15 — see `docs/claude-driver.md` § "Deferring a decision"). Every
    `session.connect()` call site must pass `permissions.can_use_tool`, even though the
    `PreToolUse` hook always resolves the call first and this callback should never run."""
    manager = _manager()
    created: list[_FakeClaudeSession] = []

    def _factory() -> _FakeClaudeSession:
        session = _FakeClaudeSession()
        created.append(session)
        return session

    monkeypatch.setattr(session_manager, "ClaudeSession", _factory)

    await manager.dispatch(
        {"session_id": "sess-new", "data": {"type": "new_session", "cwd": "/root/proj"}}
    )

    assert created[0].can_use_tool_calls == [permissions.can_use_tool]


async def test_decision_answer_registers_can_use_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager()
    live = _FakeClaudeSession()
    manager.sessions["sess1"] = live  # type: ignore[assignment]
    monkeypatch.setattr(permissions, "register_answer", lambda *_a, **_k: None)

    await manager._decision_answer(
        "sess1",
        {
            "defer_token": "tu1",
            "cwd": "/proj",
            "claude_session_id": "claude-abc",
            "answer": {"kind": "permission", "choice": "allow"},
        },
    )
    await asyncio.sleep(0)

    assert live.can_use_tool_calls == [permissions.can_use_tool]


async def test_clear_session_registers_can_use_tool() -> None:
    manager = _manager()
    live = _FakeClaudeSession()
    manager.sessions["sess1"] = live  # type: ignore[assignment]

    await manager.dispatch(
        {
            "session_id": "sess1",
            "data": {"type": "clear_session", "cwd": "/proj", "permission_mode": "default"},
        }
    )
    await asyncio.sleep(0)

    assert live.can_use_tool_calls == [permissions.can_use_tool]


async def test_shutdown_disconnects_every_live_session_and_clears_the_dict() -> None:
    manager = _manager()
    live1, live2 = _FakeClaudeSession(), _FakeClaudeSession()
    manager.sessions["sess1"] = live1  # type: ignore[assignment]
    manager.sessions["sess2"] = live2  # type: ignore[assignment]

    await manager.shutdown()

    assert live1.disconnected is True
    assert live2.disconnected is True
    assert manager.sessions == {}


# ---- create_project / add_project (Phase 6) — laptop-scoped, no session_id --------


async def _emitting_manager(laptop_name: str = "jorge-xps"):
    emitted: list[tuple[str | None, dict]] = []

    async def _capture(session_id, data):
        emitted.append((session_id, data))

    manager = session_manager.SessionManager(
        project_roots=["/root"], emit=_capture, laptop_name=laptop_name
    )
    return manager, emitted


async def test_dispatch_create_project_emits_ok_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from claude_telegram_agent import projects

    manager, emitted = await _emitting_manager()
    def _fake_create(name, git_init, roots):
        return {"project_id": "p1", "path": "/root/widget", "name": "widget"}

    monkeypatch.setattr(projects, "create_project", _fake_create)

    await manager.dispatch(
        {
            "session_id": None,
            "data": {"type": "create_project", "chat_id": 42, "name": "widget", "git_init": True},
        }
    )

    assert emitted == [
        (
            None,
            {
                "type": "project_result",
                "chat_id": 42,
                "action": "create",
                "ok": True,
                "project_id": "p1",
                "path": "/root/widget",
                "name": "widget",
            },
        )
    ]


async def test_dispatch_create_project_emits_failure_on_project_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_telegram_agent import projects

    manager, emitted = await _emitting_manager()

    def _raise(name, git_init, roots):
        raise projects.ProjectError("'widget' already exists on this laptop.")

    monkeypatch.setattr(projects, "create_project", _raise)

    await manager.dispatch(
        {"session_id": None, "data": {"type": "create_project", "chat_id": 42, "name": "widget"}}
    )

    assert emitted == [
        (
            None,
            {
                "type": "project_result",
                "chat_id": 42,
                "action": "create",
                "ok": False,
                "message": "'widget' already exists on this laptop.",
            },
        )
    ]


async def test_dispatch_add_project_passes_laptop_name_and_emits_ok_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_telegram_agent import projects

    manager, emitted = await _emitting_manager(laptop_name="jorge-xps")
    calls: list[tuple[str, list[str], str]] = []

    def _fake_add(path, roots, laptop_name):
        calls.append((path, roots, laptop_name))
        return {"project_id": "p2", "path": path, "name": "legacy-app"}

    monkeypatch.setattr(projects, "add_project", _fake_add)

    await manager.dispatch(
        {"session_id": None, "data": {"type": "add_project", "chat_id": 7, "path": "/x/legacy-app"}}
    )

    assert calls == [("/x/legacy-app", ["/root"], "jorge-xps")]
    assert emitted == [
        (
            None,
            {
                "type": "project_result",
                "chat_id": 7,
                "action": "add",
                "ok": True,
                "project_id": "p2",
                "path": "/x/legacy-app",
                "name": "legacy-app",
            },
        )
    ]


async def test_dispatch_add_project_emits_failure_on_project_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_telegram_agent import projects

    manager, emitted = await _emitting_manager()

    def _raise(path, roots, laptop_name):
        raise projects.ProjectError("That path isn't under an allowed root on jorge-xps.")

    monkeypatch.setattr(projects, "add_project", _raise)

    await manager.dispatch(
        {"session_id": None, "data": {"type": "add_project", "chat_id": 7, "path": "/etc"}}
    )

    assert emitted == [
        (
            None,
            {
                "type": "project_result",
                "chat_id": 7,
                "action": "add",
                "ok": False,
                "message": "That path isn't under an allowed root on jorge-xps.",
            },
        )
    ]
