"""CLI entry point for CLIppet."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from clippet.adapters.base import BaseSubprocessAdapter
from clippet.adapters.claude import ClaudeAdapter
from clippet.adapters.codex import CodexAdapter
from clippet.config.detector import (
    create_adapter_from_config_file,
    detect_config_type,
)
from clippet.config.environments import (
    add_environment,
    get_environment,
    list_environments,
    remove_environment,
)
from clippet.config.registry import create_runner_from_config, load_config
from clippet.isolation import (
    CredentialSet,
    EnvVarCredentialProvider,
    FileCredentialProvider,
    IsolatedEnvironment,
)
from clippet.models import AgentRequest, IsolationConfig
from clippet.parsers.extractors import parse_claude_json_output


class _ClippetArgumentParser:
    """Small dispatcher that supports both subcommands and direct agent launch."""

    def __init__(self) -> None:
        description = (
            "CLIppet - A unified adapter framework for orchestrating CLI AI agents."
        )

        self._root_parser = argparse.ArgumentParser(
            prog="clippet",
            description=description,
        )

        subparsers = self._root_parser.add_subparsers(dest="command")

        run_parser = subparsers.add_parser("run", help="Run an agent with a config")
        _add_run_arguments(run_parser)

        env_parser = subparsers.add_parser("env", help="Manage environment profiles")
        env_sub = env_parser.add_subparsers(dest="env_action")

        env_sub.add_parser("list", help="List registered environments")

        env_add = env_sub.add_parser("add", help="Register an environment profile")
        env_add.add_argument("name", help="Name for the environment")
        env_add.add_argument("config_path", help="Path to the config file")

        env_rm = env_sub.add_parser(
            "remove",
            help="Unregister an environment profile",
        )
        env_rm.add_argument("name", help="Name of the environment to remove")

        self._run_parser = argparse.ArgumentParser(
            prog="clippet",
            description=description,
        )
        _add_run_arguments(self._run_parser)

        self._env_parser = argparse.ArgumentParser(
            prog="clippet env",
            description="Manage environment profiles",
        )
        env_sub = self._env_parser.add_subparsers(dest="env_action")

        env_sub.add_parser("list", help="List registered environments")

        env_add = env_sub.add_parser("add", help="Register an environment profile")
        env_add.add_argument("name", help="Name for the environment")
        env_add.add_argument("config_path", help="Path to the config file")

        env_rm = env_sub.add_parser(
            "remove",
            help="Unregister an environment profile",
        )
        env_rm.add_argument("name", help="Name of the environment to remove")

    def parse_args(self, args: list[str] | None = None) -> argparse.Namespace:
        """Parse CLI arguments with support for top-level agent invocations."""

        argv = list(sys.argv[1:] if args is None else args)

        if argv and argv[0] == "env":
            namespace = self._env_parser.parse_args(argv[1:])
            namespace.command = "env"
            return namespace

        if argv and argv[0] == "run":
            namespace = self._run_parser.parse_args(argv[1:])
            namespace.command = "run"
            return namespace

        namespace = self._run_parser.parse_args(argv)
        namespace.command = None
        return namespace

    def print_help(self) -> None:
        """Print the top-level help message."""

        self._root_parser.print_help()


def _build_parser() -> _ClippetArgumentParser:
    """Build the top-level parser dispatcher."""
    return _ClippetArgumentParser()


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach the shared run-mode arguments to *parser*."""

    parser.add_argument(
        "-c", "--config",
        help=(
            "Path to a config file (Claude Code, Codex, or CLIppet format) "
            "or a directory to use as second-home"
        ),
    )
    parser.add_argument(
        "--codex-config",
        dest="codex_config",
        help=(
            "Path to a Codex config.toml file (model/provider settings).  "
            "When used with -c pointing to an auth.json, both files are "
            "injected into the sandbox.  Can also be used alone — the "
            "auth.json is then read from ~/.codex/auth.json."
        ),
    )
    parser.add_argument(
        "-e", "--env",
        help="Named environment profile",
    )
    parser.add_argument(
        "-p", "--prompt",
        help="Task prompt",
    )
    parser.add_argument(
        "agent_type",
        nargs="?",
        default=None,
        help=(
            "Native agent type (claude/codex/qoder) or a composite config "
            "adapter name. Optional for native config files and "
            "single-adapter composite configs."
        ),
    )


# --- handlers ---------------------------------------------------------------


def _resolve_config_path(args: argparse.Namespace) -> str | None:
    """Resolve the requested config path from ``-c`` or ``-e``.

    Returns *None* when only ``--codex-config`` is provided (without ``-c``).
    """

    config_path: str | None = getattr(args, "config", None)
    env_name: str | None = getattr(args, "env", None)
    codex_config: str | None = getattr(args, "codex_config", None)

    if config_path:
        return str(Path(config_path).resolve())

    if env_name:
        try:
            env_profile = get_environment(env_name)
        except KeyError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        return env_profile["config_path"]

    # Allow --codex-config alone (no -c required)
    if codex_config:
        return None

    print(
        "Error: Either -c/--config, -e/--env, or --codex-config must be provided.",
        file=sys.stderr,
    )
    sys.exit(1)


def _resolve_prompt(args: argparse.Namespace) -> str | None:
    """Resolve a prompt from ``-p`` or stdin, if present."""

    prompt: str | None = getattr(args, "prompt", None)
    if prompt:
        return prompt

    if not sys.stdin.isatty():
        stdin_prompt = sys.stdin.read().strip()
        if stdin_prompt:
            return stdin_prompt

    return None


def _normalize_native_agent_type(
    config_path: str,
    detected_type: str,
    agent_type: str | None,
) -> str:
    """Validate and normalize the requested native agent type."""

    if detected_type not in {"claude_code", "codex"}:
        raise ValueError(f"Unsupported native config type: {detected_type}")

    if agent_type == "qoder":
        raise ValueError(
            "'qoder' is only supported with CLIppet composite configs. "
            "Please use a CLIppet config file instead."
        )

    requested_type = agent_type
    if requested_type == "claude":
        requested_type = "claude_code"

    if requested_type is None:
        return detected_type

    if requested_type != detected_type:
        detected_name = "claude" if detected_type == "claude_code" else detected_type
        raise ValueError(
            f"Config '{config_path}' was detected as '{detected_name}', "
            f"but '{agent_type}' was requested."
        )

    return requested_type


def _resolve_clippet_adapter_name(
    config,
    agent_type: str | None,
) -> str:
    """Resolve the adapter name for a CLIppet composite config."""

    if agent_type:
        return agent_type

    adapter_names = [adapter.name for adapter in config.adapters]
    if len(adapter_names) == 1:
        return adapter_names[0]

    available = ", ".join(adapter_names) if adapter_names else "(none)"
    raise ValueError(
        "agent_type is required for CLIppet composite configs with multiple "
        f"adapters. Available adapters: {available}"
    )


def _build_interactive_command(adapter: BaseSubprocessAdapter) -> list[str]:
    """Build the interactive command for a configured adapter."""

    if isinstance(adapter, ClaudeAdapter):
        command = ["claude"]

        if adapter.model:
            command.extend(["--model", adapter.model])
        if adapter.permission_mode:
            command.extend(["--permission-mode", adapter.permission_mode])
        if adapter.allowed_tools:
            command.extend(["--allowed-tools", ",".join(adapter.allowed_tools)])
        if adapter.disallowed_tools:
            command.extend(["--disallowed-tools", ",".join(adapter.disallowed_tools)])
        if adapter.append_system_prompt:
            command.extend(["--append-system-prompt", adapter.append_system_prompt])
        if adapter.verbose:
            command.append("--verbose")

        return command

    if isinstance(adapter, CodexAdapter):
        command = ["codex"]

        if adapter.model:
            command.extend(["--model", adapter.model])
        if adapter.sandbox:
            command.extend(["--sandbox", adapter.sandbox])
        if adapter.config_overrides:
            for key, value in adapter.config_overrides.items():
                command.extend(["-c", f"{key}={value}"])

        return command

    raise ValueError(
        "Interactive launch is only supported for Claude and Codex adapters."
    )


def _build_credential_set(isolation: IsolationConfig) -> CredentialSet:
    """Build credential providers for an isolation config."""

    providers = []

    if isolation.credential_files:
        providers.append(FileCredentialProvider(isolation.credential_files))

    if isolation.env_overrides:
        providers.append(EnvVarCredentialProvider(isolation.env_overrides))

    return CredentialSet(providers)


def _run_interactive_command(
    command: list[str],
    isolation: IsolationConfig | None,
    cwd: Path,
) -> None:
    """Launch an interactive agent command with optional isolation."""

    try:
        if isolation is None:
            completed = subprocess.run(command, cwd=cwd, check=False)
        else:
            home_dir = Path(isolation.home_dir) if isolation.home_dir else None
            with IsolatedEnvironment(
                home_dir=home_dir,
                persist=isolation.persist_sandbox,
                env_whitelist=isolation.env_whitelist,
                env_blacklist=isolation.env_blacklist,
            ) as isolated_env:
                _build_credential_set(isolation).inject(isolated_env)
                completed = subprocess.run(
                    command,
                    cwd=cwd,
                    env=isolated_env.env,
                    check=False,
                )
    except FileNotFoundError:
        print(
            f"Error: CLI binary not found: {command[0] if command else 'unknown'}",
            file=sys.stderr,
        )
        sys.exit(1)
    except PermissionError:
        print(
            f"Error: Permission denied executing: {command[0] if command else 'unknown'}",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(f"Error launching agent: {exc}", file=sys.stderr)
        sys.exit(1)

    if completed.returncode != 0:
        sys.exit(completed.returncode)


def _run_interactive(
    config_path: str,
    config_format: str,
    agent_type: str | None,
    codex_config: str | None = None,
) -> None:
    """Launch an agent interactively with the requested config."""

    cwd = Path.cwd()

    # --- Second-home mode (directory) --------------------------------------
    if config_format == "second_home":
        if not agent_type:
            print(
                "Error: agent_type is required when -c points to a directory "
                "(second-home mode). Specify one of: claude, codex.",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            adapter = create_adapter_from_config_file(config_path, agent_type)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        command = _build_interactive_command(adapter)
        _run_interactive_command(command, adapter.default_isolation, cwd)
        return

    # --- CLIppet composite -------------------------------------------------
    if config_format == "clippet":
        try:
            config = load_config(config_path)
            resolved_agent = _resolve_clippet_adapter_name(config, agent_type)
            runner = create_runner_from_config(config)
            adapter = runner.get_adapter(resolved_agent)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:
            print(f"Error loading config: {exc}", file=sys.stderr)
            sys.exit(1)

        try:
            command = _build_interactive_command(adapter)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        _run_interactive_command(command, adapter.default_isolation, cwd)
        return

    # --- Native config file ------------------------------------------------
    try:
        effective_type = _normalize_native_agent_type(
            config_path,
            config_format,
            agent_type,
        )
        adapter = create_adapter_from_config_file(
            config_path, effective_type, codex_config_path=codex_config,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    command = _build_interactive_command(adapter)
    _run_interactive_command(command, adapter.default_isolation, cwd)


def _handle_run(args: argparse.Namespace) -> None:
    """Execute an agent based on the resolved arguments."""

    config_path = _resolve_config_path(args)
    agent_type: str | None = getattr(args, "agent_type", None)
    codex_config: str | None = getattr(args, "codex_config", None)

    if codex_config:
        codex_config = str(Path(codex_config).resolve())

    # --- codex-config-only shortcut (no -c) --------------------------------
    if config_path is None:
        # Only reachable when --codex-config is given without -c
        prompt = _resolve_prompt(args)
        if prompt is None:
            if not sys.stdin.isatty():
                print(
                    "Error: A prompt is required when stdin is piped. "
                    "Use -p/--prompt or provide non-empty stdin.",
                    file=sys.stderr,
                )
                sys.exit(1)
            # Interactive mode with codex-config only
            _run_codex_config_only(codex_config, interactive=True)
            return
        _run_codex_config_only(codex_config, prompt=prompt)
        return

    # 1. Detect config format
    try:
        config_format = detect_config_type(config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # 2. Resolve prompt; without one we launch the underlying CLI interactively.
    prompt = _resolve_prompt(args)
    if prompt is None:
        if not sys.stdin.isatty():
            print(
                "Error: A prompt is required when stdin is piped. "
                "Use -p/--prompt or provide non-empty stdin.",
                file=sys.stderr,
            )
            sys.exit(1)
        _run_interactive(config_path, config_format, agent_type, codex_config)
        return

    # 3. Dispatch based on format
    if config_format == "clippet":
        _run_clippet_composite(config_path, agent_type, prompt)
    elif config_format == "second_home":
        _run_second_home(config_path, agent_type, prompt)
    else:
        _run_native_config(config_path, config_format, agent_type, prompt, codex_config)


def _run_second_home(
    config_path: str, agent_type: str | None, prompt: str
) -> None:
    """Handle a second-home directory config."""

    if not agent_type:
        print(
            "Error: agent_type is required when -c points to a directory "
            "(second-home mode). Specify one of: claude, codex.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        adapter = create_adapter_from_config_file(config_path, agent_type)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    request = AgentRequest(task_prompt=prompt)

    try:
        result = adapter.run(request)
    except Exception as exc:
        print(f"Error executing agent: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_result(result)


def _run_codex_config_only(
    codex_config: str,
    prompt: str | None = None,
    interactive: bool = False,
) -> None:
    """Handle the case where only ``--codex-config`` is provided (no ``-c``).

    The ``auth.json`` is read from ``~/.codex/auth.json``.
    """

    try:
        adapter = create_adapter_from_config_file(
            config_path=None,
            agent_type="codex",
            codex_config_path=codex_config,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if interactive:
        cwd = Path.cwd()
        command = _build_interactive_command(adapter)
        _run_interactive_command(command, adapter.default_isolation, cwd)
        return

    request = AgentRequest(task_prompt=prompt)

    try:
        result = adapter.run(request)
    except Exception as exc:
        print(f"Error executing agent: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_result(result)


def _run_clippet_composite(
    config_path: str, agent_type: str | None, prompt: str
) -> None:
    """Handle a CLIppet composite config file."""

    try:
        config = load_config(config_path)
        resolved_agent = _resolve_clippet_adapter_name(config, agent_type)
        runner = create_runner_from_config(config)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        sys.exit(1)

    request = AgentRequest(task_prompt=prompt)

    try:
        result = runner.execute(resolved_agent, request)
    except Exception as exc:
        print(f"Error executing agent: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_result(result)


def _run_native_config(
    config_path: str,
    config_format: str,
    agent_type: str | None,
    prompt: str,
    codex_config: str | None = None,
) -> None:
    """Handle a native Claude Code or Codex config file."""

    try:
        effective_type = _normalize_native_agent_type(
            config_path,
            config_format,
            agent_type,
        )
        adapter = create_adapter_from_config_file(
            config_path, effective_type, codex_config_path=codex_config,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    request = AgentRequest(task_prompt=prompt)

    try:
        result = adapter.run(request)
    except Exception as exc:
        print(f"Error executing agent: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_result(result)


def _print_result(result) -> None:
    """Print an :class:`AgentResult` to stdout/stderr."""

    if result.is_success:
        output = result.raw_output
        # Attempt to extract clean text from Claude JSON output
        try:
            parsed = parse_claude_json_output(output)
            if parsed["result_text"] and not parsed["is_error"]:
                output = parsed["result_text"]
        except Exception:
            pass
        print(output)
    else:
        error_msg = result.error_message or "Agent execution failed."
        print(f"Error: {error_msg}", file=sys.stderr)
        sys.exit(1)


def _handle_env(args: argparse.Namespace) -> None:
    """Dispatch env subcommands."""

    action: str | None = getattr(args, "env_action", None)

    if action == "list":
        envs = list_environments()
        if not envs:
            print("No environments registered.")
            return
        # Print formatted table
        name_width = max(len(n) for n in envs) + 2
        print(f"{'Name':<{name_width}} Config Path")
        print(f"{'-' * name_width} {'-' * 40}")
        for name, profile in sorted(envs.items()):
            config = profile.get("config_path", "")
            print(f"{name:<{name_width}} {config}")

    elif action == "add":
        name = args.name
        config = args.config_path
        try:
            add_environment(name, config)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"Environment '{name}' added.")

    elif action == "remove":
        name = args.name
        try:
            remove_environment(name)
        except KeyError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"Environment '{name}' removed.")

    else:
        print("Error: specify an env action: list, add, or remove.", file=sys.stderr)
        sys.exit(1)


# --- main --------------------------------------------------------------------


def main() -> None:
    """CLI entry point for CLIppet."""

    parser = _build_parser()
    args = parser.parse_args()

    command = getattr(args, "command", None)

    if command == "env":
        _handle_env(args)
        return

    # If ``command`` is ``"run"`` *or* the user passed top-level flags
    # (``-c`` / ``-e`` / ``--codex-config``), treat it as a run invocation.
    if (
        command == "run"
        or getattr(args, "config", None)
        or getattr(args, "env", None)
        or getattr(args, "codex_config", None)
    ):
        _handle_run(args)
        return

    # Nothing useful provided – show help.
    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
