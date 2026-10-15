# claudepoint-agent

The laptop half of **CLAUDEPOINT**: drive Claude Code from Telegram across several
laptops and projects. This small daemon runs on each laptop, dials out to the
CLAUDEPOINT broker over a WebSocket (no inbound ports, NAT-friendly), and runs one
Claude Agent SDK session per conversation in your project folders.

> This repo is a read-only mirror of the `agent/` folder of the (private) CLAUDEPOINT
> repo. The bot and broker aren't here — on its own, this agent can't do anything
> until a CLAUDEPOINT bot hands you a pairing code.

## Install (one paste)

Prerequisite: [Claude Code](https://docs.claude.com/en/docs/claude-code) installed and
logged in (run `claude` once). No API key — the agent rides that login.

Send `/pair` to the bot. It replies with one line per OS; paste it into a terminal:

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/Jorge-P-Ferreira/claudepoint-agent/main/scripts/bootstrap.sh | bash -s -- <CODE> <BROKER_URL>
```

```powershell
# Windows (PowerShell)
$env:CP_PAIR='<CODE> <BROKER_URL>'; irm https://raw.githubusercontent.com/Jorge-P-Ferreira/claudepoint-agent/main/scripts/bootstrap.ps1 | iex
```

That installs the agent with [uv](https://docs.astral.sh/uv/) (which also fetches a
Python if needed), pairs it, and starts it in the background at login: a launchd agent
on macOS, a systemd user unit on Linux, a logon scheduled task on Windows. Projects are
the subfolders of `~/Projects` (set `CLAUDEPOINT_PROJECTS` before pasting to change
it). The pairing code works once and expires after 10 minutes.

## CLI

| Command | Action |
|---|---|
| `claude-telegram-agent pair <code> --broker <url> [--project-roots a,b]` | one-time pairing; writes config. Roots default to `~/Projects`, created if missing |
| `claude-telegram-agent run [--log-file f]` | run the daemon in the foreground |
| `claude-telegram-agent service install` / `uninstall` | start at login and restart on crash, or remove that |
| `claude-telegram-agent status` | print config, broker URL, discovered projects |
| `claude-telegram-agent unpair` | forget the local token (also remove the laptop on the bot) |

Fully remove: `claude-telegram-agent service uninstall`, `claude-telegram-agent unpair`,
`uv tool uninstall claude-telegram-agent`.

## Config

Written by `pair` to the per-OS config dir (`claude-telegram-agent status` prints it;
e.g. `~/Library/Application Support/claude-telegram-agent/config.toml` on macOS,
`~/.config/...` on Linux, `%LOCALAPPDATA%\claude-telegram-agent\...` on Windows):

```toml
broker_url       = "wss://broker.example.com/agent/ws"
laptop_token     = "..."            # secret; file is chmod 600
laptop_id        = "..."
project_roots    = ["~/Projects"]
default_mode     = "default"        # default | plan | acceptEdits | bypassPermissions
```

Logs: `~/Library/Logs/claude-telegram-agent.log` (macOS),
`~/.local/state/claude-telegram-agent/agent.log` (Linux),
`%LOCALAPPDATA%\claude-telegram-agent\agent.log` (Windows).

## Keeping a laptop reachable with the lid closed

A sleeping laptop drops the connection. On power, and only if you want it:

- **macOS:** `sudo pmset -a disablesleep 1` (undo: `... 0`).
- **Windows** (elevated PowerShell):
  `powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0; powercfg /change standby-timeout-ac 0; powercfg /setactive SCHEME_CURRENT`
  (undo: `LIDACTION 1`, and set `standby-timeout-ac` back to your old minutes).

## Safety

- `project_roots` is an allowlist enforced here on the laptop: a session can't be
  pointed at a path outside it, even by a compromised bot or broker.
- New sessions start in `default` permission mode; `bypassPermissions` (full,
  unattended tool access under `project_roots`) is an explicit `/mode` on the phone.
- The token file is 0600; `unpair` deletes it.

## Layout

```
claude_telegram_agent/
├── __main__.py         CLI (pair | run | status | unpair | service)
├── daemon.py           WS client, reconnect/backoff, heartbeat, cmd dispatch
├── pairing.py          POST /agent/pair, write config.toml
├── service.py          launchd / systemd / Task Scheduler install
├── projects.py         project discovery + the allowlist check
├── session_manager.py  session lifecycle, resume
├── claude_session.py   ClaudeSDKClient wrapper
├── permissions.py      PreToolUse defer hook -> decision_request
└── events.py           SDK stream messages -> event frames
scripts/                bootstrap.sh / bootstrap.ps1 (one-paste install), dev helpers
```

Development: `uv venv && uv pip install -e ".[dev]" && pytest`.
