"""ClaudeSDKClient wrapper — one per session.

PLACEHOLDER — no implementation. Mapping: ../../docs/claude-driver.md.

  connect(cwd, mode, resume: str | None):
      opts = ClaudeAgentOptions(
          cwd=cwd,
          permission_mode=mode,               # default | plan | acceptEdits | bypassPermissions
          can_use_tool=permissions.can_use_tool,
          hooks={"PreToolUse": [permissions.defer_hook]},
          resume=resume,                      # claude_session_id, when resuming
      )
      self._client = ClaudeSDKClient(options=opts)
      await self._client.connect()            # no prompt -> input stream stays open

  query(text)                 -> await self._client.query(text)
  set_permission_mode(mode)   -> await self._client.set_permission_mode(mode)
  interrupt()                 -> await self._client.interrupt()
  __aiter__                   -> yield messages -> events.to_frames(...)

  capture claude_session_id from the init/system message for later resume.
"""


class ClaudeSession:  # pragma: no cover - placeholder
    def __init__(self, *_args, **_kwargs):
        raise NotImplementedError("claude_telegram_agent.claude_session.ClaudeSession")
