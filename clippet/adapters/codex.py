"""Codex CLI adapter for CLIppet framework."""

from clippet.adapters.base import BaseSubprocessAdapter
from clippet.models import AgentRequest, AgentResult
from clippet.parsers.extractors import parse_codex_output


class CodexAdapter(BaseSubprocessAdapter):
    """Adapter for invoking the Codex CLI.
    
    Codex is a CLI AI agent that runs non-interactively via `codex exec`.
    
    Example usage:
        adapter = CodexAdapter(model="o4-mini", sandbox="workspace-write")
        request = AgentRequest(task_prompt="Fix the bug in main.py")
        result = adapter.run(request)
    """

    def __init__(
        self,
        model: str = "o4-mini",
        sandbox: str = "workspace-write",
        approval_mode: str = "never",
        quiet: bool = True,
        writable_root: str | None = None,
        config_overrides: dict[str, str] | None = None,
    ) -> None:
        """Initialize the Codex adapter.
        
        Args:
            model: Model name to use (default: o4-mini).
            sandbox: Sandbox policy - read-only, workspace-write, or danger-full-access
                     (default: workspace-write).
            approval_mode: Approval mode - untrusted, on-failure, on-request, or never
                           (default: never for non-interactive use).
            quiet: Enable quiet mode for non-interactive execution (default: True).
            writable_root: Optional writable root directory path.
            config_overrides: Optional dict of key=value configuration overrides.
        """
        super().__init__()
        self.model = model
        self.sandbox = sandbox
        self.approval_mode = approval_mode
        self.quiet = quiet
        self.writable_root = writable_root
        self.config_overrides = config_overrides

    @property
    def agent_name(self) -> str:
        """Return the name of the agent."""
        return "codex"

    def build_command(self, request: AgentRequest) -> list[str]:
        """Build the Codex CLI command arguments list.
        
        Args:
            request: The agent request containing task and configuration.
            
        Returns:
            List of command arguments to execute.
        """
        cmd: list[str] = ["codex", "exec"]
        
        # Add the prompt as a positional argument
        cmd.append(request.task_prompt)
        
        # Model - use request.model if set, otherwise use self.model
        effective_model = request.model if request.model else self.model
        cmd.extend(["--model", effective_model])
        
        # Sandbox policy
        cmd.extend(["--sandbox", self.sandbox])
        
        # Approval mode
        cmd.extend(["--ask-for-approval", self.approval_mode])
        
        # Quiet mode
        if self.quiet:
            cmd.append("-q")
        
        # Writable root - default to workspace_dir if not explicitly set
        writable_root = self.writable_root
        if writable_root is None:
            writable_root = str(request.workspace_dir)
        cmd.extend(["-w", writable_root])
        
        # Config overrides
        if self.config_overrides:
            for key, value in self.config_overrides.items():
                cmd.extend(["-c", f"{key}={value}"])
        
        # Handle extra_args from request
        if request.extra_args:
            for key, value in request.extra_args.items():
                if key.startswith("-"):
                    # Already formatted as a flag
                    if value is True:
                        cmd.append(key)
                    elif value is not False and value is not None:
                        cmd.extend([key, str(value)])
                else:
                    # Convert key to flag format
                    flag = f"--{key.replace('_', '-')}"
                    if value is True:
                        cmd.append(flag)
                    elif value is not False and value is not None:
                        cmd.extend([flag, str(value)])
        
        return cmd

    def get_stdin_input(self, request: AgentRequest) -> str | None:
        """Return stdin input for the subprocess.
        
        Codex exec takes the prompt as a positional argument, so no stdin is needed.
        
        Args:
            request: The agent request.
            
        Returns:
            None - Codex does not use stdin for the prompt.
        """
        return None

    def parse_output(
        self, raw_output: str, stderr: str, return_code: int
    ) -> AgentResult:
        """Parse Codex CLI output into a structured AgentResult.
        
        Args:
            raw_output: The stdout from the subprocess.
            stderr: The stderr from the subprocess.
            return_code: The process exit code.
            
        Returns:
            Parsed AgentResult with extracted information.
        """
        parsed = parse_codex_output(raw_output)
        
        is_success = not parsed["is_error"] and return_code == 0
        
        # Determine error message
        error_message: str | None = None
        if not is_success:
            if stderr and stderr.strip():
                error_message = stderr.strip()
            elif parsed.get("is_error"):
                error_message = parsed.get("result_text", "Unknown error")
        
        return AgentResult(
            raw_output=raw_output,
            is_success=is_success,
            tool_calls=[],  # Codex doesn't provide structured tool call output
            steps_count=0,
            error_message=error_message,
        )
