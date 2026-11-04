"""The headless protocols of Claude Code, Qoder CLI, pi, Copilot CLI and Antigravity CLI.

Each stream below follows the event shapes in that agent's documentation;
the adapters turn a finished run into an AgentResult, the TUI reads the same
events while the run streams.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clippet.adapters.antigravity import AntigravityAdapter, agy_error
from clippet.adapters.claude import ClaudeAdapter
from clippet.adapters.copilot import CopilotAdapter
from clippet.adapters.pi import PiAdapter
from clippet.adapters.qodercli import QoderCLIAdapter
from clippet.agents import AGENTS
from clippet.auth.logins import login_of
from clippet.models import AgentRequest
from clippet.tui.runs import READERS, Run


def lines(*events: dict) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


def streamed(agent: str, raw: str) -> Run:
    run = Run(id=1, env="base", agent=agent, prompt="p", workspace=Path("."))
    for line in raw.splitlines():
        run.feed(line)
    return run


CLAUDE_STREAM = lines(
    {"type": "system", "subtype": "init", "session_id": "sess-1", "tools": ["Bash"]},
    {"type": "assistant", "uuid": "u1", "message": {"id": "m1", "content": [
        {"type": "thinking", "thinking": "**Look first**\nthen act"},
        {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls -la"}},
    ]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": False, "content": [{"type": "text", "text": "a\nb"}]},
    ]}},
    {"type": "assistant", "uuid": "u2", "message": {"id": "m2", "content": [{"type": "text", "text": "Two files."}]}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "Two files.", "session_id": "sess-1",
     "num_turns": 2, "total_cost_usd": 0.0123,
     "usage": {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 100}},
)


class TestClaudeProtocol:
    def test_stream_json_is_asked_for_with_verbose(self) -> None:
        cmd = ClaudeAdapter(output_format="stream-json").build_command(AgentRequest(task_prompt="hi"))

        assert cmd[:4] == ["claude", "-p", "--output-format", "stream-json"]
        assert "--verbose" in cmd

    def test_a_finished_stream(self) -> None:
        result = ClaudeAdapter(output_format="stream-json").parse_output(CLAUDE_STREAM, "", 0)

        assert result.is_success
        assert result.output_text == "Two files."
        assert result.session_id == "sess-1"
        assert [call.tool_name for call in result.tool_calls] == ["Bash"]
        assert result.usage["cached_input_tokens"] == 100
        assert result.usage["cost_usd"] == pytest.approx(0.0123)

    def test_a_stream_without_a_result_failed(self) -> None:
        result = ClaudeAdapter(output_format="stream-json").parse_output(CLAUDE_STREAM.splitlines()[0], "", 0)

        assert not result.is_success
        assert "without a result" in result.error_message

    def test_the_tui_reads_it_as_it_comes(self) -> None:
        run = streamed("claude", CLAUDE_STREAM)

        kinds = [(step.kind, step.text, step.state) for step in run.steps]
        assert kinds == [
            ("reasoning", "Look first", "done"),
            ("command", "ls -la", "done"),
            ("message", "Two files.", "done"),
        ]
        assert run.steps[1].detail == "a\nb"
        assert run.session_id == "sess-1"
        assert run.tokens == 150
        assert run.cost == "$0.0123"
        assert run.answer == "Two files."


class TestQoderProtocol:
    def test_headless_runs_cannot_ask_so_they_run_yolo(self) -> None:
        cmd = QoderCLIAdapter(output_format="stream-json").build_command(
            AgentRequest(task_prompt="fix it", workspace_dir="/tmp/w"))

        assert cmd[:3] == ["qodercli", "-p", "fix it"]
        assert "--yolo" in cmd
        assert cmd[cmd.index("-o") + 1] == "stream-json"

    def test_a_permission_mode_replaces_yolo(self) -> None:
        cmd = QoderCLIAdapter(permission_mode="accept_edits").build_command(AgentRequest(task_prompt="x"))

        assert "--yolo" not in cmd
        assert cmd[cmd.index("--permission-mode") + 1] == "accept_edits"

    def test_skills_go_to_the_system_prompt(self) -> None:
        cmd = QoderCLIAdapter().build_command(AgentRequest(task_prompt="x", injected_skills=["Be terse."]))

        assert "Be terse." in cmd[cmd.index("--append-system-prompt") + 1]

    def test_it_speaks_claudes_stream(self) -> None:
        result = QoderCLIAdapter(output_format="stream-json").parse_output(CLAUDE_STREAM, "", 0)

        assert (result.is_success, result.output_text, result.session_id) == (True, "Two files.", "sess-1")
        assert READERS["qodercli"] is READERS["claude"]

    def test_an_expired_login_on_stdout_is_the_error(self) -> None:
        result = QoderCLIAdapter().parse_output("Your login has expired, run qodercli /login", "", 41)

        assert not result.is_success
        assert "expired" in result.error_message


PI_STREAM = lines(
    {"type": "session", "version": 3, "id": "pi-sess", "cwd": "/w"},
    {"type": "agent_start"},
    {"type": "tool_execution_start", "toolCallId": "call_1", "toolName": "bash", "args": {"command": "pytest -q"}},
    {"type": "tool_execution_end", "toolCallId": "call_1", "toolName": "bash",
     "result": {"content": [{"type": "text", "text": "1 failed"}]}, "isError": True, "durationMs": 900},
    {"type": "message_end", "message": {"role": "assistant", "stopReason": "toolUse",
     "content": [{"type": "toolCall", "id": "call_1", "name": "bash", "arguments": {"command": "pytest -q"}}],
     "usage": {"input": 50, "output": 10, "cacheRead": 5, "cost": {"total": 0.001}}}},
    {"type": "turn_end"},
    {"type": "message_end", "message": {"role": "assistant", "stopReason": "stop",
     "content": [{"type": "text", "text": "One test fails."}],
     "usage": {"input": 70, "output": 20, "cacheRead": 0, "cost": {"total": 0.002}}}},
    {"type": "turn_end"},
    {"type": "agent_end"},
)


class TestPiProtocol:
    def test_the_prompt_comes_last_after_a_double_dash(self) -> None:
        cmd = PiAdapter(model="sonnet", provider="anthropic", thinking="high").build_command(
            AgentRequest(task_prompt="-starts with a dash"))

        assert cmd[:3] == ["pi", "--mode", "json"]
        assert cmd[-2:] == ["--", "-starts with a dash"]
        assert cmd[cmd.index("--provider") + 1] == "anthropic"
        assert cmd[cmd.index("--thinking") + 1] == "high"

    def test_a_finished_stream(self) -> None:
        result = PiAdapter().parse_output(PI_STREAM, "", 0)

        assert result.is_success
        assert result.output_text == "One test fails."
        assert result.session_id == "pi-sess"
        assert result.steps_count == 2
        assert result.usage["input_tokens"] == 120
        assert result.usage["cost_usd"] == pytest.approx(0.003)

    def test_the_tui_reads_it_as_it_comes(self) -> None:
        run = streamed("pi", PI_STREAM)

        assert [(step.kind, step.text, step.state) for step in run.steps] == [
            ("command", "pytest -q", "failed"),
            ("message", "One test fails.", "done"),
        ]
        assert run.steps[0].detail == "1 failed"
        assert run.session_id == "pi-sess"
        assert run.cost == "$0.003"


COPILOT_STREAM = lines(
    {"type": "session.start", "data": {"sessionId": "cop-1"}, "id": "e1"},
    {"type": "assistant.reasoning", "data": {"content": "Checking the tests"}, "id": "e2"},
    {"type": "tool.execution_start", "data": {"toolCallId": "t1", "toolName": "bash",
     "arguments": {"command": "npm test"}}, "id": "e3"},
    {"type": "tool.execution_complete", "data": {"toolCallId": "t1", "success": True,
     "result": {"content": "ok 12 passed"}}, "id": "e4"},
    {"type": "assistant.message", "data": {"content": "from a sub-agent"}, "id": "e5", "agentId": "sub"},
    {"type": "assistant.message", "data": {"content": "All 12 tests pass."}, "id": "e6"},
    {"type": "result", "sessionId": "cop-1", "exitCode": 0,
     "usage": {"premiumRequests": 1, "codeChanges": {"linesAdded": 3, "linesRemoved": 1, "filesModified": ["a.py"]}}},
)


class TestCopilotProtocol:
    def test_headless_runs_allow_tools_and_keep_dash_prompts_whole(self) -> None:
        cmd = CopilotAdapter(model="gpt-5.5").build_command(AgentRequest(task_prompt="--help me"))

        assert cmd[:2] == ["copilot", "--prompt=--help me"]
        assert "--allow-all-tools" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "json"

    def test_a_finished_stream(self) -> None:
        result = CopilotAdapter().parse_output(COPILOT_STREAM, "", 0)

        assert result.is_success
        assert result.output_text == "All 12 tests pass."
        assert result.session_id == "cop-1"
        assert result.usage == {"premium_requests": 1, "lines_added": 3, "lines_removed": 1, "files_modified": 1}

    def test_a_blocked_run_failed(self) -> None:
        raw = lines({"type": "result", "sessionId": "c", "exitCode": 0, "outcome": "blocked", "blocker": "needs a login"})

        result = CopilotAdapter().parse_output(raw, "", 0)

        assert not result.is_success

    def test_the_tui_reads_it_as_it_comes(self) -> None:
        run = streamed("copilot", COPILOT_STREAM)

        assert [(step.kind, step.text, step.state) for step in run.steps] == [
            ("reasoning", "Checking the tests", "done"),
            ("command", "npm test", "done"),
            ("message", "All 12 tests pass.", "done"),
        ]
        assert run.cost == "1 premium request"
        assert run.tokens is None


AGY_STREAM = lines(
    {"event": "init", "init": {"conversation_id": "agy-1", "cwd": "/w"}},
    {"event": "step_update", "step_update": {"conversation_id": "agy-1", "step_index": 0, "state": "DONE",
     "step_type": "user_input"}},
    {"event": "step_update", "step_update": {"conversation_id": "agy-1", "step_index": 1, "state": "DONE",
     "step_type": "tool", "tool_name": "run_command",
     "tool_info": {"name": "run_command", "parameters": {"CommandLine": "echo hi"}, "output": "hi"}}},
    {"event": "step_update", "step_update": {"conversation_id": "agy-1", "step_index": 2, "state": "ACTIVE",
     "step_type": "agent_response", "text_delta": "It printed "}},
    {"event": "step_update", "step_update": {"conversation_id": "agy-1", "step_index": 2, "state": "DONE",
     "step_type": "agent_response", "text_delta": "hi."}},
    {"event": "result", "result": {"conversation_id": "agy-1", "status": "SUCCESS", "response": "It printed hi.",
     "num_turns": 1, "usage": {"input_tokens": 40, "output_tokens": 5, "thinking_tokens": 2, "cache_read_tokens": 30}}},
)


class TestAntigravityProtocol:
    def test_headless_runs_skip_permissions_unless_told(self) -> None:
        cmd = AntigravityAdapter(model="gemini-3.5-pro").build_command(AgentRequest(task_prompt="hi"))

        assert cmd[:3] == ["agy", "-p", "hi"]
        assert "--dangerously-skip-permissions" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert "--dangerously-skip-permissions" not in AntigravityAdapter(skip_permissions=False).build_command(
            AgentRequest(task_prompt="hi"))

    def test_a_finished_stream(self) -> None:
        result = AntigravityAdapter().parse_output(AGY_STREAM, "", 0)

        assert result.is_success
        assert result.output_text == "It printed hi."
        assert result.session_id == "agy-1"
        assert [call.tool_name for call in result.tool_calls] == ["run_command"]
        assert result.usage["cached_input_tokens"] == 30

    def test_an_error_line_on_stderr_is_the_error(self) -> None:
        stderr = 'AGY_ERROR: {"status":"RESOURCE_EXHAUSTED","message":"daily quota exhausted"}\n'

        result = AntigravityAdapter().parse_output("", stderr, 1)

        assert not result.is_success
        assert result.error_message == "daily quota exhausted"
        assert agy_error("plain failure\n") == "plain failure"

    def test_the_tui_reads_it_as_it_comes(self) -> None:
        run = streamed("agy", AGY_STREAM)

        assert [(step.kind, step.text, step.state) for step in run.steps] == [
            ("command", "echo hi", "done"),
            ("message", "It printed hi.", "done"),
        ]
        assert run.session_id == "agy-1"
        assert run.tokens == 45


class TestResume:
    @pytest.mark.parametrize(("agent", "args"), [
        ("codex", ["resume", "S"]),
        ("claude", ["--resume", "S"]),
        ("qodercli", ["-r", "S"]),
        ("pi", ["--session", "S"]),
        ("copilot", ["--resume=S"]),
        ("agy", ["--conversation", "S"]),
    ])
    def test_every_agent_can_pick_a_conversation_back_up(self, agent: str, args: list[str]) -> None:
        assert AGENTS[agent].resume_args("S") == args


class TestLogins:
    def test_names_are_read_and_tokens_are_not(self, tmp_path: Path) -> None:
        (tmp_path / ".claude.json").write_text(json.dumps(
            {"oauthAccount": {"emailAddress": "alice@example.com", "organizationName": "Acme"}, "primaryApiKey": "sk-x"}))
        (tmp_path / ".copilot").mkdir()
        (tmp_path / ".copilot" / "config.json").write_text(
            '// managed by the CLI\n{"lastLoggedInUser": {"host": "https://github.com", "login": "bob"}}')
        (tmp_path / ".pi" / "agent").mkdir(parents=True)
        (tmp_path / ".pi" / "agent" / "auth.json").write_text(json.dumps(
            {"openai": {"type": "api_key", "key": "sk-y"}, "anthropic": {"type": "oauth"}}))
        (tmp_path / ".gemini" / "antigravity-cli").mkdir(parents=True)
        (tmp_path / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").write_text("t")

        assert login_of(tmp_path, "claude") == "alice@example.com (Acme)"
        assert login_of(tmp_path, "copilot") == "bob"
        assert login_of(tmp_path, "pi") == "logged in to anthropic, openai"
        assert login_of(tmp_path, "agy") == "signed in"
        assert login_of(tmp_path, "qodercli") is None

    def test_broken_files_read_as_unknown(self, tmp_path: Path) -> None:
        (tmp_path / ".claude.json").write_text("{not json")

        assert login_of(tmp_path, "claude") is None
        assert login_of(None, "claude") is None
