"""Tests for isolated execution environments and credential injection."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import stat
import sys
import textwrap

import pytest

from clippet.adapters.base import BaseSubprocessAdapter
from clippet.isolation import (
    EnvVarCredentialProvider,
    FileCredentialProvider,
    IsolatedEnvironment,
)
from clippet.models import AgentRequest, AgentResult, IsolationConfig


class PythonEnvAdapter(BaseSubprocessAdapter):
    """Test adapter that reports its runtime environment as JSON."""

    @property
    def agent_name(self) -> str:
        return "python-env"

    def build_command(self, request: AgentRequest) -> list[str]:
        script = textwrap.dedent(
            """
            import json
            import os
            from pathlib import Path

            home = Path(os.environ["HOME"])
            credential_path = home / ".claude" / "credentials.json"

            print(json.dumps({
                "home": str(home),
                "default_token": os.environ.get("DEFAULT_TOKEN"),
                "request_token": os.environ.get("REQUEST_TOKEN"),
                "async_token": os.environ.get("ASYNC_TOKEN"),
                "visible_var": os.environ.get("VISIBLE_VAR"),
                "hidden_var": os.environ.get("HIDDEN_VAR"),
                "path_present": "PATH" in os.environ,
                "credential_exists": credential_path.exists(),
                "credential_content": (
                    credential_path.read_text(encoding="utf-8")
                    if credential_path.exists()
                    else None
                ),
                "credential_mode": (
                    oct(credential_path.stat().st_mode & 0o777)
                    if credential_path.exists()
                    else None
                ),
            }))
            """
        )
        return [sys.executable, "-c", script]

    def parse_output(
        self,
        raw_output: str,
        stderr: str,
        return_code: int,
    ) -> AgentResult:
        return AgentResult(
            raw_output=raw_output,
            is_success=return_code == 0,
            error_message=stderr.strip() or None,
        )


class TestIsolatedEnvironment:
    """Tests for the isolated environment context manager."""

    def test_creates_and_cleans_up_sandbox(self, tmp_path: Path):
        """Sandbox HOME should exist during the context and be removed afterward."""
        sandbox_root = tmp_path / "sandboxes"

        with IsolatedEnvironment(base_dir=sandbox_root) as isolated_env:
            sandbox_home = isolated_env.home_dir

            assert sandbox_home.exists()
            assert sandbox_home.parent == sandbox_root
            assert isolated_env.env["HOME"] == str(sandbox_home)

        assert not sandbox_home.exists()
        assert sandbox_root.exists()

    def test_persist_keeps_sandbox_directory(self, tmp_path: Path):
        """Persist mode should keep the sandbox after context exit."""
        with IsolatedEnvironment(base_dir=tmp_path, persist=True) as isolated_env:
            sandbox_home = isolated_env.home_dir

        assert sandbox_home.exists()
        shutil.rmtree(sandbox_home)

    def test_applies_whitelist_blacklist_and_overrides(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Whitelist, blacklist, and overrides should shape the sandbox env."""
        monkeypatch.setenv("VISIBLE_VAR", "visible")
        monkeypatch.setenv("HIDDEN_VAR", "hidden")

        with IsolatedEnvironment(
            base_dir=tmp_path,
            env_whitelist=["PATH", "VISIBLE_VAR", "HIDDEN_VAR"],
            env_blacklist=["HIDDEN_VAR"],
            env_overrides={"EXTRA_VAR": "extra"},
        ) as isolated_env:
            assert isolated_env.env["VISIBLE_VAR"] == "visible"
            assert "HIDDEN_VAR" not in isolated_env.env
            assert isolated_env.env["EXTRA_VAR"] == "extra"
            assert isolated_env.env["HOME"] == str(isolated_env.home_dir)

    def test_file_and_env_credential_providers(self, tmp_path: Path):
        """Credential providers should populate files and environment safely."""
        source_file = tmp_path / "credentials.json"
        source_file.write_text('{"token":"secret"}', encoding="utf-8")

        with IsolatedEnvironment(base_dir=tmp_path) as isolated_env:
            FileCredentialProvider(
                {".claude/credentials.json": source_file}
            ).inject(isolated_env)
            EnvVarCredentialProvider({"API_TOKEN": "secret-token"}).inject(
                isolated_env
            )

            destination = isolated_env.home_dir / ".claude" / "credentials.json"
            assert destination.read_text(encoding="utf-8") == '{"token":"secret"}'
            assert stat.S_IMODE(destination.stat().st_mode) == 0o600
            assert isolated_env.env["API_TOKEN"] == "secret-token"


class TestBaseAdapterIsolation:
    """Integration tests for adapter subprocess isolation."""

    def test_run_uses_sandbox_and_merges_default_isolation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Default adapter isolation and request isolation should both apply."""
        monkeypatch.setenv("VISIBLE_VAR", "visible")
        monkeypatch.setenv("HIDDEN_VAR", "hidden")

        credential_source = tmp_path / "credentials.json"
        credential_source.write_text('{"token":"sync"}', encoding="utf-8")

        adapter = PythonEnvAdapter()
        adapter.default_isolation = IsolationConfig(
            env_overrides={"DEFAULT_TOKEN": "adapter-default"},
            env_whitelist=["PATH", "VISIBLE_VAR", "HIDDEN_VAR"],
            env_blacklist=["HIDDEN_VAR"],
        )

        result = adapter.run(
            AgentRequest(
                task_prompt="report environment",
                workspace_dir=tmp_path,
                isolation=IsolationConfig(
                    credential_files={
                        ".claude/credentials.json": str(credential_source)
                    },
                    env_overrides={"REQUEST_TOKEN": "request-value"},
                ),
            )
        )

        assert result.is_success is True

        payload = json.loads(result.raw_output)
        sandbox_home = Path(payload["home"])

        assert payload["default_token"] == "adapter-default"
        assert payload["request_token"] == "request-value"
        assert payload["visible_var"] == "visible"
        assert payload["hidden_var"] is None
        assert payload["path_present"] is True
        assert payload["credential_exists"] is True
        assert payload["credential_content"] == '{"token":"sync"}'
        assert payload["credential_mode"] == "0o600"
        assert not sandbox_home.exists()

    def test_run_async_supports_persisted_sandbox(self, tmp_path: Path):
        """Async execution should receive the same isolated HOME and credentials."""
        credential_source = tmp_path / "async-credentials.json"
        credential_source.write_text('{"token":"async"}', encoding="utf-8")

        adapter = PythonEnvAdapter()
        result = asyncio.run(
            adapter.run_async(
                AgentRequest(
                    task_prompt="report environment",
                    workspace_dir=tmp_path,
                    isolation=IsolationConfig(
                        credential_files={
                            ".claude/credentials.json": str(credential_source)
                        },
                        env_overrides={"ASYNC_TOKEN": "async-value"},
                        persist_sandbox=True,
                    ),
                )
            )
        )

        assert result.is_success is True

        payload = json.loads(result.raw_output)
        sandbox_home = Path(payload["home"])

        assert payload["async_token"] == "async-value"
        assert payload["credential_content"] == '{"token":"async"}'
        assert sandbox_home.exists()

        shutil.rmtree(sandbox_home)
