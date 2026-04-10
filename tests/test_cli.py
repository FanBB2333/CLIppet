"""Tests for CLI argument parsing and execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clippet.cli import _build_parser, main
from clippet.models import AgentResult


class TestCliArgumentParsing:
    """Tests for argument parsing via _build_parser."""

    def _parse(self, argv: list[str]):
        parser = _build_parser()
        return parser.parse_args(argv)

    def test_config_with_prompt(self) -> None:
        """Parse '-c config.json -p hello'."""
        args = self._parse(["-c", "config.json", "-p", "hello"])

        assert args.config == "config.json"
        assert args.prompt == "hello"

    def test_env_with_prompt(self) -> None:
        """Parse '-e myenv -p hello'."""
        args = self._parse(["-e", "myenv", "-p", "hello"])

        assert args.env == "myenv"
        assert args.prompt == "hello"

    def test_env_list(self) -> None:
        """Parse 'env list'."""
        args = self._parse(["env", "list"])

        assert args.command == "env"
        assert args.env_action == "list"

    def test_env_add(self) -> None:
        """Parse 'env add myenv /path/to/config'."""
        args = self._parse(["env", "add", "myenv", "/path/to/config"])

        assert args.command == "env"
        assert args.env_action == "add"
        assert args.name == "myenv"
        assert args.config_path == "/path/to/config"

    def test_env_remove(self) -> None:
        """Parse 'env remove myenv'."""
        args = self._parse(["env", "remove", "myenv"])

        assert args.command == "env"
        assert args.env_action == "remove"
        assert args.name == "myenv"


class TestCliExecution:
    """Tests for CLI execution using mocked adapters."""

    def test_run_with_claude_config(self, tmp_path: Path, monkeypatch) -> None:
        """Mock create_adapter_from_config_file and adapter.run, verify called."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-ant"}}))

        mock_adapter = MagicMock()
        mock_adapter.run.return_value = AgentResult(
            is_success=True,
            raw_output="done",
        )

        monkeypatch.setattr(sys, "argv", [
            "clippet", "-c", str(cfg), "-p", "do stuff",
        ])

        with (
            patch(
                "clippet.cli.detect_config_type", return_value="claude_code"
            ) as mock_detect,
            patch(
                "clippet.cli.create_adapter_from_config_file",
                return_value=mock_adapter,
            ) as mock_create,
        ):
            main()

        mock_detect.assert_called_once()
        mock_create.assert_called_once()
        mock_adapter.run.assert_called_once()

    def test_run_with_clippet_config(self, tmp_path: Path, monkeypatch) -> None:
        """Mock load_config and create_runner_from_config for composite config."""
        from clippet.cli import _handle_run

        mock_runner = MagicMock()
        mock_runner.execute.return_value = AgentResult(
            is_success=True,
            raw_output="composite done",
        )

        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"adapters": []}))

        # Build a Namespace that _handle_run expects
        args = argparse.Namespace(
            config=str(cfg),
            env=None,
            prompt="build it",
            agent_type="claude",
        )

        with (
            patch("clippet.cli.detect_config_type", return_value="clippet"),
            patch("clippet.cli.load_config", return_value=MagicMock()),
            patch(
                "clippet.cli.create_runner_from_config",
                return_value=mock_runner,
            ),
        ):
            _handle_run(args)

        mock_runner.execute.assert_called_once()
        call_args = mock_runner.execute.call_args
        assert call_args[0][0] == "claude"

    def test_missing_config_and_env(self, monkeypatch) -> None:
        """Verify error when neither -c nor -e provided."""
        monkeypatch.setattr(sys, "argv", ["clippet", "-p", "hello"])

        with pytest.raises(SystemExit):
            main()

    def test_missing_prompt(self, tmp_path: Path, monkeypatch) -> None:
        """Verify error when no prompt provided."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"env": {"ANTHROPIC_API_KEY": "sk"}}))

        monkeypatch.setattr(sys, "argv", ["clippet", "-c", str(cfg)])
        # Simulate a TTY so stdin read won't provide a prompt
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        with (
            patch("clippet.cli.detect_config_type", return_value="claude_code"),
            pytest.raises(SystemExit),
        ):
            main()
