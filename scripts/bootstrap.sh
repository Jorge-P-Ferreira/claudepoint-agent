#!/usr/bin/env bash
set -euo pipefail

# One-paste laptop onboarding (macOS/Linux). The bot's /pair prints this line:
#
#   curl -fsSL https://raw.githubusercontent.com/Jorge-P-Ferreira/claudepoint-agent/main/scripts/bootstrap.sh | bash -s -- <CODE> <BROKER_URL>
#
# Installs the agent (via uv, which also fetches a Python if needed), pairs it, and
# starts it in the background at login. Projects live under ~/Projects unless
# CLAUDEPOINT_PROJECTS says otherwise. Safe to re-run with a fresh code.

CODE="${1:-}"
BROKER="${2:-}"
if [ -z "$CODE" ] || [ -z "$BROKER" ]; then
  echo "usage: bootstrap.sh <pairing-code> <broker-url>  (get both from /pair on the bot)" >&2
  exit 1
fi
PACKAGE="${CLAUDEPOINT_AGENT_SOURCE:-https://github.com/Jorge-P-Ferreira/claudepoint-agent/archive/refs/heads/main.zip}"
PROJECTS="${CLAUDEPOINT_PROJECTS:-$HOME/Projects}"

# Checked before pairing so a missing prerequisite doesn't burn the one-time code.
if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code isn't installed (no 'claude' on PATH)." >&2
  echo "Install it, run 'claude' once to log in, then paste the /pair line again." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "==> Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> Installing the agent"
uv tool install --force --python 3.12 "$PACKAGE"
AGENT="$(uv tool dir --bin)/claude-telegram-agent"

echo "==> Pairing (projects folder: $PROJECTS)"
mkdir -p "$PROJECTS"
"$AGENT" pair "$CODE" --broker "$BROKER" --project-roots "$PROJECTS"

echo "==> Starting it in the background at login"
"$AGENT" service install

echo
echo "Done — the bot should say this laptop is connected."
echo "If Claude Code was never logged in here, run 'claude' once to log in."
