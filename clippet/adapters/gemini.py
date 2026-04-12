"""Gemini CLI adapter for CLIppet framework.

Gemini CLI is Google's terminal-based AI assistant that provides an interactive
chat interface with Gemini models and MCP integration.
"""

from __future__ import annotations

from pathlib import Path

from clippet.adapters.base import BaseSubprocessAdapter
from clippet.models import AgentRequest, AgentResult


class GeminiAdapter(BaseSubprocessAdapter):
    """Adapter for the Gemini CLI agent.
    
    Gemini CLI is invoked via `gemini [options]` for interactive mode,
    or `gemini -p [prompt]` / `gemini [query]` for non-interactive mode.
    
    Configuration is typically done via `~/.gemini/.env` file with environment
    variables like:
    - GOOGLE_GEMINI_BASE_URL: API base URL
    - GEMINI_API_KEY: API key for authentication
    - GEMINI_MODEL: Default model to use
    """

    def __init__(
        self,
        model: str | None = None,
        sandbox: bool = False,
        yolo: bool = False,
        approval_mode: str | None = None,
        allowed_tools: list[str] | None = None,
        extensions: list[str] | None = None,
        include_directories: list[str] | None = None,
        output_format: str = "text",
        **kwargs,
    ) -> None:
        """Initialize the Gemini CLI adapter.
        
        Args:
            model: Model to use (e.g., gemini-2.5-pro, gemini-2.5-flash).
            sandbox: Run in sandbox mode.
            yolo: Automatically accept all actions without confirmation.
            approval_mode: Approval mode (default, auto_edit, yolo).
            allowed_tools: Tools that are allowed to run without confirmation.
            extensions: List of extensions to use.
            include_directories: Additional directories to include in workspace.
            output_format: Output format for non-interactive mode (text, json, stream-json).
            **kwargs: Additional options passed to base class.
        """
        super().__init__(**kwargs)
        self.model = model
        self.sandbox = sandbox
        self.yolo = yolo
        self.approval_mode = approval_mode
        self.allowed_tools = allowed_tools
        self.extensions = extensions
        self.include_directories = include_directories
        self.output_format = output_format

    @property
    def agent_name(self) -> str:
        """Return the name of the agent."""
        return "gemini"

    def build_command(self, request: AgentRequest) -> list[str]:
        """Build the Gemini CLI command.
        
        Args:
            request: The agent request containing task and configuration.
            
        Returns:
            List of command arguments to execute.
        """
        cmd = ["gemini"]
        
        # Build effective prompt — prepend skills if present.
        effective_prompt = request.task_prompt
        if request.injected_skills:
            skills_block = self._format_skills_text(request.injected_skills)
            effective_prompt = (
                skills_block
                + "\n\n---\n\n"
                + effective_prompt
            )

        # Model selection
        if self.model:
            cmd.extend(["-m", self.model])
        
        # Sandbox mode
        if self.sandbox:
            cmd.append("-s")
        
        # YOLO mode (auto-accept all)
        if self.yolo:
            cmd.append("-y")
        
        # Approval mode
        if self.approval_mode:
            cmd.extend(["--approval-mode", self.approval_mode])
        
        # Allowed tools
        if self.allowed_tools:
            for tool in self.allowed_tools:
                cmd.extend(["--allowed-tools", tool])
        
        # Extensions
        if self.extensions:
            for ext in self.extensions:
                cmd.extend(["-e", ext])
        
        # Include directories
        if self.include_directories:
            for directory in self.include_directories:
                cmd.extend(["--include-directories", directory])
        elif request.workspace_dir:
            cmd.extend(["--include-directories", request.workspace_dir])
        
        # Output format for non-interactive mode
        if self.output_format and self.output_format != "text":
            cmd.extend(["-o", self.output_format])
        
        # Add the prompt as positional argument (Gemini prefers positional over -p)
        if effective_prompt:
            cmd.append(effective_prompt)
        
        # Add extra args from request
        if request.extra_args:
            for key, value in request.extra_args.items():
                cmd.append(f"--{key}")
                if value is not None and value is not True:
                    cmd.append(str(value))
        
        return cmd

    def build_interactive_command(self, request: AgentRequest | None = None) -> list[str]:
        """Build the Gemini CLI command for interactive mode.
        
        Args:
            request: Optional agent request for configuration.
            
        Returns:
            List of command arguments to execute in interactive mode.
        """
        cmd = ["gemini"]
        
        # Model selection
        if self.model:
            cmd.extend(["-m", self.model])
        
        # Sandbox mode
        if self.sandbox:
            cmd.append("-s")
        
        # YOLO mode
        if self.yolo:
            cmd.append("-y")
        
        # Approval mode
        if self.approval_mode:
            cmd.extend(["--approval-mode", self.approval_mode])
        
        # Allowed tools
        if self.allowed_tools:
            for tool in self.allowed_tools:
                cmd.extend(["--allowed-tools", tool])
        
        # Extensions
        if self.extensions:
            for ext in self.extensions:
                cmd.extend(["-e", ext])
        
        # Include directories
        if self.include_directories:
            for directory in self.include_directories:
                cmd.extend(["--include-directories", directory])
        elif request and request.workspace_dir:
            cmd.extend(["--include-directories", request.workspace_dir])
        
        return cmd

    def get_stdin_input(self, request: AgentRequest) -> str | None:
        """Return stdin input for the subprocess.
        
        Gemini receives prompt as positional argument, not via stdin.
        
        Args:
            request: The agent request.
            
        Returns:
            None - prompt is passed as positional argument.
        """
        return None

    def parse_output(
        self, raw_output: str, stderr: str, return_code: int
    ) -> AgentResult:
        """Parse Gemini CLI output into an AgentResult.
        
        Args:
            raw_output: The stdout from the subprocess.
            stderr: The stderr from the subprocess.
            return_code: The process exit code.
            
        Returns:
            Parsed AgentResult with extracted information.
        """
        is_success = return_code == 0
        
        # Determine error message
        error_message = None
        if not is_success:
            if stderr and stderr.strip():
                error_message = stderr.strip()
            elif not raw_output.strip():
                error_message = f"Gemini CLI exited with code {return_code}"
        
        return AgentResult(
            raw_output=raw_output,
            is_success=is_success,
            tool_calls=[],  # Gemini doesn't provide structured tool call output by default
            steps_count=0,
            error_message=error_message,
        )


def parse_gemini_env_file(content: str) -> dict[str, str]:
    """Parse a Gemini .env file into a dictionary.
    
    This follows the same format as `~/.gemini/.env`:
    - Lines starting with # are comments
    - Empty lines are skipped
    - Format is KEY=VALUE
    
    Args:
        content: Raw content of the .env file.
        
    Returns:
        Dictionary of environment variable key-value pairs.
    """
    env_map: dict[str, str] = {}
    
    for line in content.splitlines():
        line = line.strip()
        
        # Skip empty lines and comments
        if not line or line.startswith("#"):
            continue
        
        # Parse KEY=VALUE
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            
            # Validate key is not empty and contains valid characters
            if key and all(c.isalnum() or c == "_" for c in key):
                env_map[key] = value
    
    return env_map


def serialize_gemini_env_file(env_map: dict[str, str]) -> str:
    """Serialize a dictionary to Gemini .env file format.
    
    Args:
        env_map: Dictionary of environment variable key-value pairs.
        
    Returns:
        Content string in .env format.
    """
    lines = []
    
    # Sort keys for stable output
    for key in sorted(env_map.keys()):
        value = env_map[key]
        lines.append(f"{key}={value}")
    
    return "\n".join(lines)
