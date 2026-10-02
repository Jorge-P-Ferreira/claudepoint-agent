# agent/scripts/

| File | Purpose |
|---|---|
| `bootstrap.sh` | **One-paste install, macOS/Linux** — the line `/pair` prints. Installs the agent with uv from the public mirror, pairs it (`~/Projects` as the project root), then `claude-telegram-agent service install`. |
| `bootstrap.ps1` | Same for **Windows** (PowerShell, `$env:CP_PAIR='<code> <broker>'; irm … \| iex`); registers a logon scheduled task. |
| `install.sh` | Dev install from a checkout: `pipx install .` (or `uv tool install .`, or `pip install --user -e .`), prints next steps. |
| `run_local.sh` | Foreground run against a local broker (pair first). |
| `install_launchd.sh` | **macOS**, from a checkout: thin wrapper over `claude-telegram-agent service install` that prefers this checkout's `.venv`. `--uninstall` removes it. |

The background-service logic itself (launchd plist / systemd user unit / Task Scheduler
XML) lives in `claude_telegram_agent/service.py`. No `ANTHROPIC_API_KEY` is needed
anywhere — the Agent SDK rides the local `claude` CLI's own login.
