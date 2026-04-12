"""Configuration utilities for CLIppet."""

from clippet.config.detector import (
    create_adapter_from_claude_config,
    create_adapter_from_codex_config,
    create_adapter_from_config_file,
    create_adapter_with_second_home,
    detect_config_type,
)
from clippet.config.project import (
    PROJECT_CONFIG_FILENAME,
    ProjectAgentConfig,
    ProjectAgentsConfig,
    ProjectConfig,
    ResolvedProjectLaunch,
    find_project_config,
    load_project_config,
    resolve_project_launch,
)
from clippet.config.registry import (
    AdapterConfig,
    ClippetConfig,
    CredentialProfileConfig,
    create_runner_from_config,
    load_config,
)

__all__ = [
    "AdapterConfig",
    "ClippetConfig",
    "CredentialProfileConfig",
    "create_adapter_from_claude_config",
    "create_adapter_from_codex_config",
    "create_adapter_from_config_file",
    "create_adapter_with_second_home",
    "create_runner_from_config",
    "detect_config_type",
    "load_config",
    # Project-level config
    "PROJECT_CONFIG_FILENAME",
    "ProjectAgentConfig",
    "ProjectAgentsConfig",
    "ProjectConfig",
    "ResolvedProjectLaunch",
    "find_project_config",
    "load_project_config",
    "resolve_project_launch",
]
