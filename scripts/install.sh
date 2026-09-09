#!/usr/bin/env bash
set -euo pipefail

# PLACEHOLDER — prints the steps, runs nothing.

echo "Install the laptop agent:"
echo "  1. cd agent && pipx install .        # or: uv tool install ."
echo "  2. On the bot:  /pair   -> copy the code"
echo "  3. claude-telegram-agent pair <code> --broker https://<broker-host>"
echo "  4. Set ANTHROPIC_API_KEY in your shell profile"
echo "  5. Enable the background service:"
echo "     mkdir -p ~/.config/systemd/user"
echo "     cp scripts/claude-telegram-agent.service ~/.config/systemd/user/"
echo "     systemctl --user enable --now claude-telegram-agent"
echo "     loginctl enable-linger \"\$USER\"   # keep it running after logout"

exit 0
