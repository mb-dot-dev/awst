"""Shared base for read-only, filterable AWS resource list screens."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Self

from textual import work
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static
from textual.worker import Worker, WorkerState

from awst.aws.models import AwsError, CredentialsError

if TYPE_CHECKING:
    from rich.text import Text
    from textual.app import ComposeResult
    from textual.binding import BindingType


class ResourceListScreen[ItemT](Screen[None]):
    """A filterable, refreshable table of one kind of AWS resource.

    Subclasses set TITLE, COLUMNS, and NOUN, and implement _list, _row, and _item_name.
    Row selection is a subclass concern: the base does nothing on Enter.
    """

    COLUMNS: ClassVar[tuple[str, ...]]
    NOUN: ClassVar[str]

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "back_or_clear", "Back"),
        ("r", "refresh", "Refresh"),
        ("slash", "focus_filter", "Filter"),
        ("l", "login", "Login"),
        ("m", "load_more", "More"),
    ]

    DEFAULT_CSS = """
    #count { height: 1; padding: 0 1; color: $text-muted; }
    #error { display: none; padding: 1 2; color: $text-error; }
    """

    def __init__(self: Self) -> None:
        super().__init__()
        self._all_items: list[ItemT] = []
        self._loaded = False
        self._show_login = False
        self._loading_more = False

    def _list(self: Self) -> list[ItemT]:
        """Fetch every item from the gateway; called on a worker thread."""
        raise NotImplementedError

    def _row(self: Self, item: ItemT, now: datetime) -> tuple[str | Text, ...]:
        """The table cells for one item, in COLUMNS order."""
        raise NotImplementedError

    def _item_name(self: Self, item: ItemT) -> str:
        """The item's unique name, used as the row key and filter target."""
        raise NotImplementedError

    def _has_more(self: Self) -> bool:
        """Whether _list_more can fetch another page; paged subclasses override both."""
        return False

    def _list_more(self: Self) -> list[ItemT]:
        """Fetch the next page; called on a worker thread, only when _has_more() is true."""
        raise NotImplementedError

    def compose(self: Self) -> ComposeResult:
        yield Static(id="count")
        yield Input(placeholder=f"filter {self.NOUN}s by name", id="filter")
        yield DataTable(id="items")
        yield Static(id="error")
        yield Footer()

    def check_action(self: Self, action: str, parameters: tuple[object, ...]) -> bool | None:  # noqa: ARG002
        if action == "login":
            return self._show_login
        if action == "load_more":
            return self._has_more() and not self._loading_more
        return True

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

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_more(self: Self) -> list[ItemT]:
        return self._list_more()

    def action_load_more(self: Self) -> None:
        if not self._has_more() or self._loading_more:
            return
        self._loading_more = True
        self.refresh_bindings()
        # No table.loading spinner here: it would hide the rows already loaded on screen.
        self.query_one("#count", Static).update("loading more…")
        self._fetch_more()

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.worker.name not in {"_fetch_items", "_fetch_more"}:
            return
        is_more = event.worker.name == "_fetch_more"
        if event.state == WorkerState.SUCCESS:
            self._show_login = False
            was_loaded = self._loaded
            self._loaded = True
            result = event.worker.result or []
            if is_more:
                self._all_items = [*self._all_items, *result]
                self._loading_more = False
            else:
                self._all_items = result
            self.refresh_bindings()
            table = self.query_one("#items", DataTable)
            table.loading = False
            self._render_rows()
            if not was_loaded:
                table.focus()
        elif event.state == WorkerState.ERROR:
            if is_more:
                self._loading_more = False
                self.refresh_bindings()
            error = event.worker.error
            if isinstance(error, AwsError):
                self._show_error(error)
            elif error is not None:
                raise error
        elif event.state == WorkerState.CANCELLED and is_more:
            # The load-more thread may still be running (cancellation is cooperative); clear the
            # flag now so the user can retry without waiting for the zombie thread to finish.
            self._loading_more = False
            self.refresh_bindings()

    def _show_error(self: Self, error: AwsError) -> None:
        self._show_login = isinstance(error, CredentialsError) and bool(getattr(self.app, "sso_login_possible", False))
        self.refresh_bindings()
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
        panel.update(self._error_text(error))
        panel.display = True

    def _error_text(self: Self, error: AwsError) -> str:
        text = error.message if error.hint is None else f"{error.message}\n{error.hint}"
        if self._show_login:
            text += "\nPress l to log in via AWS SSO."
        return text

    def _render_rows(self: Self) -> None:
        table = self.query_one("#items", DataTable)
        query = self.query_one("#filter", Input).value.strip().lower()
        visible = [item for item in self._all_items if query in self._item_name(item).lower()]
        previous = self._cursor_name(table)
        table.clear()
        now = datetime.now(tz=UTC)
        for item in visible:
            table.add_row(*self._row(item, now), key=self._item_name(item))
        names = [self._item_name(item) for item in visible]
        if previous in names:
            table.move_cursor(row=names.index(previous))
        total = len(self._all_items)
        noun = self.NOUN if total == 1 else f"{self.NOUN}s"
        suffix = "+" if self._has_more() else ""
        count = f"{len(visible)} of {total}{suffix} {noun}" if query else f"{total}{suffix} {noun}"
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

    def action_login(self: Self) -> None:
        factory = getattr(self.app, "make_sso_login_screen", None)
        if factory is None:
            return
        self.app.push_screen(factory(), self._on_login_finished)

    def _on_login_finished(self: Self, logged_in: bool | None) -> None:  # noqa: FBT001
        if logged_in:
            self.action_refresh()
