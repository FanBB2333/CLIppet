"""Named environment profile management for CLIppet.

Two entry types are supported:

* ``home`` (default for new entries) — a persistent fake ``$HOME`` living under
  ``~/.clippet/envs/<name>/``.  CLIppet creates and owns the directory.  At
  launch time the directory is used directly as ``$HOME`` (second-home mode),
  so all agent state (sessions, history, MCP, tokens) persists across runs.

* ``file`` (legacy) — a path alias pointing to an external config file.  The
  file is copied into a fresh temp HOME on each run via ``FileCredentialProvider``;
  agent state written during the run is discarded on exit.

The on-disk schema in ``~/.clippet/environments.json`` is::

    {
      "<name>": {
        "type": "home",              # or "file" (legacy entries may omit this)
        "home_dir": "/abs/path",     # present when type == "home"
        "config_path": "/abs/path",  # present when type == "file"
        "description": ""
      },
      ...
    }
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ENV_TYPE_HOME = "home"
ENV_TYPE_FILE = "file"

_SEED_SOURCES: dict[str, str] = {
    "claude": ".claude",
    "codex": ".codex",
    "gemini": ".gemini",
}


def get_clippet_root() -> Path:
    """Return ``~/.clippet/`` — CLIppet's per-user state directory."""

    return Path.home() / ".clippet"


def get_environments_file() -> Path:
    """Return the path to the environments JSON file."""

    return get_clippet_root() / "environments.json"


def get_envs_root() -> Path:
    """Return ``~/.clippet/envs/`` — root directory for HOME containers."""

    return get_clippet_root() / "envs"


def env_home_path(name: str) -> Path:
    """Return the canonical HOME path for an env name (does not check existence)."""

    if not name or "/" in name or name in (".", "..") or name.startswith("."):
        raise ValueError(
            f"Invalid environment name: '{name}'. "
            "Names must be non-empty, must not contain '/', and must not start with '.'."
        )
    return get_envs_root() / name


def load_environments() -> dict[str, dict]:
    """Read and parse the environments JSON file."""

    env_file = get_environments_file()

    if not env_file.exists():
        return {}

    try:
        with open(env_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def save_environments(envs: dict[str, dict]) -> None:
    """Write the environments dictionary as formatted JSON."""

    env_file = get_environments_file()
    env_file.parent.mkdir(parents=True, exist_ok=True)

    with open(env_file, "w", encoding="utf-8") as f:
        json.dump(envs, f, indent=2)


def entry_type(profile: dict) -> str:
    """Return the type of an env entry. Legacy entries (no ``type`` field) are
    inferred from which path field is set."""

    declared = profile.get("type")
    if declared in (ENV_TYPE_HOME, ENV_TYPE_FILE):
        return declared
    if profile.get("home_dir"):
        return ENV_TYPE_HOME
    return ENV_TYPE_FILE


def get_environment(name: str) -> dict:
    """Return the environment profile with the given name."""

    envs = load_environments()

    if name not in envs:
        available = ", ".join(sorted(envs.keys())) if envs else "(none)"
        raise KeyError(
            f"Environment '{name}' not found. "
            f"Available environments: {available}"
        )

    return envs[name]


def add_environment(
    name: str,
    config_path: str | Path,
    description: str = "",
) -> None:
    """Register a **file-mode** env entry (legacy path alias).

    Use :func:`create_home_env` for the modern HOME-container mode.
    """

    config_path = Path(config_path).resolve()

    if not config_path.exists():
        raise FileNotFoundError(f"Config file does not exist: {config_path}")

    envs = load_environments()
    envs[name] = {
        "type": ENV_TYPE_FILE,
        "config_path": str(config_path),
        "description": description,
    }
    save_environments(envs)


def _seed_from_real_home(target: Path, agents: list[str]) -> list[str]:
    """Copy selected agent config dirs from the real ``$HOME`` into *target*.

    Returns the list of agent names that were actually seeded (skips those
    whose source directory does not exist).
    """

    real_home = Path.home()
    seeded: list[str] = []
    for agent in agents:
        rel = _SEED_SOURCES.get(agent)
        if rel is None:
            continue
        src = real_home / rel
        if not src.is_dir():
            continue
        dst = target / rel
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True)
        seeded.append(agent)
    return seeded


def create_home_env(
    name: str,
    *,
    from_current: bool = False,
    agents: list[str] | None = None,
    description: str = "",
    overwrite: bool = False,
) -> Path:
    """Create a HOME-container env at ``~/.clippet/envs/<name>/``.

    Args:
        name: Env name. Must be a valid directory name (no ``/``, no leading dot).
        from_current: When True, copy the existing ``~/.claude/`` / ``~/.codex/``
            / ``~/.gemini/`` directories into the new env as seeds. When False,
            the env starts empty.
        agents: Restrict seeding to a subset of agents (``"claude"``, ``"codex"``,
            ``"gemini"``). Ignored when ``from_current`` is False. Default seeds all.
        description: Optional human-readable description.
        overwrite: When True, replaces an existing env (registry + directory).
            When False (default), raises if the env already exists.

    Returns:
        The absolute path to the new HOME directory.
    """

    home_dir = env_home_path(name)
    envs = load_environments()

    if name in envs and not overwrite:
        raise FileExistsError(
            f"Environment '{name}' already exists. "
            "Pass overwrite=True or remove it first."
        )
    if home_dir.exists() and not overwrite:
        raise FileExistsError(
            f"HOME directory already exists at '{home_dir}'. "
            "Pass overwrite=True or remove it first."
        )

    if home_dir.exists() and overwrite:
        shutil.rmtree(home_dir)

    home_dir.mkdir(parents=True, exist_ok=False)

    if from_current:
        targets = agents or list(_SEED_SOURCES.keys())
        _seed_from_real_home(home_dir, targets)

    envs[name] = {
        "type": ENV_TYPE_HOME,
        "home_dir": str(home_dir.resolve()),
        "description": description,
    }
    save_environments(envs)
    return home_dir


def clone_home_env(
    src: str,
    dst: str,
    *,
    description: str = "",
    overwrite: bool = False,
) -> Path:
    """Clone an existing HOME-container env to a new name.

    Only supports cloning ``home``-type envs. File-mode entries cannot be cloned
    (they are just path aliases — re-add them under another name instead).
    """

    profile = get_environment(src)
    if entry_type(profile) != ENV_TYPE_HOME:
        raise ValueError(
            f"Environment '{src}' is a file-mode entry and cannot be cloned. "
            "Use 'env add' to register the same config under a new name instead."
        )

    src_home = Path(profile["home_dir"])
    if not src_home.is_dir():
        raise FileNotFoundError(
            f"Source HOME directory '{src_home}' does not exist on disk."
        )

    dst_home = env_home_path(dst)
    envs = load_environments()

    if dst in envs and not overwrite:
        raise FileExistsError(f"Environment '{dst}' already exists.")
    if dst_home.exists() and not overwrite:
        raise FileExistsError(f"Target HOME directory already exists at '{dst_home}'.")

    if dst_home.exists() and overwrite:
        shutil.rmtree(dst_home)

    dst_home.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_home, dst_home, symlinks=True)

    envs[dst] = {
        "type": ENV_TYPE_HOME,
        "home_dir": str(dst_home.resolve()),
        "description": description,
    }
    save_environments(envs)
    return dst_home


def remove_environment(name: str, *, purge: bool = False) -> None:
    """Unregister an env.

    For ``home``-type entries, the HOME directory on disk is retained by default
    so user state isn't accidentally destroyed. Pass ``purge=True`` to also
    delete the directory tree.
    """

    envs = load_environments()

    if name not in envs:
        available = ", ".join(sorted(envs.keys())) if envs else "(none)"
        raise KeyError(
            f"Environment '{name}' not found. "
            f"Available environments: {available}"
        )

    profile = envs[name]
    del envs[name]
    save_environments(envs)

    if purge and entry_type(profile) == ENV_TYPE_HOME:
        home_dir = Path(profile.get("home_dir", ""))
        if home_dir.is_dir():
            shutil.rmtree(home_dir, ignore_errors=True)


def list_environments() -> dict[str, dict]:
    """Return all registered environment profiles."""

    return load_environments()
