#!/usr/bin/env bash
set -euo pipefail

# macOS: runs the laptop agent as a launchd user agent — starts at login, restarts if it
# crashes or exits, logs to ~/Library/Logs/claude-telegram-agent.log. Pair first
# (`claude-telegram-agent pair ...`). Thin wrapper over `claude-telegram-agent service`,
# which holds the actual plist logic (claude_telegram_agent/service.py).
#
#   agent/scripts/install_launchd.sh              # install (or reinstall) and start
#   agent/scripts/install_launchd.sh --uninstall  # stop and remove
#
# AGENT_BIN overrides which `claude-telegram-agent` to use (default: this checkout's
# .venv, else whatever is on PATH).

if [ "$(uname -s)" != "Darwin" ]; then
  echo "macOS only — elsewhere run 'claude-telegram-agent service install'." >&2
  exit 1
fi

AGENT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_BIN="${AGENT_BIN:-}"
if [ -z "$AGENT_BIN" ]; then
  if [ -x "$AGENT_DIR/.venv/bin/claude-telegram-agent" ]; then
    AGENT_BIN="$AGENT_DIR/.venv/bin/claude-telegram-agent"
  else
    AGENT_BIN="$(command -v claude-telegram-agent || true)"
  fi
fi
if [ -z "$AGENT_BIN" ] || [ ! -x "$AGENT_BIN" ]; then
  echo "claude-telegram-agent not found — run scripts/install.sh first, or set AGENT_BIN." >&2
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  exec "$AGENT_BIN" service uninstall
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "'claude' CLI not found on PATH — the agent needs it. Install Claude Code first." >&2
  exit 1
fi
exec "$AGENT_BIN" service install
