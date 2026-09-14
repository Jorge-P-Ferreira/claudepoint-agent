"""ClaudeSDKClient wrapper — one per session. Mapping: ../../docs/claude-driver.md.

Stays a thin wrapper around the SDK client; `session_manager.SessionManager` owns
turn-queueing (`enqueue_prompt`, Phase 3), decision-answer routing
(`permissions.register_answer` + reconnecting to resume, Phase 2), and slot lifecycle
(`close(keep_slot=...)`) on top of this.

Phase 2: `connect()` takes a `PreToolUse` hook (built by `permissions.py`, which owns
all decision classification/resume logic) instead of Phase 1's plain `can_use_tool`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher, SystemMessage


class ClaudeSession:
    def __init__(self) -> None:
        self._client: ClaudeSDKClient | None = None
        self.claude_session_id: str | None = None

    async def connect(
        self,
        cwd: str,
        mode: str,
        resume: str | None = None,
        pre_tool_use_hook: HookMatcher | None = None,
    ) -> None:
        options = ClaudeAgentOptions(
            cwd=cwd,
            permission_mode=mode,  # type: ignore[arg-type]
            resume=resume,
            hooks={"PreToolUse": [pre_tool_use_hook]} if pre_tool_use_hook is not None else None,
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
