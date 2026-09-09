"""Session lifecycle on the laptop.

PLACEHOLDER — no implementation. Model: ../../docs/session-model.md.

  sessions: dict[session_id, ClaudeSession]

  dispatch(frame):
    new_session     -> projects.within_allowlist check -> ClaudeSession.connect(cwd, mode)
                       -> emit session_status starting/live, store claude_session_id
    prompt          -> session.enqueue_prompt(text)   (queue behind a running turn)
    decision_answer -> session.resolve_decision(decision_id, defer_token, answer)
    set_mode        -> session.set_permission_mode(mode)
    cancel          -> session.interrupt()
    clear_session   -> session.close(keep_slot=True); ClaudeSession.connect(cwd, mode,
                       resume=None) on the SAME session_id; new claude_session_id
    end_session     -> session.close(); emit session_status ended
    list_projects   -> re-emit hello-style project list
    create_project  -> projects.create_project(name, roots, git_init) -> re-emit list
    add_project     -> projects.add_project(path, roots) -> re-emit list (or reject)
    remove_project  -> broker deregisters projects/{id} directly; no agent round-trip
                       needed since it never touches the filesystem

  resume(resumable): rebuild ClaudeSession from claude_session_id on demand.
"""


class SessionManager:  # pragma: no cover - placeholder
    def __init__(self, *_args, **_kwargs):
        raise NotImplementedError("claude_telegram_agent.session_manager.SessionManager")
