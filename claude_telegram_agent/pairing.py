"""One-time pairing.

PLACEHOLDER — no implementation. Flow: ../../docs/auth-and-onboarding.md.

  pair(code, broker):
    r = httpx.post(f"{broker}/agent/pair",
                   json={"code": code, "hostname": socket.gethostname(),
                         "agent_version": __version__})
    -> { laptop_id, laptop_token, broker_ws_url }
    write ~/.config/claude-telegram-agent/config.toml  (os.chmod 0o600)
"""


def pair(code: str, broker: str) -> None:  # pragma: no cover - placeholder
    raise NotImplementedError("claude_telegram_agent.pairing.pair")
