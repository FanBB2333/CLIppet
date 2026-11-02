"""Runs: the prompts you sent from the box at the bottom, one per env, while
they work and after.  Codex runs show what they do step by step; any run
can be continued interactively in the same env."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable

from clippet.agents import agent_spec
from clippet.tui.anim import dur, gleam
from clippet.tui.look import (
    AGENT_COLOR,
    RUN_WORDS,
    clip,
    heading,
    mark,
    run_mark,
    short_path,
)
from clippet.tui.runs import Run
from clippet.tui.widgets import (
    Bar,
    Confirm,
    Divider,
    Filter,
    ListTable,
    Pane,
    Place,
    cursor_to,
    row_key,
    sync_rows,
)

STEP_SIGNS = {
    "message": ("✎", "ink"),
    "reasoning": ("…", "muted"),
    "command": ("›", "info"),
    "edit": ("±", "brand"),
    "tool": ("◇", "plum"),
    "search": ("⌕", "info"),
    "todo": ("☐", "muted"),
    "warning": ("!", "warn"),
    "error": ("✕", "bad"),
}


class RunsPlace(Place):
    key = "runs"
    title = "Runs"

    BINDINGS = [
        Binding("k", "cancel", "stop", show=False),
        Binding("R", "rerun", "run again", show=False),
        Binding("c", "continue", "continue", show=False),
        Binding("y", "copy", "copy answer", show=False),
        Binding("D", "clear", "clear finished", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(id="runs")
        self._drawn: tuple | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(classes="list-col"):
                yield Bar(id="run-chips", classes="chips")
                yield Filter()
                yield ListTable("runs", saved=self.clippet.prefs.lists.get("runs"), id="run-table",
                                cursor_type="row", zebra_stripes=False)
            yield Divider(self.key)
            with Vertical(classes="detail-col"):
                yield Pane(id="run-detail")

    def on_mount(self) -> None:
        table = self.query_one("#run-table", DataTable)
        table.add_column(" ", key="state", width=1)
        table.add_column("#", key="n", width=3)
        table.add_column("env", key="env", width=12)
        table.add_column("agent", key="agent", width=8)
        table.add_column("prompt", key="prompt", width=20)
        table.add_column("time", key="time", width=7)
        table.add_column("tokens", key="tokens", width=6)

    def hints(self) -> list[tuple[str, str]]:
        return [("i", "new prompt"), ("c", "continue in the agent"), ("R", "run again"), ("k", "stop"),
                ("y", "copy answer")]

    def help_rows(self) -> list[tuple[str, str]]:
        return [
            ("i", "write a prompt; it runs in the selected env, or in every marked env"),
            ("c", "continue this run in the agent itself: shows as whom and where first"),
            ("R", "run the same prompt again"),
            ("k", "stop a run that is still going"),
            ("y", "copy the answer"),
            ("D", "clear the finished runs from the list"),
        ]

    @property
    def table(self) -> DataTable:
        return self.query_one("#run-table", DataTable)

    def current(self) -> Run | None:
        key = row_key(self.table)
        if key is None:
            return None
        return next((r for r in self.clippet.runs if str(r.id) == key), None)

    def rows(self, t: float) -> list[tuple[str, tuple]]:
        p = self.pal
        rows = []
        for run in reversed(self.clippet.runs):
            tokens = run.tokens
            rows.append((str(run.id), (
                run_mark(run.state, t, p),
                Text(str(run.id), style=p["muted"]),
                Text(clip(run.env, 12), style=p["ink"]),
                Text(run.agent, style=p[AGENT_COLOR.get(run.agent, "ink")]),
                Text(clip(run.prompt, 20), style=p["ink"]),
                Text(dur(run.elapsed) if run.started else "—", style=p["muted"]),
                Text(f"{tokens / 1000:.1f}k" if tokens else "—", style=p["muted"]),
            )))
        return rows

    def refresh_view(self) -> None:
        self.draw(self.clippet.t)

    def focus_run(self, run_id: int) -> None:
        cursor_to(self.table, str(run_id))

    def draw(self, t: float) -> None:
        p = self.pal
        sync_rows(self.table, self.rows(t))
        runs = self.clippet.runs
        chips = Text()
        counts = {state: sum(1 for r in runs if r.state == state) for state in RUN_WORDS}
        for i, (state, n) in enumerate((s, c) for s, c in counts.items() if c):
            if i:
                chips.append("  ")
            chips.append_text(run_mark(state, t, p))
            chips.append(f" {n} {RUN_WORDS[state]}", style=p["muted"])
        if not runs:
            chips.append("no runs yet", style=p["muted"])
        self.query_one("#run-chips", Bar).update(chips)
        self.show_detail(t)

    def tick(self, t: float) -> None:
        if any(r.active for r in self.clippet.runs) or self.clippet.runs_dirty:
            self.clippet.runs_dirty = False
            self.draw(t)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "run-table":
            self._drawn = None
            self.show_detail(self.clippet.t)

    def show_detail(self, t: float) -> None:
        run = self.current()
        pane = self.query_one("#run-detail", Pane)
        if run is None:
            if self._drawn != ("empty",):
                self._drawn = ("empty",)
                pane.show(self.empty())
            return
        state = (run.id, run.state, run.lines, len(run.steps), run.error, int(t * 4) if run.active else 0)
        if state == self._drawn:
            return
        follow = pane.scroll_y >= pane.max_scroll_y - 1
        self._drawn = state
        pane.show(self.detail(run, t))
        if run.active and follow:
            pane.scroll_end(animate=False)

    def empty(self) -> Group:
        p = self.pal
        hint = Text("\nNo runs yet.  ", style=p["muted"])
        hint.append("i", style=f"bold {p['brand']}")
        hint.append(" writes a prompt for the selected env; mark envs with ", style=p["muted"])
        hint.append("Space", style=f"bold {p['brand']}")
        hint.append(" in Envs to send it to several at once.", style=p["muted"])
        return Group(mark(p, 1), hint)

    def detail(self, run: Run, t: float) -> Group:
        p = self.pal
        parts: list[Any] = []
        head = Text()
        head.append_text(run_mark(run.state, t, p))
        head.append(f" #{run.id}  ", style=p["muted"])
        head.append(run.agent, style=f"bold {p[AGENT_COLOR.get(run.agent, 'ink')]}")
        head.append(" @ ", style=p["muted"])
        head.append(run.env, style=f"bold {p['ink']}")
        head.append(f"   {RUN_WORDS.get(run.state, run.state)}", style=p["muted"])
        if run.started:
            head.append(f" · {dur(run.elapsed)}", style=p["muted"])
        parts.append(head)
        where = Text(f"  in {short_path(run.workspace)}", style=p["faint"])
        if run.sandbox and run.agent == "codex":
            where.append(f" · sandbox {run.sandbox}", style=p["faint"])
        parts.append(where)
        parts.append(Text())
        prompt = Text("you ❯ ", style=f"bold {p['brand']}")
        prompt.append(run.prompt, style=p["ink"])
        parts.append(prompt)
        parts.append(Text())
        for note in run.notes:
            parts.append(Text(f"  · {note}", style=p["warn"] if note.startswith("warning") else p["muted"]))

        steps = [s for s in run.steps if s.kind != "message"]
        if steps:
            body = Text()
            for step in steps[-40:]:
                sign, color = STEP_SIGNS.get(step.kind, ("·", "muted"))
                if step.state == "running":
                    body.append("  ")
                    body.append_text(run_mark("running", t, p))
                else:
                    body.append(f"  {sign}", style=p["bad"] if step.state == "failed" else p[color])
                body.append(" ")
                text = clip(step.text, 90)
                tone = {"reasoning": p["muted"], "warning": p["warn"], "error": p["bad"]}.get(step.kind, p["ink"])
                body.append(text, style=tone)
                if step.kind == "command" and step.detail:
                    first, _, rest = step.detail.partition("\n")
                    body.append(f"  {first}", style=p["bad"] if step.state == "failed" else p["faint"])
                    if step.state == "failed" and rest:
                        for line in rest.splitlines()[-3:]:
                            body.append(f"\n      {clip(line, 86)}", style=p["faint"])
                body.append("\n")
            parts.append(body)

        if run.active:
            line = Text("  ")
            line.append_text(run_mark("running", t, p))
            line.append(" ")
            line.append_text(gleam(run.activity + "…", t, p["run"], p["hi"]))
            parts.append(line)
        answer = run.answer
        if answer:
            parts.append(Text())
            parts.append(heading("Answer", p))
            parts.append(Padding(Markdown(answer, code_theme="ansi_dark" if self.clippet.is_dark else "ansi_light"),
                                 (0, 0, 0, 2)))
        elif run.tail and run.active:  # output no reader turned into steps
            parts.append(Text("\n".join(run.tail[-8:]), style=p["faint"]))
        if run.error:
            parts.append(Text())
            parts.append(Text(f"✕ {run.error}", style=p["bad"]))
        if not run.active:
            foot = Text()
            usage = run.usage or {}
            if run.tokens is not None:
                foot.append("  tokens ", style=p["muted"])
                foot.append(f"in {usage.get('input_tokens', '—')}", style=p["ink"])
                cached = usage.get("cached_input_tokens")
                if cached:
                    foot.append(f" (cached {cached})", style=p["muted"])
                foot.append(f" · out {usage.get('output_tokens', '—')}", style=p["ink"])
                foot.append("\n")
            if run.cost:
                foot.append("  cost ", style=p["muted"])
                foot.append(run.cost, style=p["ink"])
                foot.append("\n")
            if run.session_id:
                foot.append("  session ", style=p["muted"])
                foot.append(run.session_id, style=p["faint"])
                spec = agent_spec(run.agent)
                if spec is not None and spec.resume_args(run.session_id):
                    foot.append("\n  c", style=f"bold {p['brand']}")
                    foot.append(" continues it interactively in the same env", style=p["muted"])
            if foot:
                parts.append(Text())
                parts.append(foot)
        return Group(*parts)

    # --- actions ------------------------------------------------------------------

    def action_cancel(self) -> None:
        run = self.current()
        if run is None or not run.active:
            return
        self.app.push_screen(Confirm(f"Stop run #{run.id}?", f"{run.agent} @ {run.env}"),
                             lambda yes: yes and self.clippet.cancel_run(run.id))

    def action_rerun(self) -> None:
        run = self.current()
        if run is not None:
            self.clippet.start_runs(run.prompt, [run.env], agent=run.agent, sandbox=run.sandbox)

    def action_continue(self) -> None:
        """Pick this run's conversation up interactively, after showing as whom and where."""

        from clippet.tui.views.launch import (
            Launch,
            conversation,
            resume_args,
            resume_folder,
        )

        run = self.current()
        if run is None:
            return
        env = self.clippet.env_named(run.env)
        if env is None:
            self.app.notify(f"env '{run.env}' is gone", severity="error")
            return
        args = resume_args(run.agent, run.session_id)
        folder, note = resume_folder(run.workspace, self.clippet.workspace, self.pal)
        lead = f"run #{run.id} · " if args else f"run #{run.id} left no session to resume · "

        def done(choice: tuple[str, list[str]] | None) -> None:
            if choice:
                self.clippet.launch(env, *choice, cwd=str(folder))

        self.app.push_screen(Launch(env, run.agent, folder, self.pal, args=args,
                                    session=conversation(run.prompt, self.pal, lead=lead), lock_agent=True,
                                    folder_note=note, title=f"Continue in {env.name}"), done)

    def action_copy(self) -> None:
        run = self.current()
        if run is not None and run.answer:
            self.app.copy_to_clipboard(run.answer)
            self.app.notify(clip(run.answer, 60), title="copied")

    def action_clear(self) -> None:
        self.clippet.runs = [r for r in self.clippet.runs if r.active]
        self.clippet.runs_dirty = True
        self.refresh_view()
