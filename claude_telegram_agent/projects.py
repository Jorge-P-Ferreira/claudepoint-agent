"""Project discovery + on-demand create/add.

PLACEHOLDER — no implementation.

  discover(project_roots) -> [ {project_id, path, name} ]
    * expanduser + resolve each root
    * a "project" = a root itself, or an immediate subdir (config choice);
      optionally filter to dirs containing .git / pyproject.toml / package.json
    * project_id = sha1(abspath)[:16]
  within_allowlist(path, project_roots) -> bool
    * guards `new_session`, and both operations below — the only real security
      boundary for `create_project`/`add_project` (../../shared/commands.md)
  create_project(name, project_roots, git_init=False) -> {project_id, path, name}
    * mkdir under the first configured root; NotADirectoryError if a name
      collides; `git init` when requested
  add_project(path, project_roots) -> {project_id, path, name}
    * register an existing dir; raises PermissionError if not within_allowlist
"""


def discover(project_roots: list[str]) -> list[dict]:  # pragma: no cover - placeholder
    raise NotImplementedError("claude_telegram_agent.projects.discover")


def within_allowlist(path: str, project_roots: list[str]) -> bool:  # pragma: no cover - placeholder
    raise NotImplementedError("claude_telegram_agent.projects.within_allowlist")
