"""Codex CLI adapter for CLIppet framework."""

from __future__ import annotations

import json
from typing import Any

from clippet.adapters.base import BaseSubprocessAdapter, render_extra_args
from clippet.models import AgentRequest, AgentResult, ToolCallRecord
from clippet.parsers.extractors import parse_codex_output

#: Sandbox used for headless ``codex exec`` runs when none is configured.
DEFAULT_EXEC_SANDBOX = "workspace-write"


class CodexAdapter(BaseSubprocessAdapter):
    """Adapter for invoking the Codex CLI.

    Headless runs use ``codex exec --json`` and turn its JSONL event stream
    into a structured :class:`AgentResult` (final message, tool calls, token
    usage, thread id).

    Example usage:
        adapter = CodexAdapter(sandbox="workspace-write")
        request = AgentRequest(task_prompt="Fix the bug in main.py")
        result = adapter.run(request)
    """

    isolation_agent = "codex"

    def __init__(
        self,
        model: str | None = None,
        sandbox: str | None = None,
        full_auto: bool = True,
        cd: str | None = None,
        add_dirs: list[str] | None = None,
        config_overrides: dict[str, str] | None = None,
        skip_git_repo_check: bool = True,
        json_events: bool = True,
        profile: str | None = None,
    ) -> None:
        """Initialize the Codex adapter.

        Args:
            model: Model name.  *None* (the default) omits ``--model`` so the
                Codex CLI uses the model from its own ``config.toml``.
            sandbox: Sandbox policy — ``read-only``, ``workspace-write`` or
                ``danger-full-access``.  *None* leaves interactive sessions on
                Codex's own default; headless runs fall back to
                ``workspace-write``.
            full_auto: Kept for backwards compatibility.  Codex removed the
                ``--full-auto`` flag; ``codex exec`` never prompts for
                approval, so this no longer changes the command.
            cd: Optional working-root directory (``-C``/``--cd``).
            add_dirs: Additional writable directories (``--add-dir``).
            config_overrides: Optional dict of ``key=value`` configuration
                overrides passed via ``-c``.
            skip_git_repo_check: Pass ``--skip-git-repo-check`` to
                ``codex exec`` so explicitly chosen workspaces outside Git
                repositories work.
            json_events: Use ``codex exec --json`` and parse its event stream.
            profile: Optional Codex config profile (``--profile``).
        """
        super().__init__()
        self.model = model
        self.sandbox = sandbox
        self.full_auto = full_auto
        self.cd = cd
        self.add_dirs = add_dirs
        self.config_overrides = config_overrides
        self.skip_git_repo_check = skip_git_repo_check
        self.json_events = json_events
        self.profile = profile

    @property
    def agent_name(self) -> str:
        """Return the name of the agent."""
        return "codex"

    def _config_flags(self) -> list[str]:
        flags: list[str] = []
        for key, value in (self.config_overrides or {}).items():
            flags.extend(["-c", f"{key}={value}"])
        return flags

    def build_command(self, request: AgentRequest) -> list[str]:
        """Build the ``codex exec`` command.

        When ``request.injected_skills`` is non-empty, the skill texts are
        formatted and prepended to the task prompt (separated by a clear
        delimiter) since the Codex CLI has no system-prompt injection flag.
        """
        cmd: list[str] = ["codex", "exec"]

        if self.json_events:
            cmd.append("--json")

        # Model — request.model wins; when both are None let config.toml decide.
        effective_model = request.model if request.model else self.model
        if effective_model:
            cmd.extend(["--model", effective_model])

        if self.profile:
            cmd.extend(["--profile", self.profile])

        cmd.extend(["--sandbox", self.sandbox or DEFAULT_EXEC_SANDBOX])

        if self.skip_git_repo_check:
            cmd.append("--skip-git-repo-check")

        cd = self.cd if self.cd is not None else str(request.workspace_dir)
        cmd.extend(["--cd", cd])

        for directory in self.add_dirs or []:
            cmd.extend(["--add-dir", directory])

        cmd.extend(self._config_flags())
        cmd.extend(render_extra_args(request.extra_args))
        cmd.extend(request.extra_cli_args)

        effective_prompt = request.task_prompt
        if request.injected_skills:
            skills_block = self._format_skills_text(request.injected_skills)
            effective_prompt = skills_block + "\n\n---\n\n" + effective_prompt

        # The prompt is positional; "--" keeps prompts starting with "-" intact.
        cmd.extend(["--", effective_prompt])
        return cmd

    def build_interactive_command(self, extra_args: list[str] | None = None) -> list[str]:
        """Build the interactive ``codex`` command.

        Only explicitly configured options are emitted; everything else is
        left to the Codex CLI and the ``config.toml`` of the active HOME.
        ``extra_args`` are appended verbatim (subcommands such as ``resume``
        or ``login`` included).
        """
        cmd = ["codex"]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.profile:
            cmd.extend(["--profile", self.profile])
        if self.sandbox:
            cmd.extend(["--sandbox", self.sandbox])
        cmd.extend(self._config_flags())
        cmd.extend(extra_args or [])
        return cmd

    def get_stdin_input(self, request: AgentRequest) -> str | None:
        """Codex exec takes the prompt as a positional argument."""
        return None

    def parse_output(
        self, raw_output: str, stderr: str, return_code: int
    ) -> AgentResult:
        """Parse Codex CLI output into a structured AgentResult."""

        events = parse_codex_events(raw_output)
        if events is None:
            return self._parse_plain_output(raw_output, stderr, return_code)

        summary = summarize_codex_events(events)
        failed = summary["failed"]
        is_success = return_code == 0 and not failed

        error_message: str | None = None
        if not is_success:
            error_message = (
                summary["error"]
                or (stderr.strip() if stderr and stderr.strip() else None)
                or f"codex exited with code {return_code}"
            )

        return AgentResult(
            raw_output=raw_output,
            is_success=is_success,
            tool_calls=summary["tool_calls"],
            steps_count=summary["steps"],
            error_message=error_message,
            output_text=summary["final_message"],
            usage=summary["usage"],
            session_id=summary["thread_id"],
        )

    def _parse_plain_output(
        self, raw_output: str, stderr: str, return_code: int
    ) -> AgentResult:
        parsed = parse_codex_output(raw_output)
        is_success = not parsed["is_error"] and return_code == 0

        error_message: str | None = None
        if not is_success:
            if stderr and stderr.strip():
                error_message = stderr.strip()
            elif parsed.get("is_error"):
                error_message = parsed.get("result_text", "Unknown error")
            else:
                error_message = f"codex exited with code {return_code}"

        text = parsed.get("result_text")
        return AgentResult(
            raw_output=raw_output,
            is_success=is_success,
            tool_calls=[],
            steps_count=0,
            error_message=error_message,
            output_text=text if isinstance(text, str) and text else None,
        )


# ---------------------------------------------------------------------------
# codex exec --json event stream
# ---------------------------------------------------------------------------


def parse_codex_events(raw_output: str) -> list[dict[str, Any]] | None:
    """Parse ``codex exec --json`` output into events.

    Returns *None* when the output does not look like a Codex event stream
    (e.g. plain-text output from ``json_events=False``).  Non-JSON lines in an
    event stream (warnings) are ignored.
    """

    events: list[dict[str, Any]] = []
    for line in raw_output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("type"), str):
            events.append(event)
    if not events:
        return None
    known = {"thread.started", "turn.started", "turn.completed", "turn.failed", "error"}
    if not any(e["type"] in known or e["type"].startswith("item.") for e in events):
        return None
    return events


def _tool_call_from_item(item: dict[str, Any]) -> ToolCallRecord | None:
    kind = item.get("type")
    if kind == "command_execution":
        params = {
            key: item[key]
            for key in ("command", "exit_code", "status", "aggregated_output")
            if key in item
        }
        return ToolCallRecord(tool_name="shell", parameters=params)
    if kind == "file_change":
        params = {key: item[key] for key in ("changes", "status") if key in item}
        return ToolCallRecord(tool_name="apply_patch", parameters=params)
    if kind == "mcp_tool_call":
        server = item.get("server") or "mcp"
        tool = item.get("tool") or "tool"
        params: dict[str, Any] = {"status": item.get("status")}
        arguments = item.get("arguments")
        if isinstance(arguments, dict):
            params["arguments"] = arguments
        elif arguments is not None:
            params["arguments"] = arguments
        return ToolCallRecord(tool_name=f"{server}.{tool}", parameters=params)
    if kind == "web_search":
        return ToolCallRecord(tool_name="web_search", parameters={"query": item.get("query")})
    if kind in {"collab_tool_call", "tool_call", "function_call"}:
        name = item.get("tool") or item.get("name") or kind
        return ToolCallRecord(tool_name=str(name), parameters={
            k: v for k, v in item.items() if k not in {"id", "type"}
        })
    return None


def summarize_codex_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a Codex event stream into the fields of an AgentResult."""

    final_message: str | None = None
    tool_calls: list[ToolCallRecord] = []
    steps = 0
    thread_id: str | None = None
    usage: dict[str, Any] | None = None
    turn_failed = False
    completed_turns = 0
    saw_error = False
    error: str | None = None

    for event in events:
        kind = event.get("type")
        if kind == "thread.started":
            thread_id = event.get("thread_id") or thread_id
        elif kind == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    final_message = text
                steps += 1
            elif item_type == "error":
                message = item.get("message")
                if isinstance(message, str) and message:
                    error = message
            elif item_type in ("reasoning", "todo_list"):
                continue
            else:
                record = _tool_call_from_item(item)
                if record is not None:
                    tool_calls.append(record)
                steps += 1
        elif kind == "turn.completed":
            completed_turns += 1
            turn_usage = event.get("usage")
            if isinstance(turn_usage, dict):
                # Codex reports running totals for the thread: keep the last.
                usage = dict(turn_usage)
        elif kind == "turn.failed":
            turn_failed = True
            err = event.get("error")
            if isinstance(err, dict) and err.get("message"):
                error = str(err["message"])
        elif kind == "error":
            # Also emitted for transient problems (e.g. stream reconnects),
            # so it only counts as a failure when no turn completed.
            saw_error = True
            if event.get("message"):
                error = str(event["message"])

    failed = turn_failed or (saw_error and completed_turns == 0)
    return {
        "final_message": final_message,
        "tool_calls": tool_calls,
        "steps": steps,
        "thread_id": thread_id,
        "usage": usage,
        "failed": failed,
        "error": error if failed else None,
    }
