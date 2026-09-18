"""Project discovery, creation, and registration (Phase 6: shared/commands.md)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


class ProjectError(Exception):
    """A user-facing rejection reason for `create_project`/`add_project` — `str(exc)`
    is sent back verbatim (via the `project_result` event's `message` field) as far as
    the phone, so its wording should already be phone-appropriate."""


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


def create_project(name: str, git_init: bool, project_roots: list[str]) -> dict[str, str]:
    """Creates `name` as a fresh directory under `project_roots[0]` (the only root
    `create_project` targets — `shared/commands.md` only says "one of
    `CTA_PROJECT_ROOTS`", and nothing in the manual-QA transcript exercises picking
    among several), optionally running `git init` inside it.

    Raises `ProjectError` for: an empty/`.`/`..`/path-separator-containing name (a
    cheap check *before* ever touching the filesystem, so `"../../etc"` is rejected on
    the name itself, not left to `within_allowlist` to catch after the join); a
    resolved target outside every configured root (belt-and-suspenders, since the
    cheap check above should already make this unreachable); an already-existing
    target; or a failed `mkdir`/`git init`.
    """
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ProjectError(f"'{name}' isn't a valid project name.")
    if not project_roots:
        raise ProjectError("No project roots are configured on this laptop.")

    root_path = Path(project_roots[0]).expanduser().resolve()
    target = root_path / name
    if not within_allowlist(str(target), project_roots):
        raise ProjectError(f"'{name}' would resolve outside this laptop's allowed roots.")
    if target.exists():
        raise ProjectError(f"'{name}' already exists on this laptop.")

    try:
        target.mkdir(parents=True)
    except OSError as exc:
        raise ProjectError(f"Couldn't create '{name}': {exc}") from exc

    if git_init:
        try:
            subprocess.run(
                ["git", "init"], cwd=target, check=True, capture_output=True, text=True
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ProjectError(f"Created '{name}' but `git init` failed: {exc}") from exc

    return {"project_id": _project_id(target), "path": str(target), "name": target.name}


def add_project(
    path: str, project_roots: list[str], laptop_name: str = "this laptop"
) -> dict[str, str]:
    """Registers an existing directory as a project. Raises `ProjectError` if `path`
    isn't `within_allowlist` or isn't a directory. `laptop_name` is only used to word
    the rejection message (exact copy per `docs/UX.md` §10)."""
    if not within_allowlist(path, project_roots):
        raise ProjectError(
            f"That path isn't under an allowed root on {laptop_name}. Ask whoever set "
            "up that laptop to add it to CTA_PROJECT_ROOTS, or pick a project already "
            "inside one."
        )
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise ProjectError(f"'{path}' isn't a directory.")

    return {"project_id": _project_id(resolved), "path": str(resolved), "name": resolved.name}
