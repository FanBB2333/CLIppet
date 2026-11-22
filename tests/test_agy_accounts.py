"""Cockpit's agy index, account binding and portable HOME launches."""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from clippet import cli
from clippet.adapters.base import build_credential_set, create_isolated_environment
from clippet.auth.antigravity import TOKEN_PATH, list_agy_accounts, select_agy_account
from clippet.auth.jwt import encode_unsigned_jwt
from clippet.config.detector import create_adapter_from_config_file, create_adapter_with_second_home
from clippet.config.environments import clone_home_env, get_environment


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "cockpit"
    (root / "accounts").mkdir(parents=True)
    home = tmp_path / "real-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CLIPPET_HOME", str(tmp_path / "registry"))
    monkeypatch.setenv("CLIPPET_COCKPIT_DIR", str(root))
    monkeypatch.delenv("SSH_CLIENT", raising=False)
    for user in ("alice", "bob"):
        email = f"{user}@example.org"
        detail = {"id": user, "email": email, "token": {
            "access_token": f"access-{user}", "refresh_token": f"refresh-{user}",
            "expiry_timestamp": 1_790_000_000, "id_token": encode_unsigned_jwt({"email": email}),
        }}
        (root / "accounts" / f"{user}.json").write_text(json.dumps(detail))
    # Cockpit retains an old JSON index. Only the active SQLite generation counts.
    (root / "accounts.json").write_text(json.dumps({
        "accounts": [{"id": "alice", "email": "alice@example.org"}], "current_account_id": "alice",
    }))
    with sqlite3.connect(root / "antigravity_accounts.sqlite") as db:
        db.executescript("""
            CREATE TABLE account_index_state(singleton INTEGER, state TEXT);
            CREATE TABLE summaries(source INTEGER, position INTEGER, account_id TEXT, email TEXT);
            INSERT INTO account_index_state VALUES (1, '{"source":0,"complete":true,"current_id":"bob"}');
            INSERT INTO summaries VALUES (0,1,'alice','alice@example.org'), (0,0,'bob','bob@example.org'),
                                         (1,0,'old','removed@example.org');
        """)
    return root


def invoke(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["clippet", *args])
    cli.main()
    return capsys.readouterr().out


def test_list_uses_only_index_and_never_decrypts(store, monkeypatch, capsys):
    (store / "accounts/bob.json").write_text("unreadable credential")
    rows = json.loads(invoke(monkeypatch, capsys, "auth", "list", "cockpit", "--agent", "agy", "--json"))
    assert rows == [
        {"id": "bob", "email": "bob@example.org", "is_current": True},
        {"id": "alice", "email": "alice@example.org", "is_current": False},
    ]
    assert select_agy_account("cockpit", "ALICE@example.org").native_login()["token"]["refresh_token"] == "refresh-alice"
    with pytest.raises(ValueError, match="--account"):
        select_agy_account("cockpit", None)
    with pytest.raises(ValueError, match="between 1 and 2"):
        select_agy_account("cockpit", "3")
    with pytest.raises(FileNotFoundError):
        list_agy_accounts(f"cockpit:{store / 'missing'}")


def test_legacy_index(store):
    (store / "antigravity_accounts.sqlite").unlink()
    assert [a.account_id for a in list_agy_accounts(str(store))] == ["alice"]


@pytest.mark.parametrize("source", ["cockpit", "cockpit:<dir>", "<dir>"])
def test_one_off_is_private_and_leaves_host_untouched(store, monkeypatch, source):
    monkeypatch.setenv("ANTIGRAVITY_GEMINI_DIR", str(store))
    monkeypatch.setenv("AGY_ADC_AUTH", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "base-key")
    previous_env = os.environ.copy()
    iso = create_adapter_from_config_file(source.replace("<dir>", str(store)), "agy", account="1").default_isolation
    assert "refresh-bob" not in repr(iso)
    assert not iso.sync_back
    with create_isolated_environment(iso, "agy") as env:
        build_credential_set(iso).inject(env)
        path = env.home_dir / TOKEN_PATH
        assert json.loads(path.read_bytes())["token"]["refresh_token"] == "refresh-bob"
        if os.name != "nt":
            assert path.stat().st_mode & 0o777 == 0o600
        assert env.env["SSH_CLIENT"] and env.env["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
        assert "GEMINI_API_KEY" not in env.env and "AGY_ADC_AUTH" not in env.env and "ANTIGRAVITY_GEMINI_DIR" not in env.env
    assert not path.exists()
    assert not (Path.home() / TOKEN_PATH).exists()
    assert previous_env == os.environ.copy()


def test_link_by_id_keep_newer_and_preserve_sessions(store, monkeypatch, capsys):
    invoke(monkeypatch, capsys, "env", "import", "work", "cockpit", "--agent", "agy", "--account", "1", "--bare")
    profile = get_environment("work")
    assert profile["agy_source"]["source_id"] == "bob"
    assert "refresh-" not in json.dumps(profile)
    home = Path(profile["home_dir"])
    session = home / ".gemini/antigravity-cli/conversations/example.pb"
    session.parent.mkdir()
    session.write_bytes(b"session")
    path = home / TOKEN_PATH
    with sqlite3.connect(store / "antigravity_accounts.sqlite") as db:
        db.execute("UPDATE summaries SET position=1-position")
    detail_path = store / "accounts/bob.json"
    detail = json.loads(detail_path.read_bytes())
    detail["token"].update(expiry_timestamp=1_790_004_000, access_token="updated")
    detail_path.write_text(json.dumps(detail))
    adapter = create_adapter_with_second_home(home, "agy")
    assert adapter.default_isolation.home_dir == str(home)
    assert json.loads(path.read_bytes())["token"]["access_token"] == "updated"
    local = json.loads(path.read_bytes())
    local["token"].update(expiry="2040-01-01T00:00:00Z", access_token="agy-refreshed")
    path.write_text(json.dumps(local))
    invoke(monkeypatch, capsys, "env", "sync", "work")
    assert json.loads(path.read_bytes()) == local
    assert session.read_bytes() == b"session"
    assert not (Path.home() / TOKEN_PATH).exists()
    assert "bob@example.org" in invoke(monkeypatch, capsys, "env", "status", "work")
    with pytest.raises(SystemExit):
        invoke(monkeypatch, capsys, "env", "import", "work", "cockpit", "--agent", "agy", "--account", "alice")
    assert "--overwrite" in capsys.readouterr().err
    assert json.loads(path.read_bytes()) == local
    with sqlite3.connect(store / "antigravity_accounts.sqlite") as db:
        db.execute("DELETE FROM summaries WHERE account_id='bob'")
    with pytest.raises(ValueError, match="missing"):
        create_adapter_with_second_home(home, "agy")


def test_unlinked_home_works_without_store_and_clone_requires_auth(store, monkeypatch, capsys):
    invoke(monkeypatch, capsys, "env", "import", "work", "cockpit", "--agent", "agy", "--account", "2", "--no-link")
    home = Path(get_environment("work")["home_dir"])
    store.rename(store.with_name("old-store"))
    assert create_adapter_with_second_home(home, "agy").default_isolation.env_overrides["SSH_CLIENT"]
    plain = clone_home_env("work", "plain")
    assert not (plain / TOKEN_PATH).exists() and "agy_source" not in get_environment("plain")
    with_auth = clone_home_env("work", "copied", with_auth=True)
    assert (with_auth / TOKEN_PATH).read_bytes() == (home / TOKEN_PATH).read_bytes()
    assert get_environment("copied")["agy_source"] == get_environment("work")["agy_source"]


def test_incomplete_or_conflicting_login_stops_before_launch(store, monkeypatch, capsys):
    invoke(monkeypatch, capsys, "env", "import", "work", "cockpit", "--agent", "agy", "--account", "1", "--no-link")
    home = Path(get_environment("work")["home_dir"])
    token_file = home / TOKEN_PATH
    token_file.with_name("settings.json").write_text(json.dumps({"modelProvider": "gemini"}))
    with pytest.raises(ValueError, match="modelProvider"):
        create_adapter_with_second_home(home, "agy")
    token_file.unlink()
    with pytest.raises(ValueError, match="missing"):
        create_adapter_with_second_home(home, "agy")


def test_windows_home_alias_applies_only_to_agy(tmp_path, monkeypatch):
    from clippet.models import IsolationConfig

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", "host-profile")
    isolation = IsolationConfig(home_dir=str(tmp_path))
    with create_isolated_environment(isolation, "agy") as env:
        env.update_env({"USERPROFILE": "other-profile"})
        assert env.env["USERPROFILE"] == env.env["HOME"] == str(tmp_path)
    with create_isolated_environment(isolation, "claude") as env:
        assert env.env["USERPROFILE"] == "host-profile"
    assert os.environ["USERPROFILE"] == "host-profile"
