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
        home_dir = create_home_env("work")
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
        """--from-current copies ~/.claude and ~/.codex into the new env."""
        fake_home = tmp_path / "fake-home"
        (fake_home / ".claude").mkdir(parents=True)
        (fake_home / ".claude" / "settings.json").write_text('{"k":1}')
        (fake_home / ".codex").mkdir()
        (fake_home / ".codex" / "auth.json").write_text("{}")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        home_dir = create_home_env("seeded", from_current=True)

        assert (home_dir / ".claude" / "settings.json").read_text() == '{"k":1}'
        assert (home_dir / ".codex" / "auth.json").exists()

    def test_create_home_env_seeded_filtered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--agents restricts which subtrees are seeded."""
        fake_home = tmp_path / "fake-home"
        (fake_home / ".claude").mkdir(parents=True)
        (fake_home / ".codex").mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        home_dir = create_home_env(
            "partial", from_current=True, agents=["claude"]
        )

        assert (home_dir / ".claude").exists()
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
        src_home = create_home_env("base")
        (src_home / "marker").write_text("hi")

        dst_home = clone_home_env("base", "copy")

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
