"""Base adapter classes for CLIppet.

Provides ``BaseAdapter`` — the unified abstract base for **all** adapters
(subprocess-based and API-based) — and ``BaseSubprocessAdapter`` which adds
shared subprocess execution logic on top.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import logging
from pathlib import Path
import subprocess
import time
from typing import Any, Callable

from clippet.adapters.personas import format_skill_block
from clippet.isolation import (
    ContentCredentialProvider,
    CredentialSet,
    CredentialSyncBack,
    EnvVarCredentialProvider,
    FileCredentialProvider,
    IsolatedEnvironment,
    SCOPE_AGENT,
    config_dirs_for,
    effective_scope,
    scrub_patterns_for,
)
from clippet.models import AgentRequest, AgentResult, IsolationConfig

logger = logging.getLogger("clippet")


def render_extra_args(extra_args: dict[str, Any] | None) -> list[str]:
    """Render ``AgentRequest.extra_args`` into CLI flags.

    * keys already starting with ``-`` are used verbatim, other keys become
      ``--key-name`` (underscores turned into dashes);
    * ``True`` emits the bare flag, ``False`` / ``None`` emit nothing;
    * lists/tuples repeat the flag once per value.
    """

    rendered: list[str] = []
    for key, value in (extra_args or {}).items():
        flag = key if key.startswith("-") else f"--{key.replace('_', '-')}"
        if value is True:
            rendered.append(flag)
        elif value is False or value is None:
            continue
        elif isinstance(value, (list, tuple)):
            for item in value:
                rendered.extend([flag, str(item)])
        else:
            rendered.extend([flag, str(value)])
    return rendered


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


OutputCallback = Callable[[str], None]


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

    #: Agent key used to decide which inherited environment variables are
    #: dropped in isolated runs (see :data:`clippet.isolation.AGENT_IDENTITY_ENV_VARS`).
    isolation_agent: str | None = None

    #: Give the subprocess an empty stdin when no input is sent, instead of
    #: inheriting ours (for CLIs that read piped stdin to its end first).
    close_stdin: bool = False

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
                sync = CredentialSyncBack(isolation.sync_back)
                sync.snapshot(isolated_env)
                try:
                    return self._execute_sync(
                        command=command,
                        cwd=cwd,
                        stdin_bytes=stdin_bytes,
                        env=isolated_env.env,
                        timeout=request.timeout,
                        start_time=start_time,
                    )
                finally:
                    # Also on Ctrl-C: a refreshed login must outlive the temp HOME.
                    self._finalize_sync(sync, isolated_env)
        except Exception as e:
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Failed to prepare isolated environment: {e}",
            )

    async def run_async(
        self,
        request: AgentRequest,
        on_output: OutputCallback | None = None,
    ) -> AgentResult:
        """Execute the agent asynchronously with the given request.

        Args:
            request: The agent request containing task and configuration.
            on_output: Called with each line of the agent's stdout as it
                arrives (for example Codex ``--json`` events), so a caller can
                show progress before the run ends.  Streaming runs do not hand
                the caller's stdin to the agent.

        Returns:
            AgentResult with execution results.  Cancelling the call kills the
            agent process.
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
                on_output=on_output,
            )

        try:
            with self._create_isolated_environment(isolation) as isolated_env:
                self._build_credential_set(isolation).inject(isolated_env)
                sync = CredentialSyncBack(isolation.sync_back)
                sync.snapshot(isolated_env)
                try:
                    return await self._execute_async(
                        command=command,
                        cwd=cwd,
                        stdin_bytes=stdin_bytes,
                        env=isolated_env.env,
                        timeout=request.timeout,
                        start_time=start_time,
                        on_output=on_output,
                    )
                finally:
                    # Also when the run is cancelled: a refreshed login must
                    # outlive the temp HOME.
                    self._finalize_sync(sync, isolated_env)
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

        return create_isolated_environment(isolation, self.isolation_agent)

    def _build_credential_set(self, isolation: IsolationConfig) -> CredentialSet:
        """Build credential providers for an isolation config."""

        return build_credential_set(isolation)

    @staticmethod
    def _finalize_sync(sync: CredentialSyncBack, env: IsolatedEnvironment) -> None:
        try:
            for destination in sync.finalize(env):
                logger.info("Synced refreshed credentials back to %s", destination)
        except OSError as exc:  # pragma: no cover - best effort
            logger.warning("Could not sync credentials back: %s", exc)

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
                stdin=subprocess.PIPE if stdin_bytes else (subprocess.DEVNULL if self.close_stdin else None),
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
        on_output: OutputCallback | None = None,
    ) -> AgentResult:
        """Run the subprocess asynchronously and parse its output."""

        if stdin_bytes:
            stdin = asyncio.subprocess.PIPE
        elif on_output is not None or self.close_stdin:
            stdin = asyncio.subprocess.DEVNULL
        else:
            stdin = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=stdin,
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
            if on_output is None:
                exchange = process.communicate(input=stdin_bytes)
            else:
                exchange = _stream_process(process, stdin_bytes, on_output)
            stdout_bytes, stderr_bytes = await asyncio.wait_for(exchange, timeout=timeout)
        except asyncio.TimeoutError:
            await _kill(process)
            return AgentResult(
                raw_output="",
                is_success=False,
                execution_time=time.monotonic() - start_time,
                error_message=f"Process timed out after {timeout} seconds",
            )
        except asyncio.CancelledError:
            await _kill(process)
            raise
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


async def _kill(process: asyncio.subprocess.Process) -> None:
    """Stop *process* and reap it (bounded, so a stuck child cannot hang us)."""

    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except (asyncio.TimeoutError, ProcessLookupError):  # pragma: no cover - best effort
        pass


async def _stream_process(
    process: asyncio.subprocess.Process,
    stdin_bytes: bytes | None,
    on_output: OutputCallback,
) -> tuple[bytes, bytes]:
    """Collect stdout/stderr like ``communicate()``, passing stdout on line by line."""

    async def feed() -> None:
        if process.stdin is None:
            return
        try:
            if stdin_bytes:
                process.stdin.write(stdin_bytes)
                await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    def emit(raw: bytes) -> None:
        try:
            on_output(raw.decode("utf-8", errors="replace").rstrip("\r"))
        except Exception:  # noqa: BLE001 - a broken listener must not break the run
            logger.exception("on_output callback failed")

    async def pump(stream: asyncio.StreamReader | None, sink: list[bytes], lines: bool) -> None:
        if stream is None:
            return
        pending = b""
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            sink.append(chunk)
            if lines:
                pending += chunk
                *complete, pending = pending.split(b"\n")
                for line in complete:
                    emit(line)
        if lines and pending:
            emit(pending)

    out: list[bytes] = []
    err: list[bytes] = []
    await asyncio.gather(feed(), pump(process.stdout, out, True), pump(process.stderr, err, False))
    await process.wait()
    return b"".join(out), b"".join(err)


def create_isolated_environment(
    isolation: IsolationConfig,
    agent: str | None = None,
) -> IsolatedEnvironment:
    """Build an :class:`IsolatedEnvironment` for *isolation* and *agent*.

    Every way CLIppet starts an agent in an env (interactive or headless,
    sync or async, from the CLI or the TUI) comes through here, so they all
    get the same environment.
    """

    home_dir = Path(isolation.home_dir) if isolation.home_dir else None
    scope = effective_scope(isolation.isolation_scope, agent)
    scrub = scrub_patterns_for(agent, scope) if isolation.scrub_agent_env else ()

    return IsolatedEnvironment(
        home_dir=home_dir,
        persist=isolation.persist_sandbox,
        env_whitelist=isolation.env_whitelist,
        env_blacklist=isolation.env_blacklist,
        scrub_env=scrub,
        scope=scope,
        config_dirs=config_dirs_for(agent) if scope == SCOPE_AGENT else None,
    )


def build_credential_set(isolation: IsolationConfig) -> CredentialSet:
    """Build the credential providers described by an isolation config."""

    providers = []

    if isolation.credential_files:
        providers.append(FileCredentialProvider(isolation.credential_files))

    if isolation.credential_contents:
        providers.append(ContentCredentialProvider(isolation.credential_contents))

    if isolation.env_overrides:
        providers.append(EnvVarCredentialProvider(isolation.env_overrides))

    return CredentialSet(providers)
