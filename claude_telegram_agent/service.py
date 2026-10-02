"""Background service install/uninstall: `claude-telegram-agent service {install|uninstall}`.

One backend per OS, each a per-user job that starts at login and restarts the agent if
it dies:

- macOS: a launchd user agent (`gui/<uid>`), not a system daemon — the `claude` CLI
  keeps its login in the user's keychain, only unlocked once the user has logged in.
- Linux: a systemd user unit, plus `loginctl enable-linger` (best effort) so it keeps
  running after logout.
- Windows: a Task Scheduler task at user logon, run through `pythonw.exe` so no console
  window appears; output goes to `run --log-file`.

The `render_*` functions are pure (tested without touching the OS); `install`/
`uninstall` do the writing and the launchctl/systemctl/schtasks calls. Pair first —
the job only runs what `pair` already configured.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

LABEL = "com.claudepoint.agent"
SYSTEMD_UNIT = "claude-telegram-agent.service"
WINDOWS_TASK = "ClaudePoint Agent"


def log_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "claude-telegram-agent.log"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "claude-telegram-agent" / "agent.log"
    return Path.home() / ".local" / "state" / "claude-telegram-agent" / "agent.log"


def agent_command() -> list[str]:
    """The argv the job runs. Resolved from the interpreter running this install, so it
    points at whichever venv (uv tool, pipx, dev .venv) the agent is installed in."""
    bin_dir = Path(sys.executable).parent
    if sys.platform == "win32":
        pythonw = bin_dir / "pythonw.exe"
        exe = pythonw if pythonw.exists() else Path(sys.executable)
        return [str(exe), "-m", "claude_telegram_agent", "run", "--log-file", str(log_path())]
    script = bin_dir / "claude-telegram-agent"
    if script.exists():
        return [str(script), "run"]
    return [sys.executable, "-m", "claude_telegram_agent", "run"]


def job_path_env() -> str:
    """launchd/systemd start jobs with a bare PATH, so the Agent SDK wouldn't find
    `claude` (nor `node`, for an npm-installed claude). Bake in the directories they
    resolve to right now, plus the usual system and Homebrew locations."""
    dirs: list[str] = []
    for tool in ("claude", "node"):
        found = shutil.which(tool)
        if found:
            dirs.append(str(Path(found).parent))
    dirs += [
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    return ":".join(dict.fromkeys(dirs))


# ---- renderers (pure) -----------------------------------------------------------


def render_launchd_plist(command: list[str], path_env: str, log: Path) -> bytes:
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": command,
            "EnvironmentVariables": {"PATH": path_env},
            "WorkingDirectory": str(Path.home()),
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
        }
    )


def render_systemd_unit(command: list[str], path_env: str, log: Path) -> str:
    exec_start = " ".join(_systemd_quote(arg) for arg in command)
    return (
        "[Unit]\n"
        "Description=CLAUDEPOINT laptop agent\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        f"Environment=PATH={path_env}\n"
        f"StandardOutput=append:{log}\n"
        f"StandardError=append:{log}\n"
        "Restart=always\n"
        "RestartSec=10\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemd_quote(arg: str) -> str:
    if arg and not any(c in arg for c in ' "\\\'$%'):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_windows_task_xml(command: list[str], user: str) -> str:
    """Task Scheduler XML: at-logon trigger, restart on failure, no execution time
    limit (the default kills a task after 72h), and keeps running on battery."""
    program, args = command[0], subprocess.list2cmdline(command[1:])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>CLAUDEPOINT laptop agent</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(program)}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(str(Path.home()))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


# ---- install / uninstall -------------------------------------------------------


def install() -> str:
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    command = agent_command()
    if sys.platform == "darwin":
        return _install_launchd(command, log)
    if sys.platform == "win32":
        return _install_windows(command)
    return _install_systemd(command, log)


def uninstall() -> str:
    if sys.platform == "darwin":
        _run(["launchctl", "bootout", f"{_launchd_domain()}/{LABEL}"], check=False)
        _launchd_plist().unlink(missing_ok=True)
        return f"Removed {LABEL}."
    if sys.platform == "win32":
        _run(["schtasks", "/End", "/TN", WINDOWS_TASK], check=False)
        _run(["schtasks", "/Delete", "/TN", WINDOWS_TASK, "/F"], check=False)
        return f"Removed scheduled task '{WINDOWS_TASK}'."
    _run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT], check=False)
    _systemd_unit_path().unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"], check=False)
    return f"Removed {SYSTEMD_UNIT}."


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"  # type: ignore[attr-defined,unused-ignore]


def _launchd_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _install_launchd(command: list[str], log: Path) -> str:
    plist = _launchd_plist()
    plist.parent.mkdir(parents=True, exist_ok=True)
    domain = _launchd_domain()
    _run(["launchctl", "bootout", f"{domain}/{LABEL}"], check=False)
    plist.write_bytes(render_launchd_plist(command, job_path_env(), log))
    _run(["launchctl", "bootstrap", domain, str(plist)])
    return (
        f"Installed and started {LABEL}.\n"
        f"  status: launchctl print {domain}/{LABEL} | grep -E 'state|pid'\n"
        f"  logs:   tail -f {log}"
    )


def _systemd_unit_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "systemd" / "user" / SYSTEMD_UNIT


def _install_systemd(command: list[str], log: Path) -> str:
    unit = _systemd_unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(render_systemd_unit(command, job_path_env(), log), encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT])
    user = os.environ.get("USER", "")
    if user:
        _run(["loginctl", "enable-linger", user], check=False)
    return (
        f"Installed and started {SYSTEMD_UNIT}.\n"
        f"  status: systemctl --user status {SYSTEMD_UNIT}\n"
        f"  logs:   tail -f {log}"
    )


def _install_windows(command: list[str]) -> str:
    domain = os.environ.get("USERDOMAIN", "")
    username = os.environ.get("USERNAME", "")
    user = f"{domain}\\{username}" if domain else username
    xml_path = log_path().parent / "task.xml"
    xml_path.write_text(render_windows_task_xml(command, user), encoding="utf-16")
    _run(["schtasks", "/End", "/TN", WINDOWS_TASK], check=False)
    _run(["schtasks", "/Create", "/TN", WINDOWS_TASK, "/XML", str(xml_path), "/F"])
    _run(["schtasks", "/Run", "/TN", WINDOWS_TASK])
    return (
        f"Installed and started scheduled task '{WINDOWS_TASK}' (runs at logon).\n"
        f'  status: schtasks /Query /TN "{WINDOWS_TASK}"\n'
        f"  logs:   Get-Content -Wait {log_path()}"
    )


def _run(argv: list[str], check: bool = True) -> None:
    subprocess.run(argv, check=check, capture_output=not check)
