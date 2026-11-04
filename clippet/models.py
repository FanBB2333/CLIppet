"""Data models for CLIppet framework."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr


class ToolCallRecord(BaseModel):
    """Record of a single tool call made by an agent."""

    tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)


class AgentRequest(BaseModel):
    """Request configuration for running an agent."""

    task_prompt: str
    workspace_dir: Path = Field(default_factory=Path.cwd)
    timeout: int = 900  # seconds
    model: str | None = None
    allowed_tools: list[str] | None = None
    injected_skills: list[str] = Field(default_factory=list)
    extra_args: dict[str, Any] | None = None
    # Raw arguments forwarded verbatim to the underlying CLI (placed before
    # the prompt for CLIs that take the prompt as a positional argument).
    extra_cli_args: list[str] = Field(default_factory=list)
    isolation: "IsolationConfig | None" = None


class AgentResult(BaseModel):
    """Result from an agent execution."""

    raw_output: str = ""
    is_success: bool = False
    execution_time: float = 0.0
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    steps_count: int = 0
    error_message: str | None = None
    # Final answer text extracted from the agent's native output format.
    # ``None`` means the adapter could not separate it from ``raw_output``.
    output_text: str | None = None
    # Token usage as reported by the agent (keys are agent-specific, e.g.
    # ``input_tokens`` / ``output_tokens``).
    usage: dict[str, Any] | None = None
    # Agent-native session/thread identifier, when reported.
    session_id: str | None = None

    @property
    def text(self) -> str:
        """Return the best human-readable answer: ``output_text`` or raw output."""

        return self.output_text if self.output_text is not None else self.raw_output


class IsolationConfig(BaseModel):
    """Isolation configuration for a single agent execution.

    Two isolation modes are supported:

    1. **Single-file mode** (default): A temporary directory is created and
       ``credential_files`` / ``credential_contents`` are written into it
       (paths relative to it, laid out like a HOME: ``.codex/auth.json``).  It
       is removed after the run.  Any ``env_overrides`` are applied on top.
    2. **Second-home mode**: Set ``home_dir`` to a persistent directory path that
       already contains the full config hierarchy (``.claude/``, ``.codex/``, etc.).
       The directory is used *directly*, without copying or creating a
       temporary directory, and is **never** deleted by CLIppet.

    ``isolation_scope`` decides what that directory stands in for:

    * ``"agent"``: only the agent's own config directory.  The process keeps
      your real ``HOME`` (and the XDG directories), and the agent is pointed
      at its config inside the directory (``CODEX_HOME=<dir>/.codex``), so
      git, the macOS keychain, package managers and every other tool the agent
      starts find your own configuration.
    * ``"home"``: the whole HOME: ``HOME`` is the directory and the XDG
      directory variables are dropped.  This is how CLIppet ran every agent
      before the scope existed.
    * ``None`` (the default): ``"agent"`` for agents that can be pointed at
      another config directory (Codex), ``"home"`` for the others.

    Either way the agent's config, login and sessions are kept in the same
    place (``<dir>/.codex`` for Codex), so switching scopes moves nothing.

    Whenever a run is isolated, environment variables that point an agent at a
    different config directory or identity (``CODEX_HOME``, ``OPENAI_API_KEY``,
    ``ANTHROPIC_*``, ...) are removed from the inherited environment so the
    selected directory really decides which account is used; in the ``home``
    scope the XDG directory variables go too.  Set ``scrub_agent_env=False`` to
    keep them.  ``HOME`` and, in the ``agent`` scope, the agent's config
    directory variable are set by the scope; ``env_overrides`` cannot change
    them.
    """

    home_dir: str | None = None
    credential_files: dict[str, str] = Field(default_factory=dict)
    # Generated credential files (sandbox-relative path -> file content).  Used
    # when a credential had to be converted into the agent's native format.
    credential_contents: dict[str, SecretStr] = Field(default_factory=dict)
    # After the run, copy these sandbox-relative files back to the given
    # absolute paths *if the agent changed them* (e.g. refreshed OAuth tokens).
    sync_back: dict[str, str] = Field(default_factory=dict)
    env_overrides: dict[str, str] = Field(default_factory=dict)
    env_whitelist: list[str] | None = None
    env_blacklist: list[str] | None = None
    persist_sandbox: bool = False
    scrub_agent_env: bool = True
    isolation_scope: Literal["agent", "home"] | None = None

    @property
    def is_second_home(self) -> bool:
        """Return True when a persistent second-home directory is configured."""
        return self.home_dir is not None

    def merged_with(self, override: "IsolationConfig") -> "IsolationConfig":
        """Merge another config on top of the current one."""

        merged_data = self.model_dump()

        if "home_dir" in override.model_fields_set:
            merged_data["home_dir"] = override.home_dir

        for field_name in (
            "credential_files",
            "credential_contents",
            "sync_back",
            "env_overrides",
        ):
            if field_name in override.model_fields_set:
                combined = dict(getattr(self, field_name))
                combined.update(getattr(override, field_name))
                merged_data[field_name] = combined

        for field_name in (
            "env_whitelist",
            "env_blacklist",
            "persist_sandbox",
            "scrub_agent_env",
            "isolation_scope",
        ):
            if field_name in override.model_fields_set:
                merged_data[field_name] = getattr(override, field_name)

        return IsolationConfig(**merged_data)


AgentRequest.model_rebuild()
