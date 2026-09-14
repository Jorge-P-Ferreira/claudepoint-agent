"""Bridge the SDK permission flow to Telegram decisions. Mapping: ../../docs/claude-driver.md.

Phase 2: every tool call not on the auto-allow list is parked via a `PreToolUse`
`defer` hook rather than blocked on in-process `can_use_tool` — the phone might not
answer for hours, and deferring lets the SDK end the turn (the process can even exit)
instead of sitting blocked (`docs/claude-driver.md` § "Deferring a decision").
`defer_token` is simply the SDK's own `tool_use_id` — already unique per call, nothing
of ours to mint.

Three `decision_kind`s, classified by tool name: `AskUserQuestion`, `ExitPlanMode`
(the plan-mode "I'm done, here's the plan" tool), everything else is a plain
`permission` decision. `updatedInput` on the hook's *second* pass — once an answer is
recorded in `_pending_answers` — lets the hook alone finish the call (no `can_use_tool`
needed at all): same idea as `can_use_tool`'s own `updated_input`, just issued from
`PreToolUseHookSpecificOutput` instead.

Cross-restart resume — park now, answer arrives minutes or hours later, possibly after
this process itself restarted — needs `session_manager.py` to put the answer into
`_pending_answers` *before* reconnecting `ClaudeAgentOptions(resume=claude_session_id)`,
so the hook finds it the moment the SDK re-evaluates the one parked tool call. The
exact SDK-level trigger that makes a resumed client re-evaluate its parked call isn't
pinned down by static reading of `claude_agent_sdk` 0.2.152 alone (no dedicated
"answer this deferred tool" API surfaced there) — confirm against a real `claude`
session (the Phase 2 integration test's `ls` case, then a kill-and-restart variant)
before relying on it in production.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import HookMatcher

EmitFn = Callable[[str | None, dict[str, Any]], Awaitable[None]]

# Read-only / non-mutating tools never prompt — matches the intent of
# ClaudeAgentOptions(permission_mode="default") without pestering for every Read.
_AUTO_ALLOW_TOOLS = {"Read", "Glob", "Grep", "TodoWrite", "BashOutput", "NotebookRead"}
# acceptEdits/bypassPermissions short-circuit everything except the two decision-shaped
# tools, which always need a human regardless of mode.
_AUTO_ALLOW_MODES = {"acceptEdits", "bypassPermissions"}
_ALWAYS_GATED_TOOLS = {"AskUserQuestion", "ExitPlanMode"}

# tool_use_id -> the decision_answer's `answer` payload (shared/commands.md shape),
# recorded by session_manager right before it reconnects to resume a deferred call.
_pending_answers: dict[str, dict[str, Any]] = {}


def register_answer(tool_use_id: str, answer: dict[str, Any]) -> None:
    _pending_answers[tool_use_id] = answer


def make_pre_tool_use_hook(emit: EmitFn, session_id: str) -> HookMatcher:
    """One hook per `ClaudeSession`, closing over that session's `emit` + `session_id`
    so the emitted `decision_request` frame carries the right `session_id`."""

    async def hook(
        input_data: dict[str, Any], tool_use_id: str | None, _context: Any
    ) -> dict[str, Any]:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        mode = input_data.get("permission_mode")

        if tool_name not in _ALWAYS_GATED_TOOLS and (
            tool_name in _AUTO_ALLOW_TOOLS or mode in _AUTO_ALLOW_MODES
        ):
            return _allow()

        if tool_use_id and tool_use_id in _pending_answers:
            return _answer_to_hook_output(tool_input, _pending_answers.pop(tool_use_id))

        decision_kind, payload, options = _classify(tool_name, tool_input)
        await emit(
            session_id,
            {
                "type": "decision_request",
                "decision_kind": decision_kind,
                "payload": payload,
                "options": options,
                "defer_token": tool_use_id or "",
            },
        )
        return {
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "defer"}
        }

    # `hook`'s params are typed loosely (plain dicts) for readability; the SDK's
    # `HookCallback` alias is a union of every hook event's TypedDict, which this
    # PreToolUse-only callback doesn't need to match structurally.
    return HookMatcher(matcher=None, hooks=[hook])  # type: ignore[list-item]


def _allow(updated_input: dict[str, Any] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
    if updated_input is not None:
        out["updatedInput"] = updated_input
    return {"hookSpecificOutput": out}


def _deny(message: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }
    }


def _classify(
    tool_name: str, tool_input: dict[str, Any]
) -> tuple[str, dict[str, Any], list[dict[str, Any]] | None]:
    """-> (decision_kind, payload, options) per shared/events.md."""
    if tool_name == "AskUserQuestion":
        return "ask_user_question", {"questions": tool_input.get("questions", [])}, None
    if tool_name == "ExitPlanMode":
        payload = {"plan_markdown": tool_input.get("plan", "")}
        options = [
            {"key": "approve", "label": "Approve & run"},
            {"key": "keep", "label": "Keep planning"},
            {"key": "edit", "label": "Comment"},
        ]
        return "plan_approval", payload, options
    payload = {"tool": tool_name, "input": tool_input}
    options = [
        {"key": "allow", "label": "Allow"},
        {"key": "deny", "label": "Deny"},
        {"key": "always", "label": f"Always allow {tool_name}"},
    ]
    return "permission", payload, options


def _answer_to_hook_output(tool_input: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    """Turn a resolved `shared/commands.md` `decision_answer.answer` into the
    PreToolUse output that finishes this tool call."""
    kind = answer.get("kind")

    if kind == "ask_user_question":
        if answer.get("response") is not None:
            return _allow({**tool_input, "response": answer["response"]})
        return _allow({**tool_input, "answers": answer.get("answers", {})})

    if kind == "plan_approval":
        if answer.get("choice") == "approve":
            return _allow()
        return _deny(answer.get("message") or "Keep planning.")

    # permission
    if answer.get("choice") in ("allow", "always"):
        return _allow(answer.get("updated_input"))
    return _deny(answer.get("message") or "Denied by user.")
