"""Data models for CLIppet framework."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


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
    extra_args: dict[str, Any] | None = None
    isolation: "IsolationConfig | None" = None


class AgentResult(BaseModel):
    """Result from an agent execution."""

    raw_output: str = ""
    is_success: bool = False
    execution_time: float = 0.0
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    steps_count: int = 0
    error_message: str | None = None


class IsolationConfig(BaseModel):
    """Isolation configuration for a single agent execution."""

    credential_files: dict[str, str] = Field(default_factory=dict)
    env_overrides: dict[str, str] = Field(default_factory=dict)
    env_whitelist: list[str] | None = None
    env_blacklist: list[str] | None = None
    persist_sandbox: bool = False

    def merged_with(self, override: "IsolationConfig") -> "IsolationConfig":
        """Merge another config on top of the current one."""

        merged_data = self.model_dump()

        if "credential_files" in override.model_fields_set:
            credential_files = dict(self.credential_files)
            credential_files.update(override.credential_files)
            merged_data["credential_files"] = credential_files

        if "env_overrides" in override.model_fields_set:
            env_overrides = dict(self.env_overrides)
            env_overrides.update(override.env_overrides)
            merged_data["env_overrides"] = env_overrides

        for field_name in ("env_whitelist", "env_blacklist", "persist_sandbox"):
            if field_name in override.model_fields_set:
                merged_data[field_name] = getattr(override, field_name)

        return IsolationConfig(**merged_data)


AgentRequest.model_rebuild()
