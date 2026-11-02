"""Streaming output and cancellation of subprocess adapters."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from pydantic import SecretStr

from clippet.adapters.base import BaseSubprocessAdapter
from clippet.models import AgentRequest, AgentResult, IsolationConfig


class ScriptAdapter(BaseSubprocessAdapter):
    """Runs a small Python script as the 'agent'."""

    def __init__(self, script: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.script = script

    @property
    def agent_name(self) -> str:
        return "script"

    def build_command(self, request: AgentRequest) -> list[str]:
        return [sys.executable, "-c", self.script]

    def parse_output(self, raw_output: str, stderr: str, return_code: int) -> AgentResult:
        return AgentResult(raw_output=raw_output, is_success=return_code == 0, output_text=raw_output.strip())


def _request(tmp_path: Path, **kw) -> AgentRequest:
    return AgentRequest(task_prompt="go", workspace_dir=tmp_path, timeout=30, **kw)


def test_on_output_receives_lines_as_they_arrive(tmp_path: Path) -> None:
    script = (
        "import sys, time\n"
        "print('one', flush=True); time.sleep(0.2)\n"
        "print('two', flush=True)\n"
        "sys.stdout.write('three')\n"
    )
    seen: list[tuple[str, float]] = []
    start = time.monotonic()
    result = asyncio.run(ScriptAdapter(script).run_async(
        _request(tmp_path), on_output=lambda line: seen.append((line, time.monotonic() - start))
    ))
    assert [line for line, _ in seen] == ["one", "two", "three"]
    assert seen[0][1] < seen[-1][1]  # the first line came before the process ended
    assert result.is_success
    assert result.raw_output == "one\ntwo\nthree"


def test_streaming_runs_do_not_inherit_stdin(tmp_path: Path) -> None:
    script = "import sys; print(len(sys.stdin.read()))"
    result = asyncio.run(asyncio.wait_for(
        ScriptAdapter(script).run_async(_request(tmp_path), on_output=lambda line: None), timeout=20
    ))
    assert result.output_text == "0"


def test_a_failing_listener_does_not_break_the_run(tmp_path: Path) -> None:
    def boom(line: str) -> None:
        raise RuntimeError("listener bug")

    result = asyncio.run(ScriptAdapter("print('x')").run_async(_request(tmp_path), on_output=boom))
    assert result.is_success and result.output_text == "x"


def test_cancelling_kills_the_agent(tmp_path: Path) -> None:
    script = "import os, time; print(os.getpid(), flush=True); time.sleep(60)"
    pids: list[int] = []

    async def scenario() -> None:
        task = asyncio.create_task(ScriptAdapter(script).run_async(
            _request(tmp_path), on_output=lambda line: pids.append(int(line))
        ))
        for _ in range(100):
            if pids:
                break
            await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert pids
    try:
        os.kill(pids[0], 0)
    except ProcessLookupError:
        pass
    else:  # pragma: no cover - the child must be gone
        raise AssertionError("agent process survived cancellation")


def test_cancelled_run_still_syncs_refreshed_credentials(tmp_path: Path) -> None:
    target = tmp_path / "source-auth.json"
    target.write_text("old")
    script = (
        "import os, time, pathlib\n"
        "p = pathlib.Path(os.environ['HOME']) / '.codex' / 'auth.json'\n"
        "p.write_text('refreshed')\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    isolation = IsolationConfig(
        credential_contents={".codex/auth.json": SecretStr("old")},
        sync_back={".codex/auth.json": str(target)},
    )
    ready = asyncio.Event()

    async def scenario() -> None:
        task = asyncio.create_task(ScriptAdapter(script).run_async(
            _request(tmp_path, isolation=isolation), on_output=lambda line: ready.set()
        ))
        await asyncio.wait_for(ready.wait(), timeout=20)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert target.read_text() == "refreshed"
