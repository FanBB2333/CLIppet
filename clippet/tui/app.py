"""The terminal view of CLIppet, drawn after papertown's.

Top to bottom: the masthead (counts that count up), the ribbon (what is
running right now, moving while it does), the index of places on the left
and the place itself, the box you write prompts in, and the keys that work
where you are.  Everything moves on one 12 fps clock.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import ContentSwitcher, Input, Static
from textual.worker import Worker

from clippet.config.environments import get_clippet_root
from clippet.tui.anim import Tween, breathe, dur, gleam, mix, star
from clippet.tui.data import (
    BASE,
    AccountView,
    EnvView,
    SourceView,
    attach_quota,
    default_sources,
    load_accounts,
    load_envs,
)
from clippet.tui.look import AGENT_COLOR, AGENTS, DEFAULT_THEME, PALETTES, clip, mark, short_path, themes
from clippet.tui.runs import Run, adapter_for, execute, launch_interactive, prepare_env
from clippet.tui.views import make_places
from clippet.tui.views.accounts import default_env_for, import_fields
from clippet.tui.views.project import resolve_workspace
from clippet.tui.widgets import (
    Bar,
    Confirm,
    Divider,
    Filter,
    Form,
    Help,
    ListTable,
    Pick,
    Place,
    Prompt,
    keys_line,
)

PLACES = [("envs", "Envs"), ("accounts", "Accounts"), ("runs", "Runs"), ("project", "Project"), ("checks", "Checks")]
SANDBOXES = ("workspace-write", "read-only")
APP_ACTIONS = {"compose", "help", "theme", "workspace", "place", "reload", "quit_app"}

CSS = """
Screen { background: $background; color: $foreground; }

#masthead { height: 1; background: $panel; }
#ribbon { height: 1; margin-bottom: 1; }
#body { height: 1fr; }
#nav { width: 16; height: 100%; padding: 1 0; background: $surface; }
Divider { width: 1; height: 100%; border-left: vkey $panel; }
Divider:hover, Divider.-dragging { border-left: vkey $primary; }
#nav-line { border-left: vkey $surface; }
#places { width: 1fr; height: 100%; }
#places > Place { height: 100%; }
Place > Horizontal { height: 1fr; }

.list-col { width: 74; height: 100%; }
.detail-col { width: 1fr; height: 100%; padding: 0 0 0 1; }
.chips { height: 1; padding: 0 1; }
.filter { height: 1; border: none; padding: 0 1; background: $background; }
.filter:focus { border: none; background: $surface; }

DataTable { height: 1fr; background: $background; scrollbar-size-vertical: 1; overflow-x: hidden; }
DataTable > .datatable--header { background: $background; color: $muted; text-style: none; }
DataTable:focus > .datatable--cursor { background: $block-cursor-background; color: $foreground; text-style: none; }
DataTable > .datatable--cursor { background: $block-cursor-blurred-background; color: $foreground; text-style: none; }
DataTable > .datatable--hover { background: $panel; }

Pane { height: 1fr; scrollbar-size-vertical: 1; padding: 0 1 0 0; }
#project-detail, #checks-detail { padding: 0 2; }

#composer { height: 3; margin: 0 1; padding: 0 1; border: round $primary 45%; background: $surface; }
#composer:focus-within { border: round $primary; }
#target { width: auto; height: 1; }
#say { width: 1fr; height: 1; border: none; padding: 0; background: $surface; }
#say:focus { border: none; }
#hints { height: 1; padding: 0 1; }

.modal { align: center middle; background: $background 60%; }
.dialog { width: 78; height: auto; max-height: 88%; padding: 1 2; border: round $primary; background: $surface; }
.dialog.help { width: 90; height: 86%; }
.dialog.picker { width: 86; }
#pick-list { height: auto; max-height: 22; border: none; padding: 0; background: $surface; }
.dialog-title { text-style: bold; color: $primary; margin-bottom: 1; }
.dialog-hint { color: $muted; margin-bottom: 1; }
.dialog-keys { color: $muted; margin-top: 1; }
.field-label { color: $muted; margin-top: 1; }
.field-hint { color: $faint; }
.flag-row { height: auto; margin-top: 1; }
.flag-label { padding: 1 1; width: 1fr; color: $foreground; }
.dialog Input, .dialog Select { margin-bottom: 0; }
"""


@dataclass
class Prefs:
    """What the TUI remembers between runs (``~/.clippet/tui.json``)."""

    theme: str = DEFAULT_THEME
    agent: str = "codex"
    sandbox: str = "workspace-write"
    sources: list[str] = field(default_factory=list)
    widths: dict[str, float] = field(default_factory=dict)  # where each Divider was left
    lists: dict[str, dict] = field(default_factory=dict)  # each ListTable's sort and column widths

    @classmethod
    def load(cls, path: Path) -> "Prefs":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        prefs = cls()
        if data.get("theme") in PALETTES:
            prefs.theme = data["theme"]
        if data.get("agent") in AGENTS:
            prefs.agent = data["agent"]
        if data.get("sandbox") in SANDBOXES:
            prefs.sandbox = data["sandbox"]
        if isinstance(data.get("sources"), list):
            prefs.sources = [str(s) for s in data["sources"] if isinstance(s, str)]
        if isinstance(data.get("lists"), dict):
            prefs.lists = {str(key): value for key, value in data["lists"].items() if isinstance(value, dict)}
        if isinstance(data.get("widths"), dict):
            prefs.widths = {
                str(key): float(value) for key, value in data["widths"].items()
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
            }
        return prefs

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass


class Masthead(Static):
    ALLOW_SELECT = False

    def __init__(self) -> None:
        super().__init__(id="masthead")
        self.n = {key: Tween() for key in ("envs", "accounts", "running")}

    def frame(self, t: float, dt: float) -> None:
        app: ClippetApp = self.app  # type: ignore[assignment]
        p = app.pal
        self.n["envs"].set(max(0, len(app.envs) - 1))
        self.n["accounts"].set(len(app.accounts))
        self.n["running"].set(sum(1 for r in app.runs if r.active))
        for tween in self.n.values():
            tween.step(dt)
        v = {key: tween.value for key, tween in self.n.items()}
        out = Text()
        out.append(" ✎ ", style=f"bold {p['brand']}")
        out.append("CLIppet", style=f"bold {p['ink']}")
        out.append("   ")
        out.append(f"{v['envs']:.0f}", style=f"bold {p['ink']}")
        out.append(" envs  ", style=p["muted"])
        out.append(f"{v['accounts']:.0f}", style=f"bold {p['ink']}")
        out.append(" accounts  ", style=p["muted"])
        running = round(v["running"])
        out.append(f"{running}", style=f"bold {p['run'] if running else p['muted']}")
        out.append(" running", style=p["muted"])
        right = Text()
        right.append("⌂ ", style=p["faint"])
        right.append(clip(short_path(app.workspace), 40), style=p["info"])
        right.append(f"   {datetime.now():%H:%M} ", style=p["muted"])
        gap = max(1, self.size.width - out.cell_len - right.cell_len)
        out.append(" " * gap)
        out.append_text(right)
        self.update(out, layout=False)


class Ribbon(Static):
    """What is going on right now: a turning star and a gleam while agents work."""

    ALLOW_SELECT = False

    def __init__(self) -> None:
        super().__init__(id="ribbon")

    def frame(self, t: float) -> None:
        app: ClippetApp = self.app  # type: ignore[assignment]
        p = app.pal
        active = [r for r in app.runs if r.state == "running"]
        out = Text(" ")
        if not active:
            out.append("·", style=mix(p["faint"], p["muted"], breathe(t, 3.2)))
            out.append("  all quiet", style=p["muted"])
            out.append("  ·  ", style=p["faint"])
            out.append("i", style=f"bold {p['brand']}")
            out.append(" sends a prompt to ", style=p["faint"])
            out.append(app.target_label(), style=p["ink"])
            out.append(", ", style=p["faint"])
            out.append("?", style=f"bold {p['brand']}")
            out.append(" shows every key", style=p["faint"])
        else:
            run = active[int(t / 4) % len(active)]
            out.append(star(t, 8), style=f"bold {p['brand']}")
            out.append(" ")
            out.append_text(gleam(f"#{run.id} {run.agent} @ {run.env}: {clip(run.activity, 60)}…", t, p["brand"], p["hi"]))
            more = f" · {len(active) - 1} more running" if len(active) > 1 else ""
            out.append(f"  ({dur(run.elapsed)}{more})", style=p["muted"])
        self.update(out, layout=False)


class Nav(Static):
    """The places, with a number to jump to each and what is going on there."""

    ALLOW_SELECT = False

    def __init__(self) -> None:
        super().__init__(id="nav")

    def badge(self, key: str, t: float) -> tuple[str, str]:
        app: ClippetApp = self.app  # type: ignore[assignment]
        p = app.pal
        if key == "envs":
            if app.marked:
                return f"●{len(app.marked)}", p["brand"]
            return str(max(0, len(app.envs) - 1)), p["muted"]
        if key == "accounts":
            if any(s.error for s in app.source_reports) and not app.accounts:
                return "✕", p["bad"]
            return (str(len(app.accounts)), p["muted"]) if app.accounts else ("", p["muted"])
        if key == "runs":
            running = sum(1 for r in app.runs if r.active)
            if running:
                return f"{star(t)}{running}", p["run"]
            return (str(len(app.runs)), p["muted"]) if app.runs else ("", p["muted"])
        if key == "project":
            return ("✓", p["ok"]) if app.project_found else ("", p["muted"])
        if key == "checks":
            checks = app.place_named("checks")
            problems = getattr(checks, "problems", 0)
            return (f"!{problems}", p["warn"]) if problems else ("", p["muted"])
        return "", p["muted"]

    def frame(self, t: float) -> None:
        app: ClippetApp = self.app  # type: ignore[assignment]
        p = app.pal
        out = Text()
        width = max(12, self.size.width)
        for i, (key, name) in enumerate(PLACES):
            on_it = key == app.place
            badge, color = self.badge(key, t)
            row = Text()
            row.append("▌" if on_it else " ", style=p["brand"])
            row.append(f"{i + 1} ", style=p["brand"] if on_it else p["faint"])
            row.append(name, style=f"bold {p['ink']}" if on_it else p["muted"])
            gap = width - row.cell_len - Text(badge).cell_len - 1
            row.append(" " * max(1, gap))
            row.append(badge, style=color)
            if on_it:
                row.stylize(f"on {p['raised']}")
            out.append_text(row)
            out.append("\n\n" if i < len(PLACES) - 1 else "\n")
        self.update(out, layout=False)

    def on_click(self, event: Any) -> None:
        index = (event.y - 1) // 2
        if event.y >= 1 and 0 <= index < len(PLACES):
            self.app.call_later(self.app.action_place, PLACES[index][0])  # type: ignore[attr-defined]


class SayBox(Input):
    """The line prompts are written in; its keys come before the places' and the app's."""

    BINDINGS = [
        Binding("tab", "app.next_agent", "agent", show=False),
        Binding("ctrl+b", "app.next_sandbox", "sandbox", show=False),
        Binding("escape", "app.leave_box", "leave", show=False),
    ]


class Composer(Horizontal):
    def __init__(self) -> None:
        super().__init__(id="composer")
        self.label = Bar(id="target")
        self.box = SayBox(id="say", placeholder="a prompt for the selected env — Enter runs it headless")

    def compose(self) -> ComposeResult:
        yield self.label
        yield self.box


class ClippetApp(App):
    CSS = CSS
    TITLE = "CLIppet"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        *[Binding(str(i + 1), f"place('{key}')", name, show=False) for i, (key, name) in enumerate(PLACES)],
        Binding("i", "compose", "prompt"),
        Binding("question_mark", "help", "keys"),
        Binding("ctrl+r", "reload", "reload"),
        Binding("T", "theme", "paper / ink"),
        Binding("w", "workspace", "workspace"),
        Binding("q", "quit_app", "quit"),
        Binding("ctrl+c", "leave", "quit", show=False, priority=True),
    ]

    def __init__(self, workspace: Path | None = None, *, prefs_path: Path | None = None) -> None:
        super().__init__()
        self.workspace = (workspace or Path.cwd()).expanduser().resolve()
        self.prefs_path = prefs_path or get_clippet_root() / "tui.json"
        self.prefs = Prefs.load(self.prefs_path)
        self.envs: list[EnvView] = []
        self.env_error: str | None = None
        self.accounts: list[AccountView] = []
        self.source_reports: list[SourceView] = []
        self.runs: list[Run] = []
        self.run_workers: dict[int, Worker] = {}
        self.runs_dirty = False
        self.marked: set[str] = set()
        self.selected_env: str | None = None
        self.agent = self.prefs.agent
        self.sandbox = self.prefs.sandbox
        self.place = "envs"
        self.visited: set[str] = set()
        self.project_found = False
        self.t0 = time.monotonic()
        self.t = 0.0
        self._last_frame = self.t0
        self._next_run = 1
        self._sending = False
        self._target: Text | None = None
        self._focus: object = None
        for theme in themes():
            self.register_theme(theme)
        self.theme = self.prefs.theme
        self.places: list[Place] = make_places()
        self.masthead = Masthead()
        self.ribbon = Ribbon()
        self.nav = Nav()
        self.composer = Composer()
        self.hints_bar = Bar(id="hints")

    # --- looks --------------------------------------------------------------------

    @property
    def pal(self) -> dict[str, str]:
        return PALETTES.get(self.theme, PALETTES[DEFAULT_THEME])

    @property
    def is_dark(self) -> bool:
        return self.theme != "clippet-paper"

    def compose(self) -> ComposeResult:
        yield self.masthead
        yield self.ribbon
        with Horizontal(id="body"):
            yield self.nav
            yield Divider("nav", share=False, least=12, keep=60, id="nav-line")
            with ContentSwitcher(id="places", initial="envs"):
                yield from self.places
        yield self.composer
        yield self.hints_bar

    def on_mount(self) -> None:
        for divider in self.query(Divider):
            divider.restore(self.prefs.widths.get(divider.key))
        self.reload()
        self.set_interval(1 / 12, self.frame)
        self.current.focus_first()
        self.paint_hints()

    # --- state ----------------------------------------------------------------------

    @property
    def sources(self) -> list[str]:
        out = default_sources()
        for source in self.prefs.sources:
            if source not in out:
                out.append(source)
        return out

    def env_named(self, name: str | None) -> EnvView | None:
        return next((e for e in self.envs if e.name == name), None)

    def place_named(self, key: str) -> Place | None:
        return next((p for p in self.places if p.key == key), None)

    @property
    def current(self) -> Place:
        return self.place_named(self.place) or self.places[0]

    def reload(self, select: str | None = None) -> None:
        """Read envs, accounts and the project again, and redraw what has been seen."""

        self.envs, self.env_error = load_envs()
        names = {e.name for e in self.envs}
        self.marked &= names
        if select:
            self.selected_env = select
        if self.selected_env not in names:
            self.selected_env = next((e.name for e in self.envs if not e.is_base), BASE)
        self.accounts, self.source_reports = load_accounts(self.sources, self.envs, self.prefs.sources)
        attach_quota(self.envs, self.accounts)
        from clippet.config.project import find_project_config

        try:
            self.project_found = find_project_config(self.workspace) is not None
        except Exception:  # noqa: BLE001
            self.project_found = False
        self.visited.add(self.place)
        for place in self.places:
            if place.key in self.visited:
                place.refresh_view()
        self.retarget()

    # --- the clock ------------------------------------------------------------------

    def frame(self) -> None:
        now = time.monotonic()
        dt, self._last_frame = now - self._last_frame, now
        self.t = now - self.t0
        try:
            self.masthead.frame(self.t, dt)
            self.ribbon.frame(self.t)
            self.nav.frame(self.t)
            self.paint_target()
            if self.focused is not self._focus:
                self._focus = self.focused
                self.paint_hints()
            self.current.tick(self.t)
        except Exception:  # noqa: BLE001 - a frame that fails is simply skipped
            return

    # --- places -----------------------------------------------------------------------

    async def action_place(self, key: str) -> None:
        if key not in dict(PLACES):
            return
        self.place = key
        self.query_one("#places", ContentSwitcher).current = key
        place = self.current
        first = key not in self.visited
        self.visited.add(key)
        if first or key in ("project", "runs"):
            place.refresh_view()
        place.focus_first()
        self.retarget()
        self.paint_hints()

    def paint_hints(self) -> None:
        p = self.pal
        if getattr(self.focused, "id", None) == "say":
            keys = [("Enter", "run"), ("Tab", "agent")]
            if self.agent == "codex":
                keys.append(("Ctrl+B", "sandbox"))
            keys.append(("Esc", "leave the box"))
        elif isinstance(self.focused, Filter):
            keys = [("type", "words to keep"), ("Enter / ↓", "back to the list"), ("Esc", "clear")]
        else:
            own = self.current.hints()
            if self.current.query(Filter):
                own = [*own, ("/", "filter")]
            keys = [*own, ("i", "prompt"), ("1-5", "places"), ("?", "keys"), ("q", "quit")]
            if any(k == "i" for k, _ in own):
                keys = [*own, ("1-5", "places"), ("?", "keys"), ("q", "quit")]
        line = Text(" ")
        line.append_text(keys_line(keys, p))
        self.hints_bar.update(line)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        # in a text box letters are letters, and a dialog's keys are its own
        if action in APP_ACTIONS and (isinstance(self.focused, Input) or isinstance(self.screen, ModalScreen)):
            return False
        return True

    def action_help(self) -> None:
        from clippet import __version__

        p = self.pal
        header = mark(p)
        header.append("CLIppet ", style=f"bold {p['brand']}")
        header.append(f"{__version__} — one CLI to orchestrate them all\n", style=p["muted"])
        name = dict(PLACES)[self.place]
        self.push_screen(Help([
            (f"Here ({name})", self.current.help_rows()),
            ("Everywhere", [
                ("1 … 5", "Envs / Accounts / Runs / Project / Checks"),
                ("i", "write a prompt (runs headless in the selected or marked envs)"),
                ("Tab (in the box)", f"switch agent: {' / '.join(AGENTS)}"),
                ("Ctrl+B (in the box)", "Codex sandbox: workspace-write / read-only"),
                ("w", "change the workspace runs start in"),
                ("Ctrl+R", "read everything again"),
                ("T", "paper or ink (light / dark)"),
                ("drag a │", "make the pane on its left narrower or wider; a double click puts it back"),
                ("/", "filter a list: keeps the rows with every word typed; Esc clears it"),
                ("click a header", "sort the list by it; again reverses, a third time goes back"),
                ("drag between headers", "make a column narrower or wider; a double click puts it back"),
                ("q", "quit"),
            ]),
        ], header=header))

    def on_list_table_arranged(self, message: ListTable.Arranged) -> None:
        self.prefs.lists[message.table.list_name] = message.table.state()
        self.prefs.save(self.prefs_path)

    def on_divider_moved(self, message: Divider.Moved) -> None:
        if message.width is None:
            self.prefs.widths.pop(message.divider.key, None)
        else:
            self.prefs.widths[message.divider.key] = message.width
        self.prefs.save(self.prefs_path)

    def action_theme(self) -> None:
        self.theme = "clippet-paper" if self.theme == "clippet" else "clippet"
        self.prefs.theme = self.theme
        self.prefs.save(self.prefs_path)
        self._target = None
        for place in self.places:
            if place.key in self.visited:
                place.refresh_view()
        self.paint_hints()

    def action_reload(self) -> None:
        checks = self.place_named("checks")
        if self.place == "checks" and checks is not None:
            checks.recheck()  # type: ignore[attr-defined]
        self.reload()
        self.notify("read again", timeout=2)

    def action_workspace(self) -> None:
        def done(text: str | None) -> None:
            if not text:
                return
            path = resolve_workspace(text, self.workspace)
            if path is None:
                self.notify(f"{text} is not a folder", severity="error")
                return
            self.workspace = path
            self.reload()
            self.notify(short_path(path), title="workspace")

        self.push_screen(Prompt("Workspace", hint="Prompts run here, and .clippet.json is looked up from here.",
                                value=str(self.workspace)), done)

    def action_leave(self) -> None:
        self.action_quit_app()

    def action_quit_app(self) -> None:
        active = [r for r in self.runs if r.active]
        if not active:
            self.exit()
            return
        self.push_screen(Confirm(f"{len(active)} run(s) still going", "Quit and stop them?"),
                         lambda yes: yes and self.exit())

    # --- the box at the bottom ----------------------------------------------------------

    def targets(self) -> list[str]:
        if self.marked:
            return [e.name for e in self.envs if e.name in self.marked]
        return [self.selected_env or BASE]

    def target_label(self) -> str:
        targets = self.targets()
        return targets[0] if len(targets) == 1 else f"{len(targets)} envs"

    def retarget(self) -> None:
        self._target = None

    def paint_target(self) -> None:
        p = self.pal
        out = Text()
        out.append(star(self.t, 8) if self._sending else "✎", style=f"bold {p['brand']}")
        out.append(f" {self.agent}", style=f"bold {p[AGENT_COLOR.get(self.agent, 'ink')]}")
        out.append(" @ ", style=p["muted"])
        out.append(self.target_label(), style=f"bold {p['ink']}")
        if self.agent == "codex":
            out.append(f" · {self.sandbox}", style=p["muted"])
        out.append(" ❯ ", style=p["brand"])
        if self._target is not None and self._target.plain == out.plain and self._target.spans == out.spans:
            return
        self._target = out
        self.composer.label.update(out)

    def action_compose(self) -> None:
        self.composer.box.focus()

    def action_leave_box(self) -> None:
        self.current.focus_first()

    def action_next_agent(self) -> None:
        self.agent = AGENTS[(AGENTS.index(self.agent) + 1) % len(AGENTS)] if self.agent in AGENTS else AGENTS[0]
        self.prefs.agent = self.agent
        self.prefs.save(self.prefs_path)
        self.paint_hints()

    def action_next_sandbox(self) -> None:
        self.sandbox = SANDBOXES[(SANDBOXES.index(self.sandbox) + 1) % len(SANDBOXES)]
        self.prefs.sandbox = self.sandbox
        self.prefs.save(self.prefs_path)

    @on(Input.Submitted, "#say")
    def send(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        runs = self.start_runs(prompt, self.targets())
        event.input.value = ""
        if runs:
            self.call_later(self.show_run, runs[0].id)

    async def show_run(self, run_id: int) -> None:
        await self.action_place("runs")
        runs = self.place_named("runs")
        if runs is not None:
            runs.focus_run(run_id)  # type: ignore[attr-defined]
            runs.refresh_view()

    # --- runs ---------------------------------------------------------------------------

    def start_runs(self, prompt: str, env_names: list[str], *, agent: str | None = None,
                   sandbox: str | None = None) -> list[Run]:
        agent = agent or self.agent
        sandbox = sandbox or self.sandbox
        group = self._next_run if len(env_names) > 1 else None
        created = []
        for name in env_names:
            run = Run(
                id=self._next_run,
                env=name,
                agent=agent,
                prompt=prompt,
                workspace=self.workspace,
                sandbox=sandbox if agent == "codex" else None,
                group=group,
            )
            self._next_run += 1
            self.runs.append(run)
            created.append(run)
            self.run_workers[run.id] = self.run_worker(
                self._carry(run, self.env_named(name)), group="runs", exit_on_error=False, description=f"run {run.id}"
            )
        self.runs_dirty = True
        return created

    async def _carry(self, run: Run, env: EnvView | None) -> None:
        if env is None:
            run.state, run.error, run.ended = "failed", f"env '{run.env}' is gone", time.time()
            self.runs_dirty = True
            return
        try:
            run.notes.extend(prepare_env(env, run.agent))
            adapter = adapter_for(env, run.agent, run.sandbox)
        except Exception as exc:  # noqa: BLE001 - shown on the run
            run.state, run.error, run.ended = "failed", str(exc), time.time()
            self.runs_dirty = True
            self.notify(str(exc), title=f"#{run.id} {run.agent} @ {run.env}", severity="error")
            return
        await execute(run, adapter, self._run_changed)
        self.run_workers.pop(run.id, None)
        severity = "information" if run.state == "done" else "warning"
        self.notify(clip(run.answer or run.error or run.state, 80), title=f"#{run.id} {run.agent} @ {run.env}: {run.state}",
                    severity=severity, timeout=6)
        if env.is_home:
            self.reload()

    def _run_changed(self, run: Run) -> None:
        self.runs_dirty = True

    def cancel_run(self, run_id: int) -> None:
        worker = self.run_workers.pop(run_id, None)
        if worker is not None:
            worker.cancel()
        self.runs_dirty = True

    # --- launching agents ---------------------------------------------------------------

    def launch(self, env: EnvView, agent: str, args: list[str], cwd: str | None = None) -> None:
        """Hand the terminal to *agent* in *env*; come back when it exits."""

        workdir = Path(cwd).expanduser() if cwd else self.workspace
        if not workdir.is_dir():
            workdir = self.workspace
        try:
            with self.suspend():
                who = ""
                if agent == "codex" and env.credential is not None:
                    who = f" as {env.credential.display_email or 'API key'}"
                print(f"clippet: {agent} @ {env.name}{who} — {short_path(workdir)}", flush=True)
                code, notes = launch_interactive(env, agent, args, workdir)
                if code not in (0, 130):
                    try:
                        input(f"\n[{agent} exited with {code}] press Enter to go back to clippet ")
                    except (EOFError, KeyboardInterrupt):
                        pass
        except SuspendNotSupported as exc:
            self.notify(str(exc) or "this terminal cannot hand over control", title="launch", severity="error")
            return
        for note in notes:
            bad = note.startswith(("warning", "Error"))
            self.notify(note, title=env.name, severity="warning" if bad else "information", timeout=8)
        if code not in (0, 130):
            self.notify(f"{agent} exited with {code}", title=env.name, severity="warning")
        self.reload(select=env.name)

    # --- accounts -----------------------------------------------------------------------

    def import_account(self, account: AccountView, env_name: str | None = None) -> None:
        names = [e.name for e in self.envs if not e.is_base]
        default = env_name or default_env_for(account, names)
        who = account.credential.display_email or account.credential.label or "this account"

        def done(values: dict | None) -> None:
            if values and values.get("name"):
                self._import(account, values["name"], bool(values.get("link")), overwrite=False)

        self.push_screen(Form(f"Put {who} into an env", import_fields(account, names, default),
                              hint="Only the Codex login is written; other logins never move."), done)

    def _import(self, account: AccountView, name: str, link: bool, overwrite: bool) -> None:
        from clippet.config.environments import import_codex_credential

        try:
            home, created = import_codex_credential(name, account.credential, source=account.source, link=link,
                                                    overwrite=overwrite)
        except FileExistsError as exc:
            self.push_screen(Confirm(f"Replace the login in {name}?", str(exc).replace("pass --overwrite to replace it.",
                                                                                     "Replace it?")),
                             lambda yes: yes and self._import(account, name, link, True))
            return
        except (ValueError, OSError) as exc:
            self.notify(str(exc), title="import", severity="error")
            return
        self.reload(select=name)
        verb = "created" if created else "updated"
        self.notify(f"{short_path(home)}{' · linked' if link else ''}", title=f"{verb} {name}")
        if not link and account.credential.auth_mode != "apikey":
            self.notify("The source keeps the same refresh token; whichever copy refreshes first invalidates the "
                        "other. Log in inside the env for long-term parallel use.", title=name, severity="warning",
                        timeout=10)

    def pick_account_for(self, env_name: str) -> None:
        if not self.accounts:
            self.notify("No accounts found. Press 2, then + to add a file.", severity="warning")
            return
        p = self.pal
        options = []
        for account in self.accounts:
            credential = account.credential
            label = Text(f"{account.index:>2}  ", style=p["muted"])
            label.append(credential.display_email or credential.label or "?", style=p["ink"])
            if credential.display_plan:
                label.append(f"  [{credential.display_plan}]", style=p["brand"])
            label.append(f"  {account.source_label}", style=p["info"])
            options.append((account, label))

        def done(account: AccountView | None) -> None:
            if account is not None:
                self.import_account(account, env_name)

        self.push_screen(Pick(f"Import a Codex account into {env_name}", options), done)

    def add_source(self, source: str) -> None:
        if source not in self.prefs.sources:
            self.prefs.sources.append(source)
            self.prefs.save(self.prefs_path)
        self.reload()
        report = next((s for s in self.source_reports if s.source == source), None)
        if report is not None and report.error:
            self.notify(report.error, title=report.label, severity="error")
        elif report is not None:
            self.notify(f"{report.count} account(s)", title=report.label)

    def forget_source(self, source: str) -> None:
        if source in self.prefs.sources:
            self.prefs.sources.remove(source)
            self.prefs.save(self.prefs_path)
        self.reload()


def run_tui(workspace: Path | None = None) -> None:
    ClippetApp(workspace).run()
