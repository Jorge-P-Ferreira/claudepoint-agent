"""__main__._cmd_run: what `run` does when there's no pairing to run with."""

from __future__ import annotations

import argparse

import pytest

from claude_telegram_agent import __main__ as cli
from claude_telegram_agent import config, daemon, service


def _args() -> argparse.Namespace:
    return argparse.Namespace(log_file=None)


def test_run_cleans_up_config_and_service_after_being_unpaired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps: list[str] = []

    async def fake_run(_cfg: dict) -> None:
        raise daemon.LaptopUnpaired

    monkeypatch.setattr(config, "load_config", lambda: {"broker_url": "x"})
    monkeypatch.setattr(daemon, "run", fake_run)
    def fake_delete_config() -> bool:
        steps.append("config")
        return True

    def fake_uninstall() -> str:
        steps.append("service")
        return "Removed."

    monkeypatch.setattr(config, "delete_config", fake_delete_config)
    monkeypatch.setattr(service, "uninstall", fake_uninstall)

    assert cli._cmd_run(_args()) == 0
    assert steps == ["config", "service"]  # service last: removing it stops this process


def test_run_without_config_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> dict:
        raise FileNotFoundError("no config")

    monkeypatch.setattr(config, "load_config", missing)
    assert cli._cmd_run(_args()) == 0
