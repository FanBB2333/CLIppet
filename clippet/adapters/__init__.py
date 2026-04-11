"""CLIppet adapters for various CLI AI agents."""

from clippet.adapters.base import BaseAdapter, BaseSubprocessAdapter
from clippet.adapters.claude import ClaudeAdapter
from clippet.adapters.codex import CodexAdapter
from clippet.adapters.qoder import QoderAdapter

# OpenAIAdapter is conditionally available (requires `openai` package)
try:
    from clippet.adapters.api import OpenAIAdapter

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

__all__ = [
    "BaseAdapter",
    "BaseSubprocessAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "QoderAdapter",
]

if _HAS_OPENAI:
    __all__.append("OpenAIAdapter")
