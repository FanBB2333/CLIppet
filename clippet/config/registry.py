"""Configuration registry for CLIppet adapters."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from clippet.adapters.claude import ClaudeAdapter
from clippet.adapters.codex import CodexAdapter
from clippet.adapters.qoder import QoderAdapter
from clippet.models import IsolationConfig
from clippet.orchestrator import ClippetRunner


class CredentialProfileConfig(BaseModel):
    """Reusable credential profile for isolated adapter execution."""

    files: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)

    def to_isolation_config(self) -> IsolationConfig:
        """Convert the profile into a request-level isolation config."""

        return IsolationConfig(
            credential_files={
                relative_path: _expand_string(source_path)
                for relative_path, source_path in self.files.items()
            },
            env_overrides={
                variable_name: _expand_string(value)
                for variable_name, value in self.env.items()
            },
        )


class AdapterConfig(BaseModel):
    """Configuration for a single adapter.

    Attributes:
        adapter_type: Type of adapter - "claude", "codex", or "qoder".
        name: Registration name for the adapter in the runner.
        options: Adapter constructor kwargs (model, etc.).
    """

    adapter_type: Literal["claude", "codex", "qoder"]
    name: str
    options: dict[str, Any] = Field(default_factory=dict)


class ClippetConfig(BaseModel):
    """Top-level configuration for CLIppet.

    Attributes:
        adapters: List of adapter configurations.
        default_timeout: Default timeout in seconds for agent execution.
        max_workers: Maximum number of worker threads for parallel execution.
        credential_profiles: Reusable isolation profiles referenced by adapters.
    """

    adapters: list[AdapterConfig]
    default_timeout: int = 900
    max_workers: int = 4
    credential_profiles: dict[str, CredentialProfileConfig] = Field(default_factory=dict)


def load_config(path: Path | str) -> ClippetConfig:
    """Load and parse a YAML or JSON config file into ClippetConfig.

    Args:
        path: Path to the YAML (.yml/.yaml) or JSON (.json) configuration file.

    Returns:
        Parsed ClippetConfig object.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        yaml.YAMLError: If the YAML is invalid.
        json.JSONDecodeError: If the JSON is invalid.
        pydantic.ValidationError: If the config doesn't match the schema.
    """

    path = Path(path)
    suffix = path.suffix.lower()

    with open(path, "r", encoding="utf-8") as f:
        if suffix == ".json":
            raw_config = json.load(f) or {}
        else:
            raw_config = yaml.safe_load(f) or {}

    return ClippetConfig(**raw_config)


def create_runner_from_config(config: ClippetConfig) -> ClippetRunner:
    """Create a ClippetRunner and register all adapters from config.

    Args:
        config: The ClippetConfig containing adapter definitions.

    Returns:
        Configured ClippetRunner with all adapters registered.

    Raises:
        ValueError: If an unknown adapter_type or credential profile is specified.
    """

    adapter_classes = {
        "claude": ClaudeAdapter,
        "codex": CodexAdapter,
        "qoder": QoderAdapter,
    }

    runner = ClippetRunner(max_workers=config.max_workers)

    for adapter_config in config.adapters:
        adapter_class = adapter_classes.get(adapter_config.adapter_type)

        if adapter_class is None:
            raise ValueError(
                f"Unknown adapter type: '{adapter_config.adapter_type}'. "
                f"Valid types are: {list(adapter_classes.keys())}"
            )

        adapter_options = dict(adapter_config.options)
        credential_profile_name = adapter_options.pop("credential_profile", None)

        default_isolation = None

        if credential_profile_name is not None:
            profile = config.credential_profiles.get(credential_profile_name)
            if profile is None:
                raise ValueError(
                    f"Unknown credential profile: '{credential_profile_name}'"
                )
            default_isolation = profile.to_isolation_config()

        adapter = adapter_class(**adapter_options)
        if default_isolation is not None:
            adapter.default_isolation = default_isolation
        runner.register(adapter_config.name, adapter)

    return runner


def _expand_string(value: str) -> str:
    """Expand shell-style environment variables and user home markers."""

    return os.path.expanduser(os.path.expandvars(value))
