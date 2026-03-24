"""Base subprocess adapter for CLI AI agents."""

from abc import ABC, abstractmethod
import asyncio
import subprocess
import time
from pathlib import Path
from typing import Any

from clippet.models import AgentRequest, AgentResult


class BaseSubprocessAdapter(ABC):
    """Abstract base class providing shared subprocess execution logic for all CLI adapters.
    
    Subclasses must implement:
        - agent_name: property returning the agent's name
        - build_command: method to build CLI command arguments
        - parse_output: method to parse CLI output into AgentResult
    
    Optionally override:
        - get_stdin_input: method to provide stdin input for the subprocess
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the adapter with optional configuration.
        
        Args:
            **kwargs: CLI-specific options stored as instance attributes.
                      Common options include 'model', 'api_key', etc.
        """
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Return the name of the agent this adapter interfaces with."""
        ...

    @abstractmethod
    def build_command(self, request: AgentRequest) -> list[str]:
        """Build the CLI command arguments list.
        
        Args:
            request: The agent request containing task and configuration.
            
        Returns:
            List of command arguments to execute.
        """
        ...

    @abstractmethod
    def parse_output(
        self, raw_output: str, stderr: str, return_code: int
    ) -> AgentResult:
        """Parse CLI output into a structured AgentResult.
        
        Args:
            raw_output: The stdout from the subprocess.
            stderr: The stderr from the subprocess.
            return_code: The process exit code.
            
        Returns:
            Parsed AgentResult with extracted information.
        """
        ...

    def get_stdin_input(self, request: AgentRequest) -> str | None:
        """Return input to pipe via stdin to the subprocess.
        
        Override this method in subclasses if the CLI accepts stdin input.
        
        Args:
            request: The agent request.
            
        Returns:
            String to send to stdin, or None if no stdin input needed.
        """
        return None

    def run(self, request: AgentRequest) -> AgentResult:
        """Execute the agent synchronously with the given request.
        
        Args:
            request: The agent request containing task and configuration.
            
        Returns:
            AgentResult with execution results.
        """
        start_time = time.monotonic()
        
        try:
            command = self.build_command(request)
        except Exception as e:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Failed to build command: {e}",
            )

        # Determine working directory
        cwd = Path(request.workspace_dir).resolve()
        
        # Get optional stdin input
        stdin_input = self.get_stdin_input(request)
        stdin_bytes = stdin_input.encode("utf-8") if stdin_input else None

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE if stdin_bytes else None,
                cwd=cwd,
                env=None,  # Inherit parent environment
            )
        except FileNotFoundError:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"CLI binary not found: {command[0] if command else 'unknown'}",
            )
        except PermissionError:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Permission denied executing: {command[0] if command else 'unknown'}",
            )
        except Exception as e:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Failed to start process: {e}",
            )

        try:
            stdout_bytes, stderr_bytes = process.communicate(
                input=stdin_bytes,
                timeout=request.timeout,
            )
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()  # Clean up
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Process timed out after {request.timeout} seconds",
            )
        except Exception as e:
            process.kill()
            try:
                process.communicate()
            except Exception:
                pass
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Error during process communication: {e}",
            )

        # Decode output
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        
        execution_time = time.monotonic() - start_time

        try:
            result = self.parse_output(stdout, stderr, process.returncode)
            result.execution_time = execution_time
            return result
        except Exception as e:
            return AgentResult(
                raw_output=stdout,
                is_success=False,
                execution_time=execution_time,
                error_message=f"Failed to parse output: {e}",
            )

    async def run_async(self, request: AgentRequest) -> AgentResult:
        """Execute the agent asynchronously with the given request.
        
        Args:
            request: The agent request containing task and configuration.
            
        Returns:
            AgentResult with execution results.
        """
        start_time = time.monotonic()
        
        try:
            command = self.build_command(request)
        except Exception as e:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Failed to build command: {e}",
            )

        # Determine working directory
        cwd = Path(request.workspace_dir).resolve()
        
        # Get optional stdin input
        stdin_input = self.get_stdin_input(request)
        stdin_bytes = stdin_input.encode("utf-8") if stdin_input else None

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if stdin_bytes else None,
                cwd=cwd,
                env=None,  # Inherit parent environment
            )
        except FileNotFoundError:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"CLI binary not found: {command[0] if command else 'unknown'}",
            )
        except PermissionError:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Permission denied executing: {command[0] if command else 'unknown'}",
            )
        except Exception as e:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Failed to start process: {e}",
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=stdin_bytes),
                timeout=request.timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()  # Clean up
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Process timed out after {request.timeout} seconds",
            )
        except Exception as e:
            process.kill()
            try:
                await process.communicate()
            except Exception:
                pass
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Error during process communication: {e}",
            )

        # Decode output
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        
        execution_time = time.monotonic() - start_time

        try:
            result = self.parse_output(stdout, stderr, process.returncode or 0)
            result.execution_time = execution_time
            return result
        except Exception as e:
            return AgentResult(
                raw_output=stdout,
                is_success=False,
                execution_time=execution_time,
                error_message=f"Failed to parse output: {e}",
            )
