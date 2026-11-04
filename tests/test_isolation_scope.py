"""Isolation scopes: Codex keeps your HOME in an env, only CODEX_HOME points into it.

A fake ``codex`` on PATH stands in for the real one: it reads the login in
CODEX_HOME, writes a session there, starts a "tool" process, and reports what
both saw.  No real account and no real HOME are involved.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import clippet.config.environments as env_mod
from clippet import cli
from clippet.adapters.base import create_isolated_environment
from clippet.config.detector import (
    create_adapter_from_config_file,
    create_adapter_with_second_home,
)
from clippet.config.environments import (
    create_home_env,
    get_environment,
    set_env_isolation_scope,
)
from clippet.config.registry import (
    CredentialProfileConfig,
    create_runner_from_config,
    load_config,
)
from clippet.host import host, host_home
from clippet.isolation import IsolatedEnvironment, _holds, effective_scope
from clippet.models import AgentRequest, IsolationConfig
from tests.codex_fixtures import native_auth

SEEN = ("HOME", "CODEX_HOME", "XDG_CONFIG_HOME", "CLIPPET_HOST_HOME")

FAKE_CODEX = '''
import json, os, subprocess, sys
from pathlib import Path

codex_home = Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else Path(os.environ["HOME"]) / ".codex"
auth_file = codex_home / "auth.json"
auth = json.loads(auth_file.read_text())
if os.environ.get("FAKE_CODEX_REFRESH"):
    auth["last_refresh"] = "2026-11-04T00:00:00Z"
    auth_file.write_text(json.dumps(auth))
sessions = codex_home / "sessions"
sessions.mkdir(parents=True, exist_ok=True)
(sessions / f"rollout-{os.getpid()}.jsonl").write_text("{}\\n")
names = %(names)r
tool = subprocess.run(
    [sys.executable, "-c", "import json, os, sys; print(json.dumps({n: os.environ.get(n) for n in sys.argv[1:]}))", *names],
    capture_output=True, text=True, check=True,
)
payload = {
    "agent": {n: os.environ.get(n) for n in names},
    "tool": json.loads(tool.stdout),
    "account": auth["tokens"]["account_id"],
    "codex_home": str(codex_home),
    "argv": sys.argv[1:],
}
for event in (
    {"type": "thread.started", "thread_id": "t-1"},
    {"type": "item.completed", "item": {"id": "i-1", "type": "agent_message", "text": json.dumps(payload)}},
    {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
):
    print(json.dumps(event))
'''


@pytest.fixture()
def own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Your HOME (a fake one), with a Codex login and config that no env run may touch."""

    home = tmp_path / "own"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text(json.dumps(native_auth("you@example.com", "acct-yours")))
    (home / ".codex" / "config.toml").write_text('model = "yours"\n')
    (home / ".gitconfig").write_text("[user]\n\tname = You\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("CLIPPET_HOME", str(tmp_path / "clippet"))
    for name in ("CODEX_HOME", "CODEX_API_KEY", "OPENAI_API_KEY", "FAKE_CODEX_REFRESH"):
        monkeypatch.delenv(name, raising=False)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "codex"
    fake.write_text(f"#!{sys.executable}\n" + textwrap.dedent(FAKE_CODEX % {"names": SEEN}))
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return home


def snapshot(folder: Path) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


def env_with_login(name: str, account: str) -> Path:
    home = create_home_env(name, share=[])
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text(json.dumps(native_auth(f"{account}@example.com", account)))
    return home


def run_in(home: Path, tmp_path: Path, **request) -> dict:
    result = create_adapter_with_second_home(home, "codex").run(
        AgentRequest(task_prompt="report", workspace_dir=tmp_path, **request)
    )
    assert result.is_success, result.error_message
    return json.loads(result.output_text)


# --- what the agent and its tools see -------------------------------------------------


def test_codex_keeps_your_home_and_so_do_the_tools_it_starts(own: Path, tmp_path: Path) -> None:
    home = env_with_login("work", "acct-work")

    seen = run_in(home, tmp_path)

    expected = {
        "HOME": str(own),
        "CODEX_HOME": str(home.resolve() / ".codex"),
        "XDG_CONFIG_HOME": str(own / ".config"),
        "CLIPPET_HOST_HOME": str(own),
    }
    assert seen["agent"] == expected
    assert seen["tool"] == expected  # a codex started from a tool stays in the env
    assert seen["account"] == "acct-work"
    assert 'cli_auth_credentials_store="file"' in seen["argv"]


def test_two_envs_keep_their_own_login_and_sessions(own: Path, tmp_path: Path) -> None:
    before = snapshot(own)
    alice, bob = env_with_login("alice", "acct-alice"), env_with_login("bob", "acct-bob")

    assert run_in(alice, tmp_path)["account"] == "acct-alice"
    assert run_in(bob, tmp_path)["account"] == "acct-bob"

    assert len(list((alice / ".codex" / "sessions").glob("*.jsonl"))) == 1
    assert len(list((bob / ".codex" / "sessions").glob("*.jsonl"))) == 1
    assert snapshot(own) == before  # your own .codex, .gitconfig: untouched


def test_the_home_scope_still_makes_the_env_your_home(own: Path, tmp_path: Path) -> None:
    home = env_with_login("work", "acct-work")
    set_env_isolation_scope("work", "home")

    seen = run_in(home, tmp_path)

    expected = {"HOME": str(home.resolve()), "CODEX_HOME": None, "XDG_CONFIG_HOME": None,
                "CLIPPET_HOST_HOME": str(own)}
    assert seen["agent"] == expected and seen["tool"] == expected
    assert seen["codex_home"] == str(home.resolve() / ".codex")
    assert seen["account"] == "acct-work"


def test_agents_without_a_config_directory_variable_keep_the_home_scope() -> None:
    assert effective_scope(None, "codex") == "agent"
    assert effective_scope("agent", "codex") == "agent"
    assert effective_scope("home", "codex") == "home"
    for agent in ("claude", "gemini", "qodercli", "pi", "copilot", "agy", None):
        assert effective_scope(None, agent) == "home"
        assert effective_scope("agent", agent) == "home"
    with pytest.raises(ValueError, match="unknown isolation scope"):
        effective_scope("nowhere", "codex")
    with pytest.raises(ValueError, match="needs the variable"):
        IsolatedEnvironment(scope="agent")
    with pytest.raises(ValueError, match="not a folder inside the env"):
        IsolatedEnvironment(scope="agent", config_dirs={"CODEX_HOME": "../elsewhere"})


def test_cli_sync_async_and_tui_runs_get_the_same_environment(
    own: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clippet.tui import data as tui_data
    from clippet.tui import runs as tui_runs

    home = env_with_login("work", "acct-work")
    request = AgentRequest(task_prompt="report", workspace_dir=tmp_path)
    adapter = create_adapter_with_second_home(home, "codex")

    sync = json.loads(adapter.run(request).output_text)["agent"]
    background = json.loads(asyncio.run(adapter.run_async(request)).output_text)["agent"]
    view = next(env for env in tui_data.load_envs()[0] if env.name == "work")
    tui = json.loads(asyncio.run(tui_runs.adapter_for(view, "codex").run_async(request)).output_text)["agent"]

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    with patch("clippet.cli.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0)
        cli._handle_run(cli._build_parser().parse_args(["-e", "work", "codex"]))
    interactive = {name: run.call_args.kwargs["env"].get(name) for name in SEEN}

    assert sync == background == tui == interactive
    assert interactive["HOME"] == str(own)
    assert view.isolation_scope("codex") == "agent" and view.isolation_scope("claude") == "home"
    assert view.config_dirs("codex") == {"CODEX_HOME": home / ".codex"}


def test_the_desktop_app_and_the_cli_use_the_same_codex_home(own: Path, tmp_path: Path) -> None:
    from clippet.desktop import Instance

    home = env_with_login("work", "acct-work")
    with create_isolated_environment(IsolationConfig(home_dir=str(home)), "codex") as env:
        assert Path(env.env["CODEX_HOME"]) == Instance(home.resolve()).codex_home
        assert env.env["HOME"] == str(own)


# --- what stays as it was ----------------------------------------------------------------


def test_temp_runs_keep_your_home_save_a_refresh_and_remove_only_their_folder(
    own: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "auth.json"
    source.write_text(json.dumps(native_auth("temp@example.com", "acct-temp")))
    before = snapshot(own)
    monkeypatch.setenv("FAKE_CODEX_REFRESH", "1")

    adapter = create_adapter_from_config_file(str(source), "codex")
    result = adapter.run(AgentRequest(task_prompt="report", workspace_dir=tmp_path))

    seen = json.loads(result.output_text)
    assert seen["agent"]["HOME"] == str(own)
    sandbox = Path(seen["agent"]["CODEX_HOME"]).parent
    assert sandbox.name.startswith("clippet-agent-") and not sandbox.exists()  # removed after the run
    assert seen["account"] == "acct-temp"
    assert json.loads(source.read_text())["last_refresh"] == "2026-11-04T00:00:00Z"  # the refresh was kept
    assert snapshot(own) == before


def test_an_env_is_never_deleted_and_neither_is_your_home(own: Path, tmp_path: Path) -> None:
    home = env_with_login("work", "acct-work")
    with create_isolated_environment(IsolationConfig(home_dir=str(home)), "codex"):
        pass
    assert (home / ".codex" / "auth.json").is_file()
    assert _holds(own, own) and _holds(own.parent, own) and not _holds(tmp_path / "other", own)


def test_paths_cannot_leave_the_env(own: Path) -> None:
    with IsolatedEnvironment(scope="agent", config_dirs={"CODEX_HOME": ".codex"}) as env:
        with pytest.raises(ValueError, match="escapes"):
            env.resolve_path("../outside")
        assert env.config_dirs["CODEX_HOME"].is_dir()
        assert env.process_home == own and env.env_root != own


def test_the_scope_owns_home_and_the_config_directory(own: Path) -> None:
    overrides = {"HOME": "/elsewhere", "CODEX_HOME": "/elsewhere/.codex", "RELAY_KEY": "k"}
    with create_isolated_environment(IsolationConfig(env_overrides=overrides), "codex") as env:
        env.update_env(overrides)  # how credential providers apply them
        assert env.env["HOME"] == str(own)
        assert env.env["CODEX_HOME"] == str(env.env_root.resolve() / ".codex")
        assert env.env["RELAY_KEY"] == "k"


def test_nothing_of_yours_changes(own: Path, tmp_path: Path) -> None:
    environ = dict(os.environ)
    before = snapshot(own)
    home = env_with_login("work", "acct-work")

    run_in(home, tmp_path)
    with patch("clippet.cli.subprocess.run") as run, patch.object(sys.stdin, "isatty", lambda: True):
        run.return_value = MagicMock(returncode=0)
        cli._handle_run(cli._build_parser().parse_args(["-e", "work", "codex"]))

    assert dict(os.environ) == environ
    assert snapshot(own) == before


# --- nested runs ---------------------------------------------------------------------------


def test_clippet_inside_an_env_run_finds_your_registry_and_your_dirs(
    own: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLIPPET_HOME")
    home = env_with_login("work", "acct-work")
    assert env_mod.get_clippet_root() == own / ".clippet"

    # a run in the home scope (Claude Code's) hands its tools HOME=<env> and no XDG directories
    with create_isolated_environment(IsolationConfig(home_dir=str(home)), "claude") as outer:
        inner = dict(outer.env)
    assert inner["HOME"] == str(home.resolve()) and "XDG_CONFIG_HOME" not in inner
    assert inner["CLIPPET_HOST_HOME"] == str(own)

    # ... and a clippet started there
    monkeypatch.setenv("HOME", inner["HOME"])
    monkeypatch.delenv("XDG_CONFIG_HOME")
    for name, value in inner.items():
        if name.startswith("CLIPPET_HOST_"):
            monkeypatch.setenv(name, value)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(os.environ["HOME"])))

    assert host().nested and host_home() == own
    assert env_mod.get_clippet_root() == own / ".clippet"
    assert get_environment("work")["home_dir"] == str(home.resolve())
    with create_isolated_environment(IsolationConfig(home_dir=str(home)), "codex") as env:
        assert env.env["HOME"] == str(own)
        assert env.env["XDG_CONFIG_HOME"] == str(own / ".config")
        assert env.env["CODEX_HOME"] == str(home.resolve() / ".codex")
        assert env.env["CLIPPET_HOST_HOME"] == str(own)


def test_a_bare_login_lookup_follows_codex_home(own: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from clippet.config.detector import default_codex_home

    assert default_codex_home() == own / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "work" / ".codex"))  # inside an env's Codex
    assert default_codex_home() == tmp_path / "work" / ".codex"


# --- settings ----------------------------------------------------------------------------------


def test_the_scope_is_kept_per_env_and_shown(own: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    env_with_login("work", "acct-work")

    def clippet(*argv: str) -> str:
        monkeypatch.setattr(sys, "argv", ["clippet", *argv])
        cli.main()
        return capsys.readouterr().out

    assert "agent (default): codex keeps your HOME (CODEX_HOME=<env>/.codex)" in clippet("env", "scope", "work")
    assert "home: every agent gets the env as HOME" in clippet("env", "scope", "work", "home")
    assert get_environment("work")["isolation_scope"] == "home"
    assert "isolation   : home" in clippet("env", "status", "work")
    clippet("env", "scope", "work", "default")
    assert "isolation_scope" not in get_environment("work")
    assert effective_scope(None, "codex") == "agent"


def test_configs_without_the_field_get_the_default_and_merging_keeps_it(tmp_path: Path) -> None:
    assert IsolationConfig().isolation_scope is None
    assert IsolationConfig.model_validate({"home_dir": "/x"}).isolation_scope is None
    pinned = IsolationConfig(isolation_scope="home")
    assert IsolationConfig().merged_with(pinned).isolation_scope == "home"
    assert pinned.merged_with(IsolationConfig(env_overrides={"A": "1"})).isolation_scope == "home"
    assert IsolationConfig.model_validate(pinned.model_dump()).isolation_scope == "home"
    with pytest.raises(ValueError):
        IsolationConfig(isolation_scope="nowhere")

    assert CredentialProfileConfig().to_isolation_config().isolation_scope is None
    config = tmp_path / "clippet.yaml"
    config.write_text(textwrap.dedent("""
        adapters:
          - adapter_type: codex
            name: work
            options: {credential_profile: work}
        credential_profiles:
          work:
            files: {".codex/auth.json": "/nowhere/auth.json"}
            isolation_scope: home
    """))
    runner = create_runner_from_config(load_config(config))
    assert runner.get_adapter("work").default_isolation.isolation_scope == "home"
