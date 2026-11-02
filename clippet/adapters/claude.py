"""Claude Code CLI adapter for CLIppet."""

from __future__ import annotations

import json

from clippet.adapters.base import BaseSubprocessAdapter, render_extra_args
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

    isolation_agent = "claude"

    #: Permission mode used for headless (``-p``) runs when none is configured.
    HEADLESS_PERMISSION_MODE = "bypassPermissions"

    def __init__(
        self,
        model: str | None = None,
        permission_mode: str | None = None,
        max_turns: int | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        max_budget_usd: float | None = None,
        append_system_prompt: str | None = None,
        verbose: bool = False,
    ) -> None:
        """Initialize the Claude Code adapter.
        
        Args:
            model: Model to use (e.g. sonnet, opus, claude-sonnet-4-6).  *None*
                (the default) lets Claude Code use the model configured in the
                active HOME (``settings.json`` / ``ANTHROPIC_MODEL``).
            permission_mode: Permission mode (default, acceptEdits,
                bypassPermissions, plan, auto).  *None* means
                ``bypassPermissions`` for headless runs and Claude Code's own
                default for interactive sessions.
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
        
        When ``request.injected_skills`` is non-empty the skill texts are
        formatted (using ``<skill_content>`` tags) and merged into the
        ``--append-system-prompt`` flag value.  If the adapter already has a
        constructor-level ``append_system_prompt``, the skill block is
        appended after it.
        
        Args:
            request: The agent request containing task and configuration.
            
        Returns:
            List of command arguments to execute.
        """
        # Start with base command
        cmd = ["claude", "-p", "--output-format", "json"]
        
        # Add model (request.model overrides self.model if set)
        effective_model = request.model if request.model else self.model
        if effective_model:
            cmd.extend(["--model", effective_model])

        # Add permission mode (headless runs cannot answer permission prompts)
        cmd.extend([
            "--permission-mode",
            self.permission_mode or self.HEADLESS_PERMISSION_MODE,
        ])
        
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
        
        # Build the effective --append-system-prompt value by merging the
        # adapter-level value with any per-request injected skills.
        effective_append = self.append_system_prompt or ""
        if request.injected_skills:
            skills_block = self._format_skills_text(request.injected_skills)
            if effective_append:
                effective_append = effective_append + "\n\n" + skills_block
            else:
                effective_append = skills_block

        if effective_append:
            cmd.extend(["--append-system-prompt", effective_append])
        
        # Add verbose flag if enabled
        if self.verbose:
            cmd.append("--verbose")
        
        # Add any extra args from request
        cmd.extend(render_extra_args(request.extra_args))
        cmd.extend(request.extra_cli_args)

        return cmd

    def build_interactive_command(self, extra_args: list[str] | None = None) -> list[str]:
        """Build the interactive ``claude`` command.

        Only explicitly configured options are emitted; ``extra_args`` are
        appended verbatim (e.g. ``--resume``, ``--continue``).
        """
        command = ["claude"]
        if self.model:
            command.extend(["--model", self.model])
        if self.permission_mode:
            command.extend(["--permission-mode", self.permission_mode])
        if self.allowed_tools:
            command.extend(["--allowed-tools", ",".join(self.allowed_tools)])
        if self.disallowed_tools:
            command.extend(["--disallowed-tools", ",".join(self.disallowed_tools)])
        if self.append_system_prompt:
            command.extend(["--append-system-prompt", self.append_system_prompt])
        if self.verbose:
            command.append("--verbose")
        command.extend(extra_args or [])
        return command

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
        error_message: str | None = None
        if not is_success:
            if stderr and stderr.strip():
                error_message = stderr.strip()
            elif parsed["result_text"]:
                error_message = str(parsed["result_text"])
            else:
                error_message = f"claude exited with code {return_code}"

        usage = full_data.get("usage") if isinstance(full_data, dict) else None
        result_text = parsed["result_text"]

        return AgentResult(
            raw_output=raw_output,
            is_success=is_success,
            tool_calls=tool_calls,
            steps_count=parsed["num_turns"] or 0,
            error_message=error_message,
            output_text=result_text if isinstance(result_text, str) and is_success else None,
            usage=usage if isinstance(usage, dict) else None,
            session_id=parsed.get("session_id"),
        )
