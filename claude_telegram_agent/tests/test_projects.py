"""projects.py — `within_allowlist` (the real filesystem security boundary) tested
adversarially, plus `create_project`/`add_project` (Phase 6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_telegram_agent import projects

# ---- within_allowlist — adversarial (prompt.md's explicit exit criterion) --------


def test_within_allowlist_accepts_a_real_subdirectory(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    (root / "demo").mkdir(parents=True)
    assert projects.within_allowlist(str(root / "demo"), [str(root)]) is True


def test_within_allowlist_rejects_dot_dot_traversal(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    (tmp_path / "secret").mkdir()
    traversal = str(root / ".." / "secret")
    assert projects.within_allowlist(traversal, [str(root)]) is False


def test_within_allowlist_rejects_a_symlink_escaping_the_root(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    link = root / "escape"
    link.symlink_to(outside)
    assert projects.within_allowlist(str(link), [str(root)]) is False


def test_within_allowlist_root_configured_as_a_symlink_still_bounds_correctly(
    tmp_path: Path,
) -> None:
    """A root itself can legitimately be a symlink (e.g. `~/projects` -> an external
    drive) — resolving it must still correctly accept paths under its real target and
    reject paths outside it."""
    real_root = tmp_path / "real_projects"
    real_root.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    root_link = tmp_path / "root_link"
    root_link.symlink_to(real_root)

    assert projects.within_allowlist(str(real_root / "demo"), [str(root_link)]) is True
    assert projects.within_allowlist(str(outside), [str(root_link)]) is False


def test_within_allowlist_accepts_the_root_itself(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    assert projects.within_allowlist(str(root), [str(root)]) is True


def test_within_allowlist_rejects_a_sibling_with_a_matching_string_prefix(
    tmp_path: Path,
) -> None:
    """`/root/code-evil` is not under `/root/code` even though it shares a string
    prefix — a naive `str.startswith` check would wrongly accept this."""
    root = tmp_path / "code"
    root.mkdir()
    sibling = tmp_path / "code-evil"
    sibling.mkdir()
    assert projects.within_allowlist(str(sibling), [str(root)]) is False


def test_within_allowlist_rejects_anything_outside_every_configured_root(
    tmp_path: Path,
) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    elsewhere = tmp_path / "c"
    elsewhere.mkdir()
    assert projects.within_allowlist(str(elsewhere), [str(root_a), str(root_b)]) is False
    assert projects.within_allowlist(str(root_b), [str(root_a), str(root_b)]) is True


# ---- create_project ---------------------------------------------------------------


def test_create_project_happy_path(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()

    result = projects.create_project("widget-api", git_init=False, project_roots=[str(root)])

    target = root / "widget-api"
    assert target.is_dir()
    assert result["name"] == "widget-api"
    assert result["path"] == str(target)
    assert result["project_id"] == projects._project_id(target)
    assert not (target / ".git").exists()


def test_create_project_with_git_init_creates_a_repo(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()

    projects.create_project("widget-api", git_init=True, project_roots=[str(root)])

    assert (root / "widget-api" / ".git").is_dir()


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "../escape"])
def test_create_project_rejects_invalid_names(tmp_path: Path, name: str) -> None:
    root = tmp_path / "projects"
    root.mkdir()

    with pytest.raises(projects.ProjectError):
        projects.create_project(name, git_init=False, project_roots=[str(root)])


def test_create_project_rejects_an_already_existing_target(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    (root / "widget-api").mkdir(parents=True)

    with pytest.raises(projects.ProjectError, match="already exists"):
        projects.create_project("widget-api", git_init=False, project_roots=[str(root)])


def test_create_project_no_roots_configured() -> None:
    with pytest.raises(projects.ProjectError):
        projects.create_project("widget-api", git_init=False, project_roots=[])


# ---- add_project --------------------------------------------------------------


def test_add_project_happy_path(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    existing = root / "legacy-app"
    existing.mkdir(parents=True)

    result = projects.add_project(str(existing), project_roots=[str(root)])

    assert result["name"] == "legacy-app"
    assert result["path"] == str(existing)
    assert result["project_id"] == projects._project_id(existing)


def test_add_project_rejects_a_path_outside_every_root(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()

    with pytest.raises(projects.ProjectError, match="allowed root"):
        projects.add_project(str(outside), project_roots=[str(root)], laptop_name="jorge-xps")


def test_add_project_error_message_includes_laptop_name(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()

    with pytest.raises(projects.ProjectError, match="jorge-xps"):
        projects.add_project(str(outside), project_roots=[str(root)], laptop_name="jorge-xps")


def test_add_project_rejects_a_symlink_escaping_the_root(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    link = root / "escape"
    link.symlink_to(outside)

    with pytest.raises(projects.ProjectError):
        projects.add_project(str(link), project_roots=[str(root)])


def test_add_project_rejects_a_file_not_a_directory(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    file_path = root / "not_a_dir.txt"
    file_path.write_text("hi")

    with pytest.raises(projects.ProjectError, match="isn't a directory"):
        projects.add_project(str(file_path), project_roots=[str(root)])
