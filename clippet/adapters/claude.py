"""Claude Code CLI adapter for CLIppet."""

import json

from clippet.adapters.base import BaseSubprocessAdapter
from clippet.models import AgentRequest, AgentResult
from clippet.parsers.extractors import (
    parse_claude_json_output,
    extract_tool_calls_from_claude_json,
)


class ClaudeAdapter(BaseSubprocessAdapter):
    """Adapter for invoking Claude Code CLI.
    
    This adapter interfaces with the Claude Code CLI tool, building commands
    and parsing JSON output format.
    """

    def __init__(
        self,
        model: str = "sonnet",
        permission_mode: str = "bypassPermissions",
        max_turns: int | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        max_budget_usd: float | None = None,
        append_system_prompt: str | None = None,
        verbose: bool = False,
    ) -> None:
        """Initialize the Claude Code adapter.
        
        Args:
            model: Model to use (e.g. sonnet, opus, claude-sonnet-4-6).
            permission_mode: Permission mode (default, acceptEdits, bypassPermissions, plan, auto).
            max_turns: Maximum conversation turns in non-interactive mode.
            allowed_tools: List of allowed tools (space or comma separated when passed to CLI).
            disallowed_tools: List of disallowed tools.
            max_budget_usd: Maximum API spend in USD.
            append_system_prompt: Additional system prompt text.
            verbose: Enable verbose logging.
        """
        super().__init__()
        self.model = model
        self.permission_mode = permission_mode
        self.max_turns = max_turns
        self.allowed_tools = allowed_tools
        self.disallowed_tools = disallowed_tools
        self.max_budget_usd = max_budget_usd
        self.append_system_prompt = append_system_prompt
        self.verbose = verbose

    @property
    def agent_name(self) -> str:
        """Return the name of the agent."""
        return "claude-code"

    def build_command(self, request: AgentRequest) -> list[str]:
        """Build the Claude Code CLI command.
        
        Args:
            request: The agent request containing task and configuration.
            
        Returns:
            List of command arguments to execute.
        """
        # Start with base command
        cmd = ["claude", "-p", "--output-format", "json"]
        
        # Add model (request.model overrides self.model if set)
        effective_model = request.model if request.model else self.model
        cmd.extend(["--model", effective_model])
        
        # Add permission mode
        cmd.extend(["--permission-mode", self.permission_mode])
        
        # Add working directory
        cmd.extend(["-d", str(request.workspace_dir)])
        
        # Add max turns if set
        if self.max_turns is not None:
            cmd.extend(["--max-turns", str(self.max_turns)])
        
        # Add allowed tools if set
        if self.allowed_tools:
            cmd.extend(["--allowed-tools", ",".join(self.allowed_tools)])
        
        # Add disallowed tools if set
        if self.disallowed_tools:
            cmd.extend(["--disallowed-tools", ",".join(self.disallowed_tools)])
        
        # Add max budget if set
        if self.max_budget_usd is not None:
            cmd.extend(["--max-budget-usd", str(self.max_budget_usd)])
        
        # Add append system prompt if set
        if self.append_system_prompt:
            cmd.extend(["--append-system-prompt", self.append_system_prompt])
        
        # Add verbose flag if enabled
        if self.verbose:
            cmd.append("--verbose")
        
        # Add any extra args from request
        if request.extra_args:
            for key, value in request.extra_args.items():
                cmd.extend([f"--{key}", str(value)])
        
        return cmd

    def get_stdin_input(self, request: AgentRequest) -> str | None:
        """Return the prompt to send via stdin.
        
        Claude reads the prompt from stdin in print mode (-p).
        
        Args:
            request: The agent request.
            
        Returns:
            The task prompt to send via stdin.
        """
        return request.task_prompt

    def parse_output(
        self, raw_output: str, stderr: str, return_code: int
    ) -> AgentResult:
        """Parse Claude Code CLI output into AgentResult.
        
        Args:
            raw_output: The stdout from the subprocess.
            stderr: The stderr from the subprocess.
            return_code: The process exit code.
            
        Returns:
            Parsed AgentResult with extracted information.
        """
        # Parse the JSON output
        parsed = parse_claude_json_output(raw_output)
        
        # Use full JSON for tool-call extraction (not the summarized dict)
        try:
            full_data = json.loads(raw_output.strip()) if raw_output.strip() else {}
        except json.JSONDecodeError:
            full_data = {}
        
        # Extract tool calls from the full raw JSON data
        tool_calls = extract_tool_calls_from_claude_json(full_data)
        
        # Determine success
        is_success = not parsed["is_error"] and return_code == 0
        
        # Build error message if not successful
        error_message = stderr if not is_success and stderr else None
        
        return AgentResult(
            raw_output=raw_output,
            is_success=is_success,
            tool_calls=tool_calls,
            steps_count=parsed["num_turns"] or 0,
            error_message=error_message,
        )
