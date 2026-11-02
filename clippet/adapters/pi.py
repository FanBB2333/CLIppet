"""Adapter for pi, the minimal terminal coding agent (https://pi.dev).

pi is ``@earendil-works/pi-coding-agent`` on npm (formerly
``@mariozechner/pi-coding-agent``); its binary is ``pi``.  It keeps every
login, for every model provider, in ``~/.pi/agent/auth.json`` and never asks
before running a tool.

Headless runs use ``pi --mode json``: a session header, then one JSON event
per line (assistant messages, tool executions, usage), and an exit when the
prompt is done.  pi reads piped stdin to its end before it starts, so stdin
is closed for it.
"""

from __future__ import annotations

import json
from typing import Any

from clippet.adapters.base import BaseSubprocessAdapter, render_extra_args
from clippet.models import AgentRequest, AgentResult, ToolCallRecord

#: Assistant stop reasons that mean the answer did not come through.
FAILED_STOPS = ("error", "aborted", "length")


class PiAdapter(BaseSubprocessAdapter):
    """Run pi once (``pi --mode json -- PROMPT``) or hand it the terminal (``pi ARGS``)."""

    isolation_agent = "pi"
    close_stdin = True

    def __init__(
        self,
        model: str | None = None,
        provider: str | None = None,
        thinking: str | None = None,
        tools: list[str] | None = None,
        append_system_prompt: str | None = None,
        json_events: bool = True,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            model: ``--model`` pattern (``sonnet``, ``openai/gpt-5.5:high``…).
                *None* uses the default in pi's settings.
            provider: ``--provider``; pi only takes it together with a model.
            thinking: ``--thinking`` level (off, minimal, low, medium, high, xhigh, max).
            tools: ``--tools`` allowlist (pi's own default: read, bash, edit, write).
            append_system_prompt: text (or a file path) for ``--append-system-prompt``.
            json_events: run headless with ``--mode json`` (else ``--print``,
                which writes only the final answer).
        """

        super().__init__(**kwargs)
        self.model = model
        self.provider = provider
        self.thinking = thinking
        self.tools = tools
        self.append_system_prompt = append_system_prompt
        self.json_events = json_events

    @property
    def agent_name(self) -> str:
        return "pi"

    def _options(self, model: str | None) -> list[str]:
        cmd: list[str] = []
        if model:
            if self.provider:
                cmd.extend(["--provider", self.provider])
            cmd.extend(["--model", model])
        if self.thinking:
            cmd.extend(["--thinking", self.thinking])
        if self.tools:
            cmd.extend(["--tools", ",".join(self.tools)])
        return cmd

    def build_command(self, request: AgentRequest) -> list[str]:
        cmd = ["pi", *(["--mode", "json"] if self.json_events else ["--print"])]
        cmd.extend(self._options(request.model or self.model))
        system = self.append_system_prompt or ""
        if request.injected_skills:
            skills = self._format_skills_text(request.injected_skills)
            system = f"{system}\n\n{skills}" if system else skills
        if system:
            cmd.extend(["--append-system-prompt", system])
        cmd.extend(render_extra_args(request.extra_args))
        cmd.extend(request.extra_cli_args)
        # "--" ends pi's options, so a prompt may start with "-".
        cmd.extend(["--", request.task_prompt])
        return cmd

    def build_interactive_command(self, extra_args: list[str] | None = None) -> list[str]:
        return ["pi", *self._options(self.model), *(extra_args or [])]

    def parse_output(self, raw_output: str, stderr: str, return_code: int) -> AgentResult:
        if not self.json_events:
            text = raw_output.strip()
            ok = return_code == 0
            return AgentResult(raw_output=raw_output, is_success=ok, output_text=text if ok else None,
                               error_message=None if ok else (stderr.strip() or f"pi exited with code {return_code}"))
        parsed = parse_pi_events(raw_output)
        failure = parsed["error"]
        ok = return_code == 0 and failure is None and parsed["answer"] is not None
        error = None
        if not ok:
            error = failure or stderr.strip() or (f"pi exited with code {return_code}" if return_code
                                                  else "pi gave no answer")
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


def pi_usage(usage: Any, into: dict[str, Any]) -> None:
    """Add one assistant message's ``usage`` to *into* (CLIppet's usage keys)."""

    if not isinstance(usage, dict):
        return
    for theirs, ours in (("input", "input_tokens"), ("output", "output_tokens"),
                         ("cacheRead", "cached_input_tokens"), ("cacheWrite", "cache_write_tokens")):
        value = usage.get(theirs)
        if isinstance(value, (int, float)):
            into[ours] = into.get(ours, 0) + int(value)
    cost = usage.get("cost")
    total = cost.get("total") if isinstance(cost, dict) else None
    if isinstance(total, (int, float)):
        into["cost_usd"] = round(into.get("cost_usd", 0.0) + float(total), 6)


def pi_text(message: dict[str, Any]) -> str:
    """The text blocks of a pi message, joined."""

    content = message.get("content")
    if isinstance(content, str):
        return content
    parts = [block.get("text", "") for block in content or [] if isinstance(block, dict) and block.get("type") == "text"]
    return "".join(part for part in parts if isinstance(part, str))


def parse_pi_events(raw_output: str) -> dict[str, Any]:
    """What a ``pi --mode json`` run said: its session, answer, tool calls, usage and failure."""

    out: dict[str, Any] = {"session_id": None, "answer": None, "tool_calls": [], "usage": None, "turns": 0,
                           "error": None}
    usage: dict[str, Any] = {}
    for line in raw_output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "session" and event.get("id"):
            out["session_id"] = str(event["id"])
        elif kind == "turn_end":
            out["turns"] += 1
        elif kind == "message_end" and isinstance(event.get("message"), dict):
            message = event["message"]
            if message.get("role") != "assistant":
                continue
            pi_usage(message.get("usage"), usage)
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "toolCall":
                    arguments = block.get("arguments")
                    out["tool_calls"].append(ToolCallRecord(tool_name=str(block.get("name") or "tool"),
                                                            parameters=arguments if isinstance(arguments, dict) else {}))
            stop = message.get("stopReason")
            if stop in FAILED_STOPS:
                out["error"] = str(message.get("errorMessage") or f"the model stopped: {stop}")
            elif stop != "toolUse":
                out["error"] = None
                text = pi_text(message)
                if text:
                    out["answer"] = text
    out["usage"] = usage or None
    return out
