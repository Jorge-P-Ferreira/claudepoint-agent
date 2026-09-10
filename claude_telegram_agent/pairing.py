"""One-time pairing. Flow: ../../docs/auth-and-onboarding.md."""

from __future__ import annotations

import socket

import httpx

from . import __version__, config


def pair(
    code: str,
    broker: str,
    project_roots: list[str] | None = None,
    default_mode: str = "default",
) -> None:
    response = httpx.post(
        f"{broker.rstrip('/')}/agent/pair",
        json={"code": code, "hostname": socket.gethostname(), "agent_version": __version__},
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()

    path = config.save_config(
        {
            "broker_url": data["broker_ws_url"],
            "laptop_token": data["laptop_token"],
            "laptop_id": data["laptop_id"],
            "project_roots": project_roots or ["."],
            "default_mode": default_mode,
        }
    )
    print(f"paired as {socket.gethostname()} — config written to {path}")
