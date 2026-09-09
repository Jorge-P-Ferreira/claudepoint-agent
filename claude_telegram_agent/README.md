# claude_telegram_agent/ — modules

| Module | Responsibility | Notes |
|---|---|---|
| `__main__.py` | CLI dispatch: `pair` / `run` / `status` / `unpair`. Loads config from `~/.config/claude-telegram-agent/config.toml`. | `main()` is the console-script entrypoint |
| `daemon.py` | The `run` loop. Dial `broker_url` with `Authorization: Bearer <laptop_token>`. Send `hello` (projects + resumable sessions), then `heartbeat` every 20 s. Receive `cmd` frames, dispatch per `session_id` to `session_manager`. Reconnect with backoff (1→2→5→15→30 s, jitter). Dedupe on frame `id`. | contract: `../../docs/transport-protocol.md` |
| `pairing.py` | `pair(code, broker)`: `POST {broker}/agent/pair {code, hostname, agent_version}` → `{laptop_id, laptop_token, broker_ws_url}` → write `config.toml` at 0600. | one-time |
| `projects.py` | Expand `project_roots`, list immediate subdirs (or the roots themselves) that look like projects, compute `project_id = sha1(abspath)[:16]`. Enforce the allowlist for `new_session`, `create_project`, `add_project`. | |
| `session_manager.py` | Map `session_id` → `ClaudeSession`. Handle `new_session` / `end_session` / `clear_session` (fresh client, same `session_id`, no `resume`), queue `prompt`s behind a running turn, route `decision_answer`, resume from `claude_session_id` after a restart. | |
| `claude_session.py` | Wrapper over `ClaudeSDKClient`: `connect(cwd, mode, resume=None)`, `query(text)`, `set_permission_mode(mode)`, `interrupt()`, async-iterate messages. Wires `can_use_tool` + hooks from `permissions.py`. | `../../docs/claude-driver.md` |
| `permissions.py` | `can_use_tool(tool, input, ctx)` — fast path: emit `decision_request`, await `decision_answer`, return `PermissionResultAllow/Deny` (or the `AskUserQuestion` `answers` map). Slow path: a `PreToolUse` hook returning `defer` so the process can idle/exit; resume on answer. | |
| `events.py` | Translate SDK stream messages (assistant text deltas, tool_use, tool_result, thinking, result) into `event` frames per `../../shared/events.md`. | |

All modules are placeholder stubs (docstring + signatures + `NotImplementedError`).
