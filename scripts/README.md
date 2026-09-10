# agent/scripts/

Helpers for installing and running the laptop daemon. `run_local.sh`/`install.sh` are
Linux/macOS convenience wrappers; on Windows just run the installed console script
directly (`claude-telegram-agent run`) — no wrapper needed.

| File | Purpose |
|---|---|
| `install.sh` | `pipx install .` (or `uv tool install .`, or plain `pip install --user -e .`), prints next steps. No `ANTHROPIC_API_KEY` needed — the Agent SDK rides the local `claude` CLI's own login. |
| `run_local.sh` | Foreground run against a local broker (pair first). |
| `claude-telegram-agent.service` | Template unit. On Linux: a **systemd user service** (`~/.config/systemd/user/`, `loginctl enable-linger`). On macOS the equivalent is a launchd plist — noted in comments. Not wired up for Phase 1 (Windows dev laptop). |
