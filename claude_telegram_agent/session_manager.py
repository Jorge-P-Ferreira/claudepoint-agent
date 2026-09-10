"""Session lifecycle on the laptop. Model: ../../docs/session-model.md.

Phase 1 scope: `new_session` and `prompt` only (matches prompt.md's walking skeleton).
`decision_answer`/`set_mode`/`cancel`/`clear_session`/`end_session` (Phase 2/3) and
`list_projects`/`create_project`/`add_project` (Phase 6) are added when those phases
land — `dispatch` already routes by `data.type` so adding a handler is additive.
`remove_project` never reaches here: the broker deregisters `projects/{id}` directly
since it never touches the laptop filesystem.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from . import events, projects
from .claude_session import ClaudeSession

EmitFn = Callable[[str | None, dict[str, Any]], Awaitable[None]]


class SessionManager:
    def __init__(self, project_roots: list[str], emit: EmitFn) -> None:
        self.sessions: dict[str, ClaudeSession] = {}
        self._project_roots = project_roots
        self._emit = emit

    async def dispatch(self, frame: dict[str, Any]) -> None:
        data = frame.get("data", {})
        cmd_type = data.get("type")
        session_id = frame.get("session_id")

        if cmd_type == "new_session" and session_id:
            await self._new_session(session_id, data)
        elif cmd_type == "prompt" and session_id:
            await self._prompt(session_id, data)
        # other cmd types land with the phases that need them (see module docstring)

    async def _new_session(self, session_id: str, data: dict[str, Any]) -> None:
        cwd = data["cwd"]
        mode = data.get("permission_mode", "default")

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
        await session.connect(cwd=cwd, mode=mode)
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
        await session.query(data["text"])

    async def _pump(self, session_id: str, session: ClaudeSession) -> None:
        async for message in session:
            for event_data in events.to_frames(message, session_id):
                await self._emit(session_id, event_data)
