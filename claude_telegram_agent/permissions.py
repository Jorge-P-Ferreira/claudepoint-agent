"""Bridge the SDK permission flow to Telegram decisions. Mapping: ../../docs/claude-driver.md.

Phase 2: in `default` mode (the only mode that asks — see `_ASKING_MODES`), every tool
call not on the auto-allow list is parked via a `PreToolUse`
`defer` hook rather than blocked on in-process `can_use_tool` — the phone might not
answer for hours, and deferring lets the SDK end the turn (the process can even exit)
instead of sitting blocked (`docs/claude-driver.md` § "Deferring a decision").
`defer_token` is simply the SDK's own `tool_use_id` — already unique per call, nothing
of ours to mint.

Three `decision_kind`s, classified by tool name: `AskUserQuestion`, `ExitPlanMode`
(the plan-mode "I'm done, here's the plan" tool), everything else is a plain
`permission` decision. `updatedInput` on the hook's *second* pass — once an answer is
recorded in `_pending_answers` — lets the hook alone finish the call: same idea as
`can_use_tool`'s own `updated_input`, just issued from `PreToolUseHookSpecificOutput`
instead. A `can_use_tool` callback (`can_use_tool` below) is still registered on every
session, but only because headless (no-TTY) Claude Code sessions hide `AskUserQuestion`
and `ExitPlanMode` from the tool catalog entirely unless one is present — confirmed
empirically, not documented anywhere obvious. The `PreToolUse` hook always resolves the
call first, so this callback should never actually run.

Cross-restart resume — park now, answer arrives minutes or hours later, possibly after
this process itself restarted — needs `session_manager.py` to put the answer into
`_pending_answers` *before* reconnecting `ClaudeAgentOptions(resume=claude_session_id)`,
so the hook finds it the moment the SDK re-evaluates the one parked tool call. The
exact SDK-level trigger that makes a resumed client re-evaluate its parked call isn't
pinned down by static reading of `claude_agent_sdk` 0.2.152 alone (no dedicated
"answer this deferred tool" API surfaced there) — confirm against a real `claude`
session (the Phase 2 integration test's `ls` case, then a kill-and-restart variant)
before relying on it in production.

`_session_always_allowed` (this file, now): "Always allow <Tool>" previously resolved
only the one call it was tapped on — indistinguishable from a one-time "Allow," despite
its own label — since nothing recorded the choice anywhere. Confirmed live: every
repeat call to the same tool kept prompting again. Now the choice is remembered per
`(session_id, tool_name)` for as long as that session exists (cleared on
`end_session`, but *not* on `clear_session`/a decision-answer reconnect — it's a
phone-side policy about what this session slot is allowed to do, independent of
whatever Claude-side conversation history `/clear` wipes).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import HookMatcher, PermissionResultDeny

EmitFn = Callable[[str | None, dict[str, Any]], Awaitable[None]]

# Read-only / non-mutating tools never prompt — matches the intent of
# ClaudeAgentOptions(permission_mode="default") without pestering for every Read.
_AUTO_ALLOW_TOOLS = {"Read", "Glob", "Grep", "TodoWrite", "BashOutput", "NotebookRead"}
# Only `default` mode asks before a tool call — the user opts a session into that with
# /mode. Every other mode (`plan` too: it explores freely, and Claude's own plan-mode
# instructions keep it from editing) runs tools unasked, except the two decision-shaped
# tools, which always need a human regardless of mode. A hook input with no mode at all
# is treated as asking, failing closed.
_ASKING_MODES = {"default", None}
_ALWAYS_GATED_TOOLS = {"AskUserQuestion", "ExitPlanMode"}

# Fallback when a command arrives without a `permission_mode`, and the mode a session
# drops back to once its plan is approved — mirrors broker/app/registry.py's
# DEFAULT_PERMISSION_MODE: never ask before a tool call unless /mode says otherwise.
DEFAULT_MODE = "bypassPermissions"

# tool_use_id -> the decision_answer's `answer` payload (shared/commands.md shape),
# recorded by session_manager right before it reconnects to resume a deferred call.
_pending_answers: dict[str, dict[str, Any]] = {}

# session_id -> tool names the user has tapped "Always allow <Tool>" for. Checked
# before ever emitting a new decision_request for that tool again.
_session_always_allowed: dict[str, set[str]] = {}


def register_answer(tool_use_id: str, answer: dict[str, Any]) -> None:
    _pending_answers[tool_use_id] = answer


def forget_session(session_id: str) -> None:
    """Called from `session_manager.py::_end_session` — a session that's gone has no
    more "always allow" policy to remember."""
    _session_always_allowed.pop(session_id, None)


async def can_use_tool(
    _tool_name: str, _input_data: dict[str, Any], _context: Any
) -> PermissionResultDeny:
    """Registered on every `ClaudeAgentOptions` purely so Claude Code exposes
    `AskUserQuestion`/`ExitPlanMode` at all — headless (no-TTY) sessions hide both
    tools entirely unless a `can_use_tool` callback is present (confirmed empirically;
    see `docs/claude-driver.md` § "Deferring a decision"). The `PreToolUse` hook above
    resolves (allow/deny/defer) every tool call before the SDK would ever reach this
    callback, so it should never actually run; it denies defensively if it somehow
    does, rather than silently allowing something the hook didn't vet."""
    return PermissionResultDeny(message="Unhandled by PreToolUse hook — denied defensively.")


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
            tool_name in _AUTO_ALLOW_TOOLS
            or mode not in _ASKING_MODES
            or tool_name in _session_always_allowed.get(session_id, ())
        ):
            return _allow()

        if tool_use_id and tool_use_id in _pending_answers:
            answer = _pending_answers.pop(tool_use_id)
            if answer.get("kind") == "permission" and answer.get("choice") == "always":
                _session_always_allowed.setdefault(session_id, set()).add(tool_name)
            return _answer_to_hook_output(tool_input, answer)

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
