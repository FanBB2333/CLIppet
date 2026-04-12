"""Base adapter classes for CLIppet.

Provides ``BaseAdapter`` — the unified abstract base for **all** adapters
(subprocess-based and API-based) — and ``BaseSubprocessAdapter`` which adds
shared subprocess execution logic on top.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from pathlib import Path
import subprocess
import time
from typing import Any

from clippet.adapters.personas import format_skill_block
from clippet.isolation import (
    CredentialSet,
    DirectoryCopyProvider,
    EnvVarCredentialProvider,
    FileCredentialProvider,
    IsolatedEnvironment,
)
from clippet.models import AgentRequest, AgentResult, IsolationConfig


# ---------------------------------------------------------------------------
# BaseAdapter — unified abstract base for ALL adapters
# ---------------------------------------------------------------------------


class BaseAdapter(ABC):
    """Unified abstract base class for all CLIppet adapters.

    Every adapter — whether it spawns a local CLI subprocess or calls a
    remote API — must inherit from this class and implement ``agent_name``,
    ``run``, and ``run_async``.
    """

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Return the name of the agent this adapter interfaces with."""
        ...

    @abstractmethod
    def run(self, request: AgentRequest) -> AgentResult:
        """Execute the agent synchronously with the given request."""
        ...

    @abstractmethod
    async def run_async(self, request: AgentRequest) -> AgentResult:
        """Execute the agent asynchronously with the given request."""
        ...


# ---------------------------------------------------------------------------
# BaseSubprocessAdapter — shared subprocess execution logic
# ---------------------------------------------------------------------------


class BaseSubprocessAdapter(BaseAdapter):
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

        self.default_isolation: IsolationConfig | None = kwargs.pop(
            "default_isolation",
            None,
        )

        for key, value in kwargs.items():
            setattr(self, key, value)

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

    # -- Skill injection helpers ---------------------------------------------

    @staticmethod
    def _format_skills_text(skills: list[str]) -> str:
        """Format a list of skill texts into a single block.

        Uses the shared ``format_skill_block`` helper so that the
        ``<skill_content>`` tag format is consistent across API and CLI
        adapters.

        Args:
            skills: List of raw skill text strings.

        Returns:
            Formatted skill block, or ``""`` if *skills* is empty.
        """

        return format_skill_block(skills)

    def run(self, request: AgentRequest) -> AgentResult:
        """Execute the agent synchronously with the given request.

        Args:
            request: The agent request containing task and configuration.

        Returns:
            AgentResult with execution results.
        """

        start_time = time.monotonic()
        prepared = self._prepare_execution(request, start_time)
        if isinstance(prepared, AgentResult):
            return prepared

        command, cwd, stdin_bytes = prepared
        isolation = self._resolve_isolation(request)

        if isolation is None:
            return self._execute_sync(
                command=command,
                cwd=cwd,
                stdin_bytes=stdin_bytes,
                env=None,
                timeout=request.timeout,
                start_time=start_time,
            )

        try:
            with self._create_isolated_environment(isolation) as isolated_env:
                self._build_credential_set(isolation).inject(isolated_env)
                return self._execute_sync(
                    command=command,
                    cwd=cwd,
                    stdin_bytes=stdin_bytes,
                    env=isolated_env.env,
                    timeout=request.timeout,
                    start_time=start_time,
                )
        except Exception as e:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Failed to prepare isolated environment: {e}",
            )

    async def run_async(self, request: AgentRequest) -> AgentResult:
        """Execute the agent asynchronously with the given request.

        Args:
            request: The agent request containing task and configuration.

        Returns:
            AgentResult with execution results.
        """

        start_time = time.monotonic()
        prepared = self._prepare_execution(request, start_time)
        if isinstance(prepared, AgentResult):
            return prepared

        command, cwd, stdin_bytes = prepared
        isolation = self._resolve_isolation(request)

        if isolation is None:
            return await self._execute_async(
                command=command,
                cwd=cwd,
                stdin_bytes=stdin_bytes,
                env=None,
                timeout=request.timeout,
                start_time=start_time,
            )

        try:
            with self._create_isolated_environment(isolation) as isolated_env:
                self._build_credential_set(isolation).inject(isolated_env)
                return await self._execute_async(
                    command=command,
                    cwd=cwd,
                    stdin_bytes=stdin_bytes,
                    env=isolated_env.env,
                    timeout=request.timeout,
                    start_time=start_time,
                )
        except Exception as e:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Failed to prepare isolated environment: {e}",
            )

    def _prepare_execution(
        self,
        request: AgentRequest,
        start_time: float,
    ) -> tuple[list[str], Path, bytes | None] | AgentResult:
        """Build command and execution context shared by sync and async paths."""

        try:
            command = self.build_command(request)
        except Exception as e:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Failed to build command: {e}",
            )

        cwd = Path(request.workspace_dir).resolve()
        stdin_input = self.get_stdin_input(request)
        stdin_bytes = stdin_input.encode("utf-8") if stdin_input else None

        return command, cwd, stdin_bytes

    def _resolve_isolation(self, request: AgentRequest) -> IsolationConfig | None:
        """Resolve the effective isolation config for this execution."""

        if self.default_isolation is None:
            return request.isolation

        if request.isolation is None:
            return self.default_isolation

        return self.default_isolation.merged_with(request.isolation)

    def _create_isolated_environment(
        self,
        isolation: IsolationConfig,
    ) -> IsolatedEnvironment:
        """Create an isolated runtime environment from request config."""

        home_dir = Path(isolation.home_dir) if isolation.home_dir else None

        return IsolatedEnvironment(
            home_dir=home_dir,
            persist=isolation.persist_sandbox,
            env_whitelist=isolation.env_whitelist,
            env_blacklist=isolation.env_blacklist,
        )

    def _build_credential_set(self, isolation: IsolationConfig) -> CredentialSet:
        """Build credential providers for an isolation config."""

        providers = []

        if isolation.credential_files:
            providers.append(FileCredentialProvider(isolation.credential_files))

        if isolation.env_overrides:
            providers.append(EnvVarCredentialProvider(isolation.env_overrides))

        return CredentialSet(providers)

    def _execute_sync(
        self,
        command: list[str],
        cwd: Path,
        stdin_bytes: bytes | None,
        env: dict[str, str] | None,
        timeout: int,
        start_time: float,
    ) -> AgentResult:
        """Run the subprocess synchronously and parse its output."""

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE if stdin_bytes else None,
                cwd=cwd,
                env=env,
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
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Process timed out after {timeout} seconds",
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

        return self._parse_result(
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            return_code=process.returncode,
            start_time=start_time,
        )

    async def _execute_async(
        self,
        command: list[str],
        cwd: Path,
        stdin_bytes: bytes | None,
        env: dict[str, str] | None,
        timeout: int,
        start_time: float,
    ) -> AgentResult:
        """Run the subprocess asynchronously and parse its output."""

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if stdin_bytes else None,
                cwd=cwd,
                env=env,
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
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Process timed out after {timeout} seconds",
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

        return self._parse_result(
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            return_code=process.returncode or 0,
            start_time=start_time,
        )

    def _parse_result(
        self,
        stdout_bytes: bytes | None,
        stderr_bytes: bytes | None,
        return_code: int,
        start_time: float,
    ) -> AgentResult:
        """Decode process output and hand off to adapter-specific parsing."""

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        execution_time = time.monotonic() - start_time

        try:
            result = self.parse_output(stdout, stderr, return_code)
            result.execution_time = execution_time
            return result
        except Exception as e:
            return AgentResult(
                raw_output=stdout,
                is_success=False,
                execution_time=execution_time,
                error_message=f"Failed to parse output: {e}",
            )
