"""Tests for CLI argument parsing and execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clippet.cli import _build_parser, main
from clippet.config.registry import AdapterConfig, ClippetConfig
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
        assert args.purge is False

    def test_env_remove_purge(self) -> None:
        """Parse 'env remove myenv --purge'."""
        args = self._parse(["env", "remove", "myenv", "--purge"])

        assert args.env_action == "remove"
        assert args.purge is True

    def test_env_create_default(self) -> None:
        """Parse 'env create work'."""
        args = self._parse(["env", "create", "work"])

        assert args.command == "env"
        assert args.env_action == "create"
        assert args.name == "work"
        assert args.from_current is False
        assert args.agents is None
        assert args.overwrite is False

    def test_env_create_with_flags(self) -> None:
        """Parse 'env create work --from-current --agents claude,codex --overwrite'."""
        args = self._parse([
            "env", "create", "work",
            "--from-current",
            "--agents", "claude,codex",
            "--overwrite",
        ])

        assert args.env_action == "create"
        assert args.name == "work"
        assert args.from_current is True
        assert args.agents == "claude,codex"
        assert args.overwrite is True

    def test_env_clone(self) -> None:
        """Parse 'env clone work scratch'."""
        args = self._parse(["env", "clone", "work", "scratch"])

        assert args.env_action == "clone"
        assert args.src == "work"
        assert args.dst == "scratch"

    def test_env_path(self) -> None:
        """Parse 'env path work'."""
        args = self._parse(["env", "path", "work"])

        assert args.env_action == "path"
        assert args.name == "work"

    def test_composite_adapter_name(self) -> None:
        """Composite configs should accept adapter names, not just built-in types."""
        args = self._parse(["-c", "config.json", "claude-personal"])

        assert args.config == "config.json"
        assert args.agent_type == "claude-personal"


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

    def test_run_with_single_adapter_clippet_config_auto_selects_adapter_name(
        self,
        tmp_path: Path,
    ) -> None:
        """Single-adapter composite configs should not require an explicit agent."""
        from clippet.cli import _handle_run

        mock_runner = MagicMock()
        mock_runner.execute.return_value = AgentResult(
            is_success=True,
            raw_output="composite done",
        )

        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"adapters": []}))

        args = argparse.Namespace(
            config=str(cfg),
            env=None,
            prompt="build it",
            agent_type=None,
            codex_config=None,
        )
        config = ClippetConfig(
            adapters=[
                AdapterConfig(
                    adapter_type="claude",
                    name="claude-personal",
                )
            ]
        )

        with (
            patch("clippet.cli.detect_config_type", return_value="clippet"),
            patch("clippet.cli.load_config", return_value=config),
            patch(
                "clippet.cli.create_runner_from_config",
                return_value=mock_runner,
            ),
        ):
            _handle_run(args)

        mock_runner.execute.assert_called_once()
        call_args = mock_runner.execute.call_args
        assert call_args[0][0] == "claude-personal"

    def test_interactive_single_adapter_clippet_config_auto_selects_adapter_name(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        """Interactive composite launch should infer the only adapter name."""
        from clippet.adapters.claude import ClaudeAdapter

        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"adapters": []}))

        config = ClippetConfig(
            adapters=[
                AdapterConfig(
                    adapter_type="claude",
                    name="claude-personal",
                    options={"model": "sonnet"},
                )
            ]
        )
        mock_runner = MagicMock()
        mock_runner.get_adapter.return_value = ClaudeAdapter(model="sonnet")

        monkeypatch.setattr(sys, "argv", ["clippet", "-c", str(cfg)])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        def fake_run(command, **kwargs):
            assert command == [
                "claude",
                "--model",
                "sonnet",
                "--permission-mode",
                "bypassPermissions",
            ]
            assert kwargs["cwd"] == Path.cwd()
            return MagicMock(returncode=0)

        with (
            patch("clippet.cli.detect_config_type", return_value="clippet"),
            patch("clippet.cli.load_config", return_value=config),
            patch(
                "clippet.cli.create_runner_from_config",
                return_value=mock_runner,
            ),
            patch("clippet.cli.subprocess.run", side_effect=fake_run) as mock_run,
        ):
            main()

        mock_runner.get_adapter.assert_called_once_with("claude-personal")
        mock_run.assert_called_once()

    def test_multi_adapter_clippet_config_without_agent_errors(
        self,
        tmp_path: Path,
        monkeypatch,
        capsys,
    ) -> None:
        """Composite configs with multiple adapters should still require an explicit choice."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"adapters": []}))

        config = ClippetConfig(
            adapters=[
                AdapterConfig(adapter_type="claude", name="claude-personal"),
                AdapterConfig(adapter_type="codex", name="codex-default"),
            ]
        )

        monkeypatch.setattr(sys, "argv", ["clippet", "-c", str(cfg), "-p", "build it"])

        with (
            patch("clippet.cli.detect_config_type", return_value="clippet"),
            patch("clippet.cli.load_config", return_value=config),
            pytest.raises(SystemExit),
        ):
            main()

        captured = capsys.readouterr()
        assert "Available adapters: claude-personal, codex-default" in captured.err

    def test_missing_config_and_env(self, monkeypatch) -> None:
        """Verify error when neither -c nor -e provided."""
        monkeypatch.setattr(sys, "argv", ["clippet", "-p", "hello"])

        with pytest.raises(SystemExit):
            main()

    def test_missing_prompt_launches_interactive_native_agent(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        """No prompt should launch the detected native CLI interactively."""
        cfg = tmp_path / "glm5-1-base.json"
        cfg.write_text(json.dumps({
            "env": {
                "ANTHROPIC_AUTH_TOKEN": "token",
                "ANTHROPIC_MODEL": "glm-5.1",
            },
            "skipDangerousModePermissionPrompt": True,
        }))

        monkeypatch.setattr(sys, "argv", ["clippet", "-c", str(cfg), "claude"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        def fake_run(command, **kwargs):
            home = Path(kwargs["env"]["HOME"])
            settings_path = home / ".claude" / "settings.json"

            assert command[0] == "claude"
            assert "--model" in command
            model_idx = command.index("--model")
            assert command[model_idx + 1] == "glm-5.1"
            assert kwargs["cwd"] == Path.cwd()
            assert settings_path.exists()
            assert json.loads(settings_path.read_text(encoding="utf-8"))["env"][
                "ANTHROPIC_MODEL"
            ] == "glm-5.1"

            return MagicMock(returncode=0)

        with patch("clippet.cli.subprocess.run", side_effect=fake_run) as mock_run:
            main()

        mock_run.assert_called_once()

    def test_env_launches_interactive_composite_agent(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        """Named environments should resolve to a composite config for interactive launch."""
        native_cfg = tmp_path / "claude-native.json"
        native_cfg.write_text(json.dumps({
            "env": {
                "ANTHROPIC_AUTH_TOKEN": "token",
            },
            "skipDangerousModePermissionPrompt": True,
        }))

        composite_cfg = tmp_path / "clippet-config.json"
        composite_cfg.write_text(json.dumps({
            "credential_profiles": {
                "glm": {
                    "files": {
                        ".claude/settings.json": str(native_cfg),
                    },
                    "env": {
                        "ANTHROPIC_MODEL": "glm-5.1",
                    },
                }
            },
            "adapters": [
                {
                    "adapter_type": "claude",
                    "name": "claude",
                    "options": {
                        "model": "glm-5.1",
                        "credential_profile": "glm",
                    },
                }
            ],
        }))

        monkeypatch.setattr(sys, "argv", ["clippet", "-e", "glm", "claude"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        def fake_run(command, **kwargs):
            home = Path(kwargs["env"]["HOME"])
            settings_path = home / ".claude" / "settings.json"

            assert command == [
                "claude",
                "--model",
                "glm-5.1",
                "--permission-mode",
                "bypassPermissions",
            ]
            assert settings_path.exists()
            assert kwargs["env"]["ANTHROPIC_MODEL"] == "glm-5.1"
            return MagicMock(returncode=0)

        with (
            patch(
                "clippet.cli.get_environment",
                return_value={"config_path": str(composite_cfg.resolve())},
            ),
            patch("clippet.cli.subprocess.run", side_effect=fake_run) as mock_run,
        ):
            main()

        mock_run.assert_called_once()

    def test_native_config_type_mismatch_errors(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        """A native Claude config cannot be forced to run as Codex."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "env": {"ANTHROPIC_API_KEY": "sk-ant"},
        }))

        monkeypatch.setattr(
            sys,
            "argv",
            ["clippet", "-c", str(cfg), "codex", "-p", "do stuff"],
        )

        with pytest.raises(SystemExit):
            main()


class TestProjectLevelConfig:
    """Tests for project-level .clippet.json configuration in CLI."""

    def test_project_config_launches_codex_interactively(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        """Project-level config should launch codex when invoked with 'clippet codex'."""
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".clippet.local").mkdir()
        (repo / ".clippet.local" / "auth.json").write_text(
            json.dumps({"OPENAI_API_KEY": "sk-test"}),
            encoding="utf-8",
        )
        (repo / ".clippet.local" / "config.toml").write_text(
            'model = "o4-mini"\n',
            encoding="utf-8",
        )
        (repo / ".clippet.json").write_text(
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

        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", ["clippet", "codex"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        with patch("clippet.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            main()

        assert mock_run.called
        assert mock_run.call_args[0][0][0] == "codex"

    def test_project_config_launches_claude_interactively(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        """Project-level config should launch claude when invoked with 'clippet claude'."""
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".clippet.local").mkdir()
        (repo / ".clippet.local" / "claude.json").write_text(
            json.dumps({"env": {"ANTHROPIC_API_KEY": "sk-test"}}),
            encoding="utf-8",
        )
        (repo / ".clippet.json").write_text(
            json.dumps({
                "version": 1,
                "agents": {
                    "claude": {"config_path": ".clippet.local/claude.json"}
                },
            }),
            encoding="utf-8",
        )

        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", ["clippet", "claude"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        with patch("clippet.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            main()

        assert mock_run.called
        assert mock_run.call_args[0][0][0] == "claude"

    def test_explicit_config_beats_project_config(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        """Explicit -c should override any project-level .clippet.json."""
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        # Create project config
        (repo / ".clippet.json").write_text(
            json.dumps({
                "version": 1,
                "agents": {"codex": {"config_path": "project-auth.json"}},
            }),
            encoding="utf-8",
        )
        # Create explicit config
        explicit = tmp_path / "explicit-auth.json"
        explicit.write_text(
            json.dumps({"OPENAI_API_KEY": "sk-explicit"}),
            encoding="utf-8",
        )

        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", ["clippet", "-c", str(explicit), "codex"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        with (
            patch("clippet.cli.detect_config_type", return_value="codex"),
            patch("clippet.cli.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            main()

        assert mock_run.called

    def test_project_config_missing_agent_errors(
        self,
        tmp_path: Path,
        monkeypatch,
        capsys,
    ) -> None:
        """Should error when requested agent is not in .clippet.json."""
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".clippet.json").write_text(
            json.dumps({
                "version": 1,
                "agents": {"claude": {"config_path": "claude.json"}},
            }),
            encoding="utf-8",
        )

        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", ["clippet", "codex"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "codex" in captured.err.lower()

    def test_missing_project_config_errors(
        self,
        tmp_path: Path,
        monkeypatch,
        capsys,
    ) -> None:
        """Should error when no .clippet.json and no explicit config."""
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)

        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", ["clippet", "codex"])
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert ".clippet.json" in captured.err

    def test_project_config_with_prompt(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        """Project config should work with -p prompt."""
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".clippet.local").mkdir()
        (repo / ".clippet.local" / "auth.json").write_text(
            json.dumps({"OPENAI_API_KEY": "sk-test"}),
            encoding="utf-8",
        )
        (repo / ".clippet.json").write_text(
            json.dumps({
                "version": 1,
                "agents": {
                    "codex": {"config_path": ".clippet.local/auth.json"}
                },
            }),
            encoding="utf-8",
        )

        mock_adapter = MagicMock()
        mock_adapter.run.return_value = AgentResult(
            is_success=True,
            raw_output="done",
        )
        mock_adapter.default_isolation = None

        monkeypatch.chdir(repo)
        monkeypatch.setattr(sys, "argv", ["clippet", "codex", "-p", "do stuff"])

        with (
            patch("clippet.cli.detect_config_type", return_value="codex"),
            patch(
                "clippet.cli.create_adapter_from_config_file",
                return_value=mock_adapter,
            ),
        ):
            main()

        mock_adapter.run.assert_called_once()


class TestQoderCliInteractive:
    """Tests for QoderCLI interactive launch wiring (env / second-home mode)."""

    def test_build_interactive_command_qodercli(self) -> None:
        """_build_interactive_command should return ['qodercli', ...] for QoderCLIAdapter."""
        from clippet.adapters.qodercli import QoderCLIAdapter
        from clippet.cli import _build_interactive_command

        adapter = QoderCLIAdapter(model="performance", yolo=True)
        cmd = _build_interactive_command(adapter)

        assert cmd[0] == "qodercli"
        assert "--model" in cmd and "performance" in cmd
        assert "--yolo" in cmd

    def test_run_qodercli_via_second_home_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`clippet -e <home-env> qodercli -p ...` should call adapter.run with HOME isolation."""
        home_dir = tmp_path / "qoder-env"
        home_dir.mkdir()
        (home_dir / ".qoder").mkdir()

        # Register the env so -e can find it
        import clippet.config.environments as env_mod
        from clippet.config.environments import create_home_env

        monkeypatch.setattr(env_mod, "get_clippet_root", lambda: tmp_path / ".clippet")
        # Recreate the env_dir under the redirected root
        created = create_home_env("qenv")
        # The env mod will create its own dir; copy over our .qoder marker
        (created / ".qoder").mkdir(exist_ok=True)

        mock_adapter = MagicMock()
        mock_adapter.run.return_value = AgentResult(
            is_success=True,
            raw_output="hi",
        )
        mock_adapter.default_isolation = None

        monkeypatch.setattr(
            sys, "argv", ["clippet", "-e", "qenv", "qodercli", "-p", "say hi"]
        )

        with patch(
            "clippet.cli.create_adapter_from_config_file",
            return_value=mock_adapter,
        ) as factory:
            main()

        # Factory should have been invoked with the env's HOME dir + qodercli type
        call_args = factory.call_args
        assert call_args.args[0] == str(created.resolve())
        assert "qodercli" in (call_args.args + tuple(call_args.kwargs.values()))
        mock_adapter.run.assert_called_once()
