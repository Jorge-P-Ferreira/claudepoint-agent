# agent/scripts/

Helpers for installing and running the laptop daemon. `run_local.sh`/`install.sh` are
Linux/macOS convenience wrappers; on Windows just run the installed console script
directly (`claude-telegram-agent run`) — no wrapper needed.

| File | Purpose |
|---|---|
| `install.sh` | `pipx install .` (or `uv tool install .`, or plain `pip install --user -e .`), prints next steps. No `ANTHROPIC_API_KEY` needed — the Agent SDK rides the local `claude` CLI's own login. |
| `run_local.sh` | Foreground run against a local broker (pair first). |
| `install_launchd.sh` | **macOS:** installs the agent as a launchd user agent — starts at login, restarts if it crashes, logs to `~/Library/Logs/claude-telegram-agent.log`. Pair first. `--uninstall` removes it. To keep it reachable with the lid closed, also run `sudo pmset -a disablesleep 1` (undo: `... 0`). |
| `claude-telegram-agent.service` | Template unit. On Linux: a **systemd user service** (`~/.config/systemd/user/`, `loginctl enable-linger`). |
