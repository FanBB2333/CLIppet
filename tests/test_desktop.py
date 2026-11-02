"""The Codex desktop app, one instance per env (clippet.desktop).

No app is started: ``ps``, ``codesign``, ``open`` and ``osascript`` are
stood in for, and the app is a folder with an Info.plist.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

import clippet.config.environments as env_mod
from clippet import cli, desktop
from clippet.config.environments import create_home_env, set_env_vars
from clippet.desktop import (
    DesktopError,
    Instance,
    find_app,
    open_app,
    open_command,
    running,
)

pytest.importorskip("tomllib")  # Python 3.11+


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env_mod, "get_clippet_root", lambda: tmp_path / ".clippet")
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bundle = tmp_path / "Apps" / "ChatGPT.app"
    (bundle / "Contents").mkdir(parents=True)
    with open(bundle / "Contents" / "Info.plist", "wb") as handle:
        plistlib.dump({"CFBundleIdentifier": "com.openai.codex"}, handle)
    monkeypatch.setenv(desktop.APP_ENV, str(bundle))
    monkeypatch.delenv(desktop.UNVERIFIED_ENV, raising=False)
    return bundle


class FakeMac:
    """Answers the commands clippet.desktop runs, and remembers them."""

    def __init__(self, processes: str = "", signature_ok: bool = True, team: str = desktop.TEAM_ID) -> None:
        self.processes = processes
        self.signature_ok = signature_ok
        self.team = team
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        tool = Path(command[0]).name
        if tool == "ps":
            return subprocess.CompletedProcess(command, 0, self.processes, "")
        if tool == "codesign" and "--verify" in command:
            if self.signature_ok:
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(
                command, 1, "", f"{command[-1]}: invalid signature (code or signature have been modified)\n"
                                "In architecture: arm64\n")
        if tool == "codesign":
            return subprocess.CompletedProcess(command, 0, "", f"Identifier=com.openai.codex\nTeamIdentifier={self.team}\n")
        if tool == "osascript":
            return subprocess.CompletedProcess(command, 0, "true\n", "")
        if tool == "open":
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected command {command}")

    def ran(self, tool: str) -> list[list[str]]:
        return [call for call in self.calls if Path(call[0]).name == tool]


def test_the_app_is_found_by_its_bundle_id(app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert find_app() == app

    other = tmp_path / "Other.app"
    (other / "Contents").mkdir(parents=True)
    with open(other / "Contents" / "Info.plist", "wb") as handle:
        plistlib.dump({"CFBundleIdentifier": "com.example.other"}, handle)
    monkeypatch.setenv(desktop.APP_ENV, str(other))
    with pytest.raises(DesktopError, match="not the Codex app"):
        find_app()


def test_each_env_gets_its_own_codex_home_and_electron_profile(tmp_path: Path, app: Path) -> None:
    home = tmp_path / "envs" / "work"

    command = open_command(app, Instance(home), [tmp_path / "project"], {"FOO": "1"})

    assert command == [
        "/usr/bin/open", "-n",
        "--env", f"CODEX_HOME={home}/.codex",
        "--env", f"CODEX_ELECTRON_USER_DATA_PATH={home}/.codex-desktop",
        "--env", "FOO=1",
        "-a", str(app), str(tmp_path / "project"),
        "--args", f"--user-data-dir={home}/.codex-desktop",
    ]


def test_a_running_instance_is_told_apart_from_a_similar_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work, work2 = Instance(tmp_path / "work"), Instance(tmp_path / "work2")
    fake = FakeMac(processes=(
        f"  101 /Applications/ChatGPT.app/Contents/MacOS/ChatGPT {work2.flag}\n"
        f"  102 /Applications/ChatGPT.app/Contents/Frameworks/Helper --type=gpu {work2.flag} --x\n"
        "  103 /bin/zsh\n"
    ))
    monkeypatch.setattr(subprocess, "run", fake)

    assert running(work) == []
    assert running(work2) == [101, 102]


def test_opening_an_env_starts_its_own_instance(tmp_path: Path, app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = create_home_env("work")
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "gpt-5.5"\n')
    set_env_vars("work", "codex", {"RELAY_KEY": "${RELAY_KEY}", "CODEX_FLAG": "on"})
    fake = FakeMac()
    monkeypatch.setattr(subprocess, "run", fake)
    pulled = []

    opened = open_app(home, [str(tmp_path)], before_launch=lambda: pulled.append(True) or ["pulled the newest login"])

    assert pulled == [True]
    assert opened.running == []
    [command] = fake.ran("open")
    assert command == open_command(app, Instance(home), [tmp_path], {"CODEX_FLAG": "on"})
    assert "the app reads $RELAY_KEY from your login shell" in opened.notes
    assert "pulled the newest login" in opened.notes
    assert (home / ".codex" / "config.toml").read_text() == 'model = "gpt-5.5"\ncli_auth_credentials_store = "file"\n'
    assert (home / ".codex-desktop").stat().st_mode & 0o777 == 0o700


def test_an_open_instance_is_brought_to_the_front_and_left_alone(
    tmp_path: Path, app: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = create_home_env("work")
    instance = Instance(home.resolve())
    fake = FakeMac(processes=f"  4242 {app}/Contents/MacOS/ChatGPT {instance.flag}\n")
    monkeypatch.setattr(subprocess, "run", fake)

    opened = open_app(home, before_launch=lambda: pytest.fail("a running instance's login was touched"))

    assert opened.running == [4242]
    assert fake.ran("open") == [] and fake.ran("codesign") == []
    assert "4242" in fake.ran("osascript")[0][-1]
    assert not (home / ".codex" / "config.toml").exists()


def test_an_app_that_fails_the_signature_check_is_refused_unless_asked(
    tmp_path: Path, app: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = create_home_env("work")
    monkeypatch.setattr(subprocess, "run", FakeMac(signature_ok=False))

    with pytest.raises(DesktopError, match=r"invalid signature.*CLIPPET_CODEX_APP_UNVERIFIED=1"):
        open_app(home)

    monkeypatch.setattr(subprocess, "run", FakeMac(team="ABCDE12345"))
    with pytest.raises(DesktopError, match="signed by team ABCDE12345"):
        open_app(home)

    monkeypatch.setenv(desktop.UNVERIFIED_ENV, "1")
    fake = FakeMac(signature_ok=False)
    monkeypatch.setattr(subprocess, "run", fake)
    opened = open_app(home)
    assert fake.ran("open") and any("signature check" in note for note in opened.notes)


def test_only_macos_for_now(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(DesktopError, match="macOS only"):
        open_app(tmp_path)


def main(argv: list[str]) -> None:
    cli._handle_run(cli._build_parser().parse_args(argv))


def test_clippet_e_env_codex_app_opens_the_env(tmp_path: Path, app: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    home = create_home_env("work")
    seen = {}

    def fake_open(where, paths=None, **kwargs):
        seen.update(home=Path(where), paths=paths)
        return desktop.Opened(app=app, command=["/usr/bin/open"], notes=["a note"])

    monkeypatch.setattr(desktop, "open_app", fake_open)

    main(["-e", "work", "codex", "app", str(tmp_path)])

    assert seen == {"home": home, "paths": [str(tmp_path)]}
    err = capsys.readouterr().err
    assert "opened ChatGPT.app as env 'work'" in err and "a note" in err

    with pytest.raises(SystemExit):
        main(["-e", "work", "codex", "app", "--download-url", "x"])
    assert "does not apply" in capsys.readouterr().err

    credential = tmp_path / "auth.json"
    credential.write_text("{}")
    with pytest.raises(SystemExit):
        main(["-c", str(credential), "codex", "app"])
    assert "clippet env import" in capsys.readouterr().err


def test_a_linked_login_is_not_pulled_while_the_envs_app_is_open(tmp_path: Path, monkeypatch, capsys) -> None:
    home = create_home_env("work")
    profile = {"home_dir": str(home), "codex_source": {"source": "cockpit", "linked": True}}
    pulled = []
    monkeypatch.setattr(cli, "sync_linked_codex_credential", lambda *a: pulled.append(a))
    monkeypatch.setattr(desktop, "running", lambda instance: [4242])

    cli._prepare_home_env("work", profile, "codex")

    assert pulled == []
    assert "its Codex app is open" in capsys.readouterr().err
