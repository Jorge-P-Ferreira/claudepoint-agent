"""daemon.py reconnect behavior under a forced connection drop."""

from __future__ import annotations

import pytest
import websockets

from claude_telegram_agent import daemon


async def test_run_reconnects_after_connection_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_run_once(config: dict) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise websockets.exceptions.ConnectionClosed(None, None)
        raise SystemExit  # stop the loop once we've proven a retry happened

    monkeypatch.setattr(daemon, "_run_once", fake_run_once)
    monkeypatch.setattr(daemon, "RECONNECT_DELAY_SECONDS", 0)

    with pytest.raises(SystemExit):
        await daemon.run({"broker_url": "ws://localhost", "laptop_token": "t"})

    assert calls == 2


async def test_run_reconnects_after_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_run_once(config: dict) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionRefusedError("no route to host")
        raise SystemExit

    monkeypatch.setattr(daemon, "_run_once", fake_run_once)
    monkeypatch.setattr(daemon, "RECONNECT_DELAY_SECONDS", 0)

    with pytest.raises(SystemExit):
        await daemon.run({"broker_url": "ws://localhost", "laptop_token": "t"})

    assert calls == 2
