"""Pieces the places share: the base of a place, a table that updates in
place instead of being rebuilt, a scrolling page, the line between two panes
that can be dragged, and the dialogs (a line of text, a small form, a yes/no,
a list to pick from, the keys)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import DataTable, Input, Label, OptionList, Select, Static, Switch
from textual.widgets.option_list import Option

from clippet.tui.look import pad

if TYPE_CHECKING:
    from clippet.tui.app import ClippetApp


class Place(Vertical):
    """One of the places in the index on the left."""

    key = ""
    title = ""

    @property
    def clippet(self) -> "ClippetApp":
        return self.app  # type: ignore[return-value]

    @property
    def pal(self) -> dict[str, str]:
        return self.clippet.pal

    def hints(self) -> list[tuple[str, str]]:
        return []

    def help_rows(self) -> list[tuple[str, str]]:
        return self.hints()

    def refresh_view(self) -> None:
        """Redraw from the app's current state (called after it reloads)."""

    def tick(self, t: float) -> None:
        """A frame of the clock while this place is shown."""

    def focus_first(self) -> None:
        for widget in self.query("*"):
            if widget.focusable and widget.display:
                widget.focus()
                return


class Bar(Static):
    """A piece of the interface itself (chips, hints): clicked, never selected as text."""

    ALLOW_SELECT = False


class Divider(Widget):
    """The line between two panes in a row.  Dragging it left or right gives
    the pane before it fewer or more columns; a double click puts it back.

    *key* names the width in the TUI's prefs.  With *share* the width is kept
    as a share of the row, so that it follows the terminal's size; otherwise
    it is a number of columns.  The pane keeps at least *least* columns and
    leaves at least *keep* for what comes after the line.
    """

    ALLOW_SELECT = False

    class Moved(Message):
        """A drag ended (*width* is what to remember) or a double click reset the pane (*width* is None)."""

        def __init__(self, divider: "Divider", width: float | None) -> None:
            super().__init__()
            self.divider = divider
            self.width = width

        @property
        def control(self) -> "Divider":
            return self.divider

    def __init__(self, key: str, *, share: bool = True, least: int = 24, keep: int = 30,
                 id: str | None = None) -> None:
        super().__init__(id=id, classes="divider")
        self.key = key
        self.share = share
        self.least = least
        self.keep = keep
        self._grab: tuple[int, int] | None = None
        self._width: float | None = None

    @property
    def pane(self) -> Widget | None:
        """The widget before this line in its row."""

        if self.parent is None:
            return None
        row = list(self.parent.children)
        at = row.index(self)
        return row[at - 1] if at > 0 else None

    def render(self) -> str:
        return ""  # the line is the widget's border

    def on_mouse_down(self, event: events.MouseDown) -> None:
        pane = self.pane
        if pane is None or event.button != 1:
            return
        self._grab, self._width = (event.screen_x, pane.outer_size.width), None
        self.capture_mouse()
        self.add_class("-dragging")
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._grab is None:
            return
        start, width = self._grab
        self.move_to(width + event.screen_x - start)
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._grab is None:
            return
        self._grab = None
        self.release_mouse()
        self.remove_class("-dragging")
        if self._width is not None:
            self.post_message(self.Moved(self, self._width))
        event.stop()

    def on_click(self, event: events.Click) -> None:
        event.stop()
        pane = self.pane
        if event.chain >= 2 and pane is not None:
            pane.styles.width = None  # back to the stylesheet's width
            self.post_message(self.Moved(self, None))

    def move_to(self, columns: int) -> None:
        """Give the pane before the line *columns* columns, within the limits."""

        pane, row = self.pane, self.parent
        total = row.size.width if isinstance(row, Widget) else 0
        if pane is None or total <= 0:
            return
        columns = max(self.least, min(columns, total - self.outer_size.width - self.keep))
        if self.share:
            self._width = round(100 * columns / total, 2)
            pane.styles.width = f"{self._width}%"
        else:
            self._width = columns
            pane.styles.width = columns

    def restore(self, width: float | None) -> None:
        """Apply a remembered width: a share of the row in percent, or columns."""

        pane = self.pane
        if pane is None or width is None or width <= 0:
            return
        pane.styles.width = f"{width}%" if self.share else int(width)


class Pane(VerticalScroll):
    """A scrolling page of rich text."""

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self.body = Static(classes="pane-body")

    def compose(self) -> ComposeResult:
        yield self.body

    def show(self, renderable: Any, layout: bool = True) -> None:
        self.body.update(renderable, layout=layout)


def row_key(table: DataTable) -> str | None:
    if not table.row_count:
        return None
    try:
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
    except Exception:  # noqa: BLE001
        return None


def _same(a: Any, b: Any) -> bool:
    """Cell equality that also sees a Text's base style (Text.__eq__ does not)."""

    if isinstance(a, Text) and isinstance(b, Text):
        return a.plain == b.plain and str(a.style) == str(b.style) and a.spans == b.spans
    return a == b


def sync_rows(table: DataTable, rows: list[tuple[str, tuple]]) -> None:
    """Show these rows.  The same rows in the same order are updated cell by
    cell, so the cursor and the scroll stay put; otherwise the table is
    refilled with the cursor kept on its row."""

    keys = [k for k, _ in rows]
    columns = list(table.columns.keys())
    if [k.value for k in table.rows] == keys:
        for key, cells in rows:
            for column, value in zip(columns, cells):
                if not _same(table.get_cell(key, column), value):
                    table.update_cell(key, column, value)
        return
    keep = row_key(table)
    table.clear()
    for key, cells in rows:
        table.add_row(*cells, key=key)
    if keep in keys:
        table.move_cursor(row=keys.index(keep), animate=False)


def keys_line(keys: list[tuple[str, str]], p: dict[str, str]) -> Text:
    out = Text()
    for i, (key, what) in enumerate(keys):
        if i:
            out.append(" · ", style=p["faint"])
        out.append(key, style=f"bold {p['brand']}")
        out.append(f" {what}", style=p["muted"])
    return out


# --- dialogs ------------------------------------------------------------------


class Prompt(ModalScreen[str | None]):
    """A line of text, or None when Esc is pressed."""

    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, title: str, hint: str = "", value: str = "", placeholder: str = "") -> None:
        super().__init__(classes="modal")
        self.title_, self.hint, self.value, self.placeholder = title, hint, value, placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_, classes="dialog-title")
            if self.hint:
                yield Static(self.hint, classes="dialog-hint")
            yield Input(self.value, placeholder=self.placeholder)
            yield Static(Text.assemble(("Enter", "bold"), " ok · ", ("Esc", "bold"), " cancel"), classes="dialog-keys")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


@dataclass
class Field:
    """One field of a :class:`Form`: text, a choice, or a yes/no."""

    name: str
    label: str
    default: Any = ""
    options: list[tuple[str, Any]] | None = None
    flag: bool = False
    placeholder: str = ""
    hint: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class Form(ModalScreen[dict | None]):
    """A few fields at once.  Enter in the last text field, or Ctrl+S, sends it."""

    BINDINGS = [Binding("escape", "dismiss(None)", "cancel"), Binding("ctrl+s", "send", "ok")]

    def __init__(self, title: str, fields: list[Field], hint: str = "") -> None:
        super().__init__(classes="modal")
        self.title_, self.fields, self.hint = title, fields, hint

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_, classes="dialog-title")
            if self.hint:
                yield Static(self.hint, classes="dialog-hint")
            for f in self.fields:
                if f.flag:
                    with Horizontal(classes="flag-row"):
                        yield Switch(value=bool(f.default), id=f"f-{f.name}")
                        yield Label(f.label, classes="flag-label")
                else:
                    yield Label(f.label, classes="field-label")
                    if f.options is not None:
                        values = [value for _, value in f.options]
                        default = f.default if f.default in values else Select.NULL
                        yield Select(f.options, value=default, allow_blank=default is Select.NULL, prompt="choose…",
                                     id=f"f-{f.name}")
                    else:
                        yield Input(str(f.default or ""), placeholder=f.placeholder, id=f"f-{f.name}")
                if f.hint:
                    yield Static(f.hint, classes="field-hint")
            yield Static(Text.assemble(("Enter", "bold"), " next / ok · ", ("Ctrl+S", "bold"), " ok · ",
                                       ("Esc", "bold"), " cancel"), classes="dialog-keys")

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in self.fields:
            widget = self.query_one(f"#f-{f.name}")
            value = widget.value
            if value is Select.NULL:
                value = None
            out[f.name] = value.strip() if isinstance(value, str) else value
        return out

    def on_input_submitted(self, event: Input.Submitted) -> None:
        inputs = list(self.query(Input))
        if inputs and event.input is inputs[-1]:
            self.action_send()
        else:
            self.focus_next()

    def action_send(self) -> None:
        self.dismiss(self.values())


class Confirm(ModalScreen[bool]):
    BINDINGS = [Binding("y,enter", "dismiss(True)", "yes"), Binding("n,escape", "dismiss(False)", "no")]

    def __init__(self, title: str, body: str = "") -> None:
        super().__init__(classes="modal")
        self.title_, self.body = title, body

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_, classes="dialog-title")
            if self.body:
                yield Static(self.body, classes="dialog-hint")
            yield Static(Text.assemble(("y", "bold"), " / ", ("Enter", "bold"), " yes · ", ("n", "bold"), " / ",
                                       ("Esc", "bold"), " no"), classes="dialog-keys")


class Pick(ModalScreen[Any]):
    """A list to choose one thing from; Esc chooses nothing."""

    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, title: str, options: list[tuple[Any, Text | str]], hint: str = "") -> None:
        super().__init__(classes="modal")
        self.title_, self.options, self.hint = title, options, hint

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog picker"):
            yield Label(self.title_, classes="dialog-title")
            if self.hint:
                yield Static(self.hint, classes="dialog-hint")
            yield OptionList(*[Option(label, id=str(i)) for i, (_, label) in enumerate(self.options)], id="pick-list")
            yield Static(Text.assemble(("↑↓", "bold"), " choose · ", ("Enter", "bold"), " ok · ", ("Esc", "bold"),
                                       " cancel"), classes="dialog-keys")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        index = int(event.option.id or 0)
        self.dismiss(self.options[index][0])


class Help(ModalScreen[None]):
    BINDINGS = [Binding("escape,question_mark,q", "dismiss(None)", "close")]

    def __init__(self, rows: list[tuple[str, list[tuple[str, str]]]], header: Text | None = None) -> None:
        super().__init__(classes="modal")
        self.rows = rows
        self.header = header

    def compose(self) -> ComposeResult:
        text = Text()
        if self.header is not None:
            text.append_text(self.header)
            text.append("\n")
        for i, (section, keys) in enumerate(self.rows):
            if i:
                text.append("\n")
            text.append(f"{section}\n", style="bold")
            for key, what in keys:
                text.append(f"  {pad(key, 16)}", style="bold")
                text.append(f"{what}\n")
        with Vertical(classes="dialog help"):
            yield Label("Keys", classes="dialog-title")
            with VerticalScroll():
                yield Static(text)
            yield Static(Text.assemble(("Esc", "bold"), " close"), classes="dialog-keys")
