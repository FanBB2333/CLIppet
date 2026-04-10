"""Named environment profile management for CLIppet."""

from __future__ import annotations

import json
from pathlib import Path


def get_environments_file() -> Path:
    """Return the path to the environments JSON file.

    Returns:
        Path to ``~/.clippet/environments.json``.
    """

    return Path.home() / ".clippet" / "environments.json"


def load_environments() -> dict[str, dict]:
    """Read and parse the environments JSON file.

    Returns:
        Dictionary mapping environment names to their configuration.
        Returns an empty dict if the file does not exist or contains
        invalid JSON.
    """

    env_file = get_environments_file()

    if not env_file.exists():
        return {}

    try:
        with open(env_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def save_environments(envs: dict[str, dict]) -> None:
    """Write the environments dictionary as formatted JSON.

    Creates the ``~/.clippet/`` directory if it does not already exist.

    Args:
        envs: Dictionary mapping environment names to their configuration.
    """

    env_file = get_environments_file()
    env_file.parent.mkdir(parents=True, exist_ok=True)

    with open(env_file, "w", encoding="utf-8") as f:
        json.dump(envs, f, indent=2)


def get_environment(name: str) -> dict:
    """Return the environment profile with the given name.

    Args:
        name: Name of the environment to retrieve.

    Returns:
        Dictionary with ``config_path`` and optional ``description``.

    Raises:
        KeyError: If no environment with *name* exists.
    """

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
    """Add or update a named environment profile.

    Args:
        name: Name for the environment.
        config_path: Path to the CLIppet configuration file.
        description: Optional human-readable description.

    Raises:
        FileNotFoundError: If *config_path* does not point to an existing file.
    """

    config_path = Path(config_path).resolve()

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file does not exist: {config_path}"
        )

    envs = load_environments()
    envs[name] = {
        "config_path": str(config_path),
        "description": description,
    }
    save_environments(envs)


def remove_environment(name: str) -> None:
    """Remove a named environment profile.

    Args:
        name: Name of the environment to remove.

    Raises:
        KeyError: If no environment with *name* exists.
    """

    envs = load_environments()

    if name not in envs:
        available = ", ".join(sorted(envs.keys())) if envs else "(none)"
        raise KeyError(
            f"Environment '{name}' not found. "
            f"Available environments: {available}"
        )

    del envs[name]
    save_environments(envs)


def list_environments() -> dict[str, dict]:
    """Return all registered environment profiles.

    Returns:
        Dictionary mapping environment names to their configuration.
    """

    return load_environments()
