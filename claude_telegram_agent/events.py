"""SDK stream messages -> broker `event` frames. Target shapes: ../../shared/events.md.

`to_frames` returns the `data` payload for zero or more `event` frames (the caller,
`claude_session.py`, wraps each in the frame envelope with `session_id`). Turn-bubble
state (has this turn already started a text/thinking bubble) is tracked per
`session_id` at module level so multiple `AssistantMessage`s within one turn append
into the same Telegram message rather than starting a new one each time; call
`reset(session_id)` on session end/clear to drop that state.
"""

from __future__ import annotations

import uuid
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

_SUMMARY_LIMIT = 120

_turn_state: dict[str, dict[str, Any]] = {}


def _fresh_state() -> dict[str, Any]:
    return {"turn_id": uuid.uuid4().hex, "started_text": False, "started_thinking": False}


def _state(session_id: str) -> dict[str, Any]:
    return _turn_state.setdefault(session_id, _fresh_state())


def reset(session_id: str) -> None:
    _turn_state.pop(session_id, None)


def _summarize(value: Any, limit: int = _SUMMARY_LIMIT) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _content_frames(content: Any, state: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []  # a plain string is an echoed prompt, not new output
    frames: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, TextBlock):
            frames.append(
                {
                    "type": "assistant_text",
                    "text": block.text,
                    "delta": state["started_text"],
                    "turn_id": state["turn_id"],
                }
            )
            state["started_text"] = True
        elif isinstance(block, ThinkingBlock):
            frames.append(
                {
                    "type": "thinking",
                    "text": block.thinking,
                    "delta": state["started_thinking"],
                    "turn_id": state["turn_id"],
                }
            )
            state["started_thinking"] = True
        elif isinstance(block, ToolUseBlock):
            frames.append(
                {
                    "type": "tool_use",
                    "tool": block.name,
                    "input_summary": _summarize(block.input),
                    "tool_use_id": block.id,
                    "turn_id": state["turn_id"],
                }
            )
        elif isinstance(block, ToolResultBlock):
            frames.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "ok": not bool(block.is_error),
                    "output_summary": _summarize(block.content),
                }
            )
    return frames


def to_frames(sdk_message: Any, session_id: str) -> list[dict[str, Any]]:
    state = _state(session_id)

    if isinstance(sdk_message, SystemMessage):
        if sdk_message.subtype == "init":
            return [
                {
                    "type": "session_status",
                    "status": "live",
                    "claude_session_id": sdk_message.data.get("session_id"),
                    "cwd": sdk_message.data.get("cwd"),
                }
            ]
        return []

    if isinstance(sdk_message, AssistantMessage):
        frames = _content_frames(sdk_message.content, state)
        if sdk_message.error:
            frames.append(
                {
                    "type": "error",
                    "where": "assistant",
                    "message": sdk_message.error,
                    "recoverable": True,
                }
            )
        return frames

    if isinstance(sdk_message, UserMessage):
        return _content_frames(sdk_message.content, state)

    if isinstance(sdk_message, ResultMessage):
        frame = {
            "type": "result",
            "turn_id": state["turn_id"],
            "subtype": "error" if sdk_message.is_error else "success",
            "usage": sdk_message.usage,
            "text": sdk_message.result,
        }
        _turn_state[session_id] = _fresh_state()  # next turn starts a fresh bubble
        return [frame]

    return []
