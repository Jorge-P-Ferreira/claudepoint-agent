"""Project discovery. `create_project`/`add_project` land in Phase 6 (shared/commands.md)."""

from __future__ import annotations

import hashlib
from pathlib import Path


def _project_id(path: Path) -> str:
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16]


def discover(project_roots: list[str]) -> list[dict]:
    """Each entry in `project_roots` is a container directory (e.g. `~/projects`); its
    immediate subdirectories are the projects, per project_id = sha1(abspath)[:16]."""
    projects: list[dict] = []
    seen_ids: set[str] = set()
    for root in project_roots:
        root_path = Path(root).expanduser().resolve()
        if not root_path.is_dir():
            continue
        for entry in sorted(root_path.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            project_id = _project_id(entry)
            if project_id in seen_ids:
                continue
            seen_ids.add(project_id)
            projects.append({"project_id": project_id, "path": str(entry), "name": entry.name})
    return projects


def within_allowlist(path: str, project_roots: list[str]) -> bool:
    """True if `path` resolves to somewhere under one of `project_roots`.

    The only real security boundary for `new_session`/`create_project`/`add_project` —
    resolves symlinks before comparing, so a symlink inside a root that points outside
    it correctly fails.
    """
    resolved = Path(path).expanduser().resolve()
    for root in project_roots:
        root_path = Path(root).expanduser().resolve()
        try:
            resolved.relative_to(root_path)
            return True
        except ValueError:
            continue
    return False
