"""Tests for argument passthrough, the env/auth subcommands, Codex event
parsing and project-level env pinning."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import clippet.config.environments as env_mod
from clippet.adapters.codex import CodexAdapter, parse_codex_events, summarize_codex_events
from clippet.cli import _build_parser, main, split_agent_args
from clippet.config.environments import (
    create_home_env,
    get_environment,
    set_env_isolation_scope,
)
from clippet.config.project import load_project_config, resolve_project_launch
from clippet.models import AgentRequest
from tests.codex_fixtures import cockpit_account, cockpit_export, native_auth


@pytest.fixture()
def clippet_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".clippet"
    monkeypatch.setattr(env_mod, "get_clippet_root", lambda: root)
    return root


@pytest.fixture()
def tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


# --- splitting ----------------------------------------------------------------


@pytest.mark.parametrize(
    "argv, clippet_argv, agent_args",
    [
        (["-e", "work", "codex", "resume", "--last"], ["-e", "work", "codex"], ["resume", "--last"]),
        (["-e", "work", "codex", "-c", "model=x"], ["-e", "work", "codex"], ["-c", "model=x"]),
        (["codex", "-p", "hi", "--search"], ["codex", "-p", "hi"], ["--search"]),
        (["codex", "exec", "-p", "prof"], ["codex"], ["exec", "-p", "prof"]),
        (["-e", "w", "claude", "--", "-p", "hi"], ["-e", "w", "claude"], ["-p", "hi"]),
        (["-c", "auth.json", "--", "resume"], ["-c", "auth.json"], ["resume"]),
        (["--account", "2", "-c", "x.json", "codex"], ["--account", "2", "-c", "x.json", "codex"], []),
        (["codex", "--prompt=hi"], ["codex", "--prompt=hi"], []),
    ],
)
def test_split_agent_args(argv, clippet_argv, agent_args) -> None:
    assert split_agent_args(argv) == (clippet_argv, agent_args)


def test_parser_exposes_agent_args() -> None:
    args = _build_parser().parse_args(["-e", "work", "codex", "login", "status"])
    assert args.env == "work"
    assert args.agent_type == "codex"
    assert args.agent_args == ["login", "status"]
    assert args.prompt is None


# --- interactive passthrough --------------------------------------------------


def _home_env_with_login(name: str = "work") -> Path:
    home = create_home_env(name, share=[])
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text(json.dumps(native_auth()))
    return home


def test_env_launch_forwards_subcommand(clippet_root, tty, tmp_path, monkeypatch) -> None:
    home = _home_env_with_login()
    own = tmp_path / "own-home"
    own.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: own))
    monkeypatch.setenv("CODEX_HOME", "/real/.codex")
    monkeypatch.setattr(sys, "argv", ["clippet", "-e", "work", "codex", "resume", "--last"])

    with patch("clippet.cli.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0)
        main()

    command = run.call_args[0][0]
    assert command[0] == "codex"
    assert command[-2:] == ["resume", "--last"]
    assert "--sandbox" not in command and "--model" not in command
    assert 'cli_auth_credentials_store="file"' in command
    env = run.call_args.kwargs["env"]
    # Codex keeps your HOME; the env's .codex replaces the inherited CODEX_HOME
    assert env["HOME"] == str(own)
    assert env["CODEX_HOME"] == str(home.resolve() / ".codex")


def test_env_launch_in_home_scope_replaces_home(clippet_root, tty, monkeypatch) -> None:
    home = _home_env_with_login()
    set_env_isolation_scope("work", "home")
    monkeypatch.setenv("CODEX_HOME", "/real/.codex")
    monkeypatch.setattr(sys, "argv", ["clippet", "-e", "work", "codex", "resume", "--last"])

    with patch("clippet.cli.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0)
        main()

    env = run.call_args.kwargs["env"]
    assert env["HOME"] == str(home.resolve())
    assert "CODEX_HOME" not in env


@pytest.mark.parametrize(("agent", "variable", "folder"), [
    ("pi", "PI_CODING_AGENT_DIR", ".pi/agent"),
    ("qodercli", "QODER_CONFIG_DIR", ".qoder"),
])
def test_pi_and_qoder_keep_your_home_too(clippet_root, tty, tmp_path, monkeypatch, agent, variable, folder) -> None:
    home = create_home_env("work", share=[])
    own = tmp_path / "own-home"
    own.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: own))
    monkeypatch.setenv(variable, "/real/config")
    monkeypatch.setattr(sys, "argv", ["clippet", "-e", "work", agent])

    with patch("clippet.cli.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0)
        main()
    env = run.call_args.kwargs["env"]
    assert env["HOME"] == str(own)
    assert env[variable] == str(home.resolve() / folder)
    assert (home / folder).is_dir()

    set_env_isolation_scope("work", "home")
    with patch("clippet.cli.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0)
        main()
    env = run.call_args.kwargs["env"]
    assert env["HOME"] == str(home.resolve())
    assert variable not in env


def test_passthrough_does_not_consume_stdin(clippet_root, monkeypatch) -> None:
    _home_env_with_login()
    stdin = io.StringIO("sk-secret-from-file")
    stdin.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(
        sys, "argv", ["clippet", "-e", "work", "codex", "login", "--with-api-key"]
    )

    with patch("clippet.cli.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0)
        main()

    assert run.call_args[0][0][-2:] == ["login", "--with-api-key"]
    assert stdin.read() == "sk-secret-from-file"  # left for the agent


def test_child_signal_exit_code_is_propagated(clippet_root, tty, monkeypatch) -> None:
    _home_env_with_login()
    monkeypatch.setattr(sys, "argv", ["clippet", "-e", "work", "codex"])
    with patch("clippet.cli.subprocess.run") as run:
        run.return_value = MagicMock(returncode=-2)  # killed by SIGINT
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 130


def test_headless_passthrough_lands_before_prompt(clippet_root, tty, monkeypatch, capsys) -> None:
    _home_env_with_login()
    monkeypatch.setattr(
        sys, "argv", ["clippet", "-e", "work", "codex", "-p", "say hi", "--search"]
    )
    events = "\n".join(json.dumps(e) for e in [
        {"type": "thread.started", "thread_id": "t-1"},
        {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "hi!"}},
        {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}},
    ])

    with patch("clippet.adapters.base.subprocess.Popen") as popen:
        process = popen.return_value
        process.communicate.return_value = (events.encode(), b"")
        process.returncode = 0
        main()

    command = popen.call_args[0][0]
    assert command[:3] == ["codex", "exec", "--json"]
    assert command[-3:] == ["--search", "--", "say hi"]
    assert capsys.readouterr().out.strip() == "hi!"


def test_env_files_are_refused_with_a_reason(tmp_path, tty, monkeypatch, capsys) -> None:
    """A .env file was a Gemini CLI config; it is refused, not misread."""
    cfg = tmp_path / "gemini.env"
    cfg.write_text("GEMINI_API_KEY=k\n")
    monkeypatch.setattr(sys, "argv", ["clippet", "-c", str(cfg)])
    with patch("clippet.cli.subprocess.run") as run, pytest.raises(SystemExit):
        main()
    run.assert_not_called()
    assert "Gemini CLI is no longer supported" in capsys.readouterr().err


def test_missing_env_home_dir_is_an_error(clippet_root, tty, monkeypatch, capsys) -> None:
    home = create_home_env("gone", share=[])
    home.rmdir()
    monkeypatch.setattr(sys, "argv", ["clippet", "-e", "gone", "codex"])
    with pytest.raises(SystemExit):
        main()
    assert "is missing" in capsys.readouterr().err


# --- env / auth subcommands ------------------------------------------------------


def _export(tmp_path: Path) -> Path:
    path = tmp_path / "cockpit-export.json"
    path.write_text(json.dumps(cockpit_export(
        cockpit_account("alice@example.com", "acct-alice"),
        cockpit_account("bob@example.com", "acct-bob"),
    )))
    return path


def test_auth_list_never_prints_tokens(tmp_path, monkeypatch, capsys) -> None:
    path = _export(tmp_path)
    monkeypatch.setattr(sys, "argv", ["clippet", "auth", "list", str(path)])
    main()
    out = capsys.readouterr().out
    assert "alice@example.com" in out and "bob@example.com" in out
    data = json.loads(path.read_text())
    for entry in data["accounts"]["platforms"]["codex"]["exported_data"]:
        assert entry["tokens"]["refresh_token"] not in out
        assert entry["tokens"]["access_token"] not in out


def test_auth_export_writes_native_file(tmp_path, monkeypatch) -> None:
    path = _export(tmp_path)
    out = tmp_path / "auth.json"
    monkeypatch.setattr(
        sys, "argv", ["clippet", "auth", "export", str(path), "--account", "bob", "-o", str(out)]
    )
    main()
    doc = json.loads(out.read_text())
    assert doc["auth_mode"] == "chatgpt"
    assert doc["tokens"]["account_id"] == "acct-bob"
    assert (out.stat().st_mode & 0o777) == 0o600

    with pytest.raises(SystemExit):  # refuses to overwrite without --force
        main()


def test_env_import_and_status(clippet_root, tmp_path, monkeypatch, capsys) -> None:
    path = _export(tmp_path)
    monkeypatch.setattr(
        sys, "argv",
        ["clippet", "env", "import", "alice", str(path), "--account", "alice@example.com", "--bare"],
    )
    main()
    out = capsys.readouterr().out
    assert "Created env 'alice'" in out
    assert "refresh token" in out  # sharing warning for unlinked imports
    home = Path(get_environment("alice")["home_dir"])
    assert json.loads((home / ".codex" / "auth.json").read_text())["tokens"]["account_id"] == "acct-alice"

    monkeypatch.setattr(sys, "argv", ["clippet", "env", "status", "alice"])
    main()
    status = capsys.readouterr().out
    assert "alice@example.com" in status
    assert "cockpit-export" in status

    monkeypatch.setattr(sys, "argv", ["clippet", "env", "list"])
    main()
    assert "alice@example.com" in capsys.readouterr().out


def test_env_import_ambiguous_source_lists_accounts(clippet_root, tmp_path, monkeypatch, capsys) -> None:
    path = _export(tmp_path)
    monkeypatch.setattr(sys, "argv", ["clippet", "env", "import", "x", str(path)])
    with pytest.raises(SystemExit):
        main()
    err = capsys.readouterr().err
    assert "[1] alice@example.com" in err and "[2] bob@example.com" in err


def test_env_create_reports_skipped_credentials(clippet_root, tmp_path, monkeypatch, capsys) -> None:
    fake_home = tmp_path / "real-home"
    (fake_home / ".codex").mkdir(parents=True)
    (fake_home / ".codex" / "auth.json").write_text(json.dumps(native_auth()))
    (fake_home / ".codex" / "config.toml").write_text("")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(sys, "argv", ["clippet", "env", "create", "w", "--from-current", "--no-share"])
    main()
    out = capsys.readouterr().out
    assert "skipped credentials: .codex/auth.json" in out
    assert "--with-auth" in out


# --- codex event parsing ---------------------------------------------------------


def test_codex_event_summary() -> None:
    events = [
        {"type": "thread.started", "thread_id": "th"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "1", "type": "reasoning", "text": "..."}},
        {"type": "item.completed", "item": {"id": "2", "type": "command_execution",
                                            "command": "ls", "exit_code": 0, "status": "completed"}},
        {"type": "item.completed", "item": {"id": "3", "type": "mcp_tool_call", "server": "fs",
                                            "tool": "read", "arguments": {"p": 1}, "status": "completed"}},
        {"type": "item.completed", "item": {"id": "4", "type": "file_change",
                                            "changes": [{"path": "a.py", "kind": "update"}], "status": "completed"}},
        {"type": "error", "message": "Reconnecting... 1/5"},
        {"type": "item.completed", "item": {"id": "5", "type": "agent_message", "text": "done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 3}},
    ]
    summary = summarize_codex_events(events)
    assert summary["final_message"] == "done"
    assert [t.tool_name for t in summary["tool_calls"]] == ["shell", "fs.read", "apply_patch"]
    assert summary["steps"] == 4
    assert summary["usage"] == {"input_tokens": 10, "output_tokens": 3}
    assert summary["thread_id"] == "th"
    assert summary["failed"] is False  # transient error event does not fail the run


def test_codex_parse_output_failure() -> None:
    raw = "\n".join(json.dumps(e) for e in [
        {"type": "thread.started", "thread_id": "th"},
        {"type": "turn.failed", "error": {"message": "usage limit reached"}},
    ])
    result = CodexAdapter().parse_output(raw, "", 1)
    assert result.is_success is False
    assert result.error_message == "usage limit reached"
    assert result.session_id == "th"


def test_codex_plain_text_output_still_parses() -> None:
    assert parse_codex_events("hello world") is None
    adapter = CodexAdapter(json_events=False)
    result = adapter.parse_output("hello world\n", "", 0)
    assert result.is_success and result.output_text == "hello world"
    cmd = adapter.build_command(AgentRequest(task_prompt="x", workspace_dir="/tmp"))
    assert "--json" not in cmd


# --- project-level env pinning ---------------------------------------------------


def test_project_config_can_pin_env(clippet_root, tmp_path, tty, monkeypatch) -> None:
    _home_env_with_login("work")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".clippet.json").write_text(json.dumps(
        {"version": 1, "agents": {"codex": {"env": "work"}, "agy": {"env": "work"}}}
    ))
    monkeypatch.chdir(repo)
    launch = resolve_project_launch(repo, "codex")
    assert launch.env == "work" and launch.config_path is None

    monkeypatch.setattr(sys, "argv", ["clippet", "codex"])
    with patch("clippet.cli.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0)
        main()
    assert run.call_args.kwargs["env"]["CODEX_HOME"].endswith("/envs/work/.codex")


def test_project_config_rejects_env_with_paths(tmp_path) -> None:
    cfg = tmp_path / ".clippet.json"
    cfg.write_text(json.dumps(
        {"version": 1, "agents": {"codex": {"env": "w", "config_path": "a.json"}}}
    ))
    with pytest.raises(ValueError):
        load_project_config(cfg)
