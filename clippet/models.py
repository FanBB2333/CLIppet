"""Data models for CLIppet framework."""

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


class AgentResult(BaseModel):
    """Result from an agent execution."""

    raw_output: str = ""
    is_success: bool = False
    execution_time: float = 0.0
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    steps_count: int = 0
    error_message: str | None = None
