#!/usr/bin/env bash
set -euo pipefail

# Installs the laptop agent and prints the next steps. The Claude Agent SDK shells out
# to the local `claude` CLI and rides its existing subscription login — no
# ANTHROPIC_API_KEY needed unless you deliberately want the agent to bill a separate key.

cd "$(dirname "$0")/.."

if command -v pipx >/dev/null 2>&1; then
  pipx install --force .
elif command -v uv >/dev/null 2>&1; then
  uv tool install --force .
else
  pip install --user -e .
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "warning: 'claude' CLI not found on PATH — the agent needs it. Install Claude Code first." >&2
fi

echo
echo "Installed. Next steps:"
echo "  1. On the bot: /pair -> copy the code"
echo "  2. claude-telegram-agent pair <code> --broker https://<broker-host>"
echo "  3. claude-telegram-agent run   # foreground; see run_local.sh for local broker dev"

echo
echo "Optional background service (starts at login, restarts on crash):"
echo "  claude-telegram-agent service install"
