"""Local config for the laptop agent.

Written by `pairing.pair()`, read by `run`/`status`, deleted by `unpair`. Resolves to
`~/.config/claude-telegram-agent/config.toml` on Linux/macOS (platformdirs' per-OS
equivalent elsewhere, e.g. Windows) per agent/README.md's schema:
`broker_url, laptop_token, laptop_id, project_roots, default_mode`.

Serialization is hand-rolled (not a TOML-writer dependency) since the schema is a flat
set of string/list keys — reading uses stdlib `tomllib`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import platformdirs

APP_NAME = "claude-telegram-agent"
CONFIG_FILENAME = "config.toml"


def config_path() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False)) / CONFIG_FILENAME


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"No config at {path} — run "
            "`claude-telegram-agent pair <code> --broker <url>` first."
        )
    with path.open("rb") as f:
        return tomllib.load(f)


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_config(data: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{key} = {_toml_scalar(value)}" for key, value in data.items())
    path.write_text(body + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # chmod is a no-op on Windows; NTFS ACLs aren't managed here
    return path


def delete_config() -> bool:
    path = config_path()
    if path.exists():
        path.unlink()
        return True
    return False
