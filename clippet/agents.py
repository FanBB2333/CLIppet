"""The agent CLIs CLIppet can run, and what differs between them.

For each agent: the binary and how to install it, the files it keeps under
HOME (and which of them are a login), the variables that would point it at
another identity when inherited, how to pick one of its conversations back
up, and how the TUI draws it.  The rest of CLIppet reads these tables instead
of naming agents one by one, so supporting another agent starts here.

This module imports nothing from the rest of CLIppet, so anything may import it.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SeedSpec:
    """What ``--from-current`` copies for one agent (paths relative to HOME).

    *entries* are configuration (settings, instructions, skills, MCP servers);
    *credentials* are logins, copied only with ``--with-auth``; *exclude* are
    names left out when an entry is a directory.  *scrub* names JSON files
    among the entries whose login fields are removed unless ``--with-auth``,
    mapped to those top-level keys.
    """

    entries: tuple[str, ...]
    credentials: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    scrub: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSpec:
    """One agent CLI."""

    key: str  # what CLIppet calls it: ``clippet <key>``, ``.clippet.json`` "agents"
    title: str  # what its maker calls it
    binary: str
    install: str  # how to install it, as a command or a pointer
    docs: str  # where its documentation lives
    adapter: str  # ``module:Class`` of its adapter, imported when first needed
    config_paths: tuple[str, ...]  # what it keeps under HOME; any of them means "configured here"
    seed: SeedSpec | None = None  # None: --from-current has nothing to copy for it
    identity_env: tuple[str, ...] = ()  # inherited variables dropped in env runs (``NAME`` or ``PREFIX*``)
    resume: tuple[str, ...] = ()  # arguments that reopen a conversation; ``{id}`` is its session id
    login: str = ""  # how to log in from inside the agent, said to people
    keychain: str = ""  # when its login can live outside HOME (an OS keychain), what to tell people
    letter: str = "?"  # its mark in the TUI's agent column
    color: str = "ink"  # its colour in the TUI (a palette key)
    examples: str = ""  # arguments worth showing as an example

    def resume_args(self, session_id: str | None) -> list[str]:
        """The arguments that reopen conversation *session_id* ([] when it cannot be reopened)."""

        if not session_id or not self.resume:
            return []
        return [part.replace("{id}", session_id) for part in self.resume]

    def adapter_class(self) -> Any:
        module, _, name = self.adapter.partition(":")
        return getattr(importlib.import_module(module), name)


_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(
        key="codex",
        title="Codex CLI",
        binary="codex",
        install="npm i -g @openai/codex",
        docs="https://developers.openai.com/codex/cli",
        adapter="clippet.adapters.codex:CodexAdapter",
        config_paths=(".codex",),
        seed=SeedSpec(
            entries=(
                ".codex/config.toml",
                ".codex/AGENTS.md",
                ".codex/AGENTS.override.md",
                ".codex/hooks.json",
                ".codex/keybindings.json",
                ".codex/skills",
                ".codex/prompts",
                ".codex/rules",
            ),
            credentials=(".codex/auth.json",),
        ),
        identity_env=(
            "CODEX_HOME",
            "CODEX_API_KEY",
            "CODEX_ACCESS_TOKEN",
            "CODEX_REFRESH_TOKEN_URL_OVERRIDE",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_ORG_ID",
            "OPENAI_ORGANIZATION",
            "OPENAI_PROJECT",
            "OPENAI_PROJECT_ID",
        ),
        resume=("resume", "{id}"),
        login="codex login",
        letter="X",
        color="ok",
        examples="e.g. resume --last   or   -m gpt-5.5",
    ),
    AgentSpec(
        key="claude",
        title="Claude Code",
        binary="claude",
        install="npm i -g @anthropic-ai/claude-code",
        docs="https://docs.claude.com/en/docs/claude-code",
        adapter="clippet.adapters.claude:ClaudeAdapter",
        config_paths=(".claude", ".claude.json"),
        seed=SeedSpec(
            entries=(
                ".claude/settings.json",
                ".claude/CLAUDE.md",
                ".claude/agents",
                ".claude/commands",
                ".claude/skills",
                ".claude/output-styles",
                ".claude/hooks",
                ".claude.json",
            ),
            credentials=(".claude/.credentials.json",),
            scrub={".claude.json": ("primaryApiKey", "oauthAccount")},
        ),
        identity_env=("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_*"),
        resume=("--resume", "{id}"),
        login="/login",
        letter="C",
        color="plum",
        examples="e.g. --continue   or   --model sonnet",
    ),
    AgentSpec(
        key="gemini",
        title="Gemini CLI",
        binary="gemini",
        install="npm i -g @google/gemini-cli",
        docs="https://github.com/google-gemini/gemini-cli",
        adapter="clippet.adapters.gemini:GeminiAdapter",
        config_paths=(".gemini",),
        seed=SeedSpec(
            entries=(
                ".gemini/settings.json",
                ".gemini/GEMINI.md",
                ".gemini/commands",
                ".gemini/extensions",
            ),
            credentials=(
                ".gemini/.env",
                ".gemini/oauth_creds.json",
                ".gemini/google_accounts.json",
            ),
        ),
        identity_env=(
            "GEMINI_CLI_HOME",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
            "GOOGLE_API_KEY",
            "GOOGLE_GEMINI_BASE_URL",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_GENAI_USE_VERTEXAI",
        ),
        login="/auth",
        letter="G",
        color="brand",
        examples="e.g. -m gemini-2.5-pro",
    ),
    AgentSpec(
        key="qodercli",
        title="Qoder CLI",
        binary="qodercli",
        install="see qoder.com/cli",
        docs="https://docs.qoder.com/cli",
        adapter="clippet.adapters.qodercli:QoderCLIAdapter",
        config_paths=(".qoder", ".qoder-cli"),
        # qodercli keeps its main state in ~/.qoder/ and AI-contribution stats
        # (written by `qodercli commit`) in ~/.qoder-cli/.  Its layout is not
        # documented, so the directories are copied minus obvious runtime state.
        seed=SeedSpec(
            entries=(".qoder", ".qoder-cli"),
            exclude=("logs", "log", "cache", "*.sock", "*.lock", "*.pid"),
        ),
        login="/login",
        letter="Q",
        color="info",
        examples="anything qodercli takes",
    ),
)

#: Every agent CLIppet can launch, by key, in the order pickers show them.
AGENTS: dict[str, AgentSpec] = {spec.key: spec for spec in _SPECS}

#: The keys of :data:`AGENTS`.
AGENT_KEYS: tuple[str, ...] = tuple(AGENTS)

#: Other names accepted for an agent (``claude_code`` is what config detection says).
ALIASES: dict[str, str] = {"claude_code": "claude"}


def agent_spec(name: str | None) -> AgentSpec | None:
    """The agent called *name* (or one of its aliases), or None."""

    if not name:
        return None
    return AGENTS.get(ALIASES.get(name, name))


def agent_list(separator: str = ", ") -> str:
    """The agent keys, for messages: ``codex, claude, gemini, …``."""

    return separator.join(AGENT_KEYS)
