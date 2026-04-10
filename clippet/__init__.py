"""CLIppet - A unified adapter framework for orchestrating CLI AI agents."""

from clippet.adapters import ClaudeAdapter, CodexAdapter, QoderAdapter
from clippet.config.detector import (
    create_adapter_from_claude_config,
    create_adapter_from_codex_config,
    create_adapter_from_config_file,
    detect_config_type,
)
from clippet.config.environments import (
    add_environment,
    get_environment,
    list_environments,
    load_environments,
    remove_environment,
)
from clippet.config.registry import (
    AdapterConfig,
    ClippetConfig,
    CredentialProfileConfig,
    create_runner_from_config,
    load_config,
)
from clippet.isolation import (
    CredentialProvider,
    CredentialSet,
    EnvVarCredentialProvider,
    FileCredentialProvider,
    IsolatedEnvironment,
)
from clippet.models import AgentRequest, AgentResult, IsolationConfig, ToolCallRecord
from clippet.orchestrator import ClippetRunner
from clippet.protocols import ClippetAdapter

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
    "CredentialSet",
    # Protocol
    "ClippetAdapter",
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
    # Environments
    "load_environments",
    "get_environment",
    "add_environment",
    "remove_environment",
    "list_environments",
    # Version
    "__version__",
]
