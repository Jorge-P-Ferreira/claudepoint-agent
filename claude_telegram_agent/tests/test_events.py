"""events.to_frames — canned SDK message shapes -> exact shared/events.md frames."""

from __future__ import annotations

import uuid

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

from claude_telegram_agent import events


def _session_id() -> str:
    return uuid.uuid4().hex


def test_init_system_message_becomes_session_status() -> None:
    sid = _session_id()
    message = SystemMessage(subtype="init", data={"session_id": "claude-abc", "cwd": "/proj"})

    frames = events.to_frames(message, sid)

    assert frames == [
        {
            "type": "session_status",
            "status": "live",
            "claude_session_id": "claude-abc",
            "cwd": "/proj",
        }
    ]


def test_assistant_text_starts_then_appends_within_a_turn() -> None:
    sid = _session_id()

    first = events.to_frames(AssistantMessage(content=[TextBlock(text="Hello")], model="m"), sid)
    assert first == [
        {"type": "assistant_text", "text": "Hello", "delta": False, "turn_id": first[0]["turn_id"]}
    ]

    second = events.to_frames(AssistantMessage(content=[TextBlock(text=" world")], model="m"), sid)
    assert second == [
        {
            "type": "assistant_text",
            "text": " world",
            "delta": True,
            "turn_id": first[0]["turn_id"],
        }
    ]


def test_result_message_ends_the_turn_and_resets_bubble_state() -> None:
    sid = _session_id()
    events.to_frames(AssistantMessage(content=[TextBlock(text="hi")], model="m"), sid)

    result_frames = events.to_frames(
        ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=90,
            is_error=False,
            num_turns=1,
            session_id="claude-abc",
            usage={"input_tokens": 1},
            result="hi",
        ),
        sid,
    )
    assert len(result_frames) == 1
    assert result_frames[0]["type"] == "result"
    assert result_frames[0]["subtype"] == "success"
    assert result_frames[0]["usage"] == {"input_tokens": 1}
    assert result_frames[0]["text"] == "hi"

    # Next turn's first text block starts a fresh bubble (delta=False) again.
    next_turn = events.to_frames(AssistantMessage(content=[TextBlock(text="next")], model="m"), sid)
    assert next_turn[0]["delta"] is False
    assert next_turn[0]["turn_id"] != result_frames[0]["turn_id"]


def test_thinking_and_tool_use_blocks() -> None:
    sid = _session_id()
    message = AssistantMessage(
        content=[
            ThinkingBlock(thinking="considering options", signature="sig"),
            ToolUseBlock(id="tu1", name="Bash", input={"command": "ls -la"}),
        ],
        model="m",
    )

    frames = events.to_frames(message, sid)

    assert frames[0]["type"] == "thinking"
    assert frames[0]["text"] == "considering options"
    assert frames[0]["delta"] is False

    assert frames[1] == {
        "type": "tool_use",
        "tool": "Bash",
        "input_summary": "{'command': 'ls -la'}",
        "tool_use_id": "tu1",
        "turn_id": frames[0]["turn_id"],
    }


def test_tool_result_from_user_message() -> None:
    sid = _session_id()
    message = UserMessage(
        content=[ToolResultBlock(tool_use_id="tu1", content="3 files", is_error=False)]
    )

    frames = events.to_frames(message, sid)

    assert frames == [
        {"type": "tool_result", "tool_use_id": "tu1", "ok": True, "output_summary": "3 files"}
    ]


def test_plain_string_user_message_is_ignored() -> None:
    sid = _session_id()
    message = UserMessage(content="just the echoed prompt")

    assert events.to_frames(message, sid) == []


def test_assistant_error_field_emits_error_frame() -> None:
    sid = _session_id()
    message = AssistantMessage(content=[], model="m", error="rate_limit")

    frames = events.to_frames(message, sid)

    assert frames == [
        {
            "type": "error",
            "where": "assistant",
            "message": "rate_limit",
            "recoverable": True,
        }
    ]


def test_reset_drops_turn_state() -> None:
    sid = _session_id()
    first = events.to_frames(AssistantMessage(content=[TextBlock(text="a")], model="m"), sid)
    events.reset(sid)
    second = events.to_frames(AssistantMessage(content=[TextBlock(text="b")], model="m"), sid)

    assert second[0]["delta"] is False
    assert second[0]["turn_id"] != first[0]["turn_id"]
