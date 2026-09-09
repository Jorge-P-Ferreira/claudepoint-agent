# agent/ — laptop-side daemon

**Runtime:** a long-running process on each of Jorge's laptops (systemd user service /
launchd agent / `screen`). **Language:** Python. **Package name:**
`claude-telegram-agent`, console script `claude-telegram-agent`.

## Role

- **Pair once** (`claude-telegram-agent pair <code> --broker <url>`): exchange a pairing
  code for a long-lived `laptop_token`, saved to
  `~/.config/claude-telegram-agent/config.toml` (mode 0600).
- **Run** (`claude-telegram-agent run`): dial the broker WebSocket, keep it alive,
  announce project directories, and service `cmd` frames.
- For each session, drive a **Claude Agent SDK** `ClaudeSDKClient` in the project's
  directory — see [`../docs/claude-driver.md`](../docs/claude-driver.md).
- Bridge `can_use_tool` (tool approval + `AskUserQuestion`) and a `PreToolUse` `defer`
  hook to `decision_request` events / `decision_answer` commands.
- Map SDK stream messages to `event` frames.

## CLI

| Command | Action |
|---|---|
| `claude-telegram-agent pair <code> --broker <url>` | one-time pairing; writes config |
| `claude-telegram-agent run` | start the daemon (foreground; the service file backgrounds it) |
| `claude-telegram-agent status` | print config, broker URL, discovered projects, connection state |
| `claude-telegram-agent unpair` | forget local token (also do `/unpair` on the bot) |

## Config (`~/.config/claude-telegram-agent/config.toml`)

```toml
broker_url       = "wss://broker.example.com/agent/ws"
laptop_token     = "..."            # secret; file is chmod 600
laptop_id        = "..."
project_roots    = ["~/projects", "~/work/acme"]
default_mode     = "default"        # default | plan | acceptEdits | bypassPermissions
# ANTHROPIC_API_KEY comes from the environment, not this file
```

## Files

```
agent/
├── pyproject.toml                  package + console_script entrypoint
└── claude_telegram_agent/
    ├── __main__.py                 CLI dispatch (pair | run | status | unpair)
    ├── daemon.py                   WS client, reconnect/backoff, heartbeat, cmd dispatch
    ├── pairing.py                  POST /agent/pair, write config.toml (0600)
    ├── projects.py                 enumerate project_roots -> [{project_id, path, name}]
    ├── session_manager.py          session_id -> ClaudeSession; lifecycle, resume
    ├── claude_session.py           ClaudeSDKClient wrapper: query, set_permission_mode, interrupt, resume
    ├── permissions.py              can_use_tool callback + PreToolUse(defer) hook -> decision_request
    └── events.py                   SDK stream message -> shared/events.md `event` frames
```

## Dependencies

`claude-agent-sdk`, `websockets`, `tomli`/`tomllib`, `platformdirs`, `httpx` (pairing).
`ANTHROPIC_API_KEY` in the environment for the SDK.

## Safety

- The daemon can run Claude in `bypassPermissions` — that is full, unattended tool
  access to everything under `project_roots`. New sessions start in `default`; switching
  to `bypass` is an explicit `/mode` on the phone.
- `project_roots` is an allowlist. The agent refuses `new_session` for a path outside
  it.
- Token file is 0600; `unpair` shreds it.

## Install

[`scripts/install.sh`](scripts/install.sh) (placeholder): `pipx install .`, then
`claude-telegram-agent pair …`, then enable `scripts/claude-telegram-agent.service`.
