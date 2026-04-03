"""CLIppet - A unified adapter framework for orchestrating CLI AI agents."""

from clippet.adapters import ClaudeAdapter, CodexAdapter, QoderAdapter
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
    # Version
    "__version__",
]
