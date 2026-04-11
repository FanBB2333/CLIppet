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
        model: str | None = "o4-mini",
        sandbox: str = "workspace-write",
        full_auto: bool = True,
        cd: str | None = None,
        add_dirs: list[str] | None = None,
        config_overrides: dict[str, str] | None = None,
    ) -> None:
        """Initialize the Codex adapter.
        
        Args:
            model: Model name to use (default: o4-mini).  Set to *None*
                to omit the ``--model`` flag and let the Codex CLI read the
                model from its own ``config.toml``.
            sandbox: Sandbox policy — ``read-only``, ``workspace-write``,
                or ``danger-full-access`` (default: ``workspace-write``).
            full_auto: When *True* (the default), pass ``--full-auto`` so
                that codex runs without interactive approval prompts.
            cd: Optional working-root directory (``-C``/``--cd``).
            add_dirs: Additional writable directories (``--add-dir``).
            config_overrides: Optional dict of ``key=value`` configuration
                overrides passed via ``-c``.
        """
        super().__init__()
        self.model = model
        self.sandbox = sandbox
        self.full_auto = full_auto
        self.cd = cd
        self.add_dirs = add_dirs
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

        # Model — use request.model if set, otherwise use self.model.
        # When both are None, skip --model entirely (let config.toml decide).
        effective_model = request.model if request.model else self.model
        if effective_model:
            cmd.extend(["--model", effective_model])

        # Sandbox policy
        cmd.extend(["--sandbox", self.sandbox])

        # Full-auto mode (no interactive approval prompts)
        if self.full_auto:
            cmd.append("--full-auto")

        # Working-root directory
        cd = self.cd
        if cd is None:
            cd = str(request.workspace_dir)
        cmd.extend(["--cd", cd])

        # Additional writable directories
        if self.add_dirs:
            for d in self.add_dirs:
                cmd.extend(["--add-dir", d])

        # Config overrides (-c key=value)
        if self.config_overrides:
            for key, value in self.config_overrides.items():
                cmd.extend(["-c", f"{key}={value}"])

        # Handle extra_args from request
        if request.extra_args:
            for key, value in request.extra_args.items():
                if key.startswith("-"):
                    if value is True:
                        cmd.append(key)
                    elif value is not False and value is not None:
                        cmd.extend([key, str(value)])
                else:
                    flag = f"--{key.replace('_', '-')}"
                    if value is True:
                        cmd.append(flag)
                    elif value is not False and value is not None:
                        cmd.extend([flag, str(value)])

        # Prompt is a positional argument — place it last.
        cmd.append(request.task_prompt)

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
