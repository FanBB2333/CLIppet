"""Runs started from the TUI.

A headless run is one agent, one env and one prompt, streamed while it works:
each agent's JSON events (Codex ``exec --json``, the stream-json Claude Code
and Qoder CLI share, pi's ``--mode json``, Copilot CLI's and Antigravity's)
become steps as they arrive.  An interactive launch hands the terminal to
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
            if self.agent not in READERS and self.result.raw_output:
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
    def cost(self) -> str | None:
        """What the run cost, as its agent counts it (dollars, premium requests)."""

        usage = self.usage or {}
        if isinstance(usage.get("cost_usd"), (int, float)) and usage["cost_usd"] > 0:
            return f"${usage['cost_usd']:.4f}".rstrip("0").rstrip(".") if usage["cost_usd"] < 1 else f"${usage['cost_usd']:.2f}"
        premium = usage.get("premium_requests")
        if isinstance(premium, (int, float)) and premium > 0:
            return f"{premium:g} premium request" + ("" if premium == 1 else "s")
        return None

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
        reader = READERS.get(self.agent)
        if reader is None:
            self.tail = (self.tail + [line])[-40:]
            return
        try:
            event = json.loads(line)
        except ValueError:
            if line.strip():
                self.tail = (self.tail + [line])[-40:]
            return
        if isinstance(event, dict):
            reader(self, event)


def _read_codex(run: Run, event: dict[str, Any]) -> None:
    """``codex exec --json``."""

    kind = event.get("type")
    if kind == "thread.started":
        run.session_id = event.get("thread_id") or run.session_id
    elif kind == "turn.completed" and isinstance(event.get("usage"), dict):
        run.usage = event["usage"]
    elif kind == "turn.failed":
        err = event.get("error")
        message = err.get("message") if isinstance(err, dict) else None
        run._put(Step("error", str(message or "turn failed"), state="failed"))
    elif kind == "error" and event.get("message"):
        # non-fatal: a failed turn arrives as turn.failed
        run._put(Step("warning", str(event["message"]), state="done"))
    elif kind in ("item.started", "item.updated", "item.completed") and isinstance(event.get("item"), dict):
        step = step_from_item(event["item"], finished=kind == "item.completed")
        if step is not None:
            run._put(step)


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


# --- other agents' events --------------------------------------------------------

_COMMAND_TOOLS = {"bash", "shell", "powershell", "run_command", "run_shell_command", "exec_command",
                  "execute_command", "command"}
_EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit", "create", "str_replace_editor", "apply_patch",
               "write_file", "edit_file", "replace", "write_to_file", "replace_file_content",
               "multi_replace_file_content"}
_SEARCH_TOOLS = {"websearch", "web_search", "webfetch", "web_fetch", "search_web", "read_url_content", "fetch"}


def _first(values: Any, *names: str) -> str:
    if isinstance(values, dict):
        for name in names:
            value = values.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _text_of(content: Any) -> str:
    """The text of a tool result or message: a string, or blocks carrying ``text``."""

    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return _text_of(content.get("content") if "content" in content else content.get("text"))
    if isinstance(content, list):
        return "\n".join(part for part in (_text_of(item) for item in content) if part)
    return ""


def _tail(text: str, lines: int = 6) -> str:
    return "\n".join(text.rstrip().splitlines()[-lines:])


def tool_step(name: Any, args: Any, key: str, state: str) -> Step:
    """A step for a tool call, whichever agent made it."""

    name = str(name or "tool")
    plain = name.lower().rsplit(".", 1)[-1]
    if plain in _COMMAND_TOOLS:
        return Step("command", _first(args, "command", "CommandLine", "cmd") or name, key=key, state=state)
    if plain in _EDIT_TOOLS:
        target = _first(args, "file_path", "path", "filePath", "TargetFile", "AbsolutePath", "notebook_path")
        return Step("edit", target or name, key=key, state=state)
    if plain in _SEARCH_TOOLS:
        return Step("search", _first(args, "query", "url", "Url", "Query") or name, key=key, state=state)
    if plain in ("todowrite", "update_todos", "write_todos") and isinstance(args, dict):
        todos = args.get("todos") if isinstance(args.get("todos"), list) else []
        done = [t for t in todos if isinstance(t, dict) and t.get("status") == "completed"]
        lines = [("☑ " if t in done else "☐ ") + str(t.get("content") or t.get("text") or "")
                 for t in todos if isinstance(t, dict)]
        return Step("todo", f"{len(done)}/{len(todos)} done", detail="\n".join(lines), key=key, state=state)
    hint = _first(args, "file_path", "path", "pattern", "query", "description")
    return Step("tool", f"{name} {hint}".strip(), key=key, state=state)


def _finish_tool(run: Run, key: str, failed: bool, output: str) -> None:
    step = run._step(key)
    if step is None:
        return
    step.state = "failed" if failed else "done"
    tail = _tail(output)
    if tail and step.kind != "todo":
        step.detail = tail


def _reasoning(text: str, key: str) -> Step:
    text = text.strip()
    first = text.splitlines()[0].strip().strip("*").strip() if text else ""
    return Step("reasoning", first or "thinking", detail=text, key=key, state="done")


def _read_claude(run: Run, event: dict[str, Any]) -> None:
    """Claude Code's ``--output-format stream-json``, which Qoder CLI speaks too."""

    from clippet.parsers.extractors import claude_usage

    kind = event.get("type")
    if event.get("session_id"):
        run.session_id = str(event["session_id"])
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    blocks = message.get("content") if isinstance(message.get("content"), list) else []
    if kind == "assistant":
        stem = str(event.get("uuid") or message.get("id") or run.lines)
        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            key = f"{stem}:{index}"
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                run._put(Step("message", str(block["text"]), key=key, state="done"))
            elif block.get("type") == "thinking" and block.get("thinking"):
                run._put(_reasoning(str(block["thinking"]), key))
            elif block.get("type") == "tool_use":
                run._put(tool_step(block.get("name"), block.get("input"), str(block.get("id") or key), "running"))
    elif kind == "user":
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                _finish_tool(run, str(block.get("tool_use_id") or ""), bool(block.get("is_error")),
                             _text_of(block.get("content")))
    elif kind == "result":
        run.usage = claude_usage(event.get("usage"), event.get("total_cost_usd"))
        subtype = str(event.get("subtype") or "")
        if event.get("is_error") or subtype.startswith("error"):
            errors = event.get("errors") if isinstance(event.get("errors"), list) else []
            text = "; ".join(str(e) for e in errors) or str(event.get("result") or subtype or "the run failed")
            run._put(Step("error", text, state="failed"))


def _read_pi(run: Run, event: dict[str, Any]) -> None:
    """``pi --mode json``."""

    from clippet.adapters.pi import FAILED_STOPS, pi_usage

    kind = event.get("type")
    if kind == "session" and event.get("id"):
        run.session_id = str(event["id"])
    elif kind == "message_end" and isinstance(event.get("message"), dict):
        message = event["message"]
        if message.get("role") != "assistant":
            return
        usage = dict(run.usage or {})
        pi_usage(message.get("usage"), usage)
        run.usage = usage or None
        stem = f"m{run.lines}"
        for index, block in enumerate(message.get("content") or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                run._put(Step("message", str(block["text"]), key=f"{stem}:{index}", state="done"))
            elif block.get("type") == "thinking" and block.get("thinking"):
                run._put(_reasoning(str(block["thinking"]), f"{stem}:{index}"))
        if message.get("stopReason") in FAILED_STOPS:
            text = message.get("errorMessage") or f"the model stopped: {message.get('stopReason')}"
            run._put(Step("error", str(text), state="failed"))
    elif kind == "tool_execution_start":
        run._put(tool_step(event.get("toolName"), event.get("args"), str(event.get("toolCallId") or ""), "running"))
    elif kind == "tool_execution_end":
        _finish_tool(run, str(event.get("toolCallId") or ""), bool(event.get("isError")), _text_of(event.get("result")))


def _read_copilot(run: Run, event: dict[str, Any]) -> None:
    """``copilot -p … --output-format json``."""

    from clippet.adapters.copilot import copilot_usage

    kind = event.get("type")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if kind == "assistant.message" and not event.get("agentId"):
        content = data.get("content")
        if isinstance(content, str) and content.strip():
            run._put(Step("message", content, key=str(event.get("id") or data.get("messageId") or ""), state="done"))
    elif kind == "assistant.reasoning" and not event.get("agentId"):
        text = data.get("content")
        if isinstance(text, str) and text.strip():
            run._put(_reasoning(text, str(event.get("id") or "")))
    elif kind == "tool.execution_start":
        run._put(tool_step(data.get("toolName"), data.get("arguments"), str(data.get("toolCallId") or ""), "running"))
    elif kind == "tool.execution_complete":
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        output = str(result.get("content") or error.get("message") or "")
        _finish_tool(run, str(data.get("toolCallId") or ""), data.get("success") is False, output)
    elif kind == "session.error":
        run._put(Step("error", str(data.get("message") or data.get("errorType") or "session error"), state="failed"))
    elif kind == "result":
        if event.get("sessionId"):
            run.session_id = str(event["sessionId"])
        run.usage = copilot_usage(event.get("usage"))


def _read_agy(run: Run, event: dict[str, Any]) -> None:
    """``agy -p … --output-format stream-json``."""

    from clippet.adapters.antigravity import agy_usage

    kind = event.get("event")
    payload = event.get(kind) if isinstance(kind, str) and isinstance(event.get(kind), dict) else {}
    if payload.get("conversation_id") or event.get("conversation_id"):
        run.session_id = str(payload.get("conversation_id") or event.get("conversation_id"))
    if kind == "step_update":
        key = f"s{payload.get('step_index')}"
        done = payload.get("state") == "DONE"
        if payload.get("step_type") == "agent_response":
            existing = run._step(key)
            text = (existing.text if existing is not None else "") + str(payload.get("text_delta") or "")
            if text.strip():
                run._put(Step("message", text, key=key, state="done" if done else "running"))
        elif payload.get("step_type") == "tool" and isinstance(payload.get("tool_info"), dict):
            info = payload["tool_info"]
            step = tool_step(info.get("name") or payload.get("tool_name"), info.get("parameters"), key,
                             "done" if done else "running")
            error = info.get("error") if isinstance(info.get("error"), dict) else None
            if error is not None:
                step.state = "failed"
            output = _text_of(error.get("message") if error else info.get("output"))
            if done and output:
                step.detail = _tail(output)
            run._put(step)
    elif kind == "result":
        run.usage = agy_usage(payload.get("usage"))
        if payload.get("status") not in (None, "SUCCESS"):
            run._put(Step("error", str(payload.get("error") or f"agy ended: {payload.get('status')}"), state="failed"))


#: How each agent's headless runs are read as they stream (others are shown when they end).
READERS: dict[str, Callable[[Run, dict[str, Any]], None]] = {
    "codex": _read_codex,
    "claude": _read_claude,
    "qodercli": _read_claude,
    "pi": _read_pi,
    "copilot": _read_copilot,
    "agy": _read_agy,
}


# --- starting runs -------------------------------------------------------------


def adapter_for(env: EnvView, agent: str, sandbox: str | None = None) -> BaseSubprocessAdapter:
    """The adapter that runs *agent* as *env* (``base`` is the real HOME)."""

    from clippet.agents import agent_spec
    from clippet.config.detector import (
        create_adapter_from_config_file,
        create_adapter_with_second_home,
        detect_config_type,
    )

    kwargs: dict[str, Any] = {"sandbox": sandbox} if agent == "codex" and sandbox else {}
    if agent in ("claude", "qodercli"):
        kwargs["output_format"] = "stream-json"  # read as it streams (READERS)
    if env.kind == BASE:
        spec = agent_spec(agent)
        if spec is None:
            raise ValueError(f"CLIppet does not know the agent '{agent}'")
        return spec.adapter_class()(**kwargs)
    if not env.exists or env.path is None:
        raise ValueError(f"env '{env.name}' is missing on disk: {env.path}")
    if env.is_home:
        return create_adapter_with_second_home(env.path, agent, **kwargs)
    detected = detect_config_type(str(env.path))
    if agent_spec(detected) is not agent_spec(agent):  # "claude_code" is how detection says claude
        raise ValueError(f"env '{env.name}' holds a {detected} config, so it cannot run {agent}")
    adapter = create_adapter_from_config_file(str(env.path), agent)
    for name, value in kwargs.items():
        setattr(adapter, name, value)
    return adapter


def prepare_env(env: EnvView, agent: str) -> list[str]:
    """Upkeep before a run in a HOME env: shared dotfiles, a linked Codex login."""

    from clippet.agents import agent_spec
    from clippet.config.environments import sync_linked_codex_credential
    from clippet.isolation import ensure_shared_links

    notes: list[str] = []
    if env.kind != "home" or env.path is None or not env.exists:
        return notes
    spec = agent_spec(agent)
    try:
        ensure_shared_links(env.path, [*env.share, *(spec.runtime if spec else ())])
    except OSError as exc:
        notes.append(f"could not link shared dotfiles: {exc}")
    if agent == "codex" and env.linked:
        from clippet.desktop import is_open

        if is_open(env.path):
            notes.append("its Codex app is open, so its login is not pulled now")
            return notes
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
