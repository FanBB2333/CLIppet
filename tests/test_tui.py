"""The TUI's data layer and run folding (no Textual needed)."""

from __future__ import annotations

import json
from pathlib import Path


from clippet.auth.codex import load_codex_credentials
from clippet.tui import data
from clippet.tui.runs import Run


# --- data ---------------------------------------------------------------------


def test_envs_list_base_first_with_their_accounts(world) -> None:
    envs, error = data.load_envs()
    assert error is None
    assert [e.name for e in envs] == ["base", "alice", "bob", "scratch"]
    alice = envs[1]
    assert alice.credential.display_email == "alice@example.com"
    assert alice.linked and alice.login == "ok"
    assert envs[3].credential is None and envs[3].login == "none"
    assert envs[0].is_base and envs[0].codex_home == world["home"] / ".codex"


def test_accounts_know_which_env_holds_them(world) -> None:
    envs, _ = data.load_envs()
    accounts, reports = data.load_accounts([str(world["export"])], envs, [str(world["export"])])
    assert [a.credential.display_email for a in accounts] == ["alice@example.com", "bob@example.com",
                                                             "carol@example.com"]
    assert accounts[0].holders == ["alice"] and accounts[2].holders == []
    assert accounts[2].login == "reauth"
    assert reports[0].count == 3 and reports[0].user_added and reports[0].error is None


def test_a_broken_source_is_reported_not_raised(world) -> None:
    envs, _ = data.load_envs()
    accounts, reports = data.load_accounts([str(world["tmp"] / "missing.json")], envs)
    assert accounts == [] and reports[0].error


def test_suggested_env_names_are_unique(world) -> None:
    creds = load_codex_credentials(world["export"])
    assert data.suggest_env_name(creds[0], set()) == "alice"
    assert data.suggest_env_name(creds[0], {"alice", "alice-2"}) == "alice-3"


def test_sessions_are_read_from_rollout_files(tmp_path: Path) -> None:
    day = tmp_path / ".codex" / "sessions" / "2026" / "11" / "02"
    day.mkdir(parents=True)
    rollout = day / "rollout-2026-11-02T17-14-17-01a19b89-bdd7-7f23-bc34-d24fb1549f22.jsonl"
    lines = [
        {"type": "session_meta", "payload": {"id": "01a19b89-bdd7-7f23-bc34-d24fb1549f22", "cwd": "/work",
                                             "timestamp": "2026-11-02T09:14:17Z", "originator": "codex_exec"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "<environment_context>…"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "fix the  flaky test"}]}},
    ]
    rollout.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    sessions = data.codex_sessions(tmp_path / ".codex")
    assert len(sessions) == 1
    assert sessions[0].id == "01a19b89-bdd7-7f23-bc34-d24fb1549f22"
    assert sessions[0].prompt == "fix the flaky test" and sessions[0].cwd == "/work"
    assert data.count_sessions(tmp_path / ".codex") == 1


def test_pinning_keeps_the_rest_of_clippet_json(tmp_path: Path) -> None:
    config = tmp_path / ".clippet.json"
    config.write_text(json.dumps({"version": 1, "agents": {"claude": {"config_path": "x.json"}}}))
    data.pin_project_env(config, "codex", "work")
    saved = json.loads(config.read_text())
    assert saved["agents"] == {"claude": {"config_path": "x.json"}, "codex": {"env": "work"}}
    data.pin_project_env(config, "codex", None)
    assert json.loads(config.read_text())["agents"] == {"claude": {"config_path": "x.json"}}


def test_scrubbed_variables_lists_names_only() -> None:
    names = data.scrubbed_variables({"OPENAI_API_KEY": "sk-secret", "ANTHROPIC_BASE_URL": "x", "PATH": "/bin"})
    assert names == ["ANTHROPIC_BASE_URL", "OPENAI_API_KEY"]


# --- runs ---------------------------------------------------------------------


def test_codex_events_fold_into_steps() -> None:
    run = Run(id=1, env="alice", agent="codex", prompt="go", workspace=Path("."))
    events = [
        {"type": "thread.started", "thread_id": "t-1"},
        {"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "**Plan**\nmore"}},
        {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "pytest",
                                          "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "pytest",
                                            "exit_code": 1, "aggregated_output": "1 failed\n", "status": "failed"}},
        {"type": "item.completed", "item": {"id": "i2", "type": "file_change",
                                            "changes": [{"path": "a.py", "kind": "update"}]}},
        {"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": "fixed"}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
    ]
    for event in events:
        run.feed(json.dumps(event))
    run.feed("not json")
    hook = {"type": "item.completed", "item": {"id": "w", "type": "error", "message": "hooks bypassed"}}
    run.feed(json.dumps(hook))
    run.feed(json.dumps(hook))  # Codex repeats some notices
    assert [s.kind for s in run.steps].count("warning") == 1
    assert run.session_id == "t-1"
    assert [s.kind for s in run.steps] == ["reasoning", "command", "edit", "message", "warning"]
    assert run.steps[0].text == "Plan"
    assert run.steps[1].state == "failed" and run.steps[1].detail.startswith("exit 1")
    assert run.answer == "fixed" and run.tokens == 15
    assert run.activity == "writing the answer"
