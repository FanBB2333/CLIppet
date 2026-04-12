"""Project-level .clippet.json configuration discovery and loading.

This module provides functions to discover, load, and resolve project-level
configuration files that allow running `clippet codex` or `clippet claude`
directly inside a project directory.

Security considerations:
- .clippet.json must NOT store API keys, tokens, or raw secret values
- It only references credential/config files that live elsewhere
- extra="forbid" is used to reject unknown fields (prevents inline secrets)
- Relative paths must stay inside the project root (no .. escapes)
- Discovery stops at the nearest Git root
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


PROJECT_CONFIG_FILENAME = ".clippet.json"


class ProjectAgentConfig(BaseModel):
    """Configuration for a single agent in .clippet.json.
    
    At least one of config_path or codex_config_path must be provided.
    extra="forbid" rejects unknown fields to prevent inline secrets.
    """
    model_config = ConfigDict(extra="forbid")

    config_path: str | None = None
    codex_config_path: str | None = None

    @model_validator(mode="after")
    def validate_minimum_fields(self) -> "ProjectAgentConfig":
        if self.config_path is None and self.codex_config_path is None:
            raise ValueError("At least one config path must be provided")
        return self


class ProjectAgentsConfig(BaseModel):
    """Container for agent configurations in .clippet.json.
    
    extra="forbid" rejects unknown agent names.
    """
    model_config = ConfigDict(extra="forbid")

    claude: ProjectAgentConfig | None = None
    codex: ProjectAgentConfig | None = None


class ProjectConfig(BaseModel):
    """Root schema for .clippet.json files.
    
    extra="forbid" rejects unknown top-level fields.
    """
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    agents: ProjectAgentsConfig


@dataclass(frozen=True)
class ResolvedProjectLaunch:
    """Resolved configuration for launching an agent from project config.
    
    All paths are resolved to absolute paths.
    """
    agent_type: Literal["claude", "codex"]
    project_root: Path
    config_file: Path
    config_path: str | None
    codex_config_path: str | None


def _find_git_root(start_dir: Path) -> Path | None:
    """Find the nearest Git root directory by walking up from start_dir.
    
    Args:
        start_dir: Directory to start searching from.
        
    Returns:
        Path to the Git root directory, or None if not in a Git repository.
    """
    current = start_dir.resolve()
    
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            # Reached filesystem root
            return None
        current = parent


def find_project_config(start_dir: Path) -> Path | None:
    """Find .clippet.json by walking up from start_dir to the Git root.
    
    Discovery rules:
    - Searches from start_dir upward through parent directories
    - Stops at the nearest Git root (does not search beyond)
    - If not in a Git repository, only checks the current directory
    
    Args:
        start_dir: Directory to start searching from.
        
    Returns:
        Path to .clippet.json if found, None otherwise.
    """
    start_dir = start_dir.resolve()
    git_root = _find_git_root(start_dir)
    
    if git_root is None:
        # Not in a Git repository - only check current directory
        current_only = start_dir / PROJECT_CONFIG_FILENAME
        return current_only if current_only.is_file() else None
    
    # Walk up from start_dir to git_root
    candidates = [start_dir, *start_dir.parents]
    
    for candidate in candidates:
        config_file = candidate / PROJECT_CONFIG_FILENAME
        if config_file.is_file():
            return config_file
        # Stop at git root
        if candidate == git_root:
            break
    
    return None


def load_project_config(path: Path | str) -> ProjectConfig:
    """Load and validate a .clippet.json file.
    
    Args:
        path: Path to the .clippet.json file.
        
    Returns:
        Validated ProjectConfig object.
        
    Raises:
        FileNotFoundError: If the config file does not exist.
        json.JSONDecodeError: If the file contains invalid JSON.
        pydantic.ValidationError: If the config does not match the schema
            (including unknown fields due to extra="forbid").
    """
    path = Path(path)
    content = path.read_text(encoding="utf-8")
    data = json.loads(content)
    return ProjectConfig.model_validate(data)


def _resolve_project_path(raw_path: str | None, project_root: Path) -> str | None:
    """Resolve a path from .clippet.json to an absolute path.
    
    Resolution rules:
    - None returns None
    - Absolute paths are returned as-is
    - Paths starting with ~ are expanded
    - Relative paths are resolved relative to project_root
    - Relative paths using .. to escape project_root are rejected
    
    Args:
        raw_path: The raw path string from the config file.
        project_root: The directory containing .clippet.json.
        
    Returns:
        Resolved absolute path as a string, or None if raw_path is None.
        
    Raises:
        ValueError: If a relative path escapes the project root.
    """
    if raw_path is None:
        return None
    
    path = Path(raw_path)
    
    # Handle ~ expansion
    if raw_path.startswith("~"):
        return str(path.expanduser())
    
    # Absolute paths are allowed as-is
    if path.is_absolute():
        return str(path)
    
    # Relative paths: resolve and check they stay inside project root
    resolved = (project_root / path).resolve()
    
    # Check that the resolved path is inside the project root
    try:
        resolved.relative_to(project_root)
    except ValueError:
        raise ValueError(
            f"Relative path '{raw_path}' must stay inside the project root. "
            f"Use an absolute path to reference files outside the project."
        )
    
    return str(resolved)


def resolve_project_launch(
    start_dir: Path,
    agent_type: Literal["claude", "codex"],
) -> ResolvedProjectLaunch:
    """Find and resolve project-level config for launching an agent.
    
    This is the main entry point for project-level config resolution.
    It discovers .clippet.json, loads it, validates the requested agent
    is configured, and resolves all paths to absolute paths.
    
    Args:
        start_dir: Directory to start searching from (usually cwd).
        agent_type: The agent to launch ("claude" or "codex").
        
    Returns:
        ResolvedProjectLaunch with all paths resolved.
        
    Raises:
        FileNotFoundError: If no .clippet.json is found.
        KeyError: If the requested agent is not configured.
        ValueError: If a relative path escapes the project root.
        pydantic.ValidationError: If the config is invalid.
    """
    config_file = find_project_config(start_dir)
    if config_file is None:
        raise FileNotFoundError(
            "No .clippet.json found from the current directory to the Git root."
        )

    project_root = config_file.parent.resolve()
    project_config = load_project_config(config_file)
    agent_config = getattr(project_config.agents, agent_type)
    
    if agent_config is None:
        raise KeyError(
            f"Agent '{agent_type}' is not configured in {config_file}"
        )

    return ResolvedProjectLaunch(
        agent_type=agent_type,
        project_root=project_root,
        config_file=config_file,
        config_path=_resolve_project_path(agent_config.config_path, project_root),
        codex_config_path=_resolve_project_path(
            agent_config.codex_config_path,
            project_root,
        ),
    )
