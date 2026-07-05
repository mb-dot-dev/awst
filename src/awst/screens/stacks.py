"""CloudFormation stack list screen."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from rich.text import Text
from textual import work
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static
from textual.worker import WorkerState

from awst.aws.models import AwsError
from awst.screens.formatting import relative_age, status_style
from awst.screens.stack_detail import StackDetailScreen, StackInspector

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.worker import Worker

    from awst.aws.models import StackSummary

COLUMNS = ("Name", "Status", "Created", "Updated")


class StackLister(Protocol):
    """The slice of the CloudFormation gateway this screen needs."""

    def list_stacks(self: Self) -> list[StackSummary]: ...


class StackGateway(StackLister, StackInspector, Protocol):
    """Everything the stack screens collectively need from CloudFormation."""


class StackListScreen(Screen[None]):
    """Read-only list of the account's CloudFormation stacks."""

    TITLE = "CloudFormation stacks"

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "back_or_clear", "Back"),
        ("r", "refresh", "Refresh"),
        ("slash", "focus_filter", "Filter"),
    ]

    DEFAULT_CSS = """
    #count { height: 1; padding: 0 1; color: $text-muted; }
    #error { display: none; padding: 1 2; color: $text-error; }
    """

    def __init__(self: Self, gateway: StackGateway) -> None:
        super().__init__()
        self._gateway = gateway
        self._all_stacks: list[StackSummary] = []
        self._loaded = False

    def compose(self: Self) -> ComposeResult:
        yield Static(id="count")
        yield Input(placeholder="filter stacks by name", id="filter")
        yield DataTable(id="stacks")
        yield Static(id="error")
        yield Footer()

    def on_mount(self: Self) -> None:
        table = self.query_one("#stacks", DataTable)
        table.cursor_type = "row"
        table.add_columns(*COLUMNS)
        table.loading = True
        table.focus()
        self._fetch_stacks()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_stacks(self: Self) -> list[StackSummary]:
        return self._gateway.list_stacks()

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.worker.name != "_fetch_stacks":
            return
        if event.state == WorkerState.SUCCESS:
            was_loaded = self._loaded
            self._loaded = True
            self._all_stacks = event.worker.result or []
            table = self.query_one("#stacks", DataTable)
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
        table = self.query_one("#stacks", DataTable)
        table.loading = False
        table.display = False
        self.query_one("#filter", Input).display = False
        self.query_one("#count", Static).display = False
        self.set_focus(None)
        panel = self.query_one("#error", Static)
        panel.update(error.message if error.hint is None else f"{error.message}\n{error.hint}")
        panel.display = True

    def _render_rows(self: Self) -> None:
        table = self.query_one("#stacks", DataTable)
        query = self.query_one("#filter", Input).value.strip().lower()
        visible = [stack for stack in self._all_stacks if query in stack.name.lower()]
        previous = self._cursor_stack_name(table)
        table.clear()
        now = datetime.now(tz=UTC)
        for stack in visible:
            table.add_row(
                stack.name,
                Text(stack.status, style=status_style(stack.status)),
                relative_age(stack.created, now),
                relative_age(stack.updated, now),
                key=stack.name,
            )
        names = [stack.name for stack in visible]
        if previous in names:
            table.move_cursor(row=names.index(previous))
        total = len(self._all_stacks)
        noun = "stack" if total == 1 else "stacks"
        count = f"{len(visible)} of {total} {noun}" if query else f"{total} {noun}"
        self.query_one("#count", Static).update(count)

    def _cursor_stack_name(self: Self, table: DataTable) -> str | None:
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return row_key.value

    def on_input_changed(self: Self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._render_rows()

    def on_data_table_row_selected(self: Self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        if name is not None:
            self.app.push_screen(StackDetailScreen(self._gateway, name))

    def on_screen_resume(self: Self) -> None:
        if self._loaded:  # skip the initial push; on_mount already fetches
            self.action_refresh()

    def action_focus_filter(self: Self) -> None:
        self.query_one("#filter", Input).focus()

    def action_refresh(self: Self) -> None:
        self.query_one("#error", Static).display = False
        table = self.query_one("#stacks", DataTable)
        table.display = True
        self.query_one("#filter", Input).display = True
        self.query_one("#count", Static).display = True
        if self._loaded:
            self.query_one("#count", Static).update("refreshing…")
        else:
            table.loading = True
        self._fetch_stacks()

    def action_back_or_clear(self: Self) -> None:
        filter_input = self.query_one("#filter", Input)
        if filter_input.has_focus or filter_input.value:
            filter_input.value = ""
            self.query_one("#stacks", DataTable).focus()
        else:
            self.app.pop_screen()
