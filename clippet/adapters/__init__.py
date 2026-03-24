"""CLIppet adapters for various CLI AI agents."""

from clippet.adapters.base import BaseSubprocessAdapter
from clippet.adapters.claude import ClaudeAdapter
from clippet.adapters.codex import CodexAdapter
from clippet.adapters.qoder import QoderAdapter

__all__ = [
    "BaseSubprocessAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "QoderAdapter",
]
