#!/usr/bin/env bash
set -euo pipefail

# Runs the agent in the foreground against a local broker (broker/scripts/run_local.sh
# must already be running). Pair first if you have no config yet:
#   claude-telegram-agent pair <code> --broker http://localhost:8000
#
# No ANTHROPIC_API_KEY needed — the Agent SDK uses the local `claude` CLI's own login.

exec claude-telegram-agent run
