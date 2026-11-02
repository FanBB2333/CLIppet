"""Accounts: the Codex accounts CLIppet can see — the Cockpit Tools store and
any export or token file you add — and which env holds each.  From here an
account goes into an env (new or existing) or out as a native auth.json."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable

from clippet.tui.data import BASE, AccountView, suggest_env_name
from clippet.tui.look import LOGIN_WORDS, clip, heading, login_mark, mark, moment, quota_cell, quota_lines, short_path
from clippet.tui.widgets import Bar, Confirm, Field, Pane, Place, Prompt, row_key, sync_rows

FORMAT_WORDS = {
    "codex-auth": "Codex auth.json",
    "cockpit-store": "Cockpit Tools store",
    "cockpit-export": "Cockpit Tools export",
    "cockpit-account": "Cockpit Tools account",
    "sub2api": "sub2api export",
    "cliproxyapi": "CLIProxyAPI token file",
    "tokens": "token bundle",
}


class AccountsPlace(Place):
    key = "accounts"
    title = "Accounts"

    BINDINGS = [
        Binding("x", "export", "export auth.json", show=False),
        Binding("plus", "add_source", "add source", show=False),
        Binding("minus", "forget_source", "forget source", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(id="accounts")

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(classes="list-col"):
                yield Bar(id="acct-chips", classes="chips")
                yield DataTable(id="acct-table", cursor_type="row", zebra_stripes=False)
            with Vertical(classes="detail-col"):
                yield Pane(id="acct-detail")

    def on_mount(self) -> None:
        table = self.query_one("#acct-table", DataTable)
        table.add_column(" ", key="state", width=1)
        table.add_column("#", key="n", width=3)
        table.add_column("account", key="who", width=26)
        table.add_column("plan", key="plan", width=5)
        table.add_column("quota", key="quota", width=5)
        table.add_column("from", key="from", width=10)
        table.add_column("in env", key="env", width=10)

    def hints(self) -> list[tuple[str, str]]:
        return [("Enter", "into an env"), ("x", "export auth.json"), ("+", "add a file"), ("-", "forget it")]

    def help_rows(self) -> list[tuple[str, str]]:
        return [
            ("Enter", "put this account into an env (new or existing)"),
            ("x", "write it out as a native Codex auth.json (0600)"),
            ("+", "add an account file: Cockpit export, sub2api, CLIProxyAPI, auth.json…"),
            ("-", "forget the file this account came from"),
            ("Ctrl+R", "read every source again"),
        ]

    @property
    def table(self) -> DataTable:
        return self.query_one("#acct-table", DataTable)

    def current(self) -> AccountView | None:
        key = row_key(self.table)
        return next((a for a in self.clippet.accounts if a.key == key), None)

    def refresh_view(self) -> None:
        app, p = self.clippet, self.pal
        rows = []
        for account in app.accounts:
            credential = account.credential
            who = credential.display_email or credential.label or credential.display_account_id or "?"
            if credential.auth_mode == "apikey":
                who = "API key" + (f" ({credential.label})" if credential.label else "")
            label = Text(clip(who, 24), style=p["ink"])
            if credential.is_current:
                label.append(" ★", style=p["brand"])
            holders = account.holders
            rows.append((account.key, (
                login_mark(account.login, p),
                Text(str(account.index), style=p["muted"]),
                label,
                Text(clip(credential.display_plan or "", 5), style=p["muted"]),
                quota_cell(account.quota, p),
                Text(clip(account.source_label, 10), style=p["info"]),
                Text(clip(", ".join(holders), 10) if holders else "—", style=p["ok"] if holders else p["faint"]),
            )))
        sync_rows(self.table, rows)
        chips = Text()
        if not app.sources:
            chips.append("no account sources yet", style=p["muted"])
        for i, source in enumerate(app.source_reports):
            if i:
                chips.append("  ")
            if source.error:
                chips.append("✕ ", style=p["bad"])
                chips.append(clip(source.label, 18), style=p["muted"])
            else:
                chips.append("✓ ", style=p["ok"])
                chips.append(clip(source.label, 18), style=p["ink"])
                chips.append(f" {source.count}", style=p["muted"])
        self.query_one("#acct-chips", Bar).update(chips)
        self.show_detail()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "acct-table":
            self.show_detail()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "acct-table":
            self.action_import_account()

    def show_detail(self) -> None:
        pane = self.query_one("#acct-detail", Pane)
        account = self.current()
        pane.show(self.detail(account) if account else self.empty())

    def empty(self) -> Group:
        p = self.pal
        parts: list[Any] = [mark(p, 1)]
        errors = [s for s in self.clippet.source_reports if s.error]
        if errors:
            for source in errors:
                line = Text(f"\n✕ {source.label}: ", style=p["bad"])
                line.append(source.error or "", style=p["muted"])
                parts.append(line)
            if any("cryptography" in (s.error or "") for s in errors):
                parts.append(Text("\nReading the Cockpit Tools store needs: pip install 'clippet[cockpit]'",
                                  style=p["muted"]))
        hint = Text("\nNo accounts to show.  ", style=p["muted"])
        hint.append("+", style=f"bold {p['brand']}")
        hint.append(" adds a file: a Cockpit Tools export or backup, a sub2api export, a CLIProxyAPI codex-*.json, "
                    "or a Codex auth.json.", style=p["muted"])
        parts.append(hint)
        return Group(*parts)

    def detail(self, account: AccountView) -> Group:
        p = self.pal
        credential = account.credential
        parts: list[Any] = []
        head = Text(credential.display_email or credential.label or "Codex account", style=f"bold {p['ink']}")
        if credential.display_plan:
            head.append(f"  [{credential.display_plan}]", style=p["brand"])
        if credential.is_current:
            head.append("  ★ current in Cockpit Tools", style=p["brand"])
        parts.append(head)
        state = Text("  ")
        state.append_text(login_mark(account.login, p))
        state.append(f" {LOGIN_WORDS[account.login]}", style=p["muted"])
        if credential.requires_reauth and credential.reauth_reason:
            state.append(f" ({credential.reauth_reason})", style=p["bad"])
        parts.append(state)
        parts.append(Text())
        parts.append(heading("Details", p))
        rows: list[tuple[str, str | Text]] = [
            ("kind", "API key" if credential.auth_mode == "apikey" else "ChatGPT login"),
            ("account", credential.display_account_id or "—"),
        ]
        if credential.auth_mode != "apikey":
            if account.quota is not None:
                rows += [("quota" if i == 0 else "", line) for i, line in enumerate(quota_lines(account.quota, p))]
            else:
                rows.append(("quota", Text("no reading yet", style=p["faint"])))
        rows += [
            ("label", credential.label or "—"),
            ("token", f"expires {moment(credential.access_expires_at)}" if credential.access_expires_at else "—"),
            ("refreshed", moment(credential.effective_last_refresh) if credential.effective_last_refresh else "—"),
            ("format", FORMAT_WORDS.get(credential.source_format or "", credential.source_format or "—")),
            ("source", short_path(account.source)),
        ]
        if credential.base_url:
            rows.append(("base url", credential.base_url))
        body = Text()
        for label, value in rows:
            body.append(f"  {label:<10} ", style=p["muted"])
            if isinstance(value, Text):
                body.append_text(value)
                body.append("\n")
            else:
                body.append(f"{value}\n", style=p["ink"])
        parts.append(body)
        parts.append(heading("Envs", p))
        if account.holders:
            held = Text("  ")
            for i, name in enumerate(account.holders):
                if i:
                    held.append(" · ", style=p["faint"])
                held.append(name, style=f"bold {p['ok']}")
            held.append("  hold this account\n", style=p["muted"])
            parts.append(held)
        else:
            parts.append(Text("  no env holds it yet\n", style=p["faint"]))
        hint = Text("  Enter", style=f"bold {p['brand']}")
        hint.append(" puts it into an env", style=p["muted"])
        if account.source == "cockpit" or account.source.startswith("cockpit:"):
            hint.append(" (linked: the env pulls the newest login from Cockpit before each launch)", style=p["faint"])
        parts.append(hint)
        return Group(*parts)

    # --- actions ------------------------------------------------------------------

    def action_import_account(self) -> None:
        account = self.current()
        if account is not None:
            self.clippet.import_account(account)

    def action_export(self) -> None:
        account = self.current()
        if account is None:
            return
        name = suggest_env_name(account.credential, set())
        default = str(self.clippet.workspace / f"{name}-auth.json")

        def chosen(path_text: str | None) -> None:
            if not path_text:
                return
            target = Path(path_text).expanduser()

            def write(yes: bool | None = True) -> None:
                if not yes:
                    return
                from clippet.isolation import _write_private_file

                try:
                    _write_private_file(target, account.credential.to_auth_json_text().encode("utf-8"))
                except OSError as exc:
                    self.app.notify(str(exc), title="export", severity="error")
                    return
                self.app.notify(f"{short_path(target)} (0600) — keep it private", title="exported")

            if target.exists():
                self.app.push_screen(Confirm(f"Replace {short_path(target)}?", "It already exists."), write)
            else:
                write()

        self.app.push_screen(Prompt("Export as a Codex auth.json", hint="It holds a live login; keep it private.",
                                    value=default), chosen)

    def action_add_source(self) -> None:
        def chosen(path_text: str | None) -> None:
            if not path_text:
                return
            source = path_text if path_text.startswith("cockpit") else str(Path(path_text).expanduser().resolve())
            if source not in ("cockpit",) and not source.startswith("cockpit:") and not Path(source).exists():
                self.app.notify(f"{source} does not exist", severity="error")
                return
            self.clippet.add_source(source)

        self.app.push_screen(Prompt(
            "Add an account source",
            hint="A Cockpit Tools export or backup, a sub2api export, a CLIProxyAPI codex-*.json, a Codex auth.json, "
                 "a HOME or CODEX_HOME folder, or cockpit:<dir>.",
            placeholder="~/Downloads/cockpit_auto_backup.json",
        ), chosen)

    def action_forget_source(self) -> None:
        account = self.current()
        if account is None:
            return
        if account.source not in self.clippet.prefs.sources:
            self.app.notify("Found on its own (Cockpit Tools); nothing to forget.", severity="warning")
            return
        self.app.push_screen(
            Confirm(f"Forget {account.source_label}?", "Only the TUI's list changes; the file stays."),
            lambda yes: yes and self.clippet.forget_source(account.source),
        )


def import_fields(account: AccountView, env_names: list[str], default_name: str) -> list[Field]:
    linked = account.source == "cockpit" or account.source.startswith("cockpit:")
    return [
        Field("name", "env (a new name creates it, with your Codex config but no other logins)", default_name),
        Field("link", "linked: pull the newest login from the source before each launch", linked, flag=True),
    ]


def default_env_for(account: AccountView, env_names: list[str]) -> str:
    if account.holders:
        return next((h for h in account.holders if h != BASE), account.holders[0])
    return suggest_env_name(account.credential, set(env_names))
