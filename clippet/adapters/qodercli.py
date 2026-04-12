"""QoderCLI adapter for CLIppet framework.

QoderCLI is a terminal-based AI assistant that provides an interactive chat 
interface with AI capabilities, code analysis, and MCP integration.
"""

from clippet.adapters.base import BaseSubprocessAdapter
from clippet.models import AgentRequest, AgentResult
from clippet.parsers.extractors import parse_qoder_output


class QoderCLIAdapter(BaseSubprocessAdapter):
    """Adapter for the QoderCLI agent.
    
    QoderCLI is invoked via `qodercli [options]` for interactive mode,
    or `qodercli -p [prompt]` for non-interactive mode.
    """

    def __init__(
        self,
        model: str | None = None,
        workspace: str | None = None,
        max_turns: int | None = None,
        output_format: str = "text",
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        yolo: bool = False,
        with_claude_config: bool = False,
        **kwargs,
    ) -> None:
        """Initialize the QoderCLI adapter.
        
        Args:
            model: Model level to use (auto, efficient, performance, ultimate, etc.).
            workspace: Working directory for the session.
            max_turns: Maximum number of agent iterations.
            output_format: Output format for non-interactive mode (text, json, stream-json).
            allowed_tools: List of allowed tool names.
            disallowed_tools: List of disallowed tool names.
            yolo: Bypass all permission checks.
            with_claude_config: Load claude code configurations.
            **kwargs: Additional options passed to base class.
        """
        super().__init__(**kwargs)
        self.model = model
        self.workspace = workspace
        self.max_turns = max_turns
        self.output_format = output_format
        self.allowed_tools = allowed_tools
        self.disallowed_tools = disallowed_tools
        self.yolo = yolo
        self.with_claude_config = with_claude_config

    @property
    def agent_name(self) -> str:
        """Return the name of the agent."""
        return "qodercli"

    def build_command(self, request: AgentRequest) -> list[str]:
        """Build the QoderCLI command.
        
        When ``request.injected_skills`` is non-empty, the skill texts are
        formatted and prepended to the task prompt since QoderCLI does
        not support a native system prompt injection flag.
        
        Args:
            request: The agent request containing task and configuration.
            
        Returns:
            List of command arguments to execute.
        """
        cmd = ["qodercli"]
        
        # Build effective prompt — prepend skills if present.
        effective_prompt = request.task_prompt
        if request.injected_skills:
            skills_block = self._format_skills_text(request.injected_skills)
            effective_prompt = (
                skills_block
                + "\n\n---\n\n"
                + effective_prompt
            )

        # Non-interactive mode: use -p flag
        if effective_prompt:
            cmd.extend(["-p", effective_prompt])
        
        # Model selection
        if self.model:
            cmd.extend(["--model", self.model])
        
        # Workspace directory
        if self.workspace:
            cmd.extend(["-w", self.workspace])
        elif request.workspace_dir:
            cmd.extend(["-w", str(request.workspace_dir)])
        
        # Max turns (iterations)
        if self.max_turns:
            cmd.extend(["--max-turns", str(self.max_turns)])
        
        # Output format for non-interactive mode
        if self.output_format and self.output_format != "text":
            cmd.extend(["-f", self.output_format])
        
        # Tool allowlists/blocklists
        if self.allowed_tools:
            cmd.extend(["--allowed-tools", ",".join(self.allowed_tools)])
        
        if self.disallowed_tools:
            cmd.extend(["--disallowed-tools", ",".join(self.disallowed_tools)])
        
        # Permission bypass
        if self.yolo:
            cmd.append("--yolo")
        
        # Load claude code configurations
        if self.with_claude_config:
            cmd.append("--with-claude-config")
        
        # Add extra args from request
        if request.extra_args:
            for key, value in request.extra_args.items():
                cmd.append(f"--{key}")
                if value is not None and value is not True:
                    cmd.append(str(value))
        
        return cmd

    def build_interactive_command(self, request: AgentRequest | None = None) -> list[str]:
        """Build the QoderCLI command for interactive mode.
        
        Args:
            request: Optional agent request for configuration.
            
        Returns:
            List of command arguments to execute in interactive mode.
        """
        cmd = ["qodercli"]
        
        # Model selection
        if self.model:
            cmd.extend(["--model", self.model])
        
        # Workspace directory
        if self.workspace:
            cmd.extend(["-w", self.workspace])
        elif request and request.workspace_dir:
            cmd.extend(["-w", str(request.workspace_dir)])
        
        # Max turns (iterations)
        if self.max_turns:
            cmd.extend(["--max-turns", str(self.max_turns)])
        
        # Tool allowlists/blocklists
        if self.allowed_tools:
            cmd.extend(["--allowed-tools", ",".join(self.allowed_tools)])
        
        if self.disallowed_tools:
            cmd.extend(["--disallowed-tools", ",".join(self.disallowed_tools)])
        
        # Permission bypass
        if self.yolo:
            cmd.append("--yolo")
        
        # Load claude code configurations
        if self.with_claude_config:
            cmd.append("--with-claude-config")
        
        return cmd

    def get_stdin_input(self, request: AgentRequest) -> str | None:
        """Return stdin input for the subprocess.
        
        QoderCLI receives prompt via -p flag, not via stdin.
        
        Args:
            request: The agent request.
            
        Returns:
            None - prompt is passed via -p flag.
        """
        return None

    def parse_output(
        self, raw_output: str, stderr: str, return_code: int
    ) -> AgentResult:
        """Parse QoderCLI output into an AgentResult.
        
        Args:
            raw_output: The stdout from the subprocess.
            stderr: The stderr from the subprocess.
            return_code: The process exit code.
            
        Returns:
            Parsed AgentResult with extracted information.
        """
        parsed = parse_qoder_output(raw_output)
        
        is_success = not parsed["is_error"] and return_code == 0
        
        # Determine error message
        error_message = None
        if not is_success:
            if stderr and stderr.strip():
                error_message = stderr.strip()
            elif parsed["is_error"]:
                error_message = parsed.get("result_text", "Unknown error")
        
        return AgentResult(
            raw_output=raw_output,
            is_success=is_success,
            tool_calls=[],  # QoderCLI doesn't provide structured tool call output
            steps_count=0,
            error_message=error_message,
        )


# Backwards compatibility alias
QoderAdapter = QoderCLIAdapter
