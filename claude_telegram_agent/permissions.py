"""Bridge the SDK permission flow to Telegram decisions.

Phase 1: literally always allow — per prompt.md, no decision wiring until Phase 2. The
fast-path `can_use_tool` (permission / ask_user_question / plan_approval decision
requests) and slow-path `defer_hook` (PreToolUse `defer`) described in
../../docs/claude-driver.md land then.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import PermissionResultAllow


async def can_use_tool(
    tool_name: str, input_data: dict[str, Any], context: Any
) -> PermissionResultAllow:
    return PermissionResultAllow(updated_input=input_data)
