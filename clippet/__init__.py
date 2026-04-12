"""CLIppet - A unified adapter framework for orchestrating CLI AI agents."""

from clippet.adapters import BaseAdapter, BaseSubprocessAdapter
from clippet.adapters import ClaudeAdapter, CodexAdapter, QoderAdapter
from clippet.config.detector import (
    create_adapter_from_claude_config,
    create_adapter_from_codex_config,
    create_adapter_from_config_file,
    create_adapter_with_second_home,
    detect_config_type,
)
from clippet.config.environments import (
    add_environment,
    get_environment,
    list_environments,
    load_environments,
    remove_environment,
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
from clippet.isolation import (
    AGENT_CONFIG_PATHS,
    CredentialProvider,
    CredentialSet,
    DirectoryCopyProvider,
    EnvVarCredentialProvider,
    FileCredentialProvider,
    IsolatedEnvironment,
)
from clippet.models import AgentRequest, AgentResult, IsolationConfig, ToolCallRecord
from clippet.orchestrator import ClippetRunner
from clippet.protocols import ClippetAdapter

# OpenAIAdapter is conditionally available
try:
    from clippet.adapters.api import OpenAIAdapter

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

__version__ = "0.1.0"

__all__ = [
    # Models
    "AgentRequest",
    "AgentResult",
    "IsolationConfig",
    "ToolCallRecord",
    "IsolatedEnvironment",
    "CredentialProvider",
    "FileCredentialProvider",
    "EnvVarCredentialProvider",
    "DirectoryCopyProvider",
    "CredentialSet",
    "AGENT_CONFIG_PATHS",
    # Protocol
    "ClippetAdapter",
    # Base classes
    "BaseAdapter",
    "BaseSubprocessAdapter",
    # Adapters
    "ClaudeAdapter",
    "CodexAdapter",
    "QoderAdapter",
    # Orchestrator
    "ClippetRunner",
    # Config
    "AdapterConfig",
    "ClippetConfig",
    "CredentialProfileConfig",
    "load_config",
    "create_runner_from_config",
    # Config detection
    "detect_config_type",
    "create_adapter_from_config_file",
    "create_adapter_from_claude_config",
    "create_adapter_from_codex_config",
    "create_adapter_with_second_home",
    # Project-level config
    "PROJECT_CONFIG_FILENAME",
    "ProjectAgentConfig",
    "ProjectAgentsConfig",
    "ProjectConfig",
    "ResolvedProjectLaunch",
    "find_project_config",
    "load_project_config",
    "resolve_project_launch",
    # Environments
    "load_environments",
    "get_environment",
    "add_environment",
    "remove_environment",
    "list_environments",
    # Version
    "__version__",
]

if _HAS_OPENAI:
    __all__.append("OpenAIAdapter")
