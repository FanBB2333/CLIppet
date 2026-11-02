"""Adapter for GitHub Copilot CLI (``copilot``, npm ``@github/copilot``).

From GitHub's CLI command reference
(https://docs.github.com/en/copilot/reference/copilot-cli-reference):

* ``copilot -p PROMPT`` runs once.  It never asks: a tool that needs
  approval is refused, so headless runs pass ``--allow-all-tools`` (tools
  run, but files outside the folder and URLs still need their own grants;
  ``allow_all`` passes ``--allow-all`` for those too).
* ``--output-format json`` writes one session event per line,
  ``{"type", "data", "id", "timestamp", "parentId", "agentId"?}``, then a
  ``result`` line with the session id, the exit code and premium-request
  usage (no token counts).
* ``--resume=<id>`` reopens a session (``--continue`` the newest).
"""

from __future__ import annotations

import json
from typing import Any

from clippet.adapters.base import BaseSubprocessAdapter, render_extra_args
from clippet.models import AgentRequest, AgentResult, ToolCallRecord


class CopilotAdapter(BaseSubprocessAdapter):
    """Run Copilot CLI once (``copilot -p PROMPT``) or hand it the terminal."""

    isolation_agent = "copilot"
    close_stdin = True

    def __init__(
        self,
        model: str | None = None,
        effort: str | None = None,
        allow_all: bool = False,
        allow_tools: list[str] | None = None,
        deny_tools: list[str] | None = None,
        add_dirs: list[str] | None = None,
        json_events: bool = True,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            model: ``--model`` (``auto``, ``claude-sonnet-5.5``, …); *None* uses Copilot's setting.
            effort: ``--effort`` (low, medium, high, xhigh, max).
            allow_all: ``--allow-all`` (tools, paths and URLs) instead of ``--allow-all-tools``.
            allow_tools: ``--allow-tool`` rules (``shell(git:*)``, ``write``…), one flag each.
            deny_tools: ``--deny-tool`` rules; they win over any allow.
            add_dirs: more folders the agent may touch (``--add-dir``).
            json_events: headless runs write JSON events (else ``--silent``: the answer alone).
        """

        super().__init__(**kwargs)
        self.model = model
        self.effort = effort
        self.allow_all = allow_all
        self.allow_tools = allow_tools
        self.deny_tools = deny_tools
        self.add_dirs = add_dirs
        self.json_events = json_events

    @property
    def agent_name(self) -> str:
        return "copilot"

    def _model(self, model: str | None) -> list[str]:
        cmd: list[str] = []
        if model:
            cmd.extend(["--model", model])
        if self.effort:
            cmd.extend(["--effort", self.effort])
        return cmd

    def build_command(self, request: AgentRequest) -> list[str]:
        prompt = request.task_prompt
        if request.injected_skills:  # no system-prompt flag: the skills lead the prompt
            prompt = self._format_skills_text(request.injected_skills) + "\n\n---\n\n" + prompt
        # "-p -x" would read "-x" as a flag; the "=" form keeps such a prompt whole
        cmd = ["copilot", *(["--prompt=" + prompt] if prompt.startswith("-") else ["-p", prompt])]
        cmd.extend(["--output-format", "json"] if self.json_events else ["--silent"])
        cmd.append("--allow-all" if self.allow_all else "--allow-all-tools")
        for rule in self.allow_tools or []:
            cmd.extend(["--allow-tool", rule])
        for rule in self.deny_tools or []:
            cmd.extend(["--deny-tool", rule])
        for folder in self.add_dirs or []:
            cmd.extend(["--add-dir", folder])
        cmd.extend(self._model(request.model or self.model))
        cmd.extend(render_extra_args(request.extra_args))
        cmd.extend(request.extra_cli_args)
        return cmd

    def build_interactive_command(self, extra_args: list[str] | None = None) -> list[str]:
        return ["copilot", *self._model(self.model), *(extra_args or [])]

    def parse_output(self, raw_output: str, stderr: str, return_code: int) -> AgentResult:
        if not self.json_events:
            ok = return_code == 0
            return AgentResult(raw_output=raw_output, is_success=ok, output_text=raw_output.strip() if ok else None,
                               error_message=None if ok else (stderr.strip() or f"copilot exited with code {return_code}"))
        parsed = parse_copilot_events(raw_output)
        code = parsed["exit_code"] if isinstance(parsed["exit_code"], int) else return_code
        ok = return_code == 0 and code == 0 and parsed["answer"] is not None
        error = None
        if not ok:
            error = parsed["error"] or stderr.strip() or (f"copilot exited with code {return_code or code}"
                                                           if (return_code or code) else "copilot gave no answer")
        return AgentResult(
            raw_output=raw_output,
            is_success=ok,
            tool_calls=parsed["tool_calls"],
            steps_count=parsed["turns"],
            error_message=error,
            output_text=parsed["answer"] if ok else None,
            usage=parsed["usage"],
            session_id=parsed["session_id"],
        )


def copilot_usage(usage: Any) -> dict[str, Any] | None:
    """The ``result`` line's usage, under CLIppet's names."""

    if not isinstance(usage, dict):
        return None
    out: dict[str, Any] = {}
    premium = usage.get("premiumRequests")
    if isinstance(premium, (int, float)):
        out["premium_requests"] = premium
    changes = usage.get("codeChanges")
    if isinstance(changes, dict):
        for theirs, ours in (("linesAdded", "lines_added"), ("linesRemoved", "lines_removed")):
            if isinstance(changes.get(theirs), int):
                out[ours] = changes[theirs]
        if isinstance(changes.get("filesModified"), list):
            out["files_modified"] = len(changes["filesModified"])
    return out or None


def parse_copilot_events(raw_output: str) -> dict[str, Any]:
    """What a ``copilot -p … --output-format json`` run said.

    The answer is the last non-empty ``assistant.message`` of the main agent
    (sub-agents' messages carry an ``agentId``); the ``result`` line gives the
    session id and the exit code.
    """

    out: dict[str, Any] = {"session_id": None, "answer": None, "tool_calls": [], "usage": None, "turns": 0,
                           "error": None, "exit_code": None}
    for line in raw_output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if kind == "assistant.message" and not event.get("agentId"):
            content = data.get("content")
            if isinstance(content, str) and content.strip():
                out["answer"] = content
            out["turns"] += 1
        elif kind == "tool.execution_start":
            arguments = data.get("arguments")
            out["tool_calls"].append(ToolCallRecord(tool_name=str(data.get("toolName") or "tool"),
                                                    parameters=arguments if isinstance(arguments, dict) else {}))
        elif kind == "session.error":
            out["error"] = str(data.get("message") or data.get("errorType") or "session error")
        elif kind == "result":
            if event.get("sessionId"):
                out["session_id"] = str(event["sessionId"])
            out["exit_code"] = event.get("exitCode")
            out["usage"] = copilot_usage(event.get("usage"))
            if event.get("outcome") == "blocked":
                out["error"] = f"blocked: {event.get('blocker') or 'the task could not go on'}"
    return out
