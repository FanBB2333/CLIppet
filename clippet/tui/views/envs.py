"""Envs: every HOME the agents can run in — ``base`` (your real HOME) and the
registered envs — with the Codex account each holds.  From here you launch
an agent in an env, resume a Codex session, and create, clone, describe,
sync or remove envs."""

from __future__ import annotations

import platform
import subprocess
from typing import Any

from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable

from clippet.tui.data import BASE, EnvView, SessionView, codex_sessions, count_sessions
from clippet.tui.look import (
    AGENT_COLOR,
    AGENT_LETTER,
    AGENTS,
    LOGIN_WORDS,
    clip,
    heading,
    login_mark,
    mark,
    moment,
    quota_cell,
    quota_lines,
    short_path,
)
from clippet.tui.widgets import (
    Bar,
    Confirm,
    Divider,
    Field,
    Form,
    Pane,
    Pick,
    Place,
    Prompt,
    row_key,
    sync_rows,
)

KIND_WORDS = {BASE: "your real HOME", "home": "HOME env", "file": "file alias"}


def agents_cell(env: EnvView, p: dict[str, str]) -> Text:
    out = Text()
    for agent in AGENTS:
        if env.agents.get(agent):
            out.append(AGENT_LETTER[agent], style=p[AGENT_COLOR[agent]])
        else:
            out.append("·", style=p["faint"])
    return out


class EnvsPlace(Place):
    key = "envs"
    title = "Envs"

    BINDINGS = [
        Binding("x", "launch('codex')", "codex here", show=False),
        Binding("r", "resume_last", "resume last", show=False),
        Binding("s", "pick_session", "sessions", show=False),
        Binding("space", "mark", "mark", show=False),
        Binding("n", "new", "new env", show=False),
        Binding("c", "clone", "clone", show=False),
        Binding("d", "remove", "remove", show=False),
        Binding("e", "describe", "describe", show=False),
        Binding("y", "sync", "sync login", show=False),
        Binding("a", "import_account", "import account", show=False),
        Binding("o", "open_folder", "open folder", show=False),
        Binding("p", "copy_path", "copy path", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(id="envs")
        self.sessions: dict[str, tuple[int, list[SessionView]]] = {}

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(classes="list-col"):
                yield Bar(id="env-chips", classes="chips")
                yield DataTable(id="env-table", cursor_type="row", zebra_stripes=False)
            yield Divider(self.key)
            with Vertical(classes="detail-col"):
                yield Pane(id="env-detail")

    def on_mount(self) -> None:
        table = self.query_one("#env-table", DataTable)
        table.add_column(" ", key="mark", width=1)
        table.add_column(" ", key="state", width=1)
        table.add_column("env", key="name", width=16)
        table.add_column("codex account", key="who", width=22)
        table.add_column("plan", key="plan", width=5)
        table.add_column("quota", key="quota", width=5)
        table.add_column("agents", key="agents", width=7)

    def hints(self) -> list[tuple[str, str]]:
        return [("Enter", "launch"), ("x", "codex"), ("r", "resume"), ("Space", "mark"), ("n", "new"),
                ("a", "import account")]

    def help_rows(self) -> list[tuple[str, str]]:
        return [
            ("Enter", "launch an agent here (asks which, and its arguments)"),
            ("x", "launch Codex here right away"),
            ("r / s", "resume the last Codex session / pick one"),
            ("Space", "mark envs: a prompt then runs in every marked env"),
            ("n / c", "new env / clone this one"),
            ("a", "import a Codex account into this env"),
            ("y", "pull the newest login from a linked source"),
            ("e", "change the description"),
            ("d", "remove (keeps the files unless you say otherwise)"),
            ("o / p", "open the folder / copy its path"),
        ]

    # --- drawing ----------------------------------------------------------------

    @property
    def table(self) -> DataTable:
        return self.query_one("#env-table", DataTable)

    def current(self) -> EnvView | None:
        key = row_key(self.table)
        return self.clippet.env_named(key) if key else None

    def refresh_view(self) -> None:
        app, p = self.clippet, self.pal
        rows = []
        for env in app.envs:
            name_style = p["muted"] if env.is_base else (p["bad"] if not env.exists else f"bold {p['ink']}")
            who = env.credential.display_email if env.credential else None
            if env.credential is not None and env.credential.auth_mode == "apikey":
                who = "API key" + (f" @ {env.credential.base_url}" if env.credential.base_url else "")
            who_text = Text(clip(who or "—", 20 if env.linked else 22), style=p["ink"] if who else p["faint"])
            if env.linked:
                who_text.append(" ⇄", style=p["info"])
            rows.append((env.name, (
                Text("●", style=p["brand"]) if env.name in app.marked else Text(" "),
                login_mark(env.login, p),
                Text(clip(env.name, 16), style=name_style),
                who_text,
                Text(clip(env.credential.display_plan or "", 5) if env.credential else "", style=p["muted"]),
                quota_cell(env.quota, p),
                agents_cell(env, p),
            )))
        table = self.table
        sync_rows(table, rows)
        if app.selected_env and row_key(table) != app.selected_env:
            keys = [k for k, _ in rows]
            if app.selected_env in keys:
                table.move_cursor(row=keys.index(app.selected_env), animate=False)
        chips = Text()
        registered = len(app.envs) - 1
        chips.append(f"{registered} env{'s' if registered != 1 else ''}", style=f"bold {p['ink']}")
        chips.append(" + base", style=p["muted"])
        if app.marked:
            chips.append(f"   ● {len(app.marked)} marked", style=p["brand"])
        if app.env_error:
            chips.append(f"   ✕ {clip(app.env_error, 40)}", style=p["bad"])
        self.query_one("#env-chips", Bar).update(chips)
        self.show_detail()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "env-table" or event.row_key is None:
            return
        self.clippet.selected_env = event.row_key.value
        self.clippet.retarget()
        self.show_detail()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "env-table":
            self.action_launch()

    def show_detail(self) -> None:
        env = self.current()
        pane = self.query_one("#env-detail", Pane)
        if env is None:
            pane.show(self.empty())
            return
        pane.show(self.detail(env))
        self.load_sessions(env)

    def empty(self) -> Group:
        p = self.pal
        hint = Text("\nNo env selected.  ", style=p["muted"])
        hint.append("n", style=f"bold {p['brand']}")
        hint.append(" makes one; ", style=p["muted"])
        hint.append("2", style=f"bold {p['brand']}")
        hint.append(" lists the accounts you can import.", style=p["muted"])
        return Group(mark(p, 1), hint)

    def detail(self, env: EnvView) -> Group:
        p = self.pal
        parts: list[Any] = []
        head = Text(env.name, style=f"bold {p['ink']}")
        head.append(f"   {KIND_WORDS.get(env.kind, env.kind)}", style=p["muted"])
        parts.append(head)
        parts.append(Text(short_path(env.path) if env.path else "—", style=p["muted"]))
        if env.description and not env.is_base:
            parts.append(Text(f"“{env.description}”", style=p["ink"]))
        if not env.exists:
            parts.append(Text(f"\n✕ missing on disk: {env.path}", style=p["bad"]))
            return Group(*parts)
        parts.append(Text())

        if env.is_home:
            parts.append(heading("Codex", p))
            parts.append(self.codex_block(env))
            parts.append(Text())
            parts.append(heading("Agents", p))
            line = Text("  ")
            for agent in AGENTS:
                present = env.agents.get(agent)
                line.append("✓ " if present else "· ", style=p["ok"] if present else p["faint"])
                line.append(agent, style=p[AGENT_COLOR[agent]] if present else p["muted"])
                line.append("   ")
            parts.append(line)
            if env.share and not env.is_base:
                shared = Text("  shared  ", style=p["muted"])
                for i, entry in enumerate(env.share):
                    if i:
                        shared.append(" · ", style=p["faint"])
                    target = env.path / entry if env.path else None
                    ok = target is not None and target.is_symlink() and target.exists()
                    shared.append(entry, style=p["ink"] if ok else p["warn"])
                    shared.append(" ↗" if ok else " (not linked yet)", style=p["info"] if ok else p["faint"])
                parts.append(shared)
            parts.append(Text())
            parts.append(self.sessions_block(env))
        else:
            parts.append(Text("A file alias: each run copies this file into a fresh temporary HOME.", style=p["muted"]))
        return Group(*parts)

    def codex_block(self, env: EnvView) -> Text:
        p = self.pal
        credential = env.credential
        out = Text("  ")
        out.append_text(login_mark(env.login, p))
        if credential is None:
            out.append("  not logged in", style=p["muted"])
            out.append("\n  ")
            out.append("x", style=f"bold {p['brand']}")
            out.append(" then /login, or ", style=p["muted"])
            out.append("a", style=f"bold {p['brand']}")
            out.append(" to import an account", style=p["muted"])
            return out
        if credential.auth_mode == "apikey":
            out.append("  API key", style=f"bold {p['ink']}")
            if credential.base_url:
                out.append(f" @ {credential.base_url}", style=p["muted"])
            return out
        out.append(f"  {credential.display_email or 'ChatGPT login'}", style=f"bold {p['ink']}")
        if credential.display_plan:
            out.append(f"  [{credential.display_plan}]", style=p["brand"])
        if env.login not in ("ok",):
            out.append(f"\n  {LOGIN_WORDS[env.login]}", style=p["warn"] if env.login == "stale" else p["bad"])
        rows = []
        if credential.display_account_id:
            rows.append(("account", Text(credential.display_account_id, style=p["muted"])))
        if env.quota is not None:
            rows += [("quota" if i == 0 else "", line) for i, line in enumerate(quota_lines(env.quota, p))]
        else:
            rows.append(("quota", Text("no reading yet (a Codex run here leaves one)", style=p["faint"])))
        expires = credential.access_expires_at
        refreshed = credential.effective_last_refresh
        if expires is not None:
            rows.append(("token", Text(f"expires {moment(expires)}",
                                       style=p["warn"] if credential.is_expired() else p["muted"])))
        if refreshed is not None:
            rows.append(("refreshed", Text(moment(refreshed), style=p["muted"])))
        if env.source:
            source = env.source
            text = Text(f"{short_path(source.get('source', '?'))} · {source.get('format', '?')}", style=p["muted"])
            if source.get("linked"):
                text.append("  ⇄ linked", style=p["info"])
                text.append(" (newest login pulled before each launch)", style=p["faint"])
            rows.append(("source", text))
        for label, value in rows:
            out.append(f"\n  {label:<10} ", style=p["muted"])
            out.append_text(value)
        return out

    def sessions_block(self, env: EnvView) -> Text:
        p = self.pal
        cached = self.sessions.get(env.name)
        total, sessions = cached if cached else (0, [])
        out = heading("Codex sessions", p, f"{total} kept" if cached else "reading…")
        out.append("\n")
        if cached and not sessions:
            out.append("  none yet\n", style=p["faint"])
        for session in sessions[:10]:
            stamp = session.started.astimezone().strftime("%m-%d %H:%M") if session.started else "—"
            out.append(f"  {stamp}  ", style=p["muted"])
            out.append(clip(short_path(session.cwd) or "—", 22).ljust(22), style=p["info"])
            out.append("  ")
            out.append(clip(session.prompt or "(no prompt)", 48), style=p["ink"] if session.prompt else p["faint"])
            if session.origin == "codex_exec":
                out.append("  exec", style=p["faint"])
            out.append("\n")
        if sessions:
            out.append("  r", style=f"bold {p['brand']}")
            out.append(" resumes the newest · ", style=p["muted"])
            out.append("s", style=f"bold {p['brand']}")
            out.append(" picks one\n", style=p["muted"])
        return out

    def load_sessions(self, env: EnvView) -> None:
        if not env.is_home or env.codex_home is None:
            return
        self.run_worker(lambda: self._read_sessions(env), thread=True, group=f"sessions-{env.name}",
                        exclusive=True, exit_on_error=False)

    def _read_sessions(self, env: EnvView) -> None:
        sessions = codex_sessions(env.codex_home, limit=12)
        total = count_sessions(env.codex_home)
        previous = self.sessions.get(env.name)
        fresh = (total, sessions)
        if previous is not None and previous[0] == total and [s.id for s in previous[1]] == [s.id for s in sessions]:
            return
        self.sessions[env.name] = fresh
        self.app.call_from_thread(self._sessions_ready, env.name)

    def _sessions_ready(self, name: str) -> None:
        env = self.current()
        if env is not None and env.name == name:
            self.query_one("#env-detail", Pane).show(self.detail(env))

    # --- actions ------------------------------------------------------------------

    def action_launch(self, agent: str | None = None) -> None:
        env = self.current()
        if env is None:
            return
        if agent:
            self.clippet.launch(env, agent, [])
            return
        fields = [
            Field("agent", "agent", self.clippet.agent, options=[(a, a) for a in AGENTS]),
            Field("args", "arguments passed to it (optional)", "", placeholder="e.g. resume --last   or   -m gpt-5.5"),
        ]

        def done(values: dict | None) -> None:
            if not values or not values.get("agent"):
                return
            import shlex

            try:
                args = shlex.split(values.get("args") or "")
            except ValueError as exc:
                self.app.notify(str(exc), title="arguments", severity="error")
                return
            self.clippet.launch(env, values["agent"], args)

        self.app.push_screen(Form(f"Launch in {env.name}", fields,
                                  hint="The agent takes over the terminal; clippet comes back when it exits."), done)

    def action_resume_last(self) -> None:
        env = self.current()
        if env is not None:
            self.clippet.launch(env, "codex", ["resume", "--last"])

    def action_pick_session(self) -> None:
        env = self.current()
        if env is None:
            return
        cached = self.sessions.get(env.name)
        sessions = cached[1] if cached else codex_sessions(env.codex_home, limit=12)
        if not sessions:
            self.app.notify("No Codex sessions in this env yet.", title=env.name)
            return
        p = self.pal
        options = []
        for session in sessions:
            label = Text(session.started.astimezone().strftime("%m-%d %H:%M  ") if session.started else "—  ",
                         style=p["muted"])
            label.append(clip(session.prompt or "(no prompt)", 52), style=p["ink"])
            label.append(f"\n   {clip(short_path(session.cwd), 60)}", style=p["faint"])
            options.append((session, label))

        def done(session: SessionView | None) -> None:
            if session is not None:
                self.clippet.launch(env, "codex", ["resume", session.id], cwd=session.cwd or None)

        self.app.push_screen(Pick(f"Resume a Codex session in {env.name}", options), done)

    def action_mark(self) -> None:
        env = self.current()
        if env is None:
            return
        marked = self.clippet.marked
        if env.name in marked:
            marked.discard(env.name)
        else:
            marked.add(env.name)
        self.clippet.retarget()
        self.refresh_view()

    def action_new(self) -> None:
        fields = [
            Field("name", "name", "", placeholder="e.g. work"),
            Field("description", "description", ""),
            Field("from_current", "copy my agent configuration (config.toml, settings, skills…; no logins)", True,
                  flag=True),
            Field("with_auth", "also copy my logins (refresh tokens rotate: only if you know you want a copy)",
                  False, flag=True),
        ]

        def done(values: dict | None) -> None:
            if not values or not values.get("name"):
                return
            from clippet.config.environments import create_home_env_with_report

            try:
                home, report = create_home_env_with_report(
                    values["name"],
                    from_current=bool(values.get("from_current")),
                    with_auth=bool(values.get("with_auth")),
                    description=values.get("description") or "",
                )
            except (ValueError, FileExistsError, OSError) as exc:
                self.app.notify(str(exc), title="new env", severity="error")
                return
            note = f"{short_path(home)}"
            if report.copied:
                note += f" · copied {len(report.copied)} item(s)"
            if report.skipped_credentials:
                note += " · logins left out"
            self.clippet.reload(select=values["name"])
            self.app.notify(note, title=f"created {values['name']}")

        self.app.push_screen(Form("New HOME env", fields), done)

    def action_clone(self) -> None:
        env = self.current()
        if env is None or env.kind != "home":
            self.app.notify("Only HOME envs can be cloned.", severity="warning")
            return
        fields = [
            Field("name", "new name", f"{env.name}-copy"),
            Field("with_auth", "copy the login too (it then shares a rotating refresh token)", False, flag=True),
        ]

        def done(values: dict | None) -> None:
            if not values or not values.get("name"):
                return
            from clippet.config.environments import clone_home_env

            try:
                clone_home_env(env.name, values["name"], with_auth=bool(values.get("with_auth")))
            except (ValueError, KeyError, FileExistsError, OSError) as exc:
                self.app.notify(str(exc), title="clone", severity="error")
                return
            self.clippet.reload(select=values["name"])
            self.app.notify(f"{env.name} → {values['name']}", title="cloned")

        self.app.push_screen(Form(f"Clone {env.name}", fields), done)

    def action_remove(self) -> None:
        env = self.current()
        if env is None or env.is_base:
            return
        fields = [Field("purge", f"also delete {short_path(env.path)} from disk", False, flag=True)]

        def chosen(values: dict | None) -> None:
            if values is None:
                return
            purge = bool(values.get("purge")) and env.kind == "home"
            body = (f"{short_path(env.path)} will be deleted, with its logins and sessions." if purge
                    else "The env is forgotten; its files stay where they are.")

            def confirmed(yes: bool | None) -> None:
                if not yes:
                    return
                from clippet.config.environments import remove_environment

                try:
                    remove_environment(env.name, purge=purge)
                except (KeyError, ValueError, OSError) as exc:
                    self.app.notify(str(exc), title="remove", severity="error")
                    return
                self.clippet.marked.discard(env.name)
                self.clippet.reload(select=BASE)
                self.app.notify("deleted from disk" if purge else "unregistered", title=f"removed {env.name}")

            self.app.push_screen(Confirm(f"Remove {env.name}?", body), confirmed)

        self.app.push_screen(Form(f"Remove {env.name}", fields), chosen)

    def action_describe(self) -> None:
        env = self.current()
        if env is None or env.is_base:
            return

        def done(text: str | None) -> None:
            if text is None:
                return
            from clippet.config.environments import update_environment

            try:
                update_environment(env.name, description=text)
            except (KeyError, OSError, ValueError) as exc:
                self.app.notify(str(exc), severity="error")
                return
            self.clippet.reload(select=env.name)

        self.app.push_screen(Prompt(f"Describe {env.name}", value=env.description), done)

    def action_sync(self) -> None:
        env = self.current()
        if env is None or not env.linked:
            self.app.notify("This env is not linked to an account source.", severity="warning")
            return
        from clippet.config.environments import sync_linked_codex_credential

        result = sync_linked_codex_credential(env.name, env.profile)
        words = {"updated": "pulled the newest login", "unchanged": "already up to date", "skipped": "not linked"}
        severity = "warning" if result.action in ("env-newer", "error") else "information"
        self.app.notify(result.message or words.get(result.action, result.action), title=f"sync {env.name}",
                        severity=severity)
        self.clippet.reload(select=env.name)

    def action_import_account(self) -> None:
        env = self.current()
        if env is None or env.is_base:
            self.app.notify("Pick a HOME env first (or press 2 to import into a new one).", severity="warning")
            return
        self.clippet.pick_account_for(env.name)

    def action_open_folder(self) -> None:
        env = self.current()
        if env is None or env.path is None:
            return
        opener = "open" if platform.system() == "Darwin" else "xdg-open"
        target = env.path if env.path.is_dir() else env.path.parent
        try:
            subprocess.Popen([opener, str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self.app.notify(str(exc), severity="error")

    def action_copy_path(self) -> None:
        env = self.current()
        if env is not None and env.path is not None:
            self.app.copy_to_clipboard(str(env.path))
            self.app.notify(str(env.path), title="copied")
