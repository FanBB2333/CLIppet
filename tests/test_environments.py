"""Tests for named environment profile management."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import clippet.config.environments as env_mod
from clippet.config.environments import (
    add_environment,
    get_environment,
    list_environments,
    load_environments,
    remove_environment,
)


@pytest.fixture(autouse=True)
def _isolate_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the environments file to a temp directory for every test."""
    env_file = tmp_path / "environments.json"
    monkeypatch.setattr(env_mod, "get_environments_file", lambda: env_file)


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
