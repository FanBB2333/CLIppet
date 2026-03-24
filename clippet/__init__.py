"""CLIppet - A unified adapter framework for orchestrating CLI AI agents."""

from clippet.adapters import ClaudeAdapter, CodexAdapter, QoderAdapter
from clippet.config.registry import (
    AdapterConfig,
    ClippetConfig,
    create_runner_from_config,
    load_config,
)
from clippet.models import AgentRequest, AgentResult, ToolCallRecord
from clippet.orchestrator import ClippetRunner
from clippet.protocols import ClippetAdapter

__version__ = "0.1.0"

__all__ = [
    # Models
    "AgentRequest",
    "AgentResult",
    "ToolCallRecord",
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
    "load_config",
    "create_runner_from_config",
    # Version
    "__version__",
]
