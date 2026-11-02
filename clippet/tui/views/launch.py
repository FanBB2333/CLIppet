"""What a launch is about to do, shown before it does it: the agent, who it
will be, the HOME and the folder it gets, the conversation it picks up, and
the command that will run.  Enter starts it, Tab picks another agent (unless
it continues a conversation), Esc goes back."""

from __future__ import annotations

import shlex
from datetime import datetime
from pathlib import Path

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Static

from clippet.agents import AGENTS as AGENT_SPECS
from clippet.isolation import AGENT_CONFIG_PATHS
from clippet.tui.data import EnvView
from clippet.tui.look import (
    AGENT_COLOR,
    AGENTS,
    LOGIN_WORDS,
    clip,
    quota_style,
    short_path,
)

EXAMPLES = {key: spec.examples for key, spec in AGENT_SPECS.items()}

def launch_command(env: EnvView, agent: str, args: list[str]) -> str:
    """The command line that this launch is the same as."""

    if env.is_base:
        return shlex.join([agent, *args])
    return shlex.join(["clippet", "-e", env.name, agent, *(["--", *args] if args else [])])


def _source_name(source: object) -> str:
    text = str(source or "?")
    return "Cockpit Tools" if text == "cockpit" or text.startswith("cockpit:") else short_path(text)


def resume_args(agent: str, session_id: str | None) -> list[str]:
    """The arguments that pick *agent*'s conversation *session_id* up interactively ([] when it cannot)."""

    spec = AGENT_SPECS.get(agent)
    return spec.resume_args(session_id) if spec is not None else []


def resume_folder(ran_in: object, workspace: Path, p: dict[str, str]) -> tuple[Path, Text]:
    """Where a conversation picks up: the folder it ran in while that is there, else the workspace."""

    if ran_in:
        path = Path(str(ran_in)).expanduser()
        if path.is_dir():
            return path, Text("  where it ran", style=p["faint"])
        return workspace, Text(f"  it ran in {short_path(path)}, which is gone", style=p["warn"])
    return workspace, Text("  (Esc, then w, to start somewhere else)", style=p["faint"])


def conversation(prompt: str, p: dict[str, str], started: datetime | None = None, lead: str = "") -> Text:
    """A line naming a conversation: when it started and what it was asked first."""

    text = Text(lead, style=p["muted"])
    if started is not None:
        text.append(started.astimezone().strftime("%m-%d %H:%M  "), style=p["muted"])
    text.append(f"“{clip(prompt, 70)}”" if prompt else "(no prompt)", style=p["ink"] if prompt else p["faint"])
    return text


class Launch(ModalScreen[tuple[str, list[str]] | None]):
    """Confirm a launch.  Dismissed with ``(agent, arguments)``, or None.

    *args* fills the arguments in (``resume <id>``, say) and *session* names the
    conversation they pick up; *lock_agent* keeps the agent as given, and
    *folder_note* replaces what is said after the folder.
    """

    BINDINGS = [
        Binding("escape", "dismiss(None)", "back"),
        Binding("tab", "agent(1)", "next agent", priority=True),
        Binding("shift+tab", "agent(-1)", "previous agent", priority=True),
    ]

    def __init__(self, env: EnvView, agent: str, folder: Path, pal: dict[str, str], *,
                 args: list[str] | None = None, session: Text | None = None, lock_agent: bool = False,
                 folder_note: Text | None = None, title: str | None = None) -> None:
        super().__init__(classes="modal")
        self.env, self.folder, self.pal = env, folder, pal
        self.agent = agent if agent in AGENTS else AGENTS[0]
        self.preset = list(args or [])
        self.session, self.lock_agent, self.folder_note = session, lock_agent, folder_note
        self.title_ = title or f"Launch in {env.name}"

    def compose(self) -> ComposeResult:
        keys = [("Enter", "bold"), " launch · "]
        if not self.lock_agent:
            keys += [("Tab", "bold"), " another agent · "]
        with Vertical(classes="dialog launch"):
            yield Label(self.title_, classes="dialog-title")
            yield Static(id="launch-facts")
            yield Label("arguments for the agent" + ("" if self.preset else " (optional)"), classes="field-label")
            yield Input(shlex.join(self.preset), placeholder=EXAMPLES.get(self.agent, ""), id="launch-args")
            yield Static(Text.assemble(*keys, ("Esc", "bold"), " back"), classes="dialog-keys")

    def on_mount(self) -> None:
        self.paint()
        self.query_one("#launch-args", Input).focus()

    # --- what it shows ----------------------------------------------------------

    def args(self) -> list[str] | None:
        """The arguments typed so far, or None while a quote is open."""

        try:
            return shlex.split(self.query_one("#launch-args", Input).value)
        except ValueError:
            return None

    def paint(self) -> None:
        self.query_one("#launch-facts", Static).update(self.facts())
        self.query_one("#launch-args", Input).placeholder = EXAMPLES.get(self.agent, "")

    def facts(self) -> Table:
        """The rows as a grid, so that a long value wraps under itself."""

        grid = Table.grid(padding=(0, 1))
        grid.add_column(style=self.pal["muted"], no_wrap=True, min_width=8)
        grid.add_column(ratio=1, overflow="fold")
        for label, value in self.rows():
            grid.add_row(label, value)
        return grid

    def summary(self) -> str:
        """The rows as plain text."""

        return "\n".join(f"{label} {value.plain}" for label, value in self.rows())

    def rows(self) -> list[tuple[str, Text]]:
        p, env, agent = self.pal, self.env, self.agent
        line = Text(agent, style=f"bold {p[AGENT_COLOR[agent]]}")
        if not self.lock_agent:
            line.append(f"   Tab: {' / '.join(a for a in AGENTS if a != agent)}", style=p["faint"])
        rows: list[tuple[str, Text]] = [("agent", line), ("account", self.account())]
        if self.session is not None:
            rows.append(("session", self.session))
        if env.kind == "file":
            home = Text("a fresh temporary HOME, with the file above copied in", style=p["ink"])
        elif not env.exists:
            home = Text(f"{short_path(env.path)}: missing on disk", style=p["bad"])
        else:
            home = Text(short_path(env.path) if env.path else "—", style=p["ink"])
            if env.is_base:
                home.append("  your real HOME", style=p["muted"])
        rows.append(("HOME", home))
        folder = Text(short_path(self.folder), style=p["info"])
        folder.append_text(self.folder_note if self.folder_note is not None
                           else Text("  (Esc, then w, to start somewhere else)", style=p["faint"]))
        rows.append(("folder", folder))
        args = self.args()
        rows.append(("runs", Text(launch_command(env, agent, args), style=p["muted"]) if args is not None
                     else Text("a quote is still open", style=p["warn"])))
        return rows

    def account(self) -> Text:
        p, env = self.pal, self.env
        if env.kind == "file":
            return Text(f"the credential file {short_path(env.path)}", style=p["ink"])
        if self.agent != "codex":
            text = Text(f"{self.agent}'s own login in this HOME", style=p["ink"])
            if not env.agents.get(self.agent):
                where = AGENT_CONFIG_PATHS.get(self.agent, [])
                text.append(f"  (no ~/{where[0]} here yet: it will ask you to log in)" if where
                            else "  (it may ask you to log in)", style=p["warn"])
            return text
        credential = env.credential
        if credential is None:
            return Text("not logged in: Codex will ask you to log in", style=p["warn"])
        if credential.auth_mode == "apikey":
            return Text("API key" + (f" @ {credential.base_url}" if credential.base_url else ""), style=p["ink"])
        text = Text(credential.display_email or "ChatGPT login", style=f"bold {p['ink']}")
        if credential.display_plan:
            text.append(f"  [{credential.display_plan}]", style=p["brand"])
        if env.quota is not None:
            left = env.quota.left()
            text.append(f"  {left:.0%} quota left", style=quota_style(left, p))
        if env.login in ("stale", "reauth"):
            text.append(f"\n{LOGIN_WORDS[env.login]}", style=p["warn"] if env.login == "stale" else p["bad"])
        if env.linked and env.source:
            text.append(f"\n⇄ the newest login is pulled first, from {_source_name(env.source.get('source'))}",
                        style=p["info"])
        return text

    # --- keys ---------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        self.paint()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        args = self.args()
        if args is None:
            self.app.notify("close the quote first", title="arguments", severity="error")
            return
        self.dismiss((self.agent, args))

    def action_agent(self, step: int) -> None:
        if self.lock_agent:  # the arguments pick up this agent's conversation
            return
        self.agent = AGENTS[(AGENTS.index(self.agent) + step) % len(AGENTS)]
        self.paint()
