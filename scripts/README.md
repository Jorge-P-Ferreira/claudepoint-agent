# agent/scripts/

Placeholder helpers for installing and running the laptop daemon.

| File | Purpose |
|---|---|
| `install.sh` | `pipx install .` (or `uv tool install .`), print the next steps: `claude-telegram-agent pair <code> --broker <url>`, then enable the service file. |
| `run_local.sh` | Foreground run against a local broker: `CTA_BROKER_URL=ws://localhost:8000/agent/ws claude-telegram-agent run`. |
| `claude-telegram-agent.service` | Template unit. On Linux: a **systemd user service** (`~/.config/systemd/user/`, `loginctl enable-linger`). On macOS the equivalent is a launchd plist — noted in comments. |
