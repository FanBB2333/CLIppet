"""Tests for identity isolation: env scrubbing, credential sync-back, seeding,
Codex account import/sync and Codex config detection."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

import clippet.config.environments as env_mod
from clippet.adapters.base import create_isolated_environment
from clippet.adapters.codex import CodexAdapter
from clippet.auth.codex import extract_codex_credentials
from clippet.config.detector import (
    create_adapter_from_config_file,
    create_adapter_with_second_home,
    detect_config_type,
)
from clippet.config.environments import (
    EnvironmentRegistryError,
    clone_home_env,
    create_home_env,
    create_home_env_with_report,
    get_environment,
    import_codex_credential,
    list_environments,
    read_env_codex_credential,
    remove_environment,
    save_environments,
    sync_linked_codex_credential,
)
from clippet.isolation import (
    ContentCredentialProvider,
    CredentialSyncBack,
    IsolatedEnvironment,
    copy_tree,
    ensure_shared_links,
)
from clippet.models import IsolationConfig
from tests.codex_fixtures import cockpit_account, cockpit_export, native_auth


@pytest.fixture()
def clippet_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".clippet"
    monkeypatch.setattr(env_mod, "get_clippet_root", lambda: root)
    return root


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "real-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


# --- isolation ---------------------------------------------------------------


class TestEnvScrubbing:
    def test_codex_identity_and_home_pointers_are_dropped(self, monkeypatch) -> None:
        monkeypatch.setenv("CODEX_HOME", "/real/.codex")
        monkeypatch.setenv("CODEX_API_KEY", "sk-parent")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example")
        monkeypatch.setenv("XDG_CONFIG_HOME", "/real/.config")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "kept-for-codex")
        monkeypatch.setenv("PATH_MARKER", "kept")

        iso = IsolationConfig(env_overrides={"OPENAI_API_KEY": "sk-explicit"})
        with create_isolated_environment(iso, "codex") as env:
            assert "CODEX_HOME" not in env.env
            assert "CODEX_API_KEY" not in env.env
            assert "OPENAI_BASE_URL" not in env.env
            assert "XDG_CONFIG_HOME" not in env.env
            # Unrelated variables and explicit overrides survive
            assert env.env["ANTHROPIC_API_KEY"] == "kept-for-codex"
            assert env.env["PATH_MARKER"] == "kept"
        with create_isolated_environment(iso, "codex") as env:
            env.update_env({"OPENAI_API_KEY": "sk-explicit"})
            assert env.env["OPENAI_API_KEY"] == "sk-explicit"

    def test_claude_prefix_scrub(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://parent")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "parent")
        with create_isolated_environment(IsolationConfig(), "claude") as env:
            assert not any(key.startswith("ANTHROPIC_") for key in env.env)

    def test_opt_out_keeps_variables(self, monkeypatch) -> None:
        monkeypatch.setenv("CODEX_HOME", "/real/.codex")
        iso = IsolationConfig(scrub_agent_env=False)
        with create_isolated_environment(iso, "codex") as env:
            assert env.env["CODEX_HOME"] == "/real/.codex"


class TestCredentialFiles:
    def test_content_provider_writes_private_file(self) -> None:
        with IsolatedEnvironment() as env:
            ContentCredentialProvider({".codex/auth.json": "{}"}).inject(env)
            path = env.home_dir / ".codex" / "auth.json"
            assert path.read_text() == "{}"
            assert (path.stat().st_mode & 0o777) == 0o600

    def test_sync_back_only_writes_changed_files(self, tmp_path: Path) -> None:
        owner = tmp_path / "owner-auth.json"
        with IsolatedEnvironment() as env:
            ContentCredentialProvider({".codex/auth.json": "v1"}).inject(env)
            sync = CredentialSyncBack({".codex/auth.json": str(owner)})
            sync.snapshot(env)
            assert sync.finalize(env) == []
            assert not owner.exists()

            (env.home_dir / ".codex" / "auth.json").write_text("v2-refreshed")
            assert sync.finalize(env) == [owner]
        assert owner.read_text() == "v2-refreshed"
        assert (owner.stat().st_mode & 0o777) == 0o600

    def test_copy_tree_skips_sockets(self, tmp_path: Path, monkeypatch) -> None:
        src = tmp_path / "src"
        (src / "ipc").mkdir(parents=True)
        (src / "config.toml").write_text("x")
        server = socket.socket(socket.AF_UNIX)
        # Bind via a relative path: AF_UNIX paths are limited to ~104 bytes.
        monkeypatch.chdir(src / "ipc")
        server.bind("ipc.sock")
        monkeypatch.chdir(tmp_path)
        try:
            copy_tree(src, tmp_path / "dst")
        finally:
            server.close()
        assert (tmp_path / "dst" / "config.toml").exists()
        assert not (tmp_path / "dst" / "ipc" / "ipc.sock").exists()

    def test_shared_links_never_replace_files(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        (real / ".config" / "git").mkdir(parents=True)
        (real / ".gitconfig").write_text("[user]\n")
        env_home = tmp_path / "env"
        (env_home).mkdir()
        (env_home / ".config").mkdir()

        linked = ensure_shared_links(env_home, [".gitconfig", ".config/git", ".missing"], real)
        assert linked == [".gitconfig", ".config/git"]
        assert (env_home / ".gitconfig").is_symlink()
        assert ensure_shared_links(env_home, [".gitconfig"], real) == []


# --- registry robustness ------------------------------------------------------


class TestRegistry:
    def test_corrupt_registry_is_reported_not_wiped(self, clippet_root: Path) -> None:
        clippet_root.mkdir(parents=True)
        registry = clippet_root / "environments.json"
        registry.write_text("{ broken")
        with pytest.raises(EnvironmentRegistryError):
            list_environments()
        with pytest.raises(EnvironmentRegistryError):
            create_home_env("work")
        assert registry.read_text() == "{ broken"

    def test_reserved_and_invalid_names(self, clippet_root: Path) -> None:
        for bad in ("base", "a b", "x/y", ".hidden"):
            with pytest.raises(ValueError):
                create_home_env(bad, share=[])

    def test_unknown_seed_agent_is_rejected(self, clippet_root: Path, fake_home: Path) -> None:
        with pytest.raises(ValueError, match="Unknown agent"):
            create_home_env("w", from_current=True, agents=["claud"])
        assert not (clippet_root / "envs" / "w").exists()

    def test_purge_refuses_directories_outside_envs_root(
        self, clippet_root: Path, tmp_path: Path
    ) -> None:
        outside = tmp_path / "precious"
        outside.mkdir()
        save_environments({"evil": {"type": "home", "home_dir": str(outside)}})
        with pytest.raises(ValueError, match="Refusing to purge"):
            remove_environment("evil", purge=True)
        assert outside.is_dir()
        assert "evil" in list_environments()

    def test_env_home_is_private(self, clippet_root: Path) -> None:
        home = create_home_env("private", share=[])
        assert (home.stat().st_mode & 0o777) == 0o700

    def test_default_share_links_gitconfig(self, clippet_root: Path, fake_home: Path) -> None:
        (fake_home / ".gitconfig").write_text("[user]\n  name = me\n")
        home, report = create_home_env_with_report("work")
        assert report.linked == [".gitconfig"]
        assert (home / ".gitconfig").is_symlink()
        assert get_environment("work")["share"] == [".gitconfig", ".config/git"]


class TestSeeding:
    def test_runtime_state_and_credentials_are_not_copied(
        self, clippet_root: Path, fake_home: Path
    ) -> None:
        codex = fake_home / ".codex"
        (codex / "sessions" / "2026").mkdir(parents=True)
        (codex / "sessions" / "2026" / "rollout.jsonl").write_text("{}")
        (codex / "skills" / "mine").mkdir(parents=True)
        (codex / "skills" / "mine" / "SKILL.md").write_text("# skill")
        (codex / "config.toml").write_text('model = "gpt-x"\n')
        (codex / "auth.json").write_text(json.dumps(native_auth()))
        (codex / "logs_2.sqlite").write_text("db")
        claude_json = {"mcpServers": {"x": {}}, "oauthAccount": {"emailAddress": "a@b"}, "primaryApiKey": "sk"}
        (fake_home / ".claude.json").write_text(json.dumps(claude_json))

        home, report = create_home_env_with_report("work", from_current=True, share=[])

        assert (home / ".codex" / "config.toml").exists()
        assert (home / ".codex" / "skills" / "mine" / "SKILL.md").exists()
        assert not (home / ".codex" / "sessions").exists()
        assert not (home / ".codex" / "logs_2.sqlite").exists()
        assert not (home / ".codex" / "auth.json").exists()
        assert ".codex/auth.json" in report.skipped_credentials
        copied_claude = json.loads((home / ".claude.json").read_text())
        assert copied_claude == {"mcpServers": {"x": {}}}

    def test_failed_seed_rolls_back(self, clippet_root: Path, fake_home: Path, monkeypatch) -> None:
        (fake_home / ".codex").mkdir()
        (fake_home / ".codex" / "config.toml").write_text("x")

        def boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(env_mod, "seed_from_home", boom)
        with pytest.raises(OSError):
            create_home_env("work", from_current=True)
        assert not (clippet_root / "envs" / "work").exists()
        assert "work" not in list_environments()
        create_home_env("work", share=[])  # a retry is not blocked

    def test_clone_strips_credentials_unless_requested(self, clippet_root: Path) -> None:
        src = create_home_env("origin", share=[])
        (src / ".codex").mkdir()
        (src / ".codex" / "auth.json").write_text("{}")
        (src / ".codex" / "history.jsonl").write_text("h")

        plain = clone_home_env("origin", "copy")
        assert (plain / ".codex" / "history.jsonl").exists()
        assert not (plain / ".codex" / "auth.json").exists()

        authed = clone_home_env("origin", "copy2", with_auth=True)
        assert (authed / ".codex" / "auth.json").exists()


# --- Codex account import / linked sync ----------------------------------------


def _cred(email="alice@example.com", account="acct-alice", **kw):
    return extract_codex_credentials(cockpit_export(cockpit_account(email, account, **kw)))[0]


class TestCodexImport:
    def test_import_creates_env_with_native_auth(self, clippet_root: Path, fake_home: Path) -> None:
        (fake_home / ".codex").mkdir()
        (fake_home / ".codex" / "config.toml").write_text('model = "gpt-x"\n')
        cred = _cred()

        home, created = import_codex_credential("alice", cred, source="/tmp/export.json")

        assert created is True
        auth = home / ".codex" / "auth.json"
        assert (auth.stat().st_mode & 0o777) == 0o600
        stored = json.loads(auth.read_text())
        assert stored["auth_mode"] == "chatgpt"
        assert stored["tokens"]["account_id"] == "acct-alice"
        assert (home / ".codex" / "config.toml").exists()  # config seeded
        record = get_environment("alice")["codex_source"]
        assert record["format"] == "cockpit-export"
        assert record["linked"] is False
        assert read_env_codex_credential(home).display_email == "alice@example.com"

    def test_import_refuses_to_replace_other_account(self, clippet_root: Path, fake_home: Path) -> None:
        import_codex_credential("acct", _cred(), source="s", seed_config=False)
        with pytest.raises(FileExistsError, match="--overwrite"):
            import_codex_credential("acct", _cred("bob@example.com", "acct-bob"), source="s")
        # Same account: refresh allowed without --overwrite
        import_codex_credential("acct", _cred(), source="s")
        import_codex_credential("acct", _cred("bob@example.com", "acct-bob"), source="s", overwrite=True)
        home = Path(get_environment("acct")["home_dir"])
        assert read_env_codex_credential(home).display_email == "bob@example.com"

    def test_linked_sync_pulls_newer_login(self, clippet_root: Path, fake_home: Path, tmp_path: Path) -> None:
        export = tmp_path / "export.json"
        old = cockpit_account(
            "alice@example.com", "acct-alice", issued_at=1_790_000_000,
            token_updated_at=1_790_000_000,
        )
        export.write_text(json.dumps(cockpit_export(old)))
        cred = extract_codex_credentials(json.loads(export.read_text()))[0]
        import_codex_credential("alice", cred, source=str(export), link=True, seed_config=False)
        profile = get_environment("alice")

        assert sync_linked_codex_credential("alice", profile).action == "unchanged"

        new = cockpit_account(
            "alice@example.com", "acct-alice", issued_at=1_790_500_000,
            token_updated_at=1_790_500_000,
        )
        export.write_text(json.dumps(cockpit_export(new)))
        result = sync_linked_codex_credential("alice", profile)
        assert result.action == "updated"
        home = Path(profile["home_dir"])
        assert read_env_codex_credential(home).refresh_token == new["tokens"]["refresh_token"]

        # If Codex refreshed inside the env, the env copy is newer: never overwrite it
        newest = cockpit_account(
            "alice@example.com", "acct-alice", issued_at=1_791_000_000,
            token_updated_at=1_791_000_000,
        )
        env_cred = extract_codex_credentials(cockpit_export(newest))[0]
        env_mod.write_env_codex_credential(home, env_cred)
        assert sync_linked_codex_credential("alice", profile).action == "env-newer"
        assert read_env_codex_credential(home).refresh_token == newest["tokens"]["refresh_token"]

    def test_unlinked_env_is_skipped(self, clippet_root: Path) -> None:
        create_home_env("plain", share=[])
        assert sync_linked_codex_credential("plain", get_environment("plain")).action == "skipped"


# --- detection / adapter factories ----------------------------------------------


class TestCodexDetection:
    def test_chatgpt_auth_json_no_longer_crashes(self, tmp_path: Path) -> None:
        """Regression: OPENAI_API_KEY: null used to end up in env_overrides."""
        path = tmp_path / "auth.json"
        path.write_text(json.dumps(native_auth()))
        assert detect_config_type(path) == "codex"
        adapter = create_adapter_from_config_file(path)
        assert isinstance(adapter, CodexAdapter)
        assert adapter.default_isolation.env_overrides == {}
        assert adapter.config_overrides["cli_auth_credentials_store"] == '"file"'

    def test_foreign_export_is_converted_and_selected(self, tmp_path: Path, clippet_root: Path) -> None:
        path = tmp_path / "backup.json"
        path.write_text(json.dumps(cockpit_export(
            cockpit_account("alice@example.com", "acct-alice"),
            cockpit_account("bob@example.com", "acct-bob"),
        )))
        assert detect_config_type(path) == "codex"
        with pytest.raises(ValueError, match="--account"):
            create_adapter_from_config_file(path)

        adapter = create_adapter_from_config_file(path, account="bob")
        iso = adapter.default_isolation
        assert iso.credential_files == {} or ".codex/auth.json" not in iso.credential_files
        auth = json.loads(iso.credential_contents[".codex/auth.json"].get_secret_value())
        assert auth["tokens"]["account_id"] == "acct-bob"
        assert "rt_acct-bob" not in repr(iso)  # SecretStr keeps tokens out of repr
        assert iso.sync_back[".codex/auth.json"].endswith("bob@example.com.auth.json")

    def test_top_level_array_and_jsonl(self, tmp_path: Path) -> None:
        array = tmp_path / "accounts.json"
        array.write_text(json.dumps([cockpit_account("a@example.com", "acct-a")]))
        assert detect_config_type(array) == "codex"
        jsonl = tmp_path / "accounts.jsonl"
        jsonl.write_text(json.dumps(cockpit_account("a@example.com", "acct-a")) + "\n")
        assert detect_config_type(jsonl) == "codex"

    def test_second_home_forces_file_store_when_auth_exists(self, tmp_path: Path) -> None:
        home = tmp_path / "env"
        (home / ".codex").mkdir(parents=True)
        bare = create_adapter_with_second_home(home, "codex")
        assert not bare.config_overrides
        (home / ".codex" / "auth.json").write_text(json.dumps(native_auth()))
        adapter = create_adapter_with_second_home(home, "codex")
        assert adapter.config_overrides["cli_auth_credentials_store"] == '"file"'
        assert adapter.model is None and adapter.sandbox is None

    def test_cockpit_dir_is_not_a_second_home(self, tmp_path: Path) -> None:
        root = tmp_path / "cockpit"
        root.mkdir()
        (root / "codex_accounts.json").write_text("{}")
        assert detect_config_type(root) == "codex"
