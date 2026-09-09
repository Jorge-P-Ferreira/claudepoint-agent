"""SDK stream messages -> broker `event` frames.

PLACEHOLDER — no implementation. Target shapes: ../../shared/events.md.

  to_frames(sdk_message, session_id) -> Iterable[frame]:
    AssistantMessage text block    -> event:assistant_text (delta=True after the first)
    thinking block                 -> event:thinking
    tool_use block                 -> event:tool_use  { tool, input_summary, tool_use_id }
    tool_result                    -> event:tool_result { tool_use_id, ok, output_summary }
    ResultMessage                  -> event:result   { turn_id, subtype, usage }
    init/system                    -> event:session_status { status:"live", claude_session_id }
    SDK error                      -> event:error

  input_summary/output_summary: short, single-line, no secrets.
"""


def to_frames(sdk_message, session_id: str):  # pragma: no cover - placeholder
    raise NotImplementedError("claude_telegram_agent.events.to_frames")
