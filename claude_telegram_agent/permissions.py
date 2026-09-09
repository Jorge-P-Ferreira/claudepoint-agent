"""Bridge the SDK permission flow to Telegram decisions.

PLACEHOLDER — no implementation. Detail: ../../docs/claude-driver.md.

Fast path — can_use_tool(tool_name, input_data, context):
  * normalise into a decision_request payload/options (see ../../shared/events.md):
      tool_name == "AskUserQuestion"  -> decision_kind "ask_user_question", payload.questions
      plan mode + edit/askquestion    -> decision_kind "plan_approval"
      else                            -> decision_kind "permission"
  * emit event:decision_request, await the matching decision_answer
  * return:
      PermissionResultAllow(updated_input=...)                       # permission allow
      PermissionResultDeny(message=...)                              # permission deny
      PermissionResultAllow(updated_input={"questions": q, "answers": a})   # AskUserQuestion

Slow path — defer_hook (PreToolUse):
  * for tools we route to the phone, return the `defer` decision so the SDK persists the
    call and the turn ends; the process can idle or exit
  * on a later decision_answer, session_manager resumes from claude_session_id and the
    deferred call is satisfied

A dummy PreToolUse hook returning {"continue_": True} is also needed to keep the input
stream open for can_use_tool (see the SDK user-input docs).
"""


async def can_use_tool(tool_name, input_data, context):  # pragma: no cover - placeholder
    raise NotImplementedError("claude_telegram_agent.permissions.can_use_tool")
