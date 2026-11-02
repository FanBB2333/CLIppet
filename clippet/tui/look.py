"""The TUI's colours, marks and words.

The palettes are papertown's paper palette (``web/src/hand.css``): ink on a
dark kraft sheet, and the same ink on paper.  ``MARK`` is CLIppet's
hand-drawn mark in braille dots, printed by ``scripts/draw_art.py --braille``
from the same strokes as ``resources/logo.svg``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.text import Text
from textual.theme import Theme

from clippet.agents import AGENT_KEYS
from clippet.agents import AGENTS as AGENT_SPECS
from clippet.tui.anim import bar, breathe, mix, star

if TYPE_CHECKING:
    from clippet.auth.codex import CodexCredential
    from clippet.auth.quota import QuotaReading

PALETTES: dict[str, dict[str, str]] = {
    "clippet": dict(
        brand="#d9804f", hi="#ffd2b0", ink="#ece2d3", muted="#93846f", faint="#54493d",
        ok="#8cb878", warn="#e0ad4c", bad="#e0705a", info="#7eaedc", plum="#b49ad6",
        bg="#1b1714", panel="#24201b", raised="#302922",
        run="#e08a4c", done="#6fa3d8", failed="#d66c95", queued="#a3927b",
    ),
    "clippet-paper": dict(
        brand="#c2663f", hi="#5e2a12", ink="#2f261d", muted="#857560", faint="#cfc2ac",
        ok="#5f8a52", warn="#b37f22", bad="#b9543f", info="#3a76b0", plum="#7a5c9e",
        bg="#f4eee2", panel="#fcf8f0", raised="#efe6d6",
        run="#d0743a", done="#3a76b0", failed="#a8325e", queued="#a3927b",
    ),
}
DEFAULT_THEME = "clippet"

AGENTS = AGENT_KEYS
AGENT_COLOR = {key: spec.color for key, spec in AGENT_SPECS.items()}
AGENT_LETTER = {key: spec.letter for key, spec in AGENT_SPECS.items()}

MARK: list[list[tuple[str, str]]] = [
    [("               ⢀⠔⠢⡄     ", "ink")],
    [("        ⡖⠉⠉⠒⠒⠒⠤⡼⡤⣀⣇⣀    ", "ink")],
    [("       ⢸      ⢠⣳⡃⢸⣸ ⢱   ", "ink")],
    [("  ", "ink"), ("⢀⣴⣶⣶⣶⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣧", "brand"), ("⡇ ⡎   ", "ink")],
    [("  ", "ink"), ("⢸⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣻", "brand"), ("  ⡇   ", "ink")],
    [("  ⢸", "ink"), ("⠚⠛⠉⢹⠉⠉", "brand"), ("⠉⠉⠉  ⠑⠢⠒⢹ ⢸    ", "ink")],
    [("  ⢸⡀ ⠠⣸          ⢸⡄⡜    ", "ink")],
    [("  ⠈⡇  ⢏⡱⠆         ⡇⡇    ", "ink")],
    [("   ⡇  ⠊⠉⠒", "ink"), ("⠶⠶⠶⠶", "brand"), ("⠤⢄⣀⣀⣀⡿     ", "ink")],
    [("   ⡇              ⡇     ", "ink")],
    [("   ⠱⣄⣀⣀⣀⣠⡤⠤⠤⠤⠤⠤⠴⠖⠚⠁     ", "ink")],
]


def themes() -> list[Theme]:
    out = []
    for name, p in PALETTES.items():
        out.append(
            Theme(
                name=name,
                primary=p["brand"],
                secondary=p["info"],
                accent=p["brand"],
                foreground=p["ink"],
                background=p["bg"],
                surface=p["panel"],
                panel=p["raised"],
                success=p["ok"],
                warning=p["warn"],
                error=p["bad"],
                dark=name == "clippet",
                variables={
                    "muted": p["muted"],
                    "faint": p["faint"],
                    "footer-key-foreground": p["brand"],
                    "block-cursor-background": mix(p["brand"], p["bg"], 0.55),
                    "block-cursor-foreground": p["ink"],
                    "block-cursor-blurred-background": mix(p["brand"], p["bg"], 0.8),
                    "input-selection-background": mix(p["brand"], p["bg"], 0.6),
                },
            )
        )
    return out


def mark(p: dict[str, str], lift: int = 0) -> Text:
    """The hand-drawn mark, in the palette's ink and brand."""

    out = Text("\n" * lift)
    for row in MARK:
        for text, key in row:
            out.append(text, style=p[key])
        out.append("\n")
    return out


def squiggle(width: int, p: dict[str, str]) -> Text:
    """A pencil underline for a heading."""

    return Text("~" * max(3, width), style=p["faint"])


def heading(title: str, p: dict[str, str], note: str = "") -> Text:
    """A title with a pencil squiggle under it (no trailing newline)."""

    out = Text(title, style=f"bold {p['ink']}")
    if note:
        out.append(f"  {note}", style=p["muted"])
    out.append("\n")
    out.append_text(squiggle(cell_len(title), p))
    return out


# --- states -------------------------------------------------------------------


def login_state(credential: "CodexCredential | None") -> str:
    """ok · apikey · stale (access token expired, refreshes on next use) · reauth · none"""

    if credential is None:
        return "none"
    if credential.requires_reauth:
        return "reauth"
    if credential.auth_mode == "apikey":
        return "apikey"
    if credential.is_expired():
        return "stale"
    return "ok"


LOGIN_WORDS = {
    "ok": "logged in",
    "apikey": "API key",
    "stale": "access token expired — refreshed on next use",
    "reauth": "needs a new login",
    "none": "not logged in",
}


def login_mark(state: str, p: dict[str, str]) -> Text:
    sign, color = {
        "ok": ("✓", "ok"),
        "apikey": ("✓", "info"),
        "stale": ("◷", "warn"),
        "reauth": ("✕", "bad"),
        "none": ("·", "faint"),
    }.get(state, ("·", "faint"))
    return Text(sign, style=p[color])


RUN_WORDS = {"queued": "waiting", "running": "running", "done": "done", "failed": "failed", "cancelled": "cancelled"}


def run_mark(state: str, t: float, p: dict[str, str]) -> Text:
    if state == "running":
        return Text(star(t), style=f"bold {p['run']}")
    if state == "queued":
        return Text("◷", style=mix(p["queued"], p["hi"], breathe(t) * 0.4))
    sign, color = {"done": ("✓", "done"), "failed": ("✕", "failed"), "cancelled": ("⊘", "queued")}.get(state, ("·", "muted"))
    return Text(sign, style=p[color])


# --- words --------------------------------------------------------------------


def clip(text: str | None, width: int) -> str:
    """Cut to *width* terminal cells (wide characters count two), with … when cut."""

    text = " ".join((text or "").split())
    if cell_len(text) <= width:
        return text
    out, used = "", 0
    for ch in text:
        w = cell_len(ch)
        if used + w > width - 1:
            break
        out += ch
        used += w
    return out + "…"


def pad(text: str | None, width: int) -> str:
    text = clip(text, width)
    return text + " " * (width - cell_len(text))


def relative(moment: datetime | None, now: datetime | None = None) -> str:
    """in 3d · in 5h · 12m ago · 2d ago"""

    if moment is None:
        return "—"
    now = now or datetime.now(timezone.utc)
    seconds = (moment - now).total_seconds()
    future = seconds >= 0
    s = abs(seconds)
    if s < 60:
        return "now"
    if s < 3600:
        amount = f"{int(s // 60)}m"
    elif s < 86400:
        amount = f"{int(s // 3600)}h"
    else:
        amount = f"{int(s // 86400)}d"
    return f"in {amount}" if future else f"{amount} ago"


def moment(value: datetime | None) -> str:
    if value is None:
        return "—"
    return f"{value.astimezone():%Y-%m-%d %H:%M} ({relative(value)})"


def span(value: datetime, now: datetime | None = None) -> str:
    """in 2d 3h · in 4h 10m · in 25m · 5h ago: like relative(), to two units."""

    now = now or datetime.now(timezone.utc)
    seconds = (value - now).total_seconds()
    whole = int(abs(seconds))
    if whole < 60:
        return "now"
    days, rest = divmod(whole, 86400)
    hours, minutes = rest // 3600, rest % 3600 // 60
    if days:
        text = f"{days}d {hours}h" if hours else f"{days}d"
    elif hours:
        text = f"{hours}h {minutes}m" if minutes else f"{hours}h"
    else:
        text = f"{minutes}m"
    return f"in {text}" if seconds >= 0 else f"{text} ago"


def quota_style(left: float, p: dict[str, str]) -> str:
    return p["bad"] if left <= 0.05 else p["warn"] if left <= 0.2 else p["ok"]


def quota_cell(reading: QuotaReading | None, p: dict[str, str], now: datetime | None = None) -> Text:
    """The share left in the tightest window, for a table."""

    if reading is None:
        return Text("—", style=p["faint"])
    left = reading.left(now)
    return Text(f"{left:.0%}", style=quota_style(left, p))


def quota_lines(reading: QuotaReading, p: dict[str, str], now: datetime | None = None) -> list[Text]:
    """A bar of what is left in each window and when it starts over, then where the reading came from."""

    from clippet.auth.quota import SOURCE_WORDS

    now = now or datetime.now(timezone.utc)
    lines = []
    for window in reading.windows:
        left = window.left(now)
        line = Text(f"{window.name:<5}", style=p["muted"])
        line.append_text(bar(left, 10, quota_style(left, p), p["raised"]))
        line.append(f" {left:>4.0%} left", style=p["ink"] if left > 0.05 else p["bad"])
        if window.resets_at is not None:
            verb = "reset" if window.has_reset(now) else "resets"
            line.append(f" · {verb} {span(window.resets_at, now)}", style=p["faint"])
        lines.append(line)
    source = SOURCE_WORDS.get(reading.source, reading.source)
    when = f"as of {relative(reading.taken_at, now)} · " if reading.taken_at else ""
    lines.append(Text(f"{when}{source}", style=p["faint"]))
    return lines


def short_path(path: object) -> str:
    """A path with the home directory written as ~."""

    from pathlib import Path

    text = str(path)
    home = Path.home()
    for root in dict.fromkeys((str(home), str(home.resolve()))):
        if text == root:
            return "~"
        if text.startswith(root + "/"):
            return "~" + text[len(root):]
    return text
