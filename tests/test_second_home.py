"""Tests for second-home isolation mode and enhanced codex support."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from clippet.config.detector import (
    create_adapter_from_codex_config,
    create_adapter_from_config_file,
    create_adapter_with_second_home,
    detect_config_type,
)
from clippet.isolation import (
    AGENT_CONFIG_PATHS,
    DirectoryCopyProvider,
    IsolatedEnvironment,
)
from clippet.models import IsolationConfig


# ---------------------------------------------------------------------------
# IsolationConfig.home_dir
# ---------------------------------------------------------------------------


class TestIsolationConfigHomeDir:
    """Test the new home_dir field on IsolationConfig."""

    def test_default_home_dir_is_none(self):
        config = IsolationConfig()
        assert config.home_dir is None
        assert not config.is_second_home

    def test_home_dir_set(self):
        config = IsolationConfig(home_dir="/tmp/my-home")
        assert config.home_dir == "/tmp/my-home"
        assert config.is_second_home

    def test_merge_preserves_home_dir(self):
        base = IsolationConfig(home_dir="/tmp/base")
        override = IsolationConfig(env_overrides={"KEY": "val"})
        merged = base.merged_with(override)
        assert merged.home_dir == "/tmp/base"

    def test_merge_overrides_home_dir(self):
        base = IsolationConfig(home_dir="/tmp/base")
        override = IsolationConfig(home_dir="/tmp/override")
        merged = base.merged_with(override)
        assert merged.home_dir == "/tmp/override"


# ---------------------------------------------------------------------------
# IsolatedEnvironment — second-home mode
# ---------------------------------------------------------------------------


class TestSecondHomeEnvironment:
    """Test IsolatedEnvironment with a persistent second-home directory."""

    def test_second_home_uses_specified_directory(self):
        with tempfile.TemporaryDirectory() as td:
            home_path = Path(td) / "second-home"
            with IsolatedEnvironment(home_dir=home_path) as env:
                assert env.home_dir == home_path
                assert home_path.exists()
                assert env.is_second_home
                assert env.env["HOME"] == str(home_path)

    def test_second_home_not_deleted_on_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            home_path = Path(td) / "persistent-home"
            home_path.mkdir()
            sentinel = home_path / "keep-me.txt"
            sentinel.write_text("important")

            with IsolatedEnvironment(home_dir=home_path) as env:
                assert env.is_second_home

            # After context exit, directory and contents must still exist
            assert home_path.exists()
            assert sentinel.exists()
            assert sentinel.read_text() == "important"

    def test_temp_dir_still_works_without_home_dir(self):
        with IsolatedEnvironment() as env:
            assert not env.is_second_home
            home = env.home_dir
            assert home.exists()
            assert "clippet-agent-" in home.name

        # After exit, temp dir should be cleaned up
        assert not home.exists()

    def test_second_home_env_overrides_applied(self):
        with tempfile.TemporaryDirectory() as td:
            home_path = Path(td) / "env-test"
            with IsolatedEnvironment(
                home_dir=home_path,
                env_overrides={"MY_KEY": "my_value"},
            ) as env:
                assert env.env["MY_KEY"] == "my_value"
                assert env.env["HOME"] == str(home_path)


# ---------------------------------------------------------------------------
# DirectoryCopyProvider
# ---------------------------------------------------------------------------


class TestDirectoryCopyProvider:
    """Test the DirectoryCopyProvider for copying config directories."""

    def test_copies_directory_into_sandbox(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source-config"
            source.mkdir()
            (source / "settings.json").write_text('{"key": "value"}')
            (source / "subdir").mkdir()
            (source / "subdir" / "nested.txt").write_text("nested")

            with IsolatedEnvironment() as env:
                provider = DirectoryCopyProvider(
                    {".myagent": str(source)}
                )
                provider.inject(env)

                dest = env.home_dir / ".myagent"
                assert dest.is_dir()
                assert (dest / "settings.json").read_text() == '{"key": "value"}'
                assert (dest / "subdir" / "nested.txt").read_text() == "nested"

    def test_skips_missing_source_silently(self):
        with IsolatedEnvironment() as env:
            provider = DirectoryCopyProvider(
                {".missing": "/nonexistent/path/12345"}
            )
            # Should not raise
            provider.inject(env)


# ---------------------------------------------------------------------------
# detect_config_type — second_home detection
# ---------------------------------------------------------------------------


class TestDetectSecondHome:
    """Test detection of directory paths as second-home configs."""

    def test_directory_detected_as_second_home(self):
        with tempfile.TemporaryDirectory() as td:
            result = detect_config_type(td)
            assert result == "second_home"

    def test_file_not_detected_as_second_home(self, tmp_path):
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"OPENAI_API_KEY": "sk-test"}))
        result = detect_config_type(config)
        assert result == "codex"


# ---------------------------------------------------------------------------
# create_adapter_with_second_home
# ---------------------------------------------------------------------------


class TestCreateAdapterWithSecondHome:
    """Test the second-home adapter factory."""

    def test_creates_claude_adapter_with_second_home(self, tmp_path):
        home_dir = tmp_path / "claude-home"
        home_dir.mkdir()
        (home_dir / ".claude").mkdir()

        adapter = create_adapter_with_second_home(home_dir, "claude")
        assert adapter.agent_name == "claude-code"
        assert adapter.default_isolation is not None
        assert adapter.default_isolation.home_dir == str(home_dir.resolve())
        assert adapter.default_isolation.is_second_home

    def test_creates_codex_adapter_with_second_home(self, tmp_path):
        home_dir = tmp_path / "codex-home"
        home_dir.mkdir()
        (home_dir / ".codex").mkdir()

        adapter = create_adapter_with_second_home(home_dir, "codex")
        assert adapter.agent_name == "codex"
        assert adapter.default_isolation is not None
        assert adapter.default_isolation.home_dir == str(home_dir.resolve())

    def test_unknown_agent_type_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not supported"):
            create_adapter_with_second_home(tmp_path, "unknown-agent")

    def test_directory_input_to_create_adapter_from_config_file(self, tmp_path):
        home_dir = tmp_path / "my-home"
        home_dir.mkdir()

        adapter = create_adapter_from_config_file(str(home_dir), agent_type="claude")
        assert adapter.agent_name == "claude-code"
        assert adapter.default_isolation.is_second_home

    def test_directory_without_agent_type_raises(self, tmp_path):
        home_dir = tmp_path / "no-agent"
        home_dir.mkdir()

        with pytest.raises(ValueError, match="agent_type is required"):
            create_adapter_from_config_file(str(home_dir), agent_type=None)


# ---------------------------------------------------------------------------
# Codex single-file mode — auto-copy config.toml
# ---------------------------------------------------------------------------


class TestCodexSingleFileMode:
    """Test that Codex single-file mode correctly sets up credential files."""

    def test_codex_auth_file_mapped(self, tmp_path):
        auth = tmp_path / "auth.json"
        auth.write_text(json.dumps({
            "OPENAI_API_KEY": "sk-test",
        }))

        adapter = create_adapter_from_codex_config(auth)
        iso = adapter.default_isolation
        assert iso is not None
        assert ".codex/auth.json" in iso.credential_files
        assert iso.credential_files[".codex/auth.json"] == str(auth.resolve())
        assert iso.env_overrides.get("OPENAI_API_KEY") == "sk-test"


# ---------------------------------------------------------------------------
# AGENT_CONFIG_PATHS
# ---------------------------------------------------------------------------


class TestAgentConfigPaths:
    """Test that AGENT_CONFIG_PATHS is properly defined."""

    def test_claude_paths(self):
        assert "claude" in AGENT_CONFIG_PATHS
        assert ".claude" in AGENT_CONFIG_PATHS["claude"]

    def test_codex_paths(self):
        assert "codex" in AGENT_CONFIG_PATHS
        assert ".codex" in AGENT_CONFIG_PATHS["codex"]

    def test_gemini_paths(self):
        assert "gemini" in AGENT_CONFIG_PATHS
        assert ".gemini" in AGENT_CONFIG_PATHS["gemini"]

    def test_opencode_paths(self):
        assert "opencode" in AGENT_CONFIG_PATHS
        assert ".config/opencode" in AGENT_CONFIG_PATHS["opencode"]
