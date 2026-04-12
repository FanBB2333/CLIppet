"""Agent persona definitions and skill formatting utilities.

This module provides:
  * ``PERSONA_PROMPTS`` — a mapping from persona identifiers to base system
    prompts that shape the model's behaviour to match a specific coding agent.
  * ``format_skill_block`` / ``format_single_skill`` — helpers that wrap skill
    texts in ``<skill_content>`` XML tags, matching the convention used by
    Claude Code and OpenCode when injecting skills into context.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Persona → base system prompt mapping
# ---------------------------------------------------------------------------

PERSONA_PROMPTS: dict[str, str] = {
    # --- Claude Code ---
    "claude-code": (
        "You are Claude Code, an interactive CLI tool that helps users with "
        "software engineering tasks. You can read and write files, execute "
        "commands, search codebases, and help with all aspects of software "
        "development. Follow the user's instructions carefully and use the "
        "tools available to you to complete tasks effectively."
    ),
    # --- OpenAI Codex ---
    "codex": (
        "You are Codex, an AI coding agent that runs in a sandboxed "
        "environment. You can execute shell commands, write and modify code, "
        "and help users build, debug, and improve software projects. Work "
        "autonomously to complete the task given to you."
    ),
    # --- OpenCode ---
    "opencode": (
        "You are OpenCode, a powerful CLI coding agent. You assist users with "
        "software engineering tasks including solving bugs, adding features, "
        "refactoring code, and explaining codebases. Use the tools at your "
        "disposal to explore and modify the project."
    ),
    # --- QoderCLI ---
    "qodercli": (
        "You are QoderCLI, an AI coding assistant that helps users write, edit, "
        "and debug code through a terminal-based conversational interface. "
        "Provide clear explanations and produce high-quality code that follows "
        "best practices."
    ),
    # --- Gemini CLI ---
    "gemini": (
        "You are Gemini CLI, Google's AI coding assistant that helps users with "
        "software development tasks through a terminal interface. You can analyze "
        "code, execute commands, and assist with building and debugging projects. "
        "Provide accurate and helpful responses."
    ),
    # --- Generic / default ---
    "generic": (
        "You are a helpful coding assistant."
    ),
}


# ---------------------------------------------------------------------------
# Skill formatting — <skill_content> tag convention
# ---------------------------------------------------------------------------


def format_single_skill(skill_text: str, *, name: str | None = None) -> str:
    """Wrap a single skill text in a ``<skill_content>`` XML tag.

    This mirrors the format used by Claude Code / OpenCode when loading a
    skill via the ``/skill`` slash command or the ``Skill`` tool — the
    content is placed verbatim inside the tag.

    Args:
        skill_text: The raw skill content (typically Markdown).
        name: Optional skill name used as the ``name`` attribute.  When
            *None* the attribute is omitted.

    Returns:
        The skill text wrapped in ``<skill_content>`` tags.
    """

    attr = f' name="{name}"' if name else ""
    return f"<skill_content{attr}>\n{skill_text}\n</skill_content>"


def format_skill_block(skills: list[str]) -> str:
    """Format a list of skill texts into a single block for the system prompt.

    Each skill is individually wrapped in ``<skill_content>`` tags with an
    auto-generated ``name`` attribute (``skill_1``, ``skill_2``, …).  The
    whole block is prefixed with a ``# Skills`` heading.

    When *skills* is empty, an empty string is returned so that callers
    can unconditionally concatenate the result.

    Args:
        skills: List of raw skill text strings.

    Returns:
        Formatted skill block string, or ``""`` if no skills.
    """

    if not skills:
        return ""

    parts: list[str] = []
    for idx, skill_text in enumerate(skills, start=1):
        parts.append(format_single_skill(skill_text, name=f"skill_{idx}"))

    joined = "\n\n".join(parts)
    return f"\n\n# Skills\n\n{joined}"
