"""CLI entry point for CLIppet."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
from clippet.models import AgentRequest
from clippet.parsers.extractors import parse_claude_json_output


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with subparsers."""

    parser = argparse.ArgumentParser(
        prog="clippet",
        description="CLIppet - A unified adapter framework for orchestrating CLI AI agents.",
    )

    subparsers = parser.add_subparsers(dest="command")

    # --- run (default) command ---
    run_parser = subparsers.add_parser("run", help="Run an agent with a config")
    _add_run_arguments(run_parser)

    # --- env subcommand ---
    env_parser = subparsers.add_parser("env", help="Manage environment profiles")
    env_sub = env_parser.add_subparsers(dest="env_action")

    env_sub.add_parser("list", help="List registered environments")

    env_add = env_sub.add_parser("add", help="Register an environment profile")
    env_add.add_argument("name", help="Name for the environment")
    env_add.add_argument("config_path", help="Path to the config file")

    env_rm = env_sub.add_parser("remove", help="Unregister an environment profile")
    env_rm.add_argument("name", help="Name of the environment to remove")

    # Also add run arguments to the top-level parser so that
    # ``clippet -c config.json claude -p "do stuff"`` works without ``run``.
    _add_run_arguments(parser)

    return parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach the shared run-mode arguments to *parser*."""

    parser.add_argument(
        "-c", "--config",
        help="Path to a config file (Claude Code, Codex, or CLIppet format)",
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
        choices=["claude", "codex", "qoder"],
        default=None,
        help="Agent type to run (claude, codex, or qoder)",
    )


# --- handlers ---------------------------------------------------------------


def _handle_run(args: argparse.Namespace) -> None:
    """Execute an agent based on the resolved arguments."""

    # 1. Resolve config path
    config_path: str | None = getattr(args, "config", None)
    env_name: str | None = getattr(args, "env", None)

    if config_path:
        config_path = str(Path(config_path).resolve())
    elif env_name:
        try:
            env_profile = get_environment(env_name)
        except KeyError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        config_path = env_profile["config_path"]
    else:
        print(
            "Error: Either -c/--config or -e/--env must be provided.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Resolve prompt
    prompt: str | None = getattr(args, "prompt", None)
    if not prompt:
        if not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
        if not prompt:
            print(
                "Error: A prompt is required. Use -p/--prompt or pipe via stdin.",
                file=sys.stderr,
            )
            sys.exit(1)

    agent_type: str | None = getattr(args, "agent_type", None)

    # 3. Detect config format
    try:
        config_format = detect_config_type(config_path)
    except (ValueError, FileNotFoundError, Exception) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # 4. Dispatch based on format
    if config_format == "clippet":
        _run_clippet_composite(config_path, agent_type, prompt)
    else:
        _run_native_config(config_path, agent_type, prompt)


def _run_clippet_composite(
    config_path: str, agent_type: str | None, prompt: str
) -> None:
    """Handle a CLIppet composite config file."""

    if not agent_type:
        print(
            "Error: agent_type is required for CLIppet composite configs. "
            "Specify one of: claude, codex, qoder.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        config = load_config(config_path)
        runner = create_runner_from_config(config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        sys.exit(1)

    request = AgentRequest(task_prompt=prompt)

    try:
        result = runner.execute(agent_type, request)
    except Exception as exc:
        print(f"Error executing agent: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_result(result)


def _run_native_config(
    config_path: str, agent_type: str | None, prompt: str
) -> None:
    """Handle a native Claude Code or Codex config file."""

    # Map CLI agent_type names to the values expected by the detector/factory.
    if agent_type == "claude":
        agent_type = "claude_code"
    elif agent_type == "qoder":
        print(
            "Error: 'qoder' agent type is only supported with CLIppet composite "
            "configs. Please use a CLIppet config file instead.",
            file=sys.stderr,
        )
        sys.exit(1)
    # "codex" and None pass through unchanged.

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
    # (``-c`` / ``-e``), treat it as a run invocation.
    if command == "run" or getattr(args, "config", None) or getattr(args, "env", None):
        _handle_run(args)
        return

    # Nothing useful provided – show help.
    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
