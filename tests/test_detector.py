"""Tests for config format detection and adapter creation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clippet.adapters.claude import ClaudeAdapter
from clippet.adapters.codex import CodexAdapter
from clippet.config.detector import (
    create_adapter_from_claude_config,
    create_adapter_from_codex_config,
    create_adapter_from_config_file,
    detect_config_type,
)
from clippet.models import IsolationConfig


class TestDetectConfigType:
    """Tests for detect_config_type()."""

    def test_detect_clippet_config(self, tmp_path: Path) -> None:
        """JSON with 'adapters' list -> returns 'clippet'."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"adapters": [{"adapter_type": "claude"}]}))

        assert detect_config_type(cfg) == "clippet"

    def test_detect_claude_code_config(self, tmp_path: Path) -> None:
        """JSON with 'env' containing ANTHROPIC_* keys -> returns 'claude_code'."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "env": {"ANTHROPIC_API_KEY": "sk-ant-test"},
        }))

        assert detect_config_type(cfg) == "claude_code"

    def test_detect_claude_code_config_by_keys(self, tmp_path: Path) -> None:
        """JSON with 'effortLevel' or 'skipDangerousModePermissionPrompt' -> 'claude_code'."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"effortLevel": "high"}))
        assert detect_config_type(cfg) == "claude_code"

        cfg2 = tmp_path / "config2.json"
        cfg2.write_text(json.dumps({"skipDangerousModePermissionPrompt": True}))
        assert detect_config_type(cfg2) == "claude_code"

    def test_detect_codex_config(self, tmp_path: Path) -> None:
        """JSON with 'OPENAI_API_KEY' -> returns 'codex'."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"OPENAI_API_KEY": "sk-openai-test"}))

        assert detect_config_type(cfg) == "codex"

    def test_detect_unknown_raises(self, tmp_path: Path) -> None:
        """JSON without any recognized keys -> raises ValueError."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"random_key": "value"}))

        with pytest.raises(ValueError, match="Unrecognizable config format"):
            detect_config_type(cfg)


class TestCreateAdapterFromClaudeConfig:
    """Tests for create_adapter_from_claude_config()."""

    @pytest.fixture()
    def claude_config(self, tmp_path: Path) -> Path:
        """Create a minimal Claude Code config file."""
        cfg = tmp_path / "glm4-7-base.json"
        cfg.write_text(json.dumps({
            "env": {
                "ANTHROPIC_API_KEY": "sk-ant-test",
                "ANTHROPIC_MODEL": "claude-sonnet-4-20250514",
            },
            "permissions": {"allow": []},
        }))
        return cfg

    def test_creates_claude_adapter(self, claude_config: Path) -> None:
        """Should return a ClaudeAdapter instance."""
        adapter = create_adapter_from_claude_config(claude_config)
        assert isinstance(adapter, ClaudeAdapter)

    def test_isolation_config_set(self, claude_config: Path) -> None:
        """Verify default_isolation has correct credential_files and env_overrides."""
        adapter = create_adapter_from_claude_config(claude_config)
        iso = adapter.default_isolation

        assert isinstance(iso, IsolationConfig)
        assert ".claude/settings.json" in iso.credential_files
        assert iso.credential_files[".claude/settings.json"] == str(claude_config.resolve())
        assert iso.env_overrides["ANTHROPIC_API_KEY"] == "sk-ant-test"
        assert iso.env_overrides["ANTHROPIC_MODEL"] == "claude-sonnet-4-20250514"

    def test_model_from_env(self, tmp_path: Path) -> None:
        """Model is extracted from ANTHROPIC_MODEL or ANTHROPIC_DEFAULT_SONNET_MODEL."""
        # ANTHROPIC_MODEL takes precedence
        cfg1 = tmp_path / "cfg1.json"
        cfg1.write_text(json.dumps({
            "env": {"ANTHROPIC_MODEL": "claude-opus"},
        }))
        adapter1 = create_adapter_from_claude_config(cfg1)
        assert adapter1.model == "claude-opus"

        # Falls back to ANTHROPIC_DEFAULT_SONNET_MODEL
        cfg2 = tmp_path / "cfg2.json"
        cfg2.write_text(json.dumps({
            "env": {"ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-fallback"},
        }))
        adapter2 = create_adapter_from_claude_config(cfg2)
        assert adapter2.model == "claude-sonnet-fallback"


class TestCreateAdapterFromCodexConfig:
    """Tests for create_adapter_from_codex_config()."""

    @pytest.fixture()
    def codex_config(self, tmp_path: Path) -> Path:
        """Create a minimal Codex config file."""
        cfg = tmp_path / "auth.json"
        cfg.write_text(json.dumps({
            "OPENAI_API_KEY": "sk-openai-test",
            "OPENAI_BASE_URL": "https://api.openai.com",
        }))
        return cfg

    def test_creates_codex_adapter(self, codex_config: Path) -> None:
        """Should return a CodexAdapter instance."""
        adapter = create_adapter_from_codex_config(codex_config)
        assert isinstance(adapter, CodexAdapter)

    def test_isolation_config_set(self, codex_config: Path) -> None:
        """Verify default_isolation has correct credential_files and env_overrides."""
        adapter = create_adapter_from_codex_config(codex_config)
        iso = adapter.default_isolation

        assert isinstance(iso, IsolationConfig)
        assert ".codex/auth.json" in iso.credential_files
        assert iso.credential_files[".codex/auth.json"] == str(codex_config.resolve())
        assert iso.env_overrides["OPENAI_API_KEY"] == "sk-openai-test"
        assert iso.env_overrides["OPENAI_BASE_URL"] == "https://api.openai.com"


class TestCreateAdapterFromConfigFile:
    """Tests for create_adapter_from_config_file()."""

    def test_auto_detect_claude(self, tmp_path: Path) -> None:
        """Creates adapter from Claude Code config without specifying type."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "env": {"ANTHROPIC_API_KEY": "sk-ant"},
        }))

        adapter = create_adapter_from_config_file(cfg)
        assert isinstance(adapter, ClaudeAdapter)

    def test_auto_detect_codex(self, tmp_path: Path) -> None:
        """Creates adapter from Codex config without specifying type."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"OPENAI_API_KEY": "sk-openai"}))

        adapter = create_adapter_from_config_file(cfg)
        assert isinstance(adapter, CodexAdapter)

    def test_clippet_config_raises(self, tmp_path: Path) -> None:
        """CLIppet composite config raises ValueError."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"adapters": [{"adapter_type": "claude"}]}))

        with pytest.raises(ValueError, match="CLIppet composite format"):
            create_adapter_from_config_file(cfg)

    def test_explicit_agent_type(self, tmp_path: Path) -> None:
        """Explicitly specifying agent_type overrides detection for native configs."""
        # File looks like Claude config, but we force codex
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "env": {"ANTHROPIC_API_KEY": "sk-ant"},
            "OPENAI_API_KEY": "sk-openai",
        }))

        adapter = create_adapter_from_config_file(cfg, agent_type="codex")
        assert isinstance(adapter, CodexAdapter)
