"""The service renderers are pure — checked here without touching launchd/systemd/schtasks."""

from __future__ import annotations

import plistlib
from pathlib import Path

from claude_telegram_agent import service


def test_launchd_plist_runs_agent_at_load_and_keeps_it_alive() -> None:
    raw = service.render_launchd_plist(
        ["/venv/bin/claude-telegram-agent", "run"], "/opt/claude/bin:/usr/bin", Path("/l.log")
    )
    plist = plistlib.loads(raw)
    assert plist["Label"] == service.LABEL
    assert plist["ProgramArguments"] == ["/venv/bin/claude-telegram-agent", "run"]
    assert plist["EnvironmentVariables"] == {"PATH": "/opt/claude/bin:/usr/bin"}
    assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True
    assert plist["StandardOutPath"] == plist["StandardErrorPath"] == "/l.log"


def test_systemd_unit_quotes_paths_with_spaces_and_restarts() -> None:
    unit = service.render_systemd_unit(
        ["/home/me/my venv/bin/claude-telegram-agent", "run"], "/usr/bin", Path("/l.log")
    )
    assert 'ExecStart="/home/me/my venv/bin/claude-telegram-agent" run\n' in unit
    assert "Environment=PATH=/usr/bin\n" in unit
    assert "StandardOutput=append:/l.log\n" in unit
    assert "Restart=always\n" in unit
    assert "WantedBy=default.target\n" in unit


def test_windows_task_xml_runs_at_logon_without_time_limit() -> None:
    xml = service.render_windows_task_xml(
        [
            r"C:\Users\me\uv\tools\cta\Scripts\pythonw.exe",
            "-m",
            "claude_telegram_agent",
            "run",
            "--log-file",
            r"C:\Users\me\AppData\Local\claude-telegram-agent\agent.log",
        ],
        r"LAPTOP\me & co",
    )
    assert r"<Command>C:\Users\me\uv\tools\cta\Scripts\pythonw.exe</Command>" in xml
    assert (
        r"<Arguments>-m claude_telegram_agent run --log-file "
        r"C:\Users\me\AppData\Local\claude-telegram-agent\agent.log</Arguments>"
    ) in xml
    assert "<LogonTrigger>" in xml
    assert r"<UserId>LAPTOP\me &amp; co</UserId>" in xml
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml
    assert "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml
    assert "<RestartOnFailure>" in xml


def test_agent_command_points_at_this_interpreters_venv(monkeypatch, tmp_path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude-telegram-agent").touch()
    monkeypatch.setattr(service.sys, "executable", str(bin_dir / "python"))
    monkeypatch.setattr(service.sys, "platform", "darwin")
    assert service.agent_command() == [str(bin_dir / "claude-telegram-agent"), "run"]


def test_agent_command_on_windows_uses_pythonw_and_log_file(monkeypatch, tmp_path) -> None:
    (tmp_path / "pythonw.exe").touch()
    monkeypatch.setattr(service.sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(service.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert service.agent_command() == [
        str(tmp_path / "pythonw.exe"),
        "-m",
        "claude_telegram_agent",
        "run",
        "--log-file",
        str(tmp_path / "local" / "claude-telegram-agent" / "agent.log"),
    ]
