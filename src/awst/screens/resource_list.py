"""Shared base for read-only, filterable AWS resource list screens."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Self

from textual import work
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static
from textual.worker import WorkerState

from awst.aws.models import AwsError

if TYPE_CHECKING:
    from rich.text import Text
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.worker import Worker


class ResourceListScreen[ItemT](Screen[None]):
    """A filterable, refreshable table of one kind of AWS resource.

    Subclasses set TITLE, COLUMNS, and NOUN, and implement _list, _row, and _name.
    Row selection is a subclass concern: the base does nothing on Enter.
    """

    COLUMNS: ClassVar[tuple[str, ...]]
    NOUN: ClassVar[str]

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "back_or_clear", "Back"),
        ("r", "refresh", "Refresh"),
        ("slash", "focus_filter", "Filter"),
    ]

    DEFAULT_CSS = """
    #count { height: 1; padding: 0 1; color: $text-muted; }
    #error { display: none; padding: 1 2; color: $text-error; }
    """

    def __init__(self: Self) -> None:
        super().__init__()
        self._all_items: list[ItemT] = []
        self._loaded = False

    def _list(self: Self) -> list[ItemT]:
        """Fetch every item from the gateway; called on a worker thread."""
        raise NotImplementedError

    def _row(self: Self, item: ItemT, now: datetime) -> tuple[str | Text, ...]:
        """The table cells for one item, in COLUMNS order."""
        raise NotImplementedError

    def _name(self: Self, item: ItemT) -> str:
        """The item's unique name, used as the row key and filter target."""
        raise NotImplementedError

    def compose(self: Self) -> ComposeResult:
        yield Static(id="count")
        yield Input(placeholder=f"filter {self.NOUN}s by name", id="filter")
        yield DataTable(id="items")
        yield Static(id="error")
        yield Footer()

    def on_mount(self: Self) -> None:
        table = self.query_one("#items", DataTable)
        table.cursor_type = "row"
        table.add_columns(*self.COLUMNS)
        table.loading = True
        table.focus()
        self._fetch_items()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_items(self: Self) -> list[ItemT]:
        return self._list()

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.worker.name != "_fetch_items":
            return
        if event.state == WorkerState.SUCCESS:
            was_loaded = self._loaded
            self._loaded = True
            self._all_items = event.worker.result or []
            table = self.query_one("#items", DataTable)
            table.loading = False
            self._render_rows()
            if not was_loaded:
                table.focus()
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, AwsError):
                self._show_error(error)
            elif error is not None:
                raise error

    def _show_error(self: Self, error: AwsError) -> None:
        if self._loaded:
            message = error.message if error.hint is None else f"{error.message} ({error.hint})"
            self.notify(message, title="Refresh failed", severity="error")
            self._render_rows()  # restores the count text over "refreshing…"
            return
        table = self.query_one("#items", DataTable)
        table.loading = False
        table.display = False
        self.query_one("#filter", Input).display = False
        self.query_one("#count", Static).display = False
        self.set_focus(None)
        panel = self.query_one("#error", Static)
        panel.update(error.message if error.hint is None else f"{error.message}\n{error.hint}")
        panel.display = True

    def _render_rows(self: Self) -> None:
        table = self.query_one("#items", DataTable)
        query = self.query_one("#filter", Input).value.strip().lower()
        # Use explicit method resolution to work around Textual's _name attribute shadowing
        _get_name: type = self.__class__
        visible = [item for item in self._all_items if query in _get_name._name(self, item).lower()]  # noqa: SLF001
        previous = self._cursor_name(table)
        table.clear()
        now = datetime.now(tz=UTC)
        for item in visible:
            table.add_row(*self._row(item, now), key=_get_name._name(self, item))  # noqa: SLF001
        names = [_get_name._name(self, item) for item in visible]  # noqa: SLF001
        if previous in names:
            table.move_cursor(row=names.index(previous))
        total = len(self._all_items)
        noun = self.NOUN if total == 1 else f"{self.NOUN}s"
        count = f"{len(visible)} of {total} {noun}" if query else f"{total} {noun}"
        self.query_one("#count", Static).update(count)

    def _cursor_name(self: Self, table: DataTable) -> str | None:
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return row_key.value

    def on_input_changed(self: Self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._render_rows()

    def action_focus_filter(self: Self) -> None:
        self.query_one("#filter", Input).focus()

    def action_refresh(self: Self) -> None:
        self.query_one("#error", Static).display = False
        table = self.query_one("#items", DataTable)
        table.display = True
        self.query_one("#filter", Input).display = True
        self.query_one("#count", Static).display = True
        if self._loaded:
            self.query_one("#count", Static).update("refreshing…")
        else:
            table.loading = True
        self._fetch_items()

    def action_back_or_clear(self: Self) -> None:
        filter_input = self.query_one("#filter", Input)
        if filter_input.has_focus or filter_input.value:
            filter_input.value = ""
            self.query_one("#items", DataTable).focus()
        else:
            self.app.pop_screen()
