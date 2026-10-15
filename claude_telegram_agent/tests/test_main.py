"""__main__: `pair`'s project-folder handling, and what `run` does when there's no
pairing to run with."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from claude_telegram_agent import __main__ as cli
from claude_telegram_agent import config, daemon, service


def test_pair_creates_missing_project_roots(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        cli.pairing, "pair", lambda code, broker, project_roots, default_mode: calls.append(
            project_roots
        )
    )
    root = tmp_path / "Projects"
    args = argparse.Namespace(code="c", broker="b", project_roots=str(root), mode="default")

    assert cli._cmd_pair(args) == 0

    assert root.is_dir()
    assert calls == [[str(root)]]


def test_pair_defaults_to_home_projects() -> None:
    args = cli.build_parser().parse_args(["pair", "c", "--broker", "b"])

    assert args.project_roots == str(Path.home() / "Projects")


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
