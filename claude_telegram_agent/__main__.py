"""CLI entrypoint: claude-telegram-agent {pair|run|status|unpair}.

PLACEHOLDER — no implementation.

  pair <code> --broker <url>   -> pairing.pair(...)
  run                          -> daemon.run(load_config())
  status                       -> print config + projects.discover() + last known conn state
  unpair                       -> shred ~/.config/claude-telegram-agent/config.toml
"""


def main() -> int:  # pragma: no cover - placeholder
    raise NotImplementedError("claude_telegram_agent.__main__.main")


if __name__ == "__main__":
    raise SystemExit(main())
