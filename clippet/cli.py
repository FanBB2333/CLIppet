"""CLI entry point for CLIppet.

Usage overview::

    clippet [OPTIONS] [AGENT] [AGENT ARGS...]   # launch an agent (interactive or -p)
    clippet env {list,create,import,status,sync,add,clone,path,remove} ...
    clippet auth {list,export} SOURCE ...
    clippet tui [WORKSPACE]                     # the terminal UI (also: bare clippet)

Everything after the agent name is forwarded verbatim to the agent's own CLI
(``clippet -e work codex resume --last``).  CLIppet's ``-p/--prompt`` is the
one exception: placed right after the agent name it still selects headless
mode.  Use ``--`` to forward even that (``clippet -e work claude -- -p hi``).
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clippet.adapters.base import (
    BaseSubprocessAdapter,
    build_credential_set,
    create_isolated_environment,
)
from clippet.agents import AGENT_KEYS, AGENTS, API_STYLES, agent_list, agent_spec
from clippet.auth.codex import (
    CodexCredential,
    CredentialFormatError,
    CredentialSelectionError,
    load_codex_credentials,
    select_credential,
)
from clippet.config.detector import (
    create_adapter_from_config_file,
    detect_config_type,
)
from clippet.config.environments import (
    ENV_TYPE_HOME,
    SEEDABLE_AGENTS,
    EnvironmentRegistryError,
    add_environment,
    clone_home_env,
    create_home_env_with_report,
    entry_type,
    env_vars,
    get_environment,
    import_codex_credential,
    list_environments,
    read_env_codex_credential,
    remove_environment,
    set_env_vars,
    shared_entries,
    sync_linked_codex_credential,
)
from clippet.config.project import resolve_project_launch
from clippet.config.registry import create_runner_from_config, load_config
from clippet.isolation import (
    AGENT_CONFIG_PATHS,
    CredentialSyncBack,
    ensure_shared_links,
)
from clippet.models import AgentRequest, IsolationConfig

_NATIVE_AGENTS: tuple[str, ...] = AGENT_KEYS

# CLIppet run options that take a value (used to find the agent name).
_VALUE_OPTIONS = frozenset(
    {"-c", "--config", "--codex-config", "-e", "--env", "-p", "--prompt", "--account"}
)
_PROMPT_OPTIONS = ("-p", "--prompt")


def _is_cockpit_source(value: str | None) -> bool:
    return bool(value) and (value == "cockpit" or str(value).startswith("cockpit:"))


def _note(message: str) -> None:
    print(f"clippet: {message}", file=sys.stderr)


def _fail(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(code)


# --- argument parsing ---------------------------------------------------------


def split_agent_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split run-mode *argv* into ``(clippet_argv, agent_args)``.

    * Options before the first positional token belong to CLIppet.
    * The first positional token is the agent (or composite adapter) name;
      everything after it is forwarded to the agent CLI verbatim, except a
      ``-p/--prompt VALUE`` that directly follows the agent name.
    * ``--`` ends CLIppet's options: everything after it is forwarded.
    """

    clippet_argv: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            return clippet_argv, argv[index + 1:]
        if token in _VALUE_OPTIONS:
            clippet_argv.extend(argv[index:index + 2])
            index += 2
            continue
        if token.startswith("-"):
            clippet_argv.append(token)
            index += 1
            continue

        # First positional: the agent name.
        clippet_argv.append(token)
        rest = argv[index + 1:]
        agent_args: list[str] = []
        position = 0
        while position < len(rest):
            item = rest[position]
            if item == "--":
                agent_args.extend(rest[position + 1:])
                break
            if not agent_args and item in _PROMPT_OPTIONS and position + 1 < len(rest):
                clippet_argv.extend([item, rest[position + 1]])
                position += 2
                continue
            if not agent_args and item.startswith("--prompt="):
                clippet_argv.append(item)
                position += 1
                continue
            agent_args.extend(rest[position:])
            break
        return clippet_argv, agent_args

    return clippet_argv, []


class _ClippetArgumentParser:
    """Small dispatcher that supports both subcommands and direct agent launch."""

    def __init__(self) -> None:
        description = (
            "CLIppet - A unified adapter framework for orchestrating CLI AI agents."
        )
        epilog = (
            "Everything after AGENT is passed to the agent CLI unchanged, e.g. "
            "'clippet -e work codex resume --last'. Use '--' to pass even -p."
        )

        self._root_parser = argparse.ArgumentParser(
            prog="clippet",
            description=description,
            epilog=epilog,
        )
        subparsers = self._root_parser.add_subparsers(dest="command")
        _add_run_arguments(subparsers.add_parser("run", help="Run an agent with a config"))
        _add_env_subcommands(subparsers.add_parser("env", help="Manage environments"))
        _add_auth_subcommands(subparsers.add_parser("auth", help="Inspect/convert credentials"))
        _add_tui_arguments(subparsers.add_parser("tui", help="Open the terminal UI"))

        self._run_parser = argparse.ArgumentParser(
            prog="clippet",
            description=description,
            epilog=epilog,
        )
        _add_run_arguments(self._run_parser)

        self._env_parser = argparse.ArgumentParser(
            prog="clippet env",
            description="Manage environment profiles",
        )
        _add_env_subcommands(self._env_parser)

        self._auth_parser = argparse.ArgumentParser(
            prog="clippet auth",
            description="Inspect and convert agent credentials (Codex for now)",
        )
        _add_auth_subcommands(self._auth_parser)

        self._tui_parser = argparse.ArgumentParser(
            prog="clippet tui",
            description="Open the terminal UI: envs, accounts, runs, project and checks.",
        )
        _add_tui_arguments(self._tui_parser)

    def parse_args(self, args: list[str] | None = None) -> argparse.Namespace:
        """Parse CLI arguments with support for top-level agent invocations."""

        argv = list(sys.argv[1:] if args is None else args)

        if argv and argv[0] == "env":
            namespace = self._env_parser.parse_args(argv[1:])
            namespace.command = "env"
            return namespace

        if argv and argv[0] == "auth":
            namespace = self._auth_parser.parse_args(argv[1:])
            namespace.command = "auth"
            return namespace

        if argv and argv[0] == "tui":
            namespace = self._tui_parser.parse_args(argv[1:])
            namespace.command = "tui"
            return namespace

        command: str | None = None
        if argv and argv[0] == "run":
            command = "run"
            argv = argv[1:]

        clippet_argv, agent_args = split_agent_args(argv)
        namespace = self._run_parser.parse_args(clippet_argv)
        namespace.agent_args = agent_args
        namespace.command = command
        return namespace

    def print_help(self) -> None:
        """Print the top-level help message."""

        self._root_parser.print_help()


def _build_parser() -> _ClippetArgumentParser:
    """Build the top-level parser dispatcher."""
    return _ClippetArgumentParser()


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach the shared run-mode arguments to *parser*."""

    from clippet import __version__

    parser.add_argument(
        "-c", "--config",
        help=(
            "Config or credential to launch with: Claude settings JSON, Codex "
            "credential (auth.json, Cockpit Tools / sub2api / CLIProxyAPI export, "
            "token JSON), Gemini .env, CLIppet composite config, a directory to "
            "use as HOME, or 'cockpit' for the Cockpit Tools account store"
        ),
    )
    parser.add_argument(
        "--codex-config",
        dest="codex_config",
        help=(
            "Path to a Codex config.toml file (model/provider settings).  "
            "When used with -c pointing to a Codex credential, both files are "
            "injected into the sandbox.  Can also be used alone — the "
            "credential is then read from ~/.codex/auth.json."
        ),
    )
    parser.add_argument(
        "--account",
        help=(
            "Account to use when the credential source holds several "
            "(1-based index, e-mail, e-mail local part, label or account id)"
        ),
    )
    parser.add_argument(
        "-e", "--env",
        help="Named environment (HOME container or file alias)",
    )
    parser.add_argument(
        "-p", "--prompt",
        help="Run once non-interactively with this prompt",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"clippet {__version__}",
    )
    parser.add_argument(
        "agent_type",
        nargs="?",
        default=None,
        help=(
            f"Native agent type ({agent_list('/')}) or a composite "
            "config adapter name. Optional for native config files and "
            "single-adapter composite configs."
        ),
    )


def _add_env_subcommands(parser: argparse.ArgumentParser) -> None:
    """Register the ``env`` subcommands on *parser*.

    Modern (default) mode is ``create`` — a persistent HOME container under
    ``~/.clippet/envs/<name>/`` that the agent uses directly as ``$HOME`` at
    launch (second-home mode). Legacy ``add`` is kept for file-path aliases.
    """

    sub = parser.add_subparsers(dest="env_action")

    sub.add_parser("list", help="List registered environments")

    create = sub.add_parser(
        "create",
        help="Create a persistent HOME-container env (default mode)",
    )
    create.add_argument("name", help="Name for the new env")
    create.add_argument(
        "--from-current",
        action="store_true",
        help=(
            "Seed the new env with agent configuration from the current HOME "
            "(settings, config.toml, MCP config, skills, prompts). Sessions, "
            "logs and caches are never copied."
        ),
    )
    create.add_argument(
        "--with-auth",
        action="store_true",
        help=(
            "With --from-current, also copy login credentials (e.g. "
            "~/.codex/auth.json). The copy shares OAuth refresh tokens with "
            "the current HOME; logging in inside the env is safer."
        ),
    )
    create.add_argument(
        "--agents",
        default=None,
        help=(
            "Comma-separated list of agents to seed when --from-current is set "
            f"({', '.join(SEEDABLE_AGENTS)}). Default: seed all."
        ),
    )
    create.add_argument(
        "--description", default="", help="Optional human-readable description"
    )
    create.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace any existing env with the same name",
    )
    _add_share_arguments(create)

    imp = sub.add_parser(
        "import",
        help="Store a Codex account (any supported format) in a HOME env",
    )
    imp.add_argument("name", help="Env name (created if it does not exist)")
    imp.add_argument(
        "source",
        help=(
            "Credential source: Codex auth.json, Cockpit Tools export/backup, "
            "sub2api export, CLIProxyAPI token file, token JSON, or 'cockpit' "
            "for the live Cockpit Tools account store"
        ),
    )
    imp.add_argument("--account", help="Account selector when the source holds several")
    link = imp.add_mutually_exclusive_group()
    link.add_argument(
        "--link",
        dest="link",
        action="store_true",
        default=None,
        help="Pull the newest login from the source before every launch "
        "(default for 'cockpit')",
    )
    link.add_argument(
        "--no-link",
        dest="link",
        action="store_false",
        help="Copy the credential once",
    )
    imp.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace a different Codex account already stored in the env",
    )
    imp.add_argument(
        "--bare",
        action="store_true",
        help="When creating the env, do not copy ~/.codex config (config.toml, skills, ...)",
    )
    imp.add_argument("--description", default="", help="Description for a new env")

    status = sub.add_parser("status", help="Show which identity each env carries")
    status.add_argument("name", nargs="?", help="Only show this env")
    status.add_argument("--json", action="store_true", help="Machine-readable output")

    sync = sub.add_parser("sync", help="Pull the newest login for a linked env now")
    sync.add_argument("name", help="Env name")

    add = sub.add_parser(
        "add",
        help="Register a file-path alias (legacy mode)",
    )
    add.add_argument("name", help="Name for the environment")
    add.add_argument("config_path", help="Path to the config file")

    clone = sub.add_parser("clone", help="Clone an existing HOME-container env")
    clone.add_argument("src", help="Source env name")
    clone.add_argument("dst", help="New env name")
    clone.add_argument(
        "--description", default="", help="Optional human-readable description"
    )
    clone.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the destination env if it already exists",
    )
    clone.add_argument(
        "--with-auth",
        action="store_true",
        help="Also copy login credentials (shares OAuth refresh tokens)",
    )

    path_p = sub.add_parser(
        "path",
        help="Print the on-disk path of an env (HOME dir or config file)",
    )
    path_p.add_argument("name", help="Env name")

    endpoint = sub.add_parser(
        "endpoint",
        help="Point an agent in an env at a third-party API (a relay, a compatible provider, a local server)",
        description=(
            "Point AGENT in env NAME at the API at URL, the agent's own way (variables, or its "
            "config.toml/models.json for Codex and pi). Without AGENT, show the env's endpoints."
        ),
    )
    endpoint.add_argument("name", help="Env name")
    endpoint.add_argument("agent", nargs="?", help=f"One of: {agent_list()}")
    endpoint.add_argument("url", nargs="?", help="The endpoint's base URL")
    endpoint.add_argument(
        "--key-env",
        metavar="VAR",
        help="The variable of your environment that holds the key (read at each launch, never stored)",
    )
    endpoint.add_argument("--model", help="The model to use there")
    endpoint.add_argument("--api", choices=API_STYLES, help="The API style the endpoint speaks (default: the agent's)")
    endpoint.add_argument("--clear", action="store_true", help="Stop using a third-party endpoint for AGENT")

    env_vars_p = sub.add_parser(
        "vars",
        help="Show or set the variables an env sets for an agent's runs",
        description=(
            "Variables set for AGENT's runs in env NAME, after CLIppet drops inherited ones that would "
            "change its identity. A value may name a variable of your environment as ${NAME} (quote it), "
            "so keys can stay in your shell profile."
        ),
    )
    env_vars_p.add_argument("name", help="Env name")
    env_vars_p.add_argument("agent", nargs="?", help=f"One of: {agent_list()}")
    env_vars_p.add_argument("assign", nargs="*", metavar="VAR=VALUE", help="Variables to set")
    env_vars_p.add_argument("--unset", action="append", default=[], metavar="VAR", help="A variable to remove")

    rm = sub.add_parser("remove", help="Unregister an env")
    rm.add_argument("name", help="Name of the environment to remove")
    rm.add_argument(
        "--purge",
        action="store_true",
        help=(
            "For HOME-container envs, also delete the directory on disk. "
            "Without this flag, only the registry entry is removed."
        ),
    )


def _add_share_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--share",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "Dotfile to link from the real HOME into the env (repeatable). "
            "Default: .gitconfig and .config/git, so git keeps your identity."
        ),
    )
    group.add_argument(
        "--no-share",
        action="store_true",
        help="Do not link any dotfiles from the real HOME",
    )


def _add_tui_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "workspace",
        nargs="?",
        default=None,
        help="Folder prompts run in and .clippet.json is read from (default: the current one)",
    )


def _handle_tui(workspace: str | None) -> None:
    try:
        from clippet.tui import run_tui
    except ImportError as exc:  # pragma: no cover - the package itself always imports
        _fail(str(exc))
    target = Path(workspace).expanduser() if workspace else None
    if target is not None and not target.is_dir():
        _fail(f"workspace '{workspace}' is not a folder")
    try:
        run_tui(target)
    except ImportError as exc:
        _fail(str(exc))


def _add_auth_subcommands(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="auth_action")

    lst = sub.add_parser("list", help="List the Codex accounts in a credential source")
    lst.add_argument("source", help="File, directory, or 'cockpit'")
    lst.add_argument("--json", action="store_true", help="Machine-readable output (no secrets)")

    exp = sub.add_parser("export", help="Write one account as a native Codex auth.json")
    exp.add_argument("source", help="File, directory, or 'cockpit'")
    exp.add_argument("--account", help="Account selector when the source holds several")
    exp.add_argument("-o", "--output", required=True, help="Output path, or '-' for stdout")
    exp.add_argument("--force", action="store_true", help="Overwrite an existing file")


# --- formatting helpers -------------------------------------------------------


def _fmt_moment(moment: datetime | None) -> str:
    if moment is None:
        return "-"
    local = moment.astimezone()
    delta = moment - datetime.now(timezone.utc)
    days = delta.total_seconds() / 86400
    if days >= 1:
        rel = f"in {int(days)}d"
    elif days >= 0:
        rel = f"in {int(delta.total_seconds() // 3600)}h"
    elif days > -1:
        rel = f"{int(-delta.total_seconds() // 3600)}h ago"
    else:
        rel = f"{int(-days)}d ago"
    return f"{local:%Y-%m-%d %H:%M} ({rel})"


def describe_codex_credential(credential: CodexCredential | None) -> str:
    """One-line, secret-free description of a Codex credential."""

    if credential is None:
        return "not logged in"
    if credential.auth_mode == "apikey":
        base = f" @ {credential.base_url}" if credential.base_url else ""
        return f"API key{base}"
    who = credential.display_email or credential.display_account_id or "ChatGPT login"
    plan = f" [{credential.display_plan}]" if credential.display_plan else ""
    expired = " (access token expired)" if credential.is_expired() else ""
    return f"{who}{plan}{expired}"


# --- run handlers -------------------------------------------------------------


def _prepare_home_env(name: str, profile: dict, agent_type: str | None) -> None:
    """Pre-launch upkeep for a registered HOME env (links, linked logins)."""

    home_dir = Path(profile.get("home_dir") or "")
    spec = agent_spec(agent_type)
    try:
        ensure_shared_links(home_dir, [*shared_entries(profile), *(spec.runtime if spec else ())])
    except OSError as exc:
        _note(f"could not link shared dotfiles into env '{name}': {exc}")

    if agent_type not in (None, "codex"):
        return
    from clippet.desktop import is_open

    if isinstance(profile.get("codex_source"), dict) and profile["codex_source"].get("linked") and is_open(home_dir):
        _note(f"env '{name}': its Codex app is open, so its login is not pulled now")
        return
    result = sync_linked_codex_credential(name, profile)
    if result.action == "updated":
        _note(f"env '{name}': {result.message}")
    elif result.action in ("env-newer", "error"):
        _note(f"env '{name}': warning: {result.message}")


def _resolve_config_path(args: argparse.Namespace) -> str | None:
    """Resolve the requested config path from ``-c`` or ``-e``.

    Returns *None* when only ``--codex-config`` is provided (without ``-c``),
    or when project-level config resolution should be attempted.
    """

    config_path: str | None = getattr(args, "config", None)
    env_name: str | None = getattr(args, "env", None)

    if config_path:
        if _is_cockpit_source(config_path):
            return config_path
        return str(Path(config_path).expanduser().resolve())

    if env_name:
        try:
            env_profile = get_environment(env_name)
        except KeyError as exc:
            _fail(str(exc.args[0] if exc.args else exc))
        except EnvironmentRegistryError as exc:
            _fail(str(exc))
        if entry_type(env_profile) == ENV_TYPE_HOME:
            home_dir = env_profile.get("home_dir")
            if not home_dir:
                _fail(
                    f"env '{env_name}' is a HOME-container entry but has no "
                    "'home_dir' field. The registry may be corrupted."
                )
            if not Path(home_dir).is_dir():
                _fail(
                    f"the HOME directory of env '{env_name}' is missing: {home_dir}. "
                    f"Recreate it with 'clippet env create {env_name} --overwrite'."
                )
            _prepare_home_env(env_name, env_profile, getattr(args, "agent_type", None))
            return home_dir
        return env_profile["config_path"]

    return None


def _apply_project_level_config(args: argparse.Namespace) -> bool:
    """Apply project-level .clippet.json config to args (strict variant).

    Returns True if project-level config was applied; exits when no
    ``.clippet.json`` is found or the agent is not configured in it.
    """

    has_explicit_config = any([
        getattr(args, "config", None),
        getattr(args, "env", None),
        getattr(args, "codex_config", None),
    ])
    agent_type = getattr(args, "agent_type", None)

    if has_explicit_config or agent_type not in _NATIVE_AGENTS:
        return False

    try:
        launch = resolve_project_launch(Path.cwd(), agent_type)
    except FileNotFoundError:
        _fail(
            "no explicit config was provided and no project-level .clippet.json "
            "was found from the current directory to the Git root."
        )
    except KeyError as exc:
        _fail(str(exc))
    except ValueError as exc:
        _fail(str(exc))

    _apply_launch(args, launch)
    return True


def _apply_launch(args: argparse.Namespace, launch) -> None:
    if launch.env is not None:
        args.env = launch.env
    if launch.config_path is not None:
        args.config = launch.config_path
    if launch.codex_config_path is not None:
        args.codex_config = launch.codex_config_path
    if launch.account is not None and not getattr(args, "account", None):
        args.account = launch.account


def _resolve_prompt(args: argparse.Namespace, passthrough: list[str] | None = None) -> str | None:
    """Resolve a prompt from ``-p`` or stdin, if present.

    Stdin is left alone when arguments are forwarded to the agent CLI, so
    commands like ``codex login --with-api-key < key.txt`` keep working.
    """

    prompt: str | None = getattr(args, "prompt", None)
    if prompt:
        return prompt

    if passthrough:
        return None

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

    if detected_type not in {"claude_code", "codex", "gemini"}:
        raise ValueError(f"Unsupported native config type: {detected_type}")

    if agent_type is not None and agent_type not in ("claude", "claude_code", "codex", "gemini"):
        raise ValueError(
            f"'{agent_type}' is only supported with CLIppet composite configs or "
            "HOME envs. Please use a CLIppet config file or -e <env> instead."
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


def _build_interactive_command(
    adapter: BaseSubprocessAdapter,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build the interactive command for a configured adapter."""

    builder = getattr(adapter, "build_interactive_command", None)
    if callable(builder) and getattr(adapter, "isolation_agent", None) in AGENTS:
        return builder(list(extra_args or []))

    raise ValueError(
        f"Interactive launch is only supported for the agent CLIs ({agent_list()}), not API adapters."
    )


def _ignore_sigint():
    """Let Ctrl-C reach only the agent; return the previous handler."""

    try:
        return signal.signal(signal.SIGINT, lambda _signum, _frame: None)
    except ValueError:  # not in the main thread
        return None


def _restore_sigint(previous) -> None:
    if previous is None:
        return
    try:
        signal.signal(signal.SIGINT, previous)
    except (ValueError, TypeError):
        pass


def _run_interactive_command(
    command: list[str],
    isolation: IsolationConfig | None,
    cwd: Path,
    agent: str | None = None,
) -> None:
    """Launch an interactive agent command with optional isolation.

    The child's exit status is propagated (``128 + N`` when it died from
    signal ``N``).  Credential files the agent rewrote (refreshed OAuth tokens)
    are synced back to their owner before a temporary HOME is removed.
    """

    binary = command[0] if command else "unknown"

    def _spawn(env: dict[str, str] | None) -> subprocess.CompletedProcess:
        try:
            if env is None:
                return subprocess.run(command, cwd=cwd, check=False)
            return subprocess.run(command, cwd=cwd, env=env, check=False)
        except FileNotFoundError:
            _fail(f"CLI binary not found: {binary}")
        except PermissionError:
            _fail(f"Permission denied executing: {binary}")

    previous = _ignore_sigint()
    try:
        if isolation is None:
            completed = _spawn(None)
        else:
            try:
                isolated = create_isolated_environment(isolation, agent)
                isolated.__enter__()
            except Exception as exc:  # noqa: BLE001
                _fail(f"could not prepare the isolated environment: {exc}")
            try:
                try:
                    build_credential_set(isolation).inject(isolated)
                    sync = CredentialSyncBack(isolation.sync_back)
                    sync.snapshot(isolated)
                except (OSError, ValueError) as exc:
                    _fail(f"could not inject credentials: {exc}")
                completed = _spawn(isolated.env)
                try:
                    for destination in sync.finalize(isolated):
                        _note(f"the agent refreshed its login; saved to {destination}")
                except OSError as exc:
                    _note(f"warning: could not save refreshed credentials: {exc}")
            finally:
                isolated.cleanup()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(f"launching agent failed: {exc}")
    finally:
        _restore_sigint(previous)

    returncode = completed.returncode
    if isinstance(returncode, int) and returncode < 0:
        returncode = 128 + (-returncode)
    if returncode:
        sys.exit(returncode)


def _launch_adapter(adapter: BaseSubprocessAdapter, passthrough: list[str] | None) -> None:
    try:
        command = _build_interactive_command(adapter, passthrough)
    except ValueError as exc:
        _fail(str(exc))
    _run_interactive_command(
        command,
        adapter.default_isolation,
        Path.cwd(),
        agent=getattr(adapter, "isolation_agent", None),
    )


def _run_interactive(
    config_path: str,
    config_format: str,
    agent_type: str | None,
    codex_config: str | None = None,
    account: str | None = None,
    passthrough: list[str] | None = None,
) -> None:
    """Launch an agent interactively with the requested config."""

    # --- Second-home mode (directory) --------------------------------------
    if config_format == "second_home":
        if not agent_type:
            _fail(
                "agent_type is required when -c/-e points to a HOME directory "
                f"(second-home mode). Specify one of: {agent_list()}."
            )
        try:
            adapter = create_adapter_from_config_file(config_path, agent_type)
        except ValueError as exc:
            _fail(str(exc))
        _launch_adapter(adapter, passthrough)
        return

    # --- CLIppet composite -------------------------------------------------
    if config_format == "clippet":
        try:
            config = load_config(config_path)
            resolved_agent = _resolve_clippet_adapter_name(config, agent_type)
            runner = create_runner_from_config(config)
            adapter = runner.get_adapter(resolved_agent)
        except ValueError as exc:
            _fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            _fail(f"loading config failed: {exc}")
        _launch_adapter(adapter, passthrough)
        return

    # --- Native config file ------------------------------------------------
    try:
        effective_type = _normalize_native_agent_type(
            config_path,
            config_format,
            agent_type,
        )
        adapter = create_adapter_from_config_file(
            config_path, effective_type, codex_config_path=codex_config, account=account,
        )
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))

    _launch_adapter(adapter, passthrough)


def _detect_format(config_path: str) -> str:
    if _is_cockpit_source(config_path):
        return "codex"
    return detect_config_type(config_path)


def _handle_run(args: argparse.Namespace) -> None:
    """Execute an agent based on the resolved arguments."""

    agent_type: str | None = getattr(args, "agent_type", None)
    codex_config: str | None = getattr(args, "codex_config", None)
    account: str | None = getattr(args, "account", None)
    passthrough: list[str] = list(getattr(args, "agent_args", None) or [])

    if codex_config:
        codex_config = str(Path(codex_config).expanduser().resolve())

    if agent_type == "codex" and passthrough[:1] == ["app"] and (getattr(args, "env", None) or getattr(args, "config", None)):
        _open_codex_app(args, passthrough[1:])
        return

    config_path = _resolve_config_path(args)

    # --- codex-config-only shortcut (no -c) --------------------------------
    if config_path is None and codex_config is not None:
        prompt = _resolve_prompt(args, passthrough)
        if prompt is None:
            if not passthrough and not sys.stdin.isatty():
                _fail(
                    "A prompt is required when stdin is piped. "
                    "Use -p/--prompt or provide non-empty stdin."
                )
            _run_codex_config_only(
                codex_config, interactive=True, account=account, passthrough=passthrough,
            )
            return
        _run_codex_config_only(
            codex_config, prompt=prompt, account=account, passthrough=passthrough,
        )
        return

    if config_path is None:
        _fail("Either -c/--config, -e/--env, or --codex-config must be provided.")

    # 1. Detect config format
    try:
        config_format = _detect_format(config_path)
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim
        _fail(str(exc))

    if config_format == "second_home" and codex_config:
        _fail(
            "--codex-config cannot be combined with a HOME directory; edit "
            f"{Path(config_path) / '.codex' / 'config.toml'} or pass Codex's own "
            "'-c key=value' / '--profile' options after the agent name."
        )

    # 2. Resolve prompt; without one we launch the underlying CLI interactively.
    prompt = _resolve_prompt(args, passthrough)
    if prompt is None:
        if not passthrough and not sys.stdin.isatty():
            _fail(
                "A prompt is required when stdin is piped. "
                "Use -p/--prompt or provide non-empty stdin."
            )
        _run_interactive(
            config_path, config_format, agent_type, codex_config, account, passthrough,
        )
        return

    # 3. Dispatch based on format
    if config_format == "clippet":
        _run_clippet_composite(config_path, agent_type, prompt, passthrough)
    elif config_format == "second_home":
        _run_second_home(config_path, agent_type, prompt, passthrough)
    else:
        _run_native_config(
            config_path, config_format, agent_type, prompt, codex_config, account, passthrough,
        )


def _open_codex_app(args: argparse.Namespace, app_args: list[str]) -> None:
    """``clippet -e NAME codex app [PATH...]``: the Codex desktop app as env NAME, next to the others."""

    from clippet.desktop import DesktopError, open_app

    name: str | None = getattr(args, "env", None)
    profile: dict | None = None
    if name:
        try:
            profile = get_environment(name)
        except KeyError as exc:
            _fail(str(exc.args[0] if exc.args else exc))
        if entry_type(profile) != ENV_TYPE_HOME or not profile.get("home_dir"):
            _fail(f"env '{name}' is a config-file alias; the Codex app opens as a HOME env")
        home = Path(profile["home_dir"])
    else:
        home = Path(args.config).expanduser()
        if not home.is_dir():
            _fail(
                "the Codex app opens as a HOME env, not from a credential file: import it into one "
                "('clippet env import NAME FILE'), then run 'clippet -e NAME codex app'"
            )
    options = [arg for arg in app_args if arg.startswith("-")]
    if options:
        _fail(f"clippet opens the app itself, so codex app's {' '.join(options)} does not apply here")

    def before_launch() -> None:
        if name and profile is not None:
            _prepare_home_env(name, profile, "codex")  # shared dotfiles, a linked env's newest login

    try:
        opened = open_app(home, app_args or None, before_launch=before_launch)
    except (DesktopError, ValueError, OSError) as exc:
        _fail(str(exc))
    who = f"env '{name}'" if name else str(home)
    if opened.running:
        _note(f"the Codex app is already open as {who} (pid {opened.running[0]}): brought it to the front")
    else:
        _note(f"opened {opened.app.name} as {who} (CODEX_HOME={home / '.codex'})")
    for note in opened.notes:
        _note(note)


def _execute_headless(adapter, prompt: str, passthrough: list[str] | None) -> None:
    request = AgentRequest(task_prompt=prompt, extra_cli_args=list(passthrough or []))
    try:
        result = adapter.run(request)
    except Exception as exc:  # noqa: BLE001
        _fail(f"executing agent failed: {exc}")
    _print_result(result)


def _run_second_home(
    config_path: str,
    agent_type: str | None,
    prompt: str,
    passthrough: list[str] | None = None,
) -> None:
    """Handle a second-home directory config."""

    if not agent_type:
        _fail(
            "agent_type is required when -c/-e points to a HOME directory "
            f"(second-home mode). Specify one of: {agent_list()}."
        )

    try:
        adapter = create_adapter_from_config_file(config_path, agent_type)
    except ValueError as exc:
        _fail(str(exc))

    _execute_headless(adapter, prompt, passthrough)


def _run_codex_config_only(
    codex_config: str,
    prompt: str | None = None,
    interactive: bool = False,
    account: str | None = None,
    passthrough: list[str] | None = None,
) -> None:
    """Handle the case where only ``--codex-config`` is provided (no ``-c``).

    The credential is read from ``~/.codex/auth.json``.
    """

    try:
        adapter = create_adapter_from_config_file(
            config_path=None,
            agent_type="codex",
            codex_config_path=codex_config,
            account=account,
        )
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))

    if interactive:
        _launch_adapter(adapter, passthrough)
        return

    _execute_headless(adapter, prompt or "", passthrough)


def _run_clippet_composite(
    config_path: str,
    agent_type: str | None,
    prompt: str,
    passthrough: list[str] | None = None,
) -> None:
    """Handle a CLIppet composite config file."""

    try:
        config = load_config(config_path)
        resolved_agent = _resolve_clippet_adapter_name(config, agent_type)
        runner = create_runner_from_config(config)
    except ValueError as exc:
        _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        _fail(f"loading config failed: {exc}")

    request = AgentRequest(task_prompt=prompt, extra_cli_args=list(passthrough or []))

    try:
        result = runner.execute(resolved_agent, request)
    except Exception as exc:  # noqa: BLE001
        _fail(f"executing agent failed: {exc}")

    _print_result(result)


def _run_native_config(
    config_path: str,
    config_format: str,
    agent_type: str | None,
    prompt: str,
    codex_config: str | None = None,
    account: str | None = None,
    passthrough: list[str] | None = None,
) -> None:
    """Handle a native Claude Code, Codex or Gemini config/credential."""

    try:
        effective_type = _normalize_native_agent_type(
            config_path,
            config_format,
            agent_type,
        )
        adapter = create_adapter_from_config_file(
            config_path, effective_type, codex_config_path=codex_config, account=account,
        )
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))

    _execute_headless(adapter, prompt, passthrough)


def _print_result(result) -> None:
    """Print an :class:`AgentResult` to stdout/stderr."""

    if result.is_success:
        text = getattr(result, "output_text", None)
        print(text if isinstance(text, str) else result.raw_output)
    else:
        error_msg = result.error_message or "Agent execution failed."
        print(f"Error: {error_msg}", file=sys.stderr)
        sys.exit(1)


# --- env subcommands ----------------------------------------------------------


def _entry_path(profile: dict) -> str:
    """Return the on-disk path associated with an env entry."""

    if entry_type(profile) == ENV_TYPE_HOME:
        return profile.get("home_dir", "")
    return profile.get("config_path", "")


def env_identity(profile: dict) -> dict[str, Any]:
    """Secret-free summary of the identities stored in an env entry."""

    info: dict[str, Any] = {"type": entry_type(profile), "path": _entry_path(profile)}
    if entry_type(profile) != ENV_TYPE_HOME:
        return info

    home = Path(profile.get("home_dir") or "")
    info["exists"] = home.is_dir()
    credential = read_env_codex_credential(home) if home.is_dir() else None
    info["codex"] = None if credential is None else credential.summary()
    info["codex_text"] = describe_codex_credential(credential)
    source = profile.get("codex_source")
    if isinstance(source, dict):
        info["codex_source"] = {
            key: source.get(key)
            for key in ("source", "format", "email", "linked", "imported_at")
        }
    agents = {}
    for agent, paths in AGENT_CONFIG_PATHS.items():
        agents[agent] = any((home / rel).exists() for rel in paths)
    info["agents"] = agents
    info["share"] = list(shared_entries(profile))
    info["description"] = profile.get("description", "")
    return info


def _print_env_list(envs: dict[str, dict]) -> None:
    if not envs:
        print("No environments registered.")
        return
    name_width = max(len("Name"), *(len(n) for n in envs)) + 2
    type_width = 6
    who_width = 34
    print(f"{'Name':<{name_width}} {'Type':<{type_width}} {'Codex':<{who_width}} Path")
    print(f"{'-' * name_width} {'-' * type_width} {'-' * who_width} {'-' * 40}")
    for name, profile in sorted(envs.items()):
        etype = entry_type(profile)
        who = "-"
        if etype == ENV_TYPE_HOME:
            home = Path(profile.get("home_dir") or "")
            who = describe_codex_credential(read_env_codex_credential(home)) if home.is_dir() else "(missing dir)"
            if who == "not logged in":
                who = "-"
        print(f"{name:<{name_width}} {etype:<{type_width}} {who[:who_width]:<{who_width}} {_entry_path(profile)}")


def _print_env_status(name: str, info: dict[str, Any]) -> None:
    print(f"{name}  ({info['type']})  {info['path']}")
    if info["type"] != ENV_TYPE_HOME:
        return
    if not info.get("exists"):
        print("  ! HOME directory is missing")
        return
    if info.get("description"):
        print(f"  description : {info['description']}")
    codex = info.get("codex")
    print(f"  codex       : {info['codex_text']}")
    if codex:
        expires = codex.get("access_expires_at")
        refreshed = codex.get("last_refresh")
        print(f"                access token expires {_fmt_moment(_parse_iso(expires))}")
        print(f"                last refresh {_fmt_moment(_parse_iso(refreshed))}")
    source = info.get("codex_source")
    if source:
        mode = "linked" if source.get("linked") else "copied"
        print(f"  codex source: {source.get('source')} [{source.get('format')}] ({mode})")
    present = [agent for agent, ok in info.get("agents", {}).items() if ok]
    print(f"  agent dirs  : {', '.join(present) if present else '(none yet)'}")
    if info.get("share"):
        print(f"  shared      : {', '.join(info['share'])}")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _handle_env(args: argparse.Namespace) -> None:
    """Dispatch env subcommands."""

    try:
        _dispatch_env(args)
    except EnvironmentRegistryError as exc:
        _fail(str(exc))


def _dispatch_env(args: argparse.Namespace) -> None:
    action: str | None = getattr(args, "env_action", None)

    if action == "list":
        _print_env_list(list_environments())
        return

    if action == "create":
        name = args.name
        agents = (
            [a.strip() for a in args.agents.split(",") if a.strip()]
            if args.agents
            else None
        )
        share = [] if getattr(args, "no_share", False) else getattr(args, "share", None)
        try:
            home_dir, report = create_home_env_with_report(
                name,
                from_current=args.from_current,
                agents=agents,
                with_auth=getattr(args, "with_auth", False),
                description=args.description,
                overwrite=args.overwrite,
                share=share,
            )
        except (FileExistsError, ValueError, OSError) as exc:
            _fail(str(exc))
        seeded = " (seeded from current $HOME)" if args.from_current else ""
        print(f"Environment '{name}' created at {home_dir}{seeded}.")
        if report.copied:
            print(f"  copied : {', '.join(report.copied)}")
        if report.linked:
            print(f"  linked : {', '.join(report.linked)} (from your real HOME)")
        if report.skipped_credentials:
            print(
                "  skipped credentials: "
                + ", ".join(report.skipped_credentials)
                + "\n  Log in inside the env (e.g. 'clippet -e "
                + name
                + " codex login'), import an account ('clippet env import "
                + name
                + " <source>'), or re-create with --with-auth to copy them."
            )
        return

    if action == "import":
        _env_import(args)
        return

    if action == "status":
        envs = list_environments()
        if args.name:
            if args.name not in envs:
                _fail(f"Environment '{args.name}' not found.")
            envs = {args.name: envs[args.name]}
        infos = {name: env_identity(profile) for name, profile in sorted(envs.items())}
        if args.json:
            print(json.dumps(infos, indent=2, ensure_ascii=False))
            return
        if not infos:
            print("No environments registered.")
        for name, info in infos.items():
            _print_env_status(name, info)
        return

    if action == "sync":
        try:
            profile = get_environment(args.name)
        except KeyError as exc:
            _fail(str(exc.args[0] if exc.args else exc))
        result = sync_linked_codex_credential(args.name, profile)
        if result.action == "skipped":
            print(f"Env '{args.name}' is not linked to a credential source.")
        elif result.action == "unchanged":
            print(f"Env '{args.name}' already has the newest login.")
        elif result.action == "updated":
            print(f"Env '{args.name}': {result.message}.")
        else:
            _fail(f"env '{args.name}': {result.message}")
        return

    if action == "add":
        name = args.name
        config = args.config_path
        try:
            add_environment(name, config)
        except FileNotFoundError as exc:
            _fail(str(exc))
        print(f"Environment '{name}' added (file alias).")
        return

    if action == "clone":
        try:
            home_dir = clone_home_env(
                args.src,
                args.dst,
                description=args.description,
                overwrite=args.overwrite,
                with_auth=getattr(args, "with_auth", False),
            )
        except (KeyError, FileNotFoundError, FileExistsError, ValueError) as exc:
            _fail(str(exc.args[0] if isinstance(exc, KeyError) and exc.args else exc))
        print(f"Environment '{args.dst}' cloned from '{args.src}' at {home_dir}.")
        if not getattr(args, "with_auth", False):
            print("  (login credentials were not copied; pass --with-auth to copy them)")
        return

    if action == "path":
        try:
            profile = get_environment(args.name)
        except KeyError as exc:
            _fail(str(exc.args[0] if exc.args else exc))
        print(_entry_path(profile))
        return

    if action == "remove":
        name = args.name
        purge = getattr(args, "purge", False)
        try:
            remove_environment(name, purge=purge)
        except KeyError as exc:
            _fail(str(exc.args[0] if exc.args else exc))
        except ValueError as exc:
            _fail(str(exc))
        suffix = " (directory purged)" if purge else ""
        print(f"Environment '{name}' removed{suffix}.")
        return

    if action == "endpoint":
        _env_endpoint(args)
        return

    if action == "vars":
        _env_vars(args)
        return

    _fail(
        "specify an env action: list, create, import, status, sync, add, clone, "
        "path, endpoint, vars, or remove."
    )


def _home_env_or_fail(name: str) -> dict:
    try:
        profile = get_environment(name)
    except KeyError as exc:
        _fail(str(exc.args[0] if exc.args else exc))
    if entry_type(profile) != ENV_TYPE_HOME:
        _fail(f"env '{name}' is a config-file alias; endpoints and variables are kept in HOME envs")
    return profile


def _agent_or_fail(agent: str) -> str:
    spec = agent_spec(agent)
    if spec is None:
        _fail(f"unknown agent '{agent}'; use one of {agent_list()}")
    return spec.key


def _env_endpoint(args: argparse.Namespace) -> None:
    from clippet.endpoints import clear_endpoint, endpoint_of, set_endpoint

    profile = _home_env_or_fail(args.name)
    home = Path(profile.get("home_dir") or "")
    if not args.agent:
        for key, spec in AGENTS.items():
            found = endpoint_of(home, key, env_vars(profile, key))
            if found is not None:
                print(_endpoint_line(spec.title, found))
        if not any(endpoint_of(home, key, env_vars(profile, key)) for key in AGENTS):
            print(f"env '{args.name}': every agent uses its maker's API")
        return

    agent = _agent_or_fail(args.agent)
    spec = AGENTS[agent]
    try:
        if args.clear:
            clear_endpoint(args.name, agent)
            print(f"env '{args.name}': {spec.title} uses its maker's API again")
            return
        if not args.url:
            found = endpoint_of(home, agent, env_vars(profile, agent))
            print(_endpoint_line(spec.title, found) if found else f"{spec.title}: its maker's API")
            if spec.endpoint.note:
                print(f"  {spec.endpoint.note}")
            return
        notes = set_endpoint(args.name, agent, args.url, key_env=args.key_env, model=args.model, api=args.api)
    except (KeyError, ValueError, OSError) as exc:
        _fail(str(exc.args[0] if isinstance(exc, KeyError) and exc.args else exc))
    found = endpoint_of(home, agent)
    print(f"env '{args.name}': " + (_endpoint_line(spec.title, found) if found else f"{spec.title} → {args.url}"))
    if not args.key_env:
        print("  no key: add --key-env VAR if the endpoint wants one")
    for note in notes:
        print(f"  {note}")


def _endpoint_line(title: str, endpoint) -> str:
    parts = [f"{title} → {endpoint.url}"]
    if endpoint.model:
        parts.append(f"model {endpoint.model}")
    if endpoint.key:
        parts.append(f"key {endpoint.key}")
    return ", ".join(parts) + (f"  ({endpoint.source})" if endpoint.source else "")


def _env_vars(args: argparse.Namespace) -> None:
    from clippet.endpoints import is_secret, references, shown

    profile = _home_env_or_fail(args.name)
    agents = [_agent_or_fail(args.agent)] if args.agent else list(AGENTS)
    if args.agent and (args.assign or args.unset):
        assign: dict[str, str] = {}
        for item in args.assign:
            key, sep, value = item.partition("=")
            if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                _fail(f"expected VAR=VALUE, got {item!r}")
            assign[key] = value
            if value and is_secret(key) and not references(value):
                _note(f"{key} is kept in plain text in environments.json; "
                      f"'{key}=${{VAR}}' would read it from your environment instead")
        try:
            set_env_vars(args.name, agents[0], assign, args.unset)
        except KeyError as exc:
            _fail(str(exc.args[0] if exc.args else exc))
        profile = get_environment(args.name)
    elif args.assign or args.unset:
        _fail("name the agent whose variables to change")
    shown_any = False
    for agent in agents:
        values = env_vars(profile, agent)
        for key, value in values.items():
            print(f"{agent}  {key}={shown(key, value)}")
            shown_any = True
    if not shown_any:
        print(f"env '{args.name}' sets no variables" + (f" for {agents[0]}" if args.agent else ""))


def _normalized_source(source: str) -> str:
    if _is_cockpit_source(source):
        return source
    return str(Path(source).expanduser().resolve())


def _env_import(args: argparse.Namespace) -> None:
    source = _normalized_source(args.source)
    try:
        credentials = load_codex_credentials(source)
        credential = select_credential(credentials, args.account)
    except (CredentialFormatError, CredentialSelectionError, FileNotFoundError) as exc:
        _fail(str(exc))

    if credential.requires_reauth:
        _note(
            "warning: the source marks this account as needing re-login"
            + (f" ({credential.reauth_reason})" if credential.reauth_reason else "")
        )

    link = args.link
    if link is None:
        link = credential.source_format == "cockpit-store"

    try:
        home_dir, created = import_codex_credential(
            args.name,
            credential,
            source=source,
            link=link,
            overwrite=args.overwrite,
            seed_config=not args.bare,
            description=args.description,
        )
    except (FileExistsError, ValueError, OSError) as exc:
        _fail(str(exc))

    verb = "Created" if created else "Updated"
    print(f"{verb} env '{args.name}' with Codex account {describe_codex_credential(credential)}.")
    print(f"  HOME   : {home_dir}")
    print(f"  source : {source} [{credential.source_format}]{' (linked)' if link else ''}")
    if credential.auth_mode != "apikey":
        if link:
            print(
                "  Linked: before each launch CLIppet pulls the newest login from the "
                "source, so Codex inside the env rarely needs to refresh on its own."
            )
        else:
            print(
                "  Note: the source still holds the same refresh token. OpenAI rotates "
                "refresh tokens, so whichever copy refreshes first invalidates the "
                "other. For long-term parallel use, log in separately inside the env: "
                f"clippet -e {args.name} codex login"
            )
    print(f"  Launch : clippet -e {args.name} codex")


# --- auth subcommands ---------------------------------------------------------


def _handle_auth(args: argparse.Namespace) -> None:
    action = getattr(args, "auth_action", None)
    if action not in ("list", "export"):
        _fail("specify an auth action: list or export.")

    source = _normalized_source(args.source)
    try:
        credentials = load_codex_credentials(source)
    except (CredentialFormatError, FileNotFoundError, ValueError) as exc:
        _fail(str(exc))

    if action == "list":
        if args.json:
            print(json.dumps([c.summary() for c in credentials], indent=2, ensure_ascii=False))
            return
        print(f"{len(credentials)} Codex account(s) in {source} [{credentials[0].source_format}]")
        for index, credential in enumerate(credentials, start=1):
            flags = []
            if credential.is_current:
                flags.append("current")
            if credential.requires_reauth:
                flags.append("needs re-login")
            if credential.auth_mode != "apikey" and credential.is_expired():
                flags.append("access token expired")
            flag_text = f"  <{', '.join(flags)}>" if flags else ""
            print(f"  [{index}] {describe_codex_credential(credential)}{flag_text}")
            if credential.auth_mode != "apikey":
                print(
                    f"      access token expires {_fmt_moment(credential.access_expires_at)}"
                    f" | last refresh {_fmt_moment(credential.effective_last_refresh)}"
                )
        return

    try:
        credential = select_credential(credentials, args.account)
    except CredentialSelectionError as exc:
        _fail(str(exc))
    text = credential.to_auth_json_text()
    if args.output == "-":
        sys.stdout.write(text)
        return
    target = Path(args.output).expanduser()
    if target.exists() and not args.force:
        _fail(f"{target} already exists; pass --force to overwrite it.")
    from clippet.isolation import _write_private_file

    _write_private_file(target, text.encode("utf-8"))
    print(f"Wrote Codex auth.json for {describe_codex_credential(credential)} to {target}")


# --- interactive HOME picker -------------------------------------------------


def _make_native_adapter(agent_type: str) -> BaseSubprocessAdapter:
    """Construct a default adapter for a native agent type."""

    spec = agent_spec(agent_type)
    if spec is None:
        raise ValueError(f"Unsupported native agent type: {agent_type!r}")
    return spec.adapter_class()()


def _launch_native_directly(agent_type: str, passthrough: list[str] | None = None) -> None:
    """Spawn the native CLI binary against the user's real ``$HOME``."""

    adapter = _make_native_adapter(agent_type)
    command = _build_interactive_command(adapter, passthrough)
    _run_interactive_command(command, isolation=None, cwd=Path.cwd())


def _maybe_apply_project_config(args: argparse.Namespace) -> bool:
    """Soft variant of :func:`_apply_project_level_config`.

    Tries to apply project-level ``.clippet.json`` when no explicit config
    is set.  Returns *True* on success.  Unlike the strict variant, returns
    *False* (does not exit) when no project config is found, so callers can
    fall back to the interactive picker.
    """

    has_explicit_config = any([
        getattr(args, "config", None),
        getattr(args, "env", None),
        getattr(args, "codex_config", None),
    ])
    agent_type = getattr(args, "agent_type", None)

    if has_explicit_config or agent_type not in _NATIVE_AGENTS:
        return False

    try:
        launch = resolve_project_launch(Path.cwd(), agent_type)
    except (FileNotFoundError, KeyError):
        return False
    except ValueError as exc:
        _fail(f"invalid .clippet.json: {exc}")

    _apply_launch(args, launch)
    return True


def _picker_choices(agent_type: str) -> list[tuple[str, Path | None]]:
    """Return the ordered choice list shown by the HOME picker.

    The first entry is always ``("base", None)`` — meaning "use the real
    ``$HOME``". Remaining entries are HOME-container envs sorted by name.
    """

    envs = list_environments()
    home_envs = sorted(
        (
            (name, profile)
            for name, profile in envs.items()
            if entry_type(profile) == ENV_TYPE_HOME
        ),
        key=lambda kv: kv[0],
    )
    choices: list[tuple[str, Path | None]] = [("base", None)]
    for name, profile in home_envs:
        choices.append((name, Path(profile.get("home_dir", ""))))
    return choices


def _format_choice_body(
    name: str,
    home_path: Path | None,
    config_paths: list[str],
    real_home: Path,
    agent_type: str | None = None,
) -> str:
    """Format a single choice line (without the leading marker)."""

    if home_path is None:
        body = f"{name:<16}  (real $HOME — {real_home})"
        if agent_type == "codex":
            body += f"  {describe_codex_credential(read_env_codex_credential(real_home))}"
        return body
    seeded = bool(config_paths) and any(
        (home_path / cfg).exists() for cfg in config_paths
    )
    tag = "[seeded]" if seeded else "        "
    body = f"{name:<16}  {tag}  {home_path}"
    if agent_type == "codex":
        body += f"  {describe_codex_credential(read_env_codex_credential(home_path))}"
    return body


def _can_use_arrow_picker() -> bool:
    """Return True when arrow-key navigation can be used safely.

    Requires POSIX, both stdin and stdout being real TTYs, and an importable
    ``termios`` module that can read the current terminal attributes.
    """

    if sys.platform == "win32":
        return False
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        return False
    try:
        import termios  # noqa: F401

        termios.tcgetattr(sys.stdin.fileno())
    except (ImportError, OSError, ValueError):
        return False
    return True


def _arrow_home_picker(
    agent_type: str,
    choices: list[tuple[str, Path | None]],
    config_paths: list[str],
    real_home: Path,
) -> tuple[str, Path | None] | None:
    """Render an arrow-key navigable picker.

    Controls:
      * ↑/↓: move selection
      * Enter: confirm
      * 1-9: jump to that entry and confirm
      * q / Ctrl-C: cancel
    """

    import termios
    import tty

    n = len(choices)
    bodies = [
        _format_choice_body(name, home, config_paths, real_home, agent_type)
        for name, home in choices
    ]

    sys.stderr.write(
        f"clippet {agent_type} — select HOME  "
        "(↑/↓ to move, Enter to confirm, digit to jump, 'q' to quit):\n"
    )
    sys.stderr.flush()

    selected = 0

    def _draw(initial: bool) -> None:
        if not initial:
            sys.stderr.write(f"\x1b[{n}A")  # cursor up n lines
        for i, body in enumerate(bodies):
            marker = "> " if i == selected else "  "
            sys.stderr.write(f"\x1b[2K\r{marker}{body}\n")
        sys.stderr.flush()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        _draw(initial=True)
        while True:
            ch = sys.stdin.read(1)
            if ch == "\x03":  # Ctrl-C
                return None
            if ch in ("q", "Q"):
                return None
            if ch in ("\r", "\n"):
                return choices[selected]
            if ch.isdigit() and ch != "0":
                idx = int(ch)
                if 1 <= idx <= n:
                    selected = idx - 1
                    _draw(initial=False)
                    return choices[selected]
                continue
            if ch == "\x1b":
                next1 = sys.stdin.read(1)
                # Accept both "ESC [ X" (xterm) and "ESC O X" (vt100/PuTTY)
                if next1 not in ("[", "O"):
                    continue
                next2 = sys.stdin.read(1)
                if next2 == "A":  # up
                    selected = (selected - 1) % n
                    _draw(initial=False)
                elif next2 == "B":  # down
                    selected = (selected + 1) % n
                    _draw(initial=False)
                # ignore left/right and other sequences
    except KeyboardInterrupt:
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _numeric_home_picker(
    agent_type: str,
    choices: list[tuple[str, Path | None]],
    config_paths: list[str],
    real_home: Path,
) -> tuple[str, Path | None] | None:
    """Fallback picker that reads a numeric choice via :func:`input`."""

    print(f"clippet {agent_type} — select HOME:", file=sys.stderr)
    for idx, (name, home_path) in enumerate(choices, start=1):
        body = _format_choice_body(name, home_path, config_paths, real_home, agent_type)
        print(f"  [{idx}] {body}", file=sys.stderr)
    print("  [q] quit", file=sys.stderr)

    while True:
        try:
            raw = input("Choice [1]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return None

        if raw in ("", "1"):
            return choices[0]
        if raw in ("q", "quit", "exit"):
            return None

        try:
            idx = int(raw)
        except ValueError:
            print(
                f"Invalid choice: {raw!r}. Enter a number 1-{len(choices)}, or 'q' to quit.",
                file=sys.stderr,
            )
            continue

        if 1 <= idx <= len(choices):
            return choices[idx - 1]
        print(
            f"Choice out of range: {idx} (valid: 1-{len(choices)})",
            file=sys.stderr,
        )


def _interactive_home_picker(agent_type: str) -> tuple[str, Path | None] | None:
    """Prompt the user to pick a HOME for the requested agent.

    Lists ``base`` (the real ``$HOME``) plus every registered HOME-container
    env, tagging those that already contain seeded config for *agent_type*
    (and, for Codex, showing which account each one is logged in as).

    Uses an arrow-key navigable interface when stdin/stderr are real TTYs;
    otherwise falls back to a numeric ``input()`` prompt.

    Returns:
        A ``(name, home_path)`` tuple where ``home_path`` is *None* for base
        (meaning "use the real ``$HOME``"), or *None* when the user cancels.
    """

    choices = _picker_choices(agent_type)
    config_paths = AGENT_CONFIG_PATHS.get(agent_type, [])
    real_home = Path.home()

    if _can_use_arrow_picker():
        return _arrow_home_picker(agent_type, choices, config_paths, real_home)
    return _numeric_home_picker(agent_type, choices, config_paths, real_home)


def _run_native_with_picker(args: argparse.Namespace) -> None:
    """Resolve the HOME for a bare ``clippet <agent>`` invocation.

    1. Try project-level ``.clippet.json``.
    2. If a TTY and no prompt, show the interactive HOME picker.
    3. Otherwise, fall back to the user's real ``$HOME``.
    """

    agent_type: str = args.agent_type
    passthrough: list[str] = list(getattr(args, "agent_args", None) or [])

    try:
        applied = _maybe_apply_project_config(args)
    except EnvironmentRegistryError as exc:
        _fail(str(exc))
    if applied:
        _handle_run(args)
        return

    prompt: str | None = getattr(args, "prompt", None)

    if prompt is None and sys.stdin.isatty():
        try:
            choice = _interactive_home_picker(agent_type)
        except EnvironmentRegistryError as exc:
            _fail(str(exc))
        if choice is None:
            sys.exit(0)
        name, home_path = choice
        if home_path is None:
            _launch_native_directly(agent_type, passthrough)
            return
        args.env = name
        _handle_run(args)
        return

    prompt = _resolve_prompt(args, passthrough)
    if prompt is None:
        _launch_native_directly(agent_type, passthrough)
        return

    adapter = _make_native_adapter(agent_type)
    _execute_headless(adapter, prompt, passthrough)


# --- main --------------------------------------------------------------------


def main() -> None:
    """CLI entry point for CLIppet."""

    parser = _build_parser()
    args = parser.parse_args()

    command = getattr(args, "command", None)

    if command == "env":
        _handle_env(args)
        return

    if command == "auth":
        _handle_auth(args)
        return

    if command == "tui":
        _handle_tui(getattr(args, "workspace", None))
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

    # Bare ``clippet <agent>`` invocation — try project-level config, then
    # offer an interactive HOME picker, and finally fall back to real $HOME.
    agent_type = getattr(args, "agent_type", None)
    if agent_type in _NATIVE_AGENTS:
        _run_native_with_picker(args)
        return

    if agent_type:
        _fail(
            f"unknown agent '{agent_type}'. Use one of {', '.join(_NATIVE_AGENTS)}, "
            "or pass -c <config> for composite adapter names."
        )

    # Nothing provided: in a terminal open the TUI (when Textual is there),
    # otherwise show help.
    from clippet.tui import textual_available

    if sys.stdin.isatty() and sys.stdout.isatty() and textual_available() and not getattr(args, "prompt", None):
        _handle_tui(None)
        return
    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
