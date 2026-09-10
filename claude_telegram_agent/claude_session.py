"""ClaudeSDKClient wrapper — one per session. Mapping: ../../docs/claude-driver.md.

Stays a thin wrapper around the SDK client; `session_manager.SessionManager` owns
turn-queueing (`enqueue_prompt`), decision resolution (`resolve_decision`, Phase 2),
and slot lifecycle (`close(keep_slot=...)`) on top of this.

`permissions.defer_hook`'s `PreToolUse` wiring is Phase 2 (see permissions.py) — Phase
1 connects with no hooks, `can_use_tool` only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, SystemMessage

from . import permissions


class ClaudeSession:
    def __init__(self) -> None:
        self._client: ClaudeSDKClient | None = None
        self.claude_session_id: str | None = None

    async def connect(self, cwd: str, mode: str, resume: str | None = None) -> None:
        options = ClaudeAgentOptions(
            cwd=cwd,
            permission_mode=mode,  # type: ignore[arg-type]
            can_use_tool=permissions.can_use_tool,
            resume=resume,
        )
        self._client = ClaudeSDKClient(options=options)
        await self._client.connect()  # no initial prompt — keeps the input stream open

    def _require_client(self) -> ClaudeSDKClient:
        if self._client is None:
            raise RuntimeError("ClaudeSession.connect() must be called first")
        return self._client

    async def query(self, text: str) -> None:
        await self._require_client().query(text)

    async def set_permission_mode(self, mode: str) -> None:
        await self._require_client().set_permission_mode(mode)  # type: ignore[arg-type]

    async def interrupt(self) -> None:
        await self._require_client().interrupt()

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def __aiter__(self) -> AsyncIterator[Any]:
        async for message in self._require_client().receive_messages():
            if isinstance(message, SystemMessage) and message.subtype == "init":
                self.claude_session_id = message.data.get("session_id")
            yield message
