"""Tests for configuration loading and credential profiles."""

from __future__ import annotations

from pathlib import Path

from clippet.adapters.claude import ClaudeAdapter
from clippet.config.registry import (
    ClippetConfig,
    CredentialProfileConfig,
    AdapterConfig,
    create_runner_from_config,
    load_config,
)
from clippet.models import IsolationConfig


class TestConfigLoading:
    """Tests for YAML configuration parsing."""

    def test_load_config_parses_credential_profiles(self, tmp_path: Path, monkeypatch):
        """Credential profiles should load and expand environment references."""
        monkeypatch.setenv("TEST_API_KEY", "expanded-secret")

        config_path = tmp_path / "clippet-config.yaml"
        config_path.write_text(
            """
credential_profiles:
  personal:
    files:
      ".claude/settings.json": "~/profiles/settings.json"
    env:
      ANTHROPIC_API_KEY: "${TEST_API_KEY}"
adapters:
  - adapter_type: claude
    name: claude-personal
    options:
      credential_profile: personal
            """.strip(),
            encoding="utf-8",
        )

        config = load_config(config_path)

        assert "personal" in config.credential_profiles
        profile = config.credential_profiles["personal"].to_isolation_config()
        assert profile.credential_files[".claude/settings.json"].endswith(
            "/profiles/settings.json"
        )
        assert profile.env_overrides["ANTHROPIC_API_KEY"] == "expanded-secret"


class TestRunnerCreation:
    """Tests for runner creation from config objects."""

    def test_create_runner_applies_credential_profile_as_default_isolation(self):
        """Adapters created from config should carry the referenced profile."""
        config = ClippetConfig(
            credential_profiles={
                "work": CredentialProfileConfig(
                    files={".codex/auth.json": "/tmp/codex-auth.json"},
                    env={"OPENAI_API_KEY": "sk-work"},
                )
            },
            adapters=[
                AdapterConfig(
                    adapter_type="claude",
                    name="claude-work",
                    options={"model": "opus", "credential_profile": "work"},
                )
            ],
        )

        runner = create_runner_from_config(config)
        adapter = runner.get_adapter("claude-work")

        assert isinstance(adapter, ClaudeAdapter)
        assert adapter.model == "opus"
        assert adapter.default_isolation == IsolationConfig(
            credential_files={".codex/auth.json": "/tmp/codex-auth.json"},
            env_overrides={"OPENAI_API_KEY": "sk-work"},
        )

    def test_create_runner_rejects_unknown_credential_profile(self):
        """An adapter cannot reference a credential profile that does not exist."""
        config = ClippetConfig(
            adapters=[
                AdapterConfig(
                    adapter_type="claude",
                    name="claude-missing",
                    options={"credential_profile": "missing"},
                )
            ]
        )

        try:
            create_runner_from_config(config)
        except ValueError as exc:
            assert str(exc) == "Unknown credential profile: 'missing'"
        else:
            raise AssertionError("Expected create_runner_from_config to fail")
