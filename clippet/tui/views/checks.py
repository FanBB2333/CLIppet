"""Checks: are the agent CLIs installed, where CLIppet keeps its state, can
it read Cockpit Tools, which variables isolated runs drop, and is every env
healthy."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult

from clippet.tui.data import AGENT_BINARIES, Check, agent_version, run_checks
from clippet.tui.look import heading
from clippet.tui.widgets import Pane, Place

SIGNS = {"ok": ("✓", "ok"), "warn": ("!", "warn"), "bad": ("✕", "bad"), "info": ("·", "muted")}


class ChecksPlace(Place):
    key = "checks"
    title = "Checks"

    def __init__(self) -> None:
        super().__init__(id="checks")
        self.versions: dict[str, tuple[str | None, str | None]] | None = None
        self.checks: list[Check] = []

    def compose(self) -> ComposeResult:
        yield Pane(id="checks-detail")

    def hints(self) -> list[tuple[str, str]]:
        return [("Ctrl+R", "check again")]

    def refresh_view(self) -> None:
        if self.versions is None:
            self.versions = {}
            self.query_one("#checks-detail", Pane).show(Text("checking…", style=self.pal["muted"]))
            self.run_worker(self._read_versions, thread=True, group="versions", exclusive=True, exit_on_error=False)
            return
        self.checks = run_checks(self.clippet.envs, self.versions)
        self.query_one("#checks-detail", Pane).show(self.render_checks())

    def recheck(self) -> None:
        self.versions = None
        self.refresh_view()

    def _read_versions(self) -> None:
        versions = {agent: agent_version(binary) for agent, binary in AGENT_BINARIES.items()}
        self.app.call_from_thread(self._versions_ready, versions)

    def _versions_ready(self, versions: dict[str, tuple[str | None, str | None]]) -> None:
        self.versions = versions
        self.refresh_view()

    @property
    def problems(self) -> int:
        return sum(1 for c in self.checks if c.state in ("warn", "bad"))

    def render_checks(self) -> Group:
        p = self.pal
        parts: list[Any] = []
        section = None
        body = Text()
        for check in self.checks:
            if check.section != section:
                if section is not None:
                    parts.append(body)
                    parts.append(Text())
                    body = Text()
                section = check.section
                parts.append(heading(section, p))
            sign, color = SIGNS.get(check.state, ("·", "muted"))
            body.append(f"  {sign} ", style=p[color])
            body.append(f"{check.label:<30}", style=p["ink"])
            body.append(f" {check.detail}\n", style=p["muted"] if check.state in ("ok", "info") else p[color])
        if section is not None:
            parts.append(body)
        return Group(*parts)
