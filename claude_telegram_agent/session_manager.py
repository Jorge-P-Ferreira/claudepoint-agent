"""Session lifecycle on the laptop. Model: ../../docs/session-model.md.

Phase 1: `new_session` and `prompt`. Phase 2 adds `decision_answer` — resuming a
parked tool call, possibly after this process itself restarted while the phone hadn't
answered yet (see `permissions.py`'s module docstring for the open question on the
exact SDK-level resume trigger). Phase 3 (this file, now) adds `set_mode`/`cancel`/
`end_session`/`clear_session`, each a thin composition of existing `ClaudeSession`
methods (`set_permission_mode`/`interrupt`/`disconnect`) — no SDK gap. `clear_session`
disconnects then reconnects fresh (no `resume`) on the *same* `session_id`, mirroring
`_decision_answer`'s reconnect path; the SDK's own `init` on that reconnect is what
actually changes `claude_session_id` (`events.to_frames` → `session_status` →
`registry.apply_session_status`), while `session_id`/`telegram_chat_id` never move.
`_new_session` also takes an optional `claude_session_id` (`data.get(...)`, `None` for
a normal `/new`) — the broker sets it when *resuming* a session after this process
restarted (`daemon.py`'s module docstring, `docs/session-model.md` § Resume), so the
same handler either opens a blank client or reconnects with full history depending on
who's calling it. `create_project`/`add_project` (Phase 6, this file, now) are
laptop-scoped — no `session_id` — and report back via a `project_result` event rather
than a return value, since the broker's dispatching HTTP route returns immediately
(`shared/data-model.md`'s "agent-side check happens before the broker ever writes the
doc" rule). `remove_project` never reaches here: the broker deregisters
`projects/{id}` directly since it never touches the laptop filesystem.

Phase 7 (this file, now) implements the turn-queueing `claude_session.py`'s own module
docstring has always named as this class's job: `_prompt` no longer calls
`session.query()` unconditionally — a session already mid-turn (`self._busy`) has the
new text appended to whatever's already queued (`self._queued_prompt`, blank-line
separated — a second prompt sent before the first even started doesn't replace or get
rejected, it joins on) rather than being written onto the SDK's already-open stdin
stream with no coordination, which was untested, undefined-interleaving behavior
before this. `_pump` watches for `ResultMessage` (turn end) to flip `_busy` back off
and drain any queued text. `_cancel` pops `_queued_prompt` *before* interrupting the
live turn — per `shared/commands.md`'s contract ("`cancel` preceding a queued prompt
drops it rather than sending it after"), this is the entire "cancel preempts the
queue" mechanism. Both dicts are cleared alongside `self.sessions[...]` at every point
a session's `ClaudeSession` gets replaced or torn down, so stale busy/queue state can
never leak across a reconnect.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import ResultMessage

from . import events, permissions, projects
from .claude_session import ClaudeSession

EmitFn = Callable[[str | None, dict[str, Any]], Awaitable[None]]


class SessionManager:
    def __init__(self, project_roots: list[str], emit: EmitFn, laptop_name: str = "") -> None:
        self.sessions: dict[str, ClaudeSession] = {}
        self._busy: dict[str, bool] = {}
        self._queued_prompt: dict[str, str | None] = {}
        self._project_roots = project_roots
        self._emit = emit
        self.emit = emit  # public alias — daemon.py's dispatch-failure handling uses this
        self._laptop_name = laptop_name

    async def dispatch(self, frame: dict[str, Any]) -> None:
        data = frame.get("data", {})
        cmd_type = data.get("type")
        session_id = frame.get("session_id")

        if cmd_type == "new_session" and session_id:
            await self._new_session(session_id, data)
        elif cmd_type == "prompt" and session_id:
            await self._prompt(session_id, data)
        elif cmd_type == "decision_answer" and session_id:
            await self._decision_answer(session_id, data)
        elif cmd_type == "set_mode" and session_id:
            await self._set_mode(session_id, data)
        elif cmd_type == "cancel" and session_id:
            await self._cancel(session_id)
        elif cmd_type == "end_session" and session_id:
            await self._end_session(session_id)
        elif cmd_type == "clear_session" and session_id:
            await self._clear_session(session_id, data)
        elif cmd_type == "create_project":
            await self._create_project(data)
        elif cmd_type == "add_project":
            await self._add_project(data)
        # other cmd types land with the phases that need them (see module docstring)

    async def _new_session(self, session_id: str, data: dict[str, Any]) -> None:
        cwd = data["cwd"]
        mode = data.get("permission_mode", "default")
        # Set only when the broker is resuming this session after a laptop
        # reconnect/restart (`ws_hub.py::_handle_hello`, `docs/session-model.md` §
        # Resume) — absent, and so `None`, for a normal `/new`.
        resume = data.get("claude_session_id")

        if not projects.within_allowlist(cwd, self._project_roots):
            await self._emit(
                session_id,
                {
                    "type": "error",
                    "where": "new_session",
                    "message": f"{cwd} is outside the configured project roots",
                    "recoverable": False,
                },
            )
            return

        await self._emit(session_id, {"type": "session_status", "status": "starting"})

        session = ClaudeSession()
        self.sessions[session_id] = session
        hook = permissions.make_pre_tool_use_hook(self._emit, session_id)
        await session.connect(
            cwd=cwd,
            mode=mode,
            resume=resume,
            pre_tool_use_hook=hook,
            can_use_tool=permissions.can_use_tool,
        )
        asyncio.create_task(self._pump(session_id, session))

    async def _prompt(self, session_id: str, data: dict[str, Any]) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            await self._emit(
                session_id,
                {
                    "type": "error",
                    "where": "prompt",
                    "message": "no active session",
                    "recoverable": True,
                },
            )
            return

        if self._busy.get(session_id):
            # A turn is already running — append rather than write a second `query()`
            # onto the same open stdin stream (undefined interleaving) or drop/reject
            # the new text. `_on_turn_end` sends this once the current turn's
            # `ResultMessage` arrives.
            existing = self._queued_prompt.get(session_id)
            self._queued_prompt[session_id] = (
                f"{existing}\n\n{data['text']}" if existing else data["text"]
            )
            return

        self._busy[session_id] = True
        await session.query(data["text"])

    async def _on_turn_end(self, session_id: str, session: ClaudeSession) -> None:
        self._busy[session_id] = False
        queued = self._queued_prompt.pop(session_id, None)
        if queued is None:
            return
        # Identity check: guards against a reconnect (decision answer, clear, end)
        # having replaced or removed this session_id's entry while the turn that just
        # ended was still running — a queued prompt for the *old* ClaudeSession must
        # not be sent to whatever now occupies its slot.
        if self.sessions.get(session_id) is not session:
            return
        self._busy[session_id] = True
        await session.query(queued)

    async def _decision_answer(self, session_id: str, data: dict[str, Any]) -> None:
        """Resolve a parked `PreToolUse` defer. `permissions.register_answer` makes the
        answer available *before* the hook can possibly re-fire, then a fresh connect
        (with `resume=`) re-attaches to the session — whether or not it was still live
        in `self.sessions`, unifying the "phone answered quickly" and "this process
        restarted first" cases into one path."""
        answer = data.get("answer", {})
        defer_token = data.get("defer_token", "")
        permissions.register_answer(defer_token, answer)
        # Whatever turn/queue state this session_id had belonged to the connection
        # being replaced below — never carry it into the reconnected one.
        self._busy.pop(session_id, None)
        self._queued_prompt.pop(session_id, None)

        session = self.sessions.get(session_id)
        if session is not None:
            await session.disconnect()
        else:
            session = ClaudeSession()
            self.sessions[session_id] = session
            events.reset(session_id)  # fresh turn-bubble state for the resumed session

        mode = data.get("permission_mode", "default")
        if answer.get("kind") == "plan_approval" and answer.get("choice") == "approve":
            # Per the Agent SDK's own permission-evaluation rules, `plan` mode routes
            # every file-edit/shell-write tool through the approval callback regardless
            # of any earlier allow — approving the `ExitPlanMode` call itself does not
            # exit plan mode. Without switching modes here, "Approve & run" would loop
            # Claude straight back into proposing another plan, since it can never act
            # on the one just approved (confirmed live, 2026-09-16).
            mode = "default"

        hook = permissions.make_pre_tool_use_hook(self._emit, session_id)
        await session.connect(
            cwd=data["cwd"],
            mode=mode,
            resume=data.get("claude_session_id"),
            pre_tool_use_hook=hook,
            can_use_tool=permissions.can_use_tool,
        )
        asyncio.create_task(self._pump(session_id, session))

    async def _set_mode(self, session_id: str, data: dict[str, Any]) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            await self._emit(
                session_id,
                {
                    "type": "error",
                    "where": "set_mode",
                    "message": "no active session",
                    "recoverable": True,
                },
            )
            return
        await session.set_permission_mode(data["mode"])

    async def _cancel(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            await self._emit(
                session_id,
                {
                    "type": "error",
                    "where": "cancel",
                    "message": "no active session",
                    "recoverable": True,
                },
            )
            return
        # Drop any not-yet-sent queued prompt *before* interrupting the live turn —
        # this is the entire "cancel preempts the queue" mechanism
        # (`shared/commands.md`'s `prompt`/`cancel` contract).
        self._queued_prompt.pop(session_id, None)
        await session.interrupt()

    async def _end_session(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        self._busy.pop(session_id, None)
        self._queued_prompt.pop(session_id, None)
        permissions.forget_session(session_id)
        if session is not None:
            await session.disconnect()
        events.reset(session_id)
        await self._emit(session_id, {"type": "session_status", "status": "ended"})

    async def _clear_session(self, session_id: str, data: dict[str, Any]) -> None:
        """Drop `claude_session_id`, reconnect fresh (no `resume`) on the *same*
        `session_id` — same chat binding, blank history (`shared/commands.md`'s
        `clear_session` row). Mirrors `_decision_answer`'s "still live vs. dead"
        reconnect split."""
        self._busy.pop(session_id, None)
        self._queued_prompt.pop(session_id, None)
        session = self.sessions.get(session_id)
        if session is not None:
            await session.disconnect()
        else:
            session = ClaudeSession()
            self.sessions[session_id] = session
        events.reset(session_id)

        hook = permissions.make_pre_tool_use_hook(self._emit, session_id)
        await session.connect(
            cwd=data["cwd"],
            mode=data.get("permission_mode", "default"),
            pre_tool_use_hook=hook,
            can_use_tool=permissions.can_use_tool,
        )
        asyncio.create_task(self._pump(session_id, session))

    async def _create_project(self, data: dict[str, Any]) -> None:
        chat_id = data["chat_id"]
        try:
            project = projects.create_project(
                data["name"], data.get("git_init", False), self._project_roots
            )
            await self._emit(
                None,
                {
                    "type": "project_result",
                    "chat_id": chat_id,
                    "action": "create",
                    "ok": True,
                    **project,
                },
            )
        except projects.ProjectError as exc:
            await self._emit(
                None,
                {
                    "type": "project_result",
                    "chat_id": chat_id,
                    "action": "create",
                    "ok": False,
                    "message": str(exc),
                },
            )

    async def _add_project(self, data: dict[str, Any]) -> None:
        chat_id = data["chat_id"]
        try:
            project = projects.add_project(data["path"], self._project_roots, self._laptop_name)
            await self._emit(
                None,
                {
                    "type": "project_result",
                    "chat_id": chat_id,
                    "action": "add",
                    "ok": True,
                    **project,
                },
            )
        except projects.ProjectError as exc:
            await self._emit(
                None,
                {
                    "type": "project_result",
                    "chat_id": chat_id,
                    "action": "add",
                    "ok": False,
                    "message": str(exc),
                },
            )

    async def _pump(self, session_id: str, session: ClaudeSession) -> None:
        async for message in session:
            for event_data in events.to_frames(message, session_id):
                await self._emit(session_id, event_data)
            if isinstance(message, ResultMessage):
                await self._on_turn_end(session_id, session)

    async def shutdown(self) -> None:
        """Disconnect every live session's `ClaudeSDKClient` — `daemon.py::run` calls
        this whenever it exits (Ctrl+C, a real process shutdown), so a `claude`
        subprocess never outlives this process. Without this, restarting the agent by
        hand (as opposed to the whole laptop losing power, which kills every process
        at once) leaves the old subprocess resident, holding its session's transcript
        — and a fresh process's resume-from-restart attempt on that same session
        (`docs/session-model.md` § Resume) then hangs contending with it rather than
        reconnecting cleanly."""
        for session in list(self.sessions.values()):
            await session.disconnect()
        self.sessions.clear()
