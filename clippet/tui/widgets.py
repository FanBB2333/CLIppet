"""Pieces the places share: the base of a place, a list that updates in
place instead of being rebuilt and can be sorted, filtered and have its
columns resized, a scrolling page, the line between two panes that can be
dragged, and the dialogs (a line of text, a small form, a yes/no, a list to
pick from, the keys)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rich.cells import cell_len
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import DataTable, Input, Label, OptionList, Select, Static, Switch
from textual.widgets.data_table import ColumnKey
from textual.widgets.option_list import Option

from clippet.tui.look import pad

if TYPE_CHECKING:
    from clippet.tui.app import ClippetApp


class Place(Vertical):
    """One of the places in the index on the left."""

    BINDINGS = [Binding("slash", "filter", "filter", show=False)]

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

    def action_filter(self) -> None:
        """/ starts typing in the list's filter, if the place has one."""

        for line in self.query(Filter):
            line.open()
            return

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


def cursor_to(table: DataTable, key: str | None) -> bool:
    """Put the cursor on the row with *key*, wherever sorting put it."""

    order = [k.value for k in table.rows]
    if key is None or key not in order:
        return False
    table.move_cursor(row=order.index(key), animate=False)
    return True


_MISSING = {"", "—", "-", "–", "·", "…"}
_NUMBER = re.compile(r"^([-+]?\d+(?:\.\d+)?)\s*([kK%]?)$")
_DURATION = re.compile(r"^(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?$")


def sort_value(cell: Any) -> tuple[int, float, str] | None:
    """How a cell sorts: numbers (49%, 12.4k, 3m 04s) by value before words; None when empty."""

    text = (cell.plain if isinstance(cell, Text) else str(cell)).strip()
    if text in _MISSING:
        return None
    match = _NUMBER.match(text)
    if match:
        return (0, float(match.group(1)) * (1000 if match.group(2) in ("k", "K") else 1), "")
    match = _DURATION.match(text)
    if match and any(match.groups()):
        days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
        return (0, float(((days * 24 + hours) * 60 + minutes) * 60 + seconds), "")
    return (1, 0.0, text.casefold())


def _same(a: Any, b: Any) -> bool:
    """Cell equality that also sees a Text's base style (Text.__eq__ does not)."""

    if isinstance(a, Text) and isinstance(b, Text):
        return a.plain == b.plain and str(a.style) == str(b.style) and a.spans == b.spans
    return a == b


def sync_rows(table: DataTable, rows: list[tuple[str, tuple]]) -> None:
    """Show these rows.  The same rows in the same order are updated cell by
    cell, so the cursor and the scroll stay put; otherwise the table is
    refilled with the cursor kept on its row."""

    if isinstance(table, ListTable):
        table.source_rows = rows
        rows = table.arrange(rows)
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


class ListTable(DataTable):
    """A list of rows that the reader arranges: a click on a header sorts by
    that column (again for the reverse, a third time for the place's own
    order), a :class:`Filter` above it keeps the rows that match, and the gap
    between two headers can be dragged to make a column narrower or wider (a
    double click puts it back).

    *list_name* names the list in the TUI's prefs, and *saved* is what was
    remembered for it (see :meth:`state`).
    """

    class Arranged(Message):
        """The sort or a column's width changed: something worth remembering."""

        def __init__(self, table: "ListTable") -> None:
            super().__init__()
            self.table = table

        @property
        def control(self) -> "ListTable":
            return self.table

    def __init__(self, list_name: str, *, saved: dict | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.list_name = list_name
        self.saved = saved if isinstance(saved, dict) else {}
        self.source_rows: list[tuple[str, tuple]] = []
        self.filter_text = ""
        self.sort_key: str | None = None
        self.sort_reverse = False
        self._labels: dict[str, str] = {}
        self._defaults: dict[str, int] = {}  # the width each column was added with
        self._widths: dict[str, int] = {}  # the width the reader gave it
        self._grab: tuple[str, int, int] | None = None
        self._dragged = False

    # --- columns ---------------------------------------------------------------------

    def add_column(self, label: Any, *, width: int | None = None, key: str | None = None,
                   default: Any = None) -> ColumnKey:
        column_key = super().add_column(label, width=width, key=key, default=default)
        name = column_key.value
        self._labels[name] = label.plain if isinstance(label, Text) else str(label)
        self._defaults[name] = width or cell_len(self._labels[name])
        widths = self.saved.get("widths")
        if isinstance(widths, dict) and isinstance(widths.get(name), int) and widths[name] > 0:
            self._widths[name] = widths[name]
        sort = self.saved.get("sort")
        if isinstance(sort, list) and len(sort) == 2 and sort[0] == name:
            self.sort_key, self.sort_reverse = name, bool(sort[1])
        self._show_column(name)
        return column_key

    def _show_column(self, name: str) -> None:
        column = self.columns.get(ColumnKey(name))
        if column is None:
            return
        label = self._labels[name]
        width = self._widths.get(name, self._defaults[name])
        if name == self.sort_key:
            label += "↓" if self.sort_reverse else "↑"
            width = max(width, cell_len(label))  # room for the arrow
        column.label = Text(label)
        column.width = width
        column.auto_width = False

    def _relayout(self) -> None:
        self._require_update_dimensions = True
        self._clear_caches()
        self.refresh()

    def set_width(self, name: str, width: int) -> None:
        """Give column *name* this many cells (between its padding)."""

        self._widths[name] = max(1, width)
        self._show_column(name)
        self._relayout()

    def reset_width(self, name: str) -> None:
        self._widths.pop(name, None)
        self._show_column(name)
        self._relayout()

    def state(self) -> dict:
        """What to remember: the sort and the widths the reader changed."""

        return {"sort": [self.sort_key, self.sort_reverse] if self.sort_key else None,
                "widths": dict(self._widths)}

    # --- rows --------------------------------------------------------------------------

    def arrange(self, rows: list[tuple[str, tuple]]) -> list[tuple[str, tuple]]:
        """The rows that match the filter, in the chosen order."""

        words = self.filter_text.casefold().split()
        if words:
            def text(cells: tuple) -> str:
                return " ".join(c.plain if isinstance(c, Text) else str(c) for c in cells).casefold()

            rows = [row for row in rows if all(word in text(row[1]) for word in words)]
        names = [column.key.value for column in self.ordered_columns]
        if self.sort_key in names:
            at = names.index(self.sort_key)
            valued = [(sort_value(row[1][at]) if at < len(row[1]) else None, row) for row in rows]
            present = [pair for pair in valued if pair[0] is not None]
            present.sort(key=lambda pair: pair[0], reverse=self.sort_reverse)
            rows = [row for _, row in present] + [row for value, row in valued if value is None]
        return rows

    def rearrange(self) -> None:
        sync_rows(self, self.source_rows)

    def filter_by(self, text: str) -> None:
        self.filter_text = text
        self.rearrange()

    def sort_by(self, name: str | None, reverse: bool = False) -> None:
        old, self.sort_key, self.sort_reverse = self.sort_key, name, reverse
        for column in {old, name} - {None}:
            self._show_column(column)
        self._relayout()
        self.rearrange()
        self.post_message(self.Arranged(self))

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        event.stop()
        name = event.column_key.value
        if self.sort_key != name:
            self.sort_by(name)
        elif not self.sort_reverse:
            self.sort_by(name, reverse=True)
        else:
            self.sort_by(None)

    # --- dragging a gap between headers ---------------------------------------------

    def _gap_at(self, event: events.MouseEvent) -> str | None:
        """The column whose right edge is under the mouse, on the header row."""

        if not self.show_header:
            return None
        offset = event.get_content_offset(self)
        if offset is None or offset.y >= self.header_height:
            return None
        x = offset.x + int(self.scroll_x)
        edge = self._row_label_column_width
        for column in self.ordered_columns:
            edge += column.get_render_width(self)
            if edge - 1 <= x <= edge:
                return column.key.value
            if x < edge - 1:
                return None
        return None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._dragged = False
        name = self._gap_at(event) if event.button == 1 else None
        if name is None:
            return
        self._grab = (name, event.screen_x, self._widths.get(name, self._defaults[name]))
        self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._grab is None:
            return
        name, start, width = self._grab
        self.set_width(name, width + event.screen_x - start)
        self._dragged = True
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._grab is None:
            return
        self._grab = None
        self.release_mouse()
        if self._dragged:
            self.post_message(self.Arranged(self))
        event.stop()

    def on_click(self, event: events.Click) -> None:
        if self._dragged:  # the click that ends a drag sorts nothing
            self._dragged = False
            event.prevent_default()
            event.stop()
            return
        name = self._gap_at(event)
        if name is not None:  # a gap, not a header: no sorting
            event.prevent_default()
            event.stop()
            if event.chain >= 2:
                self.reset_width(name)
                self.post_message(self.Arranged(self))


class Filter(Input):
    """The line above a list that keeps only the rows matching what is typed
    (every word, anywhere in the row).  / or a click starts typing; Enter or
    ↓ goes back to the list; Esc empties it."""

    can_focus = False  # only while typing, so that the list keeps the keys

    BINDINGS = [
        Binding("escape", "empty", "clear", show=False),
        Binding("down", "back", "list", show=False),
    ]

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(placeholder="/ filter · click a header to sort · drag between two to resize",
                         id=id, classes="filter")

    @property
    def table(self) -> ListTable | None:
        parent = self.parent
        found = parent.query(ListTable) if parent is not None else []
        return found.first() if found else None

    def open(self) -> None:
        self.can_focus = True
        self.focus()

    def on_click(self, event: events.Click) -> None:
        if not self.has_focus:
            self.open()

    def on_blur(self, event: events.Blur) -> None:
        self.can_focus = False

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        if self.table is not None:
            self.table.filter_by(self.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_back()

    def action_back(self) -> None:
        if self.table is not None:
            self.table.focus()

    def action_empty(self) -> None:
        self.value = ""
        self.action_back()


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
