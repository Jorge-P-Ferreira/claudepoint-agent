#!/usr/bin/env bash
set -euo pipefail

# macOS: runs the laptop agent as a launchd user agent — starts at login, restarts if it
# crashes or exits, logs to ~/Library/Logs/claude-telegram-agent.log. Pair first
# (`claude-telegram-agent pair ...`); this only runs what `pair` already configured.
#
# It's a user agent (gui/<uid>), not a system daemon, on purpose: the `claude` CLI keeps
# its login in the user's keychain, which is only unlocked once the user has logged in.
#
#   agent/scripts/install_launchd.sh              # install (or reinstall) and start
#   agent/scripts/install_launchd.sh --uninstall  # stop and remove
#
# AGENT_BIN overrides which `claude-telegram-agent` to run (default: this checkout's
# .venv, else whatever is on PATH).

LABEL="com.claudepoint.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/claude-telegram-agent.log"
DOMAIN="gui/$(id -u)"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "macOS only — on Linux use scripts/claude-telegram-agent.service." >&2
  exit 1
fi

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$PLIST"
  echo "Removed $LABEL."
  exit 0
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

# launchd starts jobs with a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin), so the Agent
# SDK wouldn't find `claude` (nor `node`, for an npm-installed claude). Bake in the
# directories they resolve to right now, plus the usual Homebrew locations.
CLAUDE_BIN="$(command -v claude || true)"
if [ -z "$CLAUDE_BIN" ]; then
  echo "'claude' CLI not found on PATH — the agent needs it. Install Claude Code first." >&2
  exit 1
fi
JOB_PATH="$(dirname "$CLAUDE_BIN")"
NODE_BIN="$(command -v node || true)"
[ -n "$NODE_BIN" ] && JOB_PATH="$JOB_PATH:$(dirname "$NODE_BIN")"
JOB_PATH="$JOB_PATH:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$AGENT_BIN</string>
    <string>run</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$JOB_PATH</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>$HOME</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$LOG</string>
  <key>StandardErrorPath</key>
  <string>$LOG</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null

launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed and started $LABEL ($AGENT_BIN)."
echo "  status:  launchctl print $DOMAIN/$LABEL | grep -E 'state|pid'"
echo "  logs:    tail -f $LOG"
echo "  remove:  $0 --uninstall"
