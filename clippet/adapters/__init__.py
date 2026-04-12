"""CLIppet adapters for various CLI AI agents."""

from clippet.adapters.base import BaseAdapter, BaseSubprocessAdapter
from clippet.adapters.claude import ClaudeAdapter
from clippet.adapters.codex import CodexAdapter
from clippet.adapters.gemini import GeminiAdapter
from clippet.adapters.personas import PERSONA_PROMPTS, format_skill_block, format_single_skill
from clippet.adapters.qodercli import QoderCLIAdapter, QoderAdapter

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
    "GeminiAdapter",
    "PERSONA_PROMPTS",
    "QoderAdapter",  # Backwards compatibility alias
    "QoderCLIAdapter",
    "format_single_skill",
    "format_skill_block",
]

if _HAS_OPENAI:
    __all__.append("OpenAIAdapter")
