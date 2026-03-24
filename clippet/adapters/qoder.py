"""Qoder CLI adapter for CLIppet framework."""

from clippet.adapters.base import BaseSubprocessAdapter
from clippet.models import AgentRequest, AgentResult
from clippet.parsers.extractors import parse_qoder_output


class QoderAdapter(BaseSubprocessAdapter):
    """Adapter for the Qoder CLI agent.
    
    Qoder is invoked via `qoder chat [prompt] [options]`.
    """

    def __init__(
        self,
        mode: str = "agent",
        add_files: list[str] | None = None,
        profile: str | None = None,
        new_window: bool = False,
        reuse_window: bool = False,
        **kwargs,
    ) -> None:
        """Initialize the Qoder adapter.
        
        Args:
            mode: Qoder mode - 'ask', 'edit', or 'agent'. Defaults to 'agent'.
            add_files: List of file paths to add to the chat context.
            profile: Workspace profile name.
            new_window: If True, open in a new window.
            reuse_window: If True, reuse an existing window.
            **kwargs: Additional options passed to base class.
        """
        super().__init__(**kwargs)
        self.mode = mode
        self.add_files = add_files
        self.profile = profile
        self.new_window = new_window
        self.reuse_window = reuse_window

    @property
    def agent_name(self) -> str:
        """Return the name of the agent."""
        return "qoder"

    def build_command(self, request: AgentRequest) -> list[str]:
        """Build the Qoder CLI command.
        
        Args:
            request: The agent request containing task and configuration.
            
        Returns:
            List of command arguments to execute.
        """
        cmd = ["qoder", "chat"]
        
        # Add the prompt as positional argument
        cmd.append(request.task_prompt)
        
        # Add mode
        cmd.extend(["--mode", self.mode])
        
        # Add files to context
        if self.add_files:
            for file_path in self.add_files:
                cmd.extend(["-a", file_path])
        
        # Add profile
        if self.profile:
            cmd.extend(["--profile", self.profile])
        
        # Window options (mutually exclusive in practice)
        if self.new_window:
            cmd.append("--new-window")
        elif self.reuse_window:
            cmd.append("--reuse-window")
        
        # Add extra args from request
        if request.extra_args:
            for key, value in request.extra_args.items():
                cmd.append(f"--{key}")
                if value is not None and value is not True:
                    cmd.append(str(value))
        
        return cmd

    def get_stdin_input(self, request: AgentRequest) -> str | None:
        """Return stdin input for the subprocess.
        
        Qoder receives prompt as positional argument, not via stdin.
        
        Args:
            request: The agent request.
            
        Returns:
            None - prompt is passed as positional argument.
        """
        return None

    def parse_output(
        self, raw_output: str, stderr: str, return_code: int
    ) -> AgentResult:
        """Parse Qoder CLI output into an AgentResult.
        
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
            tool_calls=[],  # Qoder doesn't provide structured tool call output in chat mode
            steps_count=0,
            error_message=error_message,
        )
