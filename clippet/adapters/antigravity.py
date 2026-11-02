"""Adapter for Google Antigravity CLI (``agy``).

From https://antigravity.google/docs/cli/headless/:

* ``agy -p PROMPT`` runs once and exits; the answer goes to stdout and
  diagnostics to stderr.
* ``--output-format stream-json`` writes an ``init`` event, ``step_update``
  events (``user_input``, ``agent_response`` with ``text_delta``, ``tool``
  with ``tool_info``, ``checkpoint``) and a ``result`` event with the
  conversation id, ``status``, ``response``, ``error`` and token ``usage``.
* A tool that needs approval is soft-denied: the run goes on and still exits
  0.  Shell commands need approval by default, so headless runs pass
  ``--dangerously-skip-permissions`` (as Claude Code runs get
  ``bypassPermissions``) unless told otherwise.
* ``--conversation <id>`` resumes a conversation, ``-c`` the newest here.
"""

from __future__ import annotations

import json
from typing import Any

from clippet.adapters.base import BaseSubprocessAdapter, render_extra_args
from clippet.models import AgentRequest, AgentResult, ToolCallRecord


class AntigravityAdapter(BaseSubprocessAdapter):
    """Run ``agy`` once (``agy -p PROMPT``) or hand it the terminal."""

    isolation_agent = "agy"
    close_stdin = True

    def __init__(
        self,
        model: str | None = None,
        effort: str | None = None,
        mode: str | None = None,
        skip_permissions: bool = True,
        add_dirs: list[str] | None = None,
        json_events: bool = True,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            model: ``--model`` slug (``agy models`` lists them); *None* uses agy's setting.
            effort: ``--effort``.
            mode: ``--mode`` (accept-edits, auto, plan).
            skip_permissions: headless runs pass ``--dangerously-skip-permissions``;
                False leaves agy's own rules (shell commands are then refused).
            add_dirs: more folders for the workspace (``--add-dir``).
            json_events: headless runs write ``stream-json`` events (else plain text).
        """

        super().__init__(**kwargs)
        self.model = model
        self.effort = effort
        self.mode = mode
        self.skip_permissions = skip_permissions
        self.add_dirs = add_dirs
        self.json_events = json_events

    @property
    def agent_name(self) -> str:
        return "agy"

    def _options(self, model: str | None) -> list[str]:
        cmd: list[str] = []
        if model:
            cmd.extend(["--model", model])
        if self.effort:
            cmd.extend(["--effort", self.effort])
        if self.mode:
            cmd.extend(["--mode", self.mode])
        for folder in self.add_dirs or []:
            cmd.extend(["--add-dir", folder])
        return cmd

    def build_command(self, request: AgentRequest) -> list[str]:
        prompt = request.task_prompt
        if request.injected_skills:  # no system-prompt flag: the skills lead the prompt
            prompt = self._format_skills_text(request.injected_skills) + "\n\n---\n\n" + prompt
        cmd = ["agy", *(["--prompt=" + prompt] if prompt.startswith("-") else ["-p", prompt])]
        cmd.extend(["--output-format", "stream-json" if self.json_events else "text"])
        if self.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.extend(self._options(request.model or self.model))
        cmd.extend(render_extra_args(request.extra_args))
        cmd.extend(request.extra_cli_args)
        return cmd

    def build_interactive_command(self, extra_args: list[str] | None = None) -> list[str]:
        return ["agy", *self._options(self.model), *(extra_args or [])]

    def parse_output(self, raw_output: str, stderr: str, return_code: int) -> AgentResult:
        if not self.json_events:
            ok = return_code == 0
            return AgentResult(raw_output=raw_output, is_success=ok, output_text=raw_output.strip() if ok else None,
                               error_message=None if ok else (agy_error(stderr) or f"agy exited with code {return_code}"))
        parsed = parse_agy_events(raw_output)
        ok = return_code == 0 and parsed["status"] == "SUCCESS"
        error = None
        if not ok:
            error = parsed["error"] or agy_error(stderr) or (
                f"agy exited with code {return_code}" if return_code else f"agy ended: {parsed['status'] or 'no result'}")
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


def agy_error(stderr: str) -> str | None:
    """The message of an ``AGY_ERROR: {...}`` line on stderr, or its last line."""

    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("AGY_ERROR:"):
            try:
                data = json.loads(line[len("AGY_ERROR:"):].strip())
            except ValueError:
                return line
            if isinstance(data, dict):
                return str(data.get("message") or data.get("error") or line)
            return line
    return lines[-1] if lines else None


def agy_usage(usage: Any) -> dict[str, Any] | None:
    if not isinstance(usage, dict):
        return None
    out = dict(usage)
    if isinstance(usage.get("cache_read_tokens"), (int, float)):
        out["cached_input_tokens"] = int(usage["cache_read_tokens"])
    return out


def parse_agy_events(raw_output: str) -> dict[str, Any]:
    """What an ``agy -p … --output-format stream-json`` run said (its ``result`` event decides)."""

    out: dict[str, Any] = {"session_id": None, "answer": None, "tool_calls": [], "usage": None, "turns": 0,
                           "status": None, "error": None}
    for line in raw_output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("event")
        if event.get("conversation_id"):
            out["session_id"] = str(event["conversation_id"])
        if kind == "step_update" and isinstance(event.get("step_update"), dict):
            step = event["step_update"]
            info = step.get("tool_info") if isinstance(step.get("tool_info"), dict) else None
            if step.get("step_type") == "tool" and step.get("state") == "DONE" and info is not None:
                parameters = info.get("parameters")
                out["tool_calls"].append(ToolCallRecord(tool_name=str(info.get("name") or step.get("tool_name") or "tool"),
                                                        parameters=parameters if isinstance(parameters, dict) else {}))
        elif kind == "result" and isinstance(event.get("result"), dict):
            result = event["result"]
            if result.get("conversation_id"):
                out["session_id"] = str(result["conversation_id"])
            out["status"] = result.get("status")
            response = result.get("response")
            out["answer"] = response if isinstance(response, str) else None
            out["turns"] = result.get("num_turns") or 0
            out["usage"] = agy_usage(result.get("usage"))
            error = result.get("error")
            out["error"] = str(error) if error else (None if result.get("status") == "SUCCESS"
                                                     else f"agy ended: {result.get('status')}")
    return out
