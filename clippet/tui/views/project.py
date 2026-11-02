"""Project: the workspace runs start in, the ``.clippet.json`` that applies to
it, and what each agent resolves to there.  An agent can be pinned to an
env, so ``clippet codex`` in this project always runs as that account."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding

from clippet.tui.data import load_project, pin_project_env, project_config_target
from clippet.tui.look import AGENT_COLOR, AGENTS, heading, short_path
from clippet.tui.widgets import Confirm, Field, Form, Pane, Place


class ProjectPlace(Place):
    key = "project"
    title = "Project"

    BINDINGS = [
        Binding("p", "pin", "pin an env", show=False),
        Binding("w", "app.workspace", "workspace", show=False),
        Binding("e", "edit", "edit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(id="project")
        self.view = None

    def compose(self) -> ComposeResult:
        yield Pane(id="project-detail")

    def hints(self) -> list[tuple[str, str]]:
        return [("p", "pin an env"), ("w", "workspace"), ("e", "edit .clippet.json")]

    def help_rows(self) -> list[tuple[str, str]]:
        return [
            ("p", "pin an agent to an env in .clippet.json (or unpin it)"),
            ("w", "change the workspace runs start in"),
            ("e", "open .clippet.json in $EDITOR"),
        ]

    def refresh_view(self) -> None:
        self.view = load_project(self.clippet.workspace)
        self.query_one("#project-detail", Pane).show(self.render_view())

    def render_view(self) -> Group:
        p = self.pal
        view = self.view
        parts: list[Any] = []
        head = Text("workspace  ", style=p["muted"])
        head.append(short_path(view.workspace), style=f"bold {p['ink']}")
        parts.append(head)
        parts.append(Text("prompts you send run here; w changes it", style=p["faint"]))
        parts.append(Text())
        if view.error:
            parts.append(Text(f"✕ {view.error}", style=p["bad"]))
            parts.append(Text())
        if view.config_path is None:
            parts.append(heading(".clippet.json", p, "none found up to the Git root"))
            hint = Text("  ", style=p["muted"])
            hint.append("p", style=f"bold {p['brand']}")
            hint.append(f" pins an agent to an env and creates {short_path(project_config_target(view.workspace))}",
                        style=p["muted"])
            parts.append(hint)
            parts.append(Text())
            example = '{\n  "version": 1,\n  "agents": {\n    "codex": { "env": "work" }\n  }\n}'
            parts.append(Syntax(example, "json", theme="ansi_dark" if self.clippet.is_dark else "ansi_light",
                                background_color="default"))
            return Group(*parts)

        parts.append(heading(".clippet.json", p, short_path(view.config_path)))
        table = Text()
        for agent in AGENTS:
            launch = view.launches.get(agent)
            table.append(f"  {agent:<9}", style=p[AGENT_COLOR[agent]])
            if launch is None:
                table.append("not set — runs ask which HOME to use\n", style=p["faint"])
                continue
            if "error" in launch:
                table.append(f"✕ {launch['error']}\n", style=p["bad"])
                continue
            if launch.get("env"):
                env = self.clippet.env_named(launch["env"])
                table.append("env ", style=p["muted"])
                table.append(launch["env"], style=f"bold {p['ink']}")
                if env is None:
                    table.append("  ✕ no such env", style=p["bad"])
                elif env.credential is not None and agent == "codex":
                    table.append(f"  {env.credential.display_email or 'API key'}", style=p["ok"])
            elif launch.get("config_path"):
                table.append("file ", style=p["muted"])
                table.append(short_path(launch["config_path"]), style=p["ink"])
                if launch.get("codex_config_path"):
                    table.append(f" + {short_path(launch['codex_config_path'])}", style=p["muted"])
            if launch.get("account"):
                table.append(f"  account {launch['account']}", style=p["muted"])
            table.append("\n")
        parts.append(table)
        parts.append(Text())
        raw = view.config_path.read_text(encoding="utf-8") if view.config_path.is_file() else ""
        if raw:
            parts.append(Syntax(raw.rstrip(), "json", theme="ansi_dark" if self.clippet.is_dark else "ansi_light",
                                background_color="default", line_numbers=False))
        return Group(*parts)

    def action_pin(self) -> None:
        envs = [e.name for e in self.clippet.envs if not e.is_base]
        if not envs:
            self.app.notify("Create an env first (1, then n).", severity="warning")
            return
        target = project_config_target(self.clippet.workspace)
        fields = [
            Field("agent", "agent", self.clippet.agent, options=[(a, a) for a in AGENTS]),
            Field("env", "env (choose none to unpin)", None, options=[("none — unpin", ""), *[(n, n) for n in envs]]),
        ]

        def done(values: dict | None) -> None:
            if not values or not values.get("agent") or values.get("env") is None:
                return
            agent, env_name = values["agent"], values.get("env") or None

            def write(yes: bool | None) -> None:
                if not yes:
                    return
                try:
                    pin_project_env(target, agent, env_name)
                except (OSError, ValueError) as exc:
                    self.app.notify(str(exc), title=".clippet.json", severity="error")
                    return
                self.app.notify(f"{agent} → {env_name}" if env_name else f"{agent} unpinned", title=short_path(target))
                self.refresh_view()

            verb = "Create" if not target.exists() else "Update"
            self.app.push_screen(Confirm(f"{verb} {short_path(target)}?",
                                         f"{agent} → env {env_name}" if env_name else f"remove {agent}"), write)

        self.app.push_screen(Form("Pin an agent to an env", fields,
                                  hint=f"Written to {short_path(target)}; other entries are kept."), done)

    def action_edit(self) -> None:
        view = self.view
        target = view.config_path if view and view.config_path else project_config_target(self.clippet.workspace)
        if not target.exists():
            self.app.notify("No .clippet.json yet; p creates one.", severity="warning")
            return
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        try:
            with self.app.suspend():
                subprocess.run([*shlex.split(editor), str(target)], check=False)
        except Exception as exc:  # noqa: BLE001 - SuspendNotSupported, missing editor…
            self.app.notify(str(exc), title="edit", severity="error")
        self.refresh_view()


def resolve_workspace(text: str, current: Path) -> Path | None:
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = current / path
    path = path.resolve()
    return path if path.is_dir() else None

