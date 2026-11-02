"""Tests for named environment profile management."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import clippet.config.environments as env_mod
from clippet.config.environments import (
    ENV_TYPE_FILE,
    ENV_TYPE_HOME,
    add_environment,
    clone_home_env,
    create_home_env,
    create_home_env_with_report,
    entry_type,
    env_home_path,
    get_environment,
    list_environments,
    load_environments,
    remove_environment,
)


@pytest.fixture(autouse=True)
def _isolate_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the environments file and envs root to a temp directory."""
    clippet_root = tmp_path / ".clippet"
    monkeypatch.setattr(env_mod, "get_clippet_root", lambda: clippet_root)


class TestEnvironments:
    """Tests for environment CRUD operations."""

    def test_load_empty(self) -> None:
        """When no file exists, returns empty dict."""
        assert load_environments() == {}

    def test_add_and_get(self, tmp_path: Path) -> None:
        """Add an environment, then retrieve it."""
        # Create a dummy config file so add_environment doesn't raise
        cfg = tmp_path / "my-config.json"
        cfg.write_text("{}")

        add_environment("dev", cfg)
        profile = get_environment("dev")

        assert profile["config_path"] == str(cfg.resolve())

    def test_add_nonexistent_config_path(self) -> None:
        """Adding env with nonexistent config path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Config file does not exist"):
            add_environment("bad", "/no/such/file.json")

    def test_get_missing_raises(self) -> None:
        """Getting nonexistent environment raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            get_environment("nonexistent")

    def test_remove_environment(self, tmp_path: Path) -> None:
        """Add then remove, verify it's gone."""
        cfg = tmp_path / "cfg.json"
        cfg.write_text("{}")

        add_environment("staging", cfg)
        assert "staging" in list_environments()

        remove_environment("staging")
        assert "staging" not in list_environments()

    def test_remove_missing_raises(self) -> None:
        """Removing nonexistent raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            remove_environment("ghost")

    def test_list_environments(self, tmp_path: Path) -> None:
        """Add multiple, list returns all."""
        for name in ("alpha", "beta", "gamma"):
            cfg = tmp_path / f"{name}.json"
            cfg.write_text("{}")
            add_environment(name, cfg)

        envs = list_environments()
        assert set(envs.keys()) == {"alpha", "beta", "gamma"}

    def test_add_marks_file_type(self, tmp_path: Path) -> None:
        """Legacy add_environment records type='file'."""
        cfg = tmp_path / "cfg.json"
        cfg.write_text("{}")
        add_environment("legacy", cfg)
        assert entry_type(get_environment("legacy")) == ENV_TYPE_FILE


class TestHomeEnv:
    """Tests for HOME-container env operations."""

    def test_create_home_env_empty(self) -> None:
        """create_home_env without --from-current makes an empty HOME dir."""
        home_dir = create_home_env("work", share=[])
        assert home_dir.is_dir()
        assert home_dir == env_home_path("work")
        # Empty: no .claude/.codex/.gemini inside
        assert not any(home_dir.iterdir())

        profile = get_environment("work")
        assert entry_type(profile) == ENV_TYPE_HOME
        assert profile["home_dir"] == str(home_dir.resolve())

    def test_create_home_env_seeded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--from-current copies agent configuration but not credentials."""
        fake_home = tmp_path / "fake-home"
        (fake_home / ".claude").mkdir(parents=True)
        (fake_home / ".claude" / "settings.json").write_text('{"k":1}')
        (fake_home / ".codex").mkdir()
        (fake_home / ".codex" / "auth.json").write_text("{}")
        (fake_home / ".codex" / "config.toml").write_text('model = "m"\n')
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        home_dir = create_home_env("seeded", from_current=True)

        assert (home_dir / ".claude" / "settings.json").read_text() == '{"k":1}'
        assert (home_dir / ".codex" / "config.toml").exists()
        # OAuth credentials are not copied unless explicitly requested
        assert not (home_dir / ".codex" / "auth.json").exists()

    def test_create_home_env_seeded_with_auth(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """with_auth=True also copies credential files (0600)."""
        fake_home = tmp_path / "fake-home"
        (fake_home / ".codex").mkdir(parents=True)
        (fake_home / ".codex" / "auth.json").write_text("{}")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        home_dir = create_home_env("authed", from_current=True, with_auth=True)

        auth = home_dir / ".codex" / "auth.json"
        assert auth.exists()
        assert (auth.stat().st_mode & 0o777) == 0o600

    def test_create_home_env_seeds_qodercli_config_not_login(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--from-current copies Qoder CLI's settings, not its login, BYOK keys or sessions."""
        fake_home = tmp_path / "fake-home"
        qoder = fake_home / ".qoder"
        (qoder / "skills" / "review").mkdir(parents=True)
        (qoder / "skills" / "review" / "SKILL.md").write_text("review")
        (qoder / "AGENTS.md").write_text("be brief")
        (qoder / "settings.json").write_text(json.dumps({
            "permissions": {"allow": ["Read"]},
            "modelConfigs": {"default": "performance", "customModels": [{"apiKey": "sk-byok"}]},
        }))
        (qoder / ".auth").mkdir()
        (qoder / ".auth" / "user").write_text("secret")
        (qoder / ".models" / "uid").mkdir(parents=True)
        (qoder / ".models" / "uid" / "customs").write_text("encrypted")
        (qoder / "projects" / "p").mkdir(parents=True)
        (fake_home / ".qoder-cli" / "ai-stats").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        home_dir, report = create_home_env_with_report("with-qoder", from_current=True, agents=["qodercli"])

        assert (home_dir / ".qoder" / "AGENTS.md").read_text() == "be brief"
        assert (home_dir / ".qoder" / "skills" / "review" / "SKILL.md").is_file()
        settings = json.loads((home_dir / ".qoder" / "settings.json").read_text())
        assert settings == {"permissions": {"allow": ["Read"]}, "modelConfigs": {"default": "performance"}}
        for left_behind in (".qoder/.auth", ".qoder/.models", ".qoder/projects", ".qoder-cli"):
            assert not (home_dir / left_behind).exists(), left_behind
        assert ".qoder/.auth" in report.skipped_credentials
        assert ".qoder/settings.json: modelConfigs.customModels" in report.skipped_credentials

    def test_create_home_env_seeds_qodercli_login_with_auth(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--with-auth also copies Qoder CLI's login and BYOK models, privately."""
        fake_home = tmp_path / "fake-home"
        (fake_home / ".qoder" / ".auth").mkdir(parents=True)
        (fake_home / ".qoder" / ".auth" / "user").write_text("secret")
        (fake_home / ".qoder" / ".keychain-salt").write_text("salt")
        (fake_home / ".claude").mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        home_dir = create_home_env("qoder-auth", from_current=True, with_auth=True, agents=["qodercli"])

        assert (home_dir / ".qoder" / ".auth" / "user").read_text() == "secret"
        assert (home_dir / ".qoder" / ".keychain-salt").stat().st_mode & 0o777 == 0o600
        assert (home_dir / ".qoder" / ".auth").stat().st_mode & 0o777 == 0o700
        assert not (home_dir / ".claude").exists()

    def test_create_home_env_seeded_filtered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--agents restricts which subtrees are seeded."""
        fake_home = tmp_path / "fake-home"
        (fake_home / ".claude").mkdir(parents=True)
        (fake_home / ".claude" / "settings.json").write_text("{}")
        (fake_home / ".codex").mkdir()
        (fake_home / ".codex" / "config.toml").write_text("")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        home_dir = create_home_env(
            "partial", from_current=True, agents=["claude"]
        )

        assert (home_dir / ".claude" / "settings.json").exists()
        assert not (home_dir / ".codex").exists()

    def test_create_home_env_existing_without_overwrite(self) -> None:
        create_home_env("dup")
        with pytest.raises(FileExistsError):
            create_home_env("dup")

    def test_create_home_env_overwrite(self) -> None:
        first = create_home_env("redo")
        (first / "marker").write_text("v1")
        second = create_home_env("redo", overwrite=True)
        assert second == first
        assert not (second / "marker").exists()

    def test_create_home_env_invalid_name(self) -> None:
        for bad in ("", ".hidden", "../escape", "foo/bar"):
            with pytest.raises(ValueError):
                create_home_env(bad)

    def test_clone_home_env(self) -> None:
        src_home = create_home_env("origin")
        (src_home / "marker").write_text("hi")

        dst_home = clone_home_env("origin", "copy")

        assert dst_home != src_home
        assert (dst_home / "marker").read_text() == "hi"
        assert entry_type(get_environment("copy")) == ENV_TYPE_HOME

    def test_clone_rejects_file_entry(self, tmp_path: Path) -> None:
        cfg = tmp_path / "cfg.json"
        cfg.write_text("{}")
        add_environment("legacy", cfg)
        with pytest.raises(ValueError, match="cannot be cloned"):
            clone_home_env("legacy", "newname")

    def test_remove_home_env_keeps_directory_by_default(self) -> None:
        home_dir = create_home_env("retain")
        remove_environment("retain")
        assert "retain" not in list_environments()
        assert home_dir.is_dir()  # NOT purged

    def test_remove_home_env_with_purge(self) -> None:
        home_dir = create_home_env("toss")
        remove_environment("toss", purge=True)
        assert "toss" not in list_environments()
        assert not home_dir.exists()

    def test_entry_type_legacy_inference(self) -> None:
        """Old entries with no 'type' field still resolve correctly."""
        assert entry_type({"config_path": "/x"}) == ENV_TYPE_FILE
        assert entry_type({"home_dir": "/x"}) == ENV_TYPE_HOME


def test_clippet_home_env_var_relocates_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # drop the autouse redirect for this test
    monkeypatch.setenv("CLIPPET_HOME", str(tmp_path / "state"))
    assert env_mod.get_clippet_root() == tmp_path / "state"
    assert env_mod.get_envs_root() == tmp_path / "state" / "envs"
