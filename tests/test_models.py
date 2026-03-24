"""Unit tests for CLIppet data models."""

from datetime import datetime
from pathlib import Path

import pytest

from clippet.models import AgentRequest, AgentResult, ToolCallRecord


class TestToolCallRecord:
    """Tests for ToolCallRecord model."""

    def test_creation_with_defaults(self):
        """Test creating a ToolCallRecord with default values."""
        record = ToolCallRecord(tool_name="read_file")
        
        assert record.tool_name == "read_file"
        assert record.parameters == {}
        assert isinstance(record.timestamp, datetime)

    def test_creation_with_custom_values(self):
        """Test creating a ToolCallRecord with custom values."""
        custom_time = datetime(2024, 1, 15, 10, 30, 0)
        params = {"file": "test.py", "line": 42}
        
        record = ToolCallRecord(
            tool_name="search_codebase",
            parameters=params,
            timestamp=custom_time,
        )
        
        assert record.tool_name == "search_codebase"
        assert record.parameters == params
        assert record.timestamp == custom_time

    def test_serialization_deserialization(self):
        """Test that ToolCallRecord can be serialized and deserialized."""
        params = {"query": "test", "max_results": 10}
        record = ToolCallRecord(tool_name="grep_code", parameters=params)
        
        # Serialize to dict
        data = record.model_dump()
        
        assert data["tool_name"] == "grep_code"
        assert data["parameters"] == params
        assert "timestamp" in data
        
        # Deserialize back
        restored = ToolCallRecord.model_validate(data)
        
        assert restored.tool_name == record.tool_name
        assert restored.parameters == record.parameters


class TestAgentRequest:
    """Tests for AgentRequest model."""

    def test_creation_with_defaults(self):
        """Test creating an AgentRequest with default values."""
        request = AgentRequest(task_prompt="Fix the bug")
        
        assert request.task_prompt == "Fix the bug"
        assert request.timeout == 900  # Default timeout
        assert request.workspace_dir == Path.cwd()
        assert request.model is None
        assert request.allowed_tools is None
        assert request.extra_args is None

    def test_creation_with_all_fields(self):
        """Test creating an AgentRequest with all fields specified."""
        workspace = Path("/tmp/workspace")
        tools = ["read_file", "write_file"]
        extra = {"verbose": True}
        
        request = AgentRequest(
            task_prompt="Implement feature X",
            workspace_dir=workspace,
            timeout=1800,
            model="gpt-4",
            allowed_tools=tools,
            extra_args=extra,
        )
        
        assert request.task_prompt == "Implement feature X"
        assert request.workspace_dir == workspace
        assert request.timeout == 1800
        assert request.model == "gpt-4"
        assert request.allowed_tools == tools
        assert request.extra_args == extra

    def test_workspace_dir_as_path(self):
        """Test that workspace_dir is properly converted to Path."""
        # String input should work via Pydantic validation
        request = AgentRequest(
            task_prompt="Test task",
            workspace_dir="/some/path",
        )
        
        assert isinstance(request.workspace_dir, Path)
        assert str(request.workspace_dir) == "/some/path"


class TestAgentResult:
    """Tests for AgentResult model."""

    def test_creation_with_defaults(self):
        """Test creating an AgentResult with default values."""
        result = AgentResult()
        
        assert result.raw_output == ""
        assert result.is_success is False
        assert result.execution_time == 0.0
        assert result.tool_calls == []
        assert result.steps_count == 0
        assert result.error_message is None

    def test_creation_with_full_data(self):
        """Test creating an AgentResult with full data."""
        tool_calls = [
            ToolCallRecord(tool_name="read_file", parameters={"file": "main.py"}),
            ToolCallRecord(tool_name="write_file", parameters={"file": "out.py"}),
        ]
        
        result = AgentResult(
            raw_output="Task completed successfully",
            is_success=True,
            execution_time=15.5,
            tool_calls=tool_calls,
            steps_count=5,
            error_message=None,
        )
        
        assert result.raw_output == "Task completed successfully"
        assert result.is_success is True
        assert result.execution_time == 15.5
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].tool_name == "read_file"
        assert result.tool_calls[1].tool_name == "write_file"
        assert result.steps_count == 5
        assert result.error_message is None

    def test_result_with_error_message(self):
        """Test creating an AgentResult with an error message."""
        result = AgentResult(
            raw_output="",
            is_success=False,
            execution_time=2.0,
            error_message="Connection timeout",
        )
        
        assert result.is_success is False
        assert result.error_message == "Connection timeout"
        assert result.raw_output == ""
