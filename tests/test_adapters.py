"""Unit tests for CLIppet adapters (command building and output parsing).

These tests verify command construction and output parsing WITHOUT actually
running any CLI commands.
"""

import json
from pathlib import Path

import pytest

from clippet.adapters.claude import ClaudeAdapter
from clippet.adapters.codex import CodexAdapter
from clippet.adapters.qodercli import QoderCLIAdapter
from clippet.models import AgentRequest, AgentResult


class TestClaudeAdapter:
    """Tests for ClaudeAdapter command building and output parsing."""

    def test_agent_name(self):
        """Verify adapter returns correct agent name."""
        adapter = ClaudeAdapter()
        assert adapter.agent_name == "claude-code"

    def test_build_command_defaults(self):
        """Verify base command includes required flags with defaults."""
        adapter = ClaudeAdapter()
        request = AgentRequest(
            task_prompt="Fix the bug",
            workspace_dir="/tmp/workspace",
        )
        
        cmd = adapter.build_command(request)
        
        # Check base command
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd
        
        # Check default model
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "sonnet"
        
        # Check default permission mode
        assert "--permission-mode" in cmd
        perm_idx = cmd.index("--permission-mode")
        assert cmd[perm_idx + 1] == "bypassPermissions"
        assert "-d" not in cmd

    def test_build_command_with_options(self):
        """Verify optional parameters appear in command."""
        adapter = ClaudeAdapter(
            max_turns=10,
            allowed_tools=["read_file", "write_file"],
            max_budget_usd=5.0,
        )
        request = AgentRequest(
            task_prompt="Implement feature",
            workspace_dir="/tmp/workspace",
        )
        
        cmd = adapter.build_command(request)
        
        # Check max_turns
        assert "--max-turns" in cmd
        turns_idx = cmd.index("--max-turns")
        assert cmd[turns_idx + 1] == "10"
        
        # Check allowed_tools
        assert "--allowed-tools" in cmd
        tools_idx = cmd.index("--allowed-tools")
        assert cmd[tools_idx + 1] == "read_file,write_file"
        
        # Check max_budget_usd
        assert "--max-budget-usd" in cmd
        budget_idx = cmd.index("--max-budget-usd")
        assert cmd[budget_idx + 1] == "5.0"

    def test_build_command_model_override(self):
        """Verify request.model overrides adapter default model."""
        adapter = ClaudeAdapter(model="sonnet")
        request = AgentRequest(
            task_prompt="Test task",
            workspace_dir="/tmp/workspace",
            model="opus",  # Override
        )
        
        cmd = adapter.build_command(request)
        
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "opus"

    def test_parse_output_success(self):
        """Test parsing successful Claude JSON output."""
        adapter = ClaudeAdapter()
        
        mock_output = json.dumps({
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Task completed successfully",
            "num_turns": 3,
            "cost_usd": 0.05,
            "duration_ms": 15000,
            "session_id": "sess_abc123",
        })
        
        result = adapter.parse_output(mock_output, "", 0)
        
        assert isinstance(result, AgentResult)
        assert result.is_success is True
        assert result.steps_count == 3
        assert result.raw_output == mock_output
        assert result.error_message is None

    def test_parse_output_error(self):
        """Test parsing error Claude JSON output."""
        adapter = ClaudeAdapter()
        
        mock_output = json.dumps({
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "API rate limit exceeded",
            "num_turns": 1,
            "cost_usd": 0.0,
            "duration_ms": 500,
            "session_id": "sess_xyz789",
        })
        
        result = adapter.parse_output(mock_output, "Rate limit error", 1)
        
        assert result.is_success is False
        assert result.error_message == "Rate limit error"

    def test_get_stdin_input(self):
        """Verify get_stdin_input returns the task prompt."""
        adapter = ClaudeAdapter()
        request = AgentRequest(
            task_prompt="Analyze the code",
            workspace_dir="/tmp",
        )
        
        stdin = adapter.get_stdin_input(request)
        
        assert stdin == "Analyze the code"


class TestCodexAdapter:
    """Tests for CodexAdapter command building and output parsing."""

    def test_agent_name(self):
        """Verify adapter returns correct agent name."""
        adapter = CodexAdapter()
        assert adapter.agent_name == "codex"

    def test_build_command_defaults(self):
        """Verify base command includes required flags with defaults."""
        adapter = CodexAdapter()
        request = AgentRequest(
            task_prompt="Refactor the function",
            workspace_dir="/tmp/project",
        )
        
        cmd = adapter.build_command(request)
        
        # Check base command
        assert cmd[0] == "codex"
        assert cmd[1] == "exec"
        
        # Check prompt is in command
        assert "Refactor the function" in cmd
        
        # Check default model
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "o4-mini"
        
        # Check default sandbox
        assert "--sandbox" in cmd
        sandbox_idx = cmd.index("--sandbox")
        assert cmd[sandbox_idx + 1] == "workspace-write"
        
        # Check full-auto mode
        assert "--full-auto" in cmd

    def test_build_command_with_options(self):
        """Verify custom sandbox and add_dirs appear in command."""
        adapter = CodexAdapter(
            sandbox="danger-full-access",
            add_dirs=["/var/data"],
        )
        request = AgentRequest(
            task_prompt="Write tests",
            workspace_dir="/tmp/workspace",
        )
        
        cmd = adapter.build_command(request)
        
        # Check custom sandbox
        sandbox_idx = cmd.index("--sandbox")
        assert cmd[sandbox_idx + 1] == "danger-full-access"
        
        # Check additional writable directory
        assert "--add-dir" in cmd
        add_idx = cmd.index("--add-dir")
        assert cmd[add_idx + 1] == "/var/data"

    def test_parse_output_success(self):
        """Test parsing successful Codex output."""
        adapter = CodexAdapter()
        
        mock_output = "Successfully refactored the function"
        
        result = adapter.parse_output(mock_output, "", 0)
        
        assert isinstance(result, AgentResult)
        assert result.is_success is True
        assert result.raw_output == mock_output
        assert result.error_message is None

    def test_parse_output_error(self):
        """Test parsing error Codex output."""
        adapter = CodexAdapter()
        
        mock_output = ""
        stderr = "Command failed: file not found"
        
        result = adapter.parse_output(mock_output, stderr, 1)
        
        assert result.is_success is False
        assert result.error_message == "Command failed: file not found"


class TestQoderCLIAdapter:
    """Tests for QoderCLIAdapter command building and output parsing."""

    def test_agent_name(self):
        """Verify adapter returns correct agent name."""
        adapter = QoderCLIAdapter()
        assert adapter.agent_name == "qodercli"

    def test_build_command_defaults(self):
        """Verify base command includes required flags with defaults."""
        adapter = QoderCLIAdapter()
        request = AgentRequest(
            task_prompt="Debug the issue",
            workspace_dir="/tmp/project",
        )
        
        cmd = adapter.build_command(request)
        
        # Check base command
        assert cmd[0] == "qodercli"
        
        # Check prompt is in command via -p flag
        assert "-p" in cmd
        p_idx = cmd.index("-p")
        assert cmd[p_idx + 1] == "Debug the issue"
        
        # Check workspace
        assert "-w" in cmd
        w_idx = cmd.index("-w")
        assert cmd[w_idx + 1] == "/tmp/project"

    def test_build_command_with_options(self):
        """Verify optional parameters appear in command."""
        adapter = QoderCLIAdapter(
            model="auto",
            max_turns=25,
            yolo=True,
        )
        request = AgentRequest(
            task_prompt="Review these files",
            workspace_dir="/tmp/project",
        )
        
        cmd = adapter.build_command(request)
        
        # Check model flag
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "auto"
        
        # Check max-turns flag
        assert "--max-turns" in cmd
        turns_idx = cmd.index("--max-turns")
        assert cmd[turns_idx + 1] == "25"
        
        # Check yolo flag
        assert "--yolo" in cmd

    def test_parse_output_success(self):
        """Test parsing successful QoderCLI output."""
        adapter = QoderCLIAdapter()
        
        mock_output = "Analysis complete: Found 3 issues"
        
        result = adapter.parse_output(mock_output, "", 0)
        
        assert isinstance(result, AgentResult)
        assert result.is_success is True
        assert result.raw_output == mock_output
        assert result.error_message is None

    def test_parse_output_error(self):
        """Test parsing error QoderCLI output."""
        adapter = QoderCLIAdapter()
        
        mock_output = ""
        stderr = "Connection refused"
        
        result = adapter.parse_output(mock_output, stderr, 1)
        
        assert result.is_success is False
        assert result.error_message == "Connection refused"
