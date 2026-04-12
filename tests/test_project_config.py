"""Tests for project-level .clippet.json configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clippet.config.project import (
    find_project_config,
    load_project_config,
    resolve_project_launch,
)


def test_find_project_config_walks_up_to_git_root(tmp_path: Path) -> None:
    """Test that find_project_config walks up from nested dirs to find .clippet.json."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".clippet.json").write_text(
        json.dumps({
            "version": 1,
            "agents": {
                "claude": {"config_path": ".clippet.local/claude.json"}
            },
        }),
        encoding="utf-8",
    )
    nested = repo / "src" / "pkg"
    nested.mkdir(parents=True)

    assert find_project_config(nested) == repo / ".clippet.json"


def test_find_project_config_returns_none_when_no_config(tmp_path: Path) -> None:
    """Test that find_project_config returns None when no .clippet.json exists."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "src" / "pkg"
    nested.mkdir(parents=True)

    assert find_project_config(nested) is None


def test_find_project_config_stops_at_git_root(tmp_path: Path) -> None:
    """Test that find_project_config stops searching at Git root, not beyond."""
    # Create a .clippet.json outside the git repo
    (tmp_path / ".clippet.json").write_text(
        json.dumps({
            "version": 1,
            "agents": {"claude": {"config_path": "should-not-be-found.json"}},
        }),
        encoding="utf-8",
    )
    
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    
    # Should NOT find the .clippet.json above the git root
    assert find_project_config(repo) is None


def test_find_project_config_checks_current_dir_outside_git(tmp_path: Path) -> None:
    """Test that when not in a git repo, only the current directory is checked."""
    # No .git anywhere
    (tmp_path / ".clippet.json").write_text(
        json.dumps({
            "version": 1,
            "agents": {"codex": {"config_path": "auth.json"}},
        }),
        encoding="utf-8",
    )
    
    # Found in current dir
    assert find_project_config(tmp_path) == tmp_path / ".clippet.json"
    
    # Not found from nested dir (no git root to walk up to)
    nested = tmp_path / "nested"
    nested.mkdir()
    assert find_project_config(nested) is None


def test_resolve_project_launch_rejects_relative_escape(tmp_path: Path) -> None:
    """Test that paths using .. to escape project root are rejected."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    config_file = repo / ".clippet.json"
    config_file.write_text(
        json.dumps({
            "version": 1,
            "agents": {
                "claude": {"config_path": "../secret.json"}
            },
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must stay inside the project root"):
        resolve_project_launch(repo, "claude")


def test_load_project_config_rejects_unknown_fields(tmp_path: Path) -> None:
    """Test that extra fields in config (potential inline secrets) are rejected."""
    config_file = tmp_path / ".clippet.json"
    config_file.write_text(
        json.dumps({
            "version": 1,
            "agents": {
                "codex": {
                    "config_path": ".clippet.local/auth.json",
                    "OPENAI_API_KEY": "should-not-be-inline",
                }
            },
        }),
        encoding="utf-8",
    )

    with pytest.raises(Exception):
        load_project_config(config_file)


def test_load_project_config_rejects_invalid_version(tmp_path: Path) -> None:
    """Test that unsupported version numbers are rejected."""
    config_file = tmp_path / ".clippet.json"
    config_file.write_text(
        json.dumps({
            "version": 999,
            "agents": {"claude": {"config_path": "config.json"}},
        }),
        encoding="utf-8",
    )

    with pytest.raises(Exception):
        load_project_config(config_file)


def test_resolve_project_launch_codex_with_both_paths(tmp_path: Path) -> None:
    """Test resolving codex launch with both config_path and codex_config_path."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".clippet.local").mkdir()
    (repo / ".clippet.local" / "auth.json").write_text("{}", encoding="utf-8")
    (repo / ".clippet.local" / "config.toml").write_text("", encoding="utf-8")
    
    config_file = repo / ".clippet.json"
    config_file.write_text(
        json.dumps({
            "version": 1,
            "agents": {
                "codex": {
                    "config_path": ".clippet.local/auth.json",
                    "codex_config_path": ".clippet.local/config.toml",
                }
            },
        }),
        encoding="utf-8",
    )

    result = resolve_project_launch(repo, "codex")
    
    assert result.agent_type == "codex"
    assert result.project_root == repo
    assert result.config_file == config_file
    assert result.config_path == str(repo / ".clippet.local" / "auth.json")
    assert result.codex_config_path == str(repo / ".clippet.local" / "config.toml")


def test_resolve_project_launch_claude_with_config_path(tmp_path: Path) -> None:
    """Test resolving claude launch with only config_path."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".clippet.local").mkdir()
    (repo / ".clippet.local" / "claude.json").write_text("{}", encoding="utf-8")
    
    config_file = repo / ".clippet.json"
    config_file.write_text(
        json.dumps({
            "version": 1,
            "agents": {
                "claude": {"config_path": ".clippet.local/claude.json"}
            },
        }),
        encoding="utf-8",
    )

    result = resolve_project_launch(repo, "claude")
    
    assert result.agent_type == "claude"
    assert result.project_root == repo
    assert result.config_path == str(repo / ".clippet.local" / "claude.json")
    assert result.codex_config_path is None


def test_resolve_project_launch_missing_agent_config(tmp_path: Path) -> None:
    """Test that requesting an agent not in config raises KeyError."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    config_file = repo / ".clippet.json"
    config_file.write_text(
        json.dumps({
            "version": 1,
            "agents": {
                "claude": {"config_path": ".clippet.local/claude.json"}
            },
        }),
        encoding="utf-8",
    )

    with pytest.raises(KeyError, match="codex"):
        resolve_project_launch(repo, "codex")


def test_resolve_project_launch_absolute_path_allowed(tmp_path: Path) -> None:
    """Test that absolute paths are allowed and resolved correctly."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    
    # Create a config file outside the repo with absolute path reference
    external_config = tmp_path / "external" / "auth.json"
    external_config.parent.mkdir(parents=True)
    external_config.write_text("{}", encoding="utf-8")
    
    config_file = repo / ".clippet.json"
    config_file.write_text(
        json.dumps({
            "version": 1,
            "agents": {
                "codex": {"config_path": str(external_config)}
            },
        }),
        encoding="utf-8",
    )

    result = resolve_project_launch(repo, "codex")
    
    assert result.config_path == str(external_config)


def test_resolve_project_launch_tilde_expansion(tmp_path: Path) -> None:
    """Test that paths with ~ are expanded correctly."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    
    config_file = repo / ".clippet.json"
    config_file.write_text(
        json.dumps({
            "version": 1,
            "agents": {
                "codex": {"config_path": "~/.codex/auth.json"}
            },
        }),
        encoding="utf-8",
    )

    result = resolve_project_launch(repo, "codex")
    
    # The path should be expanded, not contain ~
    assert "~" not in result.config_path
    assert result.config_path == str(Path.home() / ".codex" / "auth.json")


def test_load_project_config_requires_at_least_one_path(tmp_path: Path) -> None:
    """Test that at least one config path must be provided per agent."""
    config_file = tmp_path / ".clippet.json"
    config_file.write_text(
        json.dumps({
            "version": 1,
            "agents": {
                "codex": {}  # No paths provided
            },
        }),
        encoding="utf-8",
    )

    with pytest.raises(Exception):
        load_project_config(config_file)
