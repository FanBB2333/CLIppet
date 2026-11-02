"""QoderCLI adapter for CLIppet framework.

Qoder CLI (binary ``qodercli``; its docs now call the command ``qoder``, a
dispatcher that can also open the Qoder IDE) is a terminal coding agent.
Checked against https://docs.qoder.com/cli/ for version 1.1.81:

* ``-p``/``--print`` runs once; the prompt follows it.
* ``-o``/``--output-format`` is ``text``, ``json`` or ``stream-json`` (it was
  ``-f``); ``stream-json`` speaks Claude Code's protocol (``system``/``init``,
  ``assistant``, ``user``, ``result``).
* Headless runs refuse everything that would ask, shell commands included,
  unless a permission mode allows it, so headless runs default to ``--yolo``
  as Claude Code's default to ``bypassPermissions``.
* ``--append-system-prompt`` takes extra instructions (skills go there).
* ``--with-claude-config`` is gone; the option is accepted and ignored.
"""

from clippet.adapters.base import BaseSubprocessAdapter, render_extra_args
from clippet.models import AgentRequest, AgentResult
from clippet.parsers.extractors import parse_claude_stream, parse_qoder_output

OUTPUT_FORMATS = ("text", "json", "stream-json")


class QoderCLIAdapter(BaseSubprocessAdapter):
    """Adapter for the QoderCLI agent.

    QoderCLI is invoked via `qodercli [options]` for interactive mode,
    or `qodercli -p [prompt]` for non-interactive mode.
    """

    isolation_agent = "qodercli"

    def __init__(
        self,
        model: str | None = None,
        workspace: str | None = None,
        max_turns: int | None = None,
        output_format: str = "text",
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        yolo: bool = False,
        with_claude_config: bool = False,
        permission_mode: str | None = None,
        append_system_prompt: str | None = None,
        **kwargs,
    ) -> None:
        """Initialize the QoderCLI adapter.

        Args:
            model: Model tier (auto, efficient, performance, ultimate) or a
                model id from ``qodercli --list-models``.
            workspace: Working directory for the session (``-w``).
            max_turns: Maximum number of agent iterations.
            output_format: Output format for non-interactive mode (text, json, stream-json).
            allowed_tools: Tool rules allowed without asking (``Read,Bash(git status)``).
            disallowed_tools: Tool rules always refused.
            yolo: Bypass every permission check, also in interactive sessions.
            with_claude_config: Ignored: Qoder CLI no longer has ``--with-claude-config``.
            permission_mode: ``default``, ``accept_edits``, ``auto``,
                ``bypass_permissions`` or ``dont_ask``.  *None* means
                ``--yolo`` for headless runs (which cannot answer a prompt)
                and Qoder CLI's own default for interactive sessions.
            append_system_prompt: Extra instructions for ``--append-system-prompt``.
            **kwargs: Additional options passed to base class.
        """
        super().__init__(**kwargs)
        self.model = model
        self.workspace = workspace
        self.max_turns = max_turns
        self.output_format = output_format if output_format in OUTPUT_FORMATS else "text"
        self.allowed_tools = allowed_tools
        self.disallowed_tools = disallowed_tools
        self.yolo = yolo
        self.with_claude_config = with_claude_config
        self.permission_mode = permission_mode
        self.append_system_prompt = append_system_prompt

    @property
    def agent_name(self) -> str:
        """Return the name of the agent."""
        return "qodercli"

    def _shared(self, workspace: str | None, headless: bool) -> list[str]:
        """Options both modes take: model, folder, turns, tools, permissions."""

        cmd: list[str] = []
        if self.model:
            cmd.extend(["--model", self.model])
        if workspace:
            cmd.extend(["-w", workspace])
        if self.max_turns:
            cmd.extend(["--max-turns", str(self.max_turns)])
        if self.allowed_tools:
            cmd.extend(["--allowed-tools", ",".join(self.allowed_tools)])
        if self.disallowed_tools:
            cmd.extend(["--disallowed-tools", ",".join(self.disallowed_tools)])
        if self.yolo or (headless and self.permission_mode is None):
            cmd.append("--yolo")
        elif self.permission_mode:
            cmd.extend(["--permission-mode", self.permission_mode])
        return cmd

    def build_command(self, request: AgentRequest) -> list[str]:
        """Build the headless ``qodercli -p PROMPT`` command.

        Skills in ``request.injected_skills`` go to ``--append-system-prompt``.
        """
        cmd = ["qodercli", "-p", request.task_prompt]
        cmd.extend(self._shared(self.workspace or (str(request.workspace_dir) if request.workspace_dir else None),
                                headless=True))
        if self.output_format != "text":
            cmd.extend(["-o", self.output_format])
        system = self.append_system_prompt or ""
        if request.injected_skills:
            skills = self._format_skills_text(request.injected_skills)
            system = f"{system}\n\n{skills}" if system else skills
        if system:
            cmd.extend(["--append-system-prompt", system])
        cmd.extend(render_extra_args(request.extra_args))
        cmd.extend(request.extra_cli_args)
        return cmd

    def build_interactive_command(
        self,
        extra_args: list[str] | AgentRequest | None = None,
        request: AgentRequest | None = None,
    ) -> list[str]:
        """Build the QoderCLI command for interactive mode.

        Args:
            extra_args: Arguments appended verbatim (passthrough).
            request: Optional agent request for configuration.

        Returns:
            List of command arguments to execute in interactive mode.
        """
        if isinstance(extra_args, AgentRequest):  # backwards-compatible call
            request, extra_args = extra_args, None
        workspace = self.workspace or (str(request.workspace_dir) if request and request.workspace_dir else None)
        cmd = ["qodercli", *self._shared(workspace, headless=False)]
        if self.append_system_prompt:
            cmd.extend(["--append-system-prompt", self.append_system_prompt])
        cmd.extend(extra_args or [])
        return cmd

    def get_stdin_input(self, request: AgentRequest) -> str | None:
        """QoderCLI receives the prompt as an argument, not via stdin."""
        return None

    def parse_output(
        self, raw_output: str, stderr: str, return_code: int
    ) -> AgentResult:
        """Parse QoderCLI output into an AgentResult.

        ``json`` is the ``result`` event alone; ``stream-json`` adds every
        message before it.  Text output is the answer itself.
        """
        if self.output_format != "text":
            parsed = parse_claude_stream(raw_output)
            ok = return_code == 0 and not parsed["is_error"]
            error = None
            if not ok:
                error = (parsed["error"] if parsed["result"] is not None else None) or stderr.strip() \
                    or parsed["error"] or f"qodercli exited with code {return_code}"
            return AgentResult(
                raw_output=raw_output,
                is_success=ok,
                tool_calls=parsed["tool_calls"],
                steps_count=parsed["num_turns"] or 0,
                error_message=error,
                output_text=parsed["answer"] if ok else None,
                usage=parsed["usage"],
                session_id=parsed["session_id"],
            )

        parsed = parse_qoder_output(raw_output)
        is_success = not parsed["is_error"] and return_code == 0
        error_message = None
        if not is_success:
            if stderr and stderr.strip():
                error_message = stderr.strip()
            elif parsed["is_error"] or parsed.get("result_text"):
                # an expired login is printed on stdout (exit 41)
                error_message = parsed.get("result_text") or "Unknown error"
            else:
                error_message = f"qodercli exited with code {return_code}"
        result_text = parsed.get("result_text")
        return AgentResult(
            raw_output=raw_output,
            is_success=is_success,
            tool_calls=[],  # text output has no structured tool calls
            steps_count=0,
            error_message=error_message,
            output_text=result_text if is_success and isinstance(result_text, str) else None,
        )


# Backwards compatibility alias
QoderAdapter = QoderCLIAdapter
