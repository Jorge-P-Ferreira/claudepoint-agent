"""The `run` loop — WebSocket client to the broker.

PLACEHOLDER — no implementation. Contract: ../../docs/transport-protocol.md.

  * connect(broker_url, Authorization: Bearer laptop_token, subprotocol "cta.v1")
  * send `hello` { agent_version, laptop_name, projects, resumable_sessions }
  * heartbeat every 20s; answer `ping` with `heartbeat`
  * recv loop: dedupe on frame id; `cmd` -> session_manager.dispatch(frame)
  * send `ack` for every `cmd`
  * on drop: backoff 1,2,5,15,30s (+/-20% jitter) then reconnect and re-`hello`
"""


async def run(config: dict) -> None:  # pragma: no cover - placeholder
    raise NotImplementedError("claude_telegram_agent.daemon.run")
