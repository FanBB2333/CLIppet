"""Runs started from the TUI.

A headless run is one agent, one env and one prompt, streamed while it works:
Codex runs show their ``exec --json`` events as they arrive, other agents
their output when they finish.  An interactive launch hands the terminal to
the agent CLI the way ``clippet -e NAME AGENT ARGS...`` would, and comes back
with its exit code and anything CLIppet had to say.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from clippet.adapters.base import BaseSubprocessAdapter
from clippet.models import AgentRequest, AgentResult
from clippet.tui.data import BASE, EnvView

DEFAULT_TIMEOUT = 1800


@dataclass
class Step:
    """One thing a run did: a message, a command, an edit, a tool call…"""

    kind: str  # message | reasoning | command | edit | tool | search | todo | warning | error
    text: str
    detail: str = ""
    state: str = ""  # running | done | failed
    key: str = ""


@dataclass
class Run:
    id: int
    env: str
    agent: str
    prompt: str
    workspace: Path
    sandbox: str | None = None
    group: int | None = None
    state: str = "queued"  # queued | running | done | failed | cancelled
    created: float = field(default_factory=time.time)
    started: float | None = None
    ended: float | None = None
    steps: list[Step] = field(default_factory=list)
    lines: int = 0
    session_id: str | None = None
    usage: dict[str, Any] | None = None
    result: AgentResult | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    tail: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")

    @property
    def elapsed(self) -> float | None:
        if self.started is None:
            return None
        return (self.ended or time.time()) - self.started

    @property
    def answer(self) -> str:
        if self.result is not None:
            if self.result.output_text:
                return self.result.output_text
            if self.agent != "codex" and self.result.raw_output:
                return self.result.raw_output
        messages = [s.text for s in self.steps if s.kind == "message"]
        return messages[-1] if messages else ""

    @property
    def tokens(self) -> int | None:
        usage = self.usage or {}
        total = 0
        found = False
        for key in ("input_tokens", "output_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                total += int(value)
                found = True
        return total if found else None

    @property
    def activity(self) -> str:
        """What it is doing now, in a few words (for the ribbon)."""

        for step in reversed(self.steps):
            if step.kind == "command":
                return f"running `{step.text}`" if step.state == "running" else f"ran `{step.text}`"
            if step.kind == "edit":
                return f"editing {step.text}"
            if step.kind == "tool":
                return f"calling {step.text}"
            if step.kind == "search":
                return f"searching {step.text}"
            if step.kind == "reasoning":
                return "thinking"
            if step.kind == "message":
                return "writing the answer"
        return "starting" if self.state == "running" else self.state

    # --- Codex events ---------------------------------------------------------

    def _step(self, key: str) -> Step | None:
        if not key:
            return None
        for step in reversed(self.steps):
            if step.key == key:
                return step
        return None

    def _put(self, step: Step) -> None:
        existing = self._step(step.key)
        if existing is None:
            last = self.steps[-1] if self.steps else None
            if step.kind == "warning" and any(s.kind == "warning" and s.text == step.text for s in self.steps):
                return  # Codex repeats some warnings; once is enough
            if last is not None and not step.key and (last.kind, last.text) == (step.kind, step.text):
                return
            self.steps.append(step)
        else:
            existing.kind, existing.text, existing.detail, existing.state = step.kind, step.text, step.detail, step.state

    def feed(self, line: str) -> None:
        """Take one line of the agent's stdout."""

        self.lines += 1
        if self.agent != "codex":
            self.tail = (self.tail + [line])[-40:]
            return
        try:
            event = json.loads(line)
        except ValueError:
            if line.strip():
                self.tail = (self.tail + [line])[-40:]
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "thread.started":
            self.session_id = event.get("thread_id") or self.session_id
        elif kind == "turn.completed" and isinstance(event.get("usage"), dict):
            self.usage = event["usage"]
        elif kind == "turn.failed":
            err = event.get("error")
            message = err.get("message") if isinstance(err, dict) else None
            self._put(Step("error", str(message or "turn failed"), state="failed"))
        elif kind == "error" and event.get("message"):
            # non-fatal: a failed turn arrives as turn.failed
            self._put(Step("warning", str(event["message"]), state="done"))
        elif kind in ("item.started", "item.updated", "item.completed") and isinstance(event.get("item"), dict):
            step = step_from_item(event["item"], finished=kind == "item.completed")
            if step is not None:
                self._put(step)


def step_from_item(item: dict[str, Any], finished: bool) -> Step | None:
    kind = item.get("type")
    key = str(item.get("id") or "")
    status = str(item.get("status") or "")
    state = "failed" if status == "failed" else ("done" if finished or status == "completed" else "running")
    if kind == "agent_message":
        return Step("message", str(item.get("text") or ""), key=key, state=state)
    if kind == "reasoning":
        text = str(item.get("text") or "").strip()
        first = text.splitlines()[0].strip().strip("*").strip() if text else ""
        return Step("reasoning", first or "thinking", detail=text, key=key, state=state)
    if kind == "command_execution":
        exit_code = item.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0 and finished:
            state = "failed"
        output = str(item.get("aggregated_output") or "")
        detail = f"exit {exit_code}" if exit_code is not None else ""
        tail = "\n".join(output.rstrip().splitlines()[-6:])
        return Step("command", str(item.get("command") or ""), detail=(detail + ("\n" + tail if tail else "")).strip(),
                    key=key, state=state)
    if kind == "file_change":
        changes = item.get("changes") or []
        parts = []
        for change in changes if isinstance(changes, list) else []:
            if isinstance(change, dict):
                parts.append(f"{change.get('kind', 'edit')} {change.get('path', '')}".strip())
        return Step("edit", ", ".join(parts) or "files", key=key, state=state)
    if kind == "mcp_tool_call":
        return Step("tool", f"{item.get('server') or 'mcp'}.{item.get('tool') or 'tool'}", key=key, state=state)
    if kind == "web_search":
        return Step("search", str(item.get("query") or ""), key=key, state=state)
    if kind == "todo_list":
        items = item.get("items") or []
        total = len(items) if isinstance(items, list) else 0
        done = sum(1 for i in items if isinstance(i, dict) and i.get("completed")) if total else 0
        lines = [("☑ " if i.get("completed") else "☐ ") + str(i.get("text", "")) for i in items if isinstance(i, dict)]
        return Step("todo", f"{done}/{total} done", detail="\n".join(lines), key=key, state=state)
    if kind == "error":  # Codex's non-fatal notices (e.g. hook trust) come as error items
        return Step("warning", str(item.get("message") or "warning"), state="done")
    if kind in ("collab_tool_call", "tool_call", "function_call"):
        return Step("tool", str(item.get("tool") or item.get("name") or kind), key=key, state=state)
    return None


# --- starting runs -------------------------------------------------------------


def adapter_for(env: EnvView, agent: str, sandbox: str | None = None) -> BaseSubprocessAdapter:
    """The adapter that runs *agent* as *env* (``base`` is the real HOME)."""

    from clippet.adapters.claude import ClaudeAdapter
    from clippet.adapters.codex import CodexAdapter
    from clippet.adapters.gemini import GeminiAdapter
    from clippet.adapters.qodercli import QoderCLIAdapter
    from clippet.config.detector import (
        create_adapter_from_config_file,
        create_adapter_with_second_home,
        detect_config_type,
    )

    kwargs: dict[str, Any] = {"sandbox": sandbox} if agent == "codex" and sandbox else {}
    if env.kind == BASE:
        classes = {"codex": CodexAdapter, "claude": ClaudeAdapter, "gemini": GeminiAdapter, "qodercli": QoderCLIAdapter}
        return classes[agent](**kwargs)
    if not env.exists or env.path is None:
        raise ValueError(f"env '{env.name}' is missing on disk: {env.path}")
    if env.is_home:
        return create_adapter_with_second_home(env.path, agent, **kwargs)
    detected = detect_config_type(str(env.path))
    if detected != agent:
        raise ValueError(f"env '{env.name}' holds a {detected} config, so it cannot run {agent}")
    adapter = create_adapter_from_config_file(str(env.path), agent)
    if kwargs:
        adapter.sandbox = sandbox
    return adapter


def prepare_env(env: EnvView, agent: str) -> list[str]:
    """Upkeep before a run in a HOME env: shared dotfiles, a linked Codex login."""

    from clippet.config.environments import sync_linked_codex_credential
    from clippet.isolation import ensure_shared_links

    notes: list[str] = []
    if env.kind != "home" or env.path is None or not env.exists:
        return notes
    try:
        ensure_shared_links(env.path, env.share)
    except OSError as exc:
        notes.append(f"could not link shared dotfiles: {exc}")
    if agent == "codex":
        result = sync_linked_codex_credential(env.name, env.profile)
        if result.action == "updated":
            notes.append(result.message)
        elif result.action in ("env-newer", "error"):
            notes.append(f"warning: {result.message}")
    return notes


async def execute(
    run: Run,
    adapter: BaseSubprocessAdapter,
    on_change: Callable[[Run], None],
    timeout: int = DEFAULT_TIMEOUT,
) -> Run:
    """Carry out *run* with *adapter*, telling *on_change* whenever it moves."""

    run.state = "running"
    run.started = time.time()
    on_change(run)
    request = AgentRequest(task_prompt=run.prompt, workspace_dir=run.workspace, timeout=timeout)

    def on_output(line: str) -> None:
        run.feed(line)
        on_change(run)

    try:
        result = await adapter.run_async(request, on_output=on_output)
    except asyncio.CancelledError:
        run.state = "cancelled"
        run.ended = time.time()
        on_change(run)
        raise
    except Exception as exc:  # noqa: BLE001 - shown on the run
        run.state = "failed"
        run.error = str(exc)
        run.ended = time.time()
        on_change(run)
        return run
    run.result = result
    run.session_id = result.session_id or run.session_id
    run.usage = result.usage or run.usage
    run.state = "done" if result.is_success else "failed"
    if not result.is_success:
        run.error = result.error_message or "the agent reported a failure"
    run.ended = time.time()
    on_change(run)
    return run


# --- interactive launches --------------------------------------------------------


class _Tee(io.TextIOBase):
    """Writes to the terminal and keeps a copy."""

    def __init__(self, stream: Any, copy: io.StringIO) -> None:
        self.stream = stream
        self.copy = copy

    def write(self, text: str) -> int:
        self.copy.write(text)
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, ValueError):
            pass
        return len(text)

    def flush(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            self.stream.flush()


def launch_interactive(env: EnvView, agent: str, args: list[str], cwd: Path) -> tuple[int, list[str]]:
    """Run *agent* in the foreground as ``clippet -e ENV AGENT -- ARGS`` would.

    Call it while the TUI is suspended.  Returns ``(exit code, notes)`` where
    the notes are what CLIppet printed (a pulled login, a refreshed token).
    """

    from clippet import cli

    copy = io.StringIO()
    code = 0
    previous = os.getcwd()
    with contextlib.redirect_stderr(_Tee(sys.stderr, copy)):
        try:
            os.chdir(cwd)
            if env.kind == BASE:
                cli._launch_native_directly(agent, list(args))
            else:
                argv = ["-e", env.name, agent]
                if args:
                    argv += ["--", *args]
                cli._handle_run(cli._build_parser().parse_args(argv))
        except SystemExit as exc:
            if isinstance(exc.code, int):
                code = exc.code
            elif exc.code is None:
                code = 0
            else:
                code = 1
                copy.write(f"Error: {exc.code}\n")
        except Exception as exc:  # noqa: BLE001 - reported back in the TUI
            code = 1
            copy.write(f"Error: {exc}\n")
        finally:
            os.chdir(previous)
    notes = []
    for line in copy.getvalue().splitlines():
        if line.startswith("clippet: "):
            notes.append(line[len("clippet: "):])
        elif line.startswith("Error: "):
            notes.append(line)
    return code, notes
