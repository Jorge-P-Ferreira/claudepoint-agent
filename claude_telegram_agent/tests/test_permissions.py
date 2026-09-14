"""permissions.py — the PreToolUse hook that classifies, defers, and (on the second
pass, once an answer is registered) finishes every gated tool call."""

from __future__ import annotations

from typing import Any

import pytest

from claude_telegram_agent import permissions


class _Emitted:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, dict[str, Any]]] = []

    async def __call__(self, session_id: str | None, data: dict[str, Any]) -> None:
        self.calls.append((session_id, data))


def _hook(emit: _Emitted, session_id: str = "sess1"):
    matcher = permissions.make_pre_tool_use_hook(emit, session_id)
    return matcher.hooks[0]


async def test_auto_allow_tool_never_prompts() -> None:
    emit = _Emitted()
    result = await _hook(emit)({"tool_name": "Read", "tool_input": {}}, "tu1", None)

    assert result == {
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
    }
    assert emit.calls == []


async def test_gated_tool_first_pass_emits_decision_request_and_defers() -> None:
    emit = _Emitted()
    result = await _hook(emit, "sess-x")(
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}}, "tu2", None
    )

    assert result["hookSpecificOutput"]["permissionDecision"] == "defer"
    assert len(emit.calls) == 1
    session_id, data = emit.calls[0]
    assert session_id == "sess-x"
    assert data == {
        "type": "decision_request",
        "decision_kind": "permission",
        "payload": {"tool": "Bash", "input": {"command": "rm -rf build"}},
        "options": [
            {"key": "allow", "label": "Allow"},
            {"key": "deny", "label": "Deny"},
            {"key": "always", "label": "Always allow Bash"},
        ],
        "defer_token": "tu2",
    }


async def test_second_pass_with_a_registered_allow_finishes_without_re_emitting() -> None:
    emit = _Emitted()
    hook = _hook(emit)
    permissions.register_answer("tu3", {"kind": "permission", "choice": "allow"})

    result = await hook({"tool_name": "Write", "tool_input": {"path": "x"}}, "tu3", None)

    assert result == {
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
    }
    assert emit.calls == []  # no second decision_request


async def test_second_pass_with_a_registered_deny_carries_the_reason() -> None:
    emit = _Emitted()
    hook = _hook(emit)
    permissions.register_answer(
        "tu4", {"kind": "permission", "choice": "deny", "message": "no thanks"}
    )

    result = await hook({"tool_name": "Bash", "tool_input": {}}, "tu4", None)

    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert result["hookSpecificOutput"]["permissionDecisionReason"] == "no thanks"


async def test_ask_user_question_is_classified_and_deferred() -> None:
    emit = _Emitted()
    questions = [{"question": "Which db?", "options": [{"label": "Postgres"}]}]

    result = await _hook(emit)(
        {"tool_name": "AskUserQuestion", "tool_input": {"questions": questions}}, "tu5", None
    )

    assert result["hookSpecificOutput"]["permissionDecision"] == "defer"
    _, data = emit.calls[0]
    assert data["decision_kind"] == "ask_user_question"
    assert data["payload"] == {"questions": questions}
    assert data["options"] is None


async def test_ask_user_question_answer_replays_answers_into_updated_input() -> None:
    emit = _Emitted()
    hook = _hook(emit)
    tool_input = {"questions": [{"question": "Which db?"}]}
    permissions.register_answer(
        "tu6", {"kind": "ask_user_question", "answers": {"Which db?": "Postgres"}}
    )

    result = await hook({"tool_name": "AskUserQuestion", "tool_input": tool_input}, "tu6", None)

    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert result["hookSpecificOutput"]["updatedInput"] == {
        "questions": [{"question": "Which db?"}],
        "answers": {"Which db?": "Postgres"},
    }


async def test_ask_user_question_freeform_response_replays_as_response() -> None:
    emit = _Emitted()
    hook = _hook(emit)
    permissions.register_answer("tu7", {"kind": "ask_user_question", "response": "just use sqlite"})

    result = await hook(
        {"tool_name": "AskUserQuestion", "tool_input": {"questions": []}}, "tu7", None
    )

    assert result["hookSpecificOutput"]["updatedInput"]["response"] == "just use sqlite"


async def test_exit_plan_mode_is_classified_as_plan_approval() -> None:
    emit = _Emitted()
    result = await _hook(emit)(
        {"tool_name": "ExitPlanMode", "tool_input": {"plan": "## Plan\n1. Do it"}}, "tu8", None
    )

    assert result["hookSpecificOutput"]["permissionDecision"] == "defer"
    _, data = emit.calls[0]
    assert data["decision_kind"] == "plan_approval"
    assert data["payload"] == {"plan_markdown": "## Plan\n1. Do it"}
    assert [o["key"] for o in data["options"]] == ["approve", "keep", "edit"]


async def test_plan_approval_approve_allows_keep_denies() -> None:
    emit = _Emitted()
    hook = _hook(emit)
    permissions.register_answer("tu9a", {"kind": "plan_approval", "choice": "approve"})
    permissions.register_answer(
        "tu9b", {"kind": "plan_approval", "choice": "keep", "message": "add more detail"}
    )

    approved = await hook({"tool_name": "ExitPlanMode", "tool_input": {"plan": "x"}}, "tu9a", None)
    kept = await hook({"tool_name": "ExitPlanMode", "tool_input": {"plan": "x"}}, "tu9b", None)

    assert approved["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert kept["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert kept["hookSpecificOutput"]["permissionDecisionReason"] == "add more detail"


@pytest.mark.parametrize("mode", ["acceptEdits", "bypassPermissions"])
async def test_permissive_modes_skip_ordinary_tools(mode: str) -> None:
    emit = _Emitted()
    result = await _hook(emit)(
        {"tool_name": "Bash", "tool_input": {}, "permission_mode": mode}, "tu10", None
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert emit.calls == []


@pytest.mark.parametrize("mode", ["acceptEdits", "bypassPermissions"])
async def test_permissive_modes_still_gate_ask_user_question(mode: str) -> None:
    emit = _Emitted()
    result = await _hook(emit)(
        {"tool_name": "AskUserQuestion", "tool_input": {"questions": []}, "permission_mode": mode},
        "tu11",
        None,
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "defer"
    assert len(emit.calls) == 1
