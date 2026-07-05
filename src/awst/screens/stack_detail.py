"""CloudFormation stack detail screen."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from rich.text import Text
from textual import work
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static, TabbedContent, TabPane
from textual.worker import WorkerState

from awst.aws.models import AwsError, StackNotFoundError
from awst.screens.confirm import ConfirmScreen
from awst.screens.formatting import relative_age, status_style

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.worker import Worker

    from awst.aws.models import StackDetail

RESOURCE_COLUMNS = ("Logical ID", "Physical ID", "Type", "Status")
EVENT_COLUMNS = ("Time", "Logical ID", "Type", "Status", "Reason")


class StackInspector(Protocol):
    """The slice of the CloudFormation gateway this screen needs."""

    def get_stack_detail(self: Self, name: str) -> StackDetail: ...

    def delete_stack(self: Self, name: str) -> None: ...


class StackDetailScreen(Screen[None]):
    """Detail view of one CloudFormation stack, able to delete it."""

    TITLE = "Stack details"

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "back", "Back"),
        ("r", "refresh", "Refresh"),
        ("d", "delete", "Delete stack"),
    ]

    DEFAULT_CSS = """
    #overview-info { height: auto; padding: 1 2 0 2; }
    .heading { height: 1; padding: 0 2; margin-top: 1; text-style: bold; }
    .none-label { display: none; height: 1; padding: 0 2; color: $text-muted; }
    #parameters, #outputs { height: auto; }
    #error { display: none; padding: 1 2; color: $text-error; }
    """

    def __init__(self: Self, gateway: StackInspector, stack_name: str) -> None:
        super().__init__()
        self._gateway = gateway
        self._stack_name = stack_name
        self._loaded = False

    def compose(self: Self) -> ComposeResult:
        with TabbedContent(id="tabs"):
            with TabPane("Overview", id="overview-tab"), VerticalScroll():
                yield Static(id="overview-info")
                yield Static("Parameters", classes="heading")
                yield Static("none", id="parameters-none", classes="none-label")
                yield DataTable(id="parameters")
                yield Static("Outputs", classes="heading")
                yield Static("none", id="outputs-none", classes="none-label")
                yield DataTable(id="outputs")
            with TabPane("Resources", id="resources-tab"):
                yield DataTable(id="resources")
            with TabPane("Events", id="events-tab"):
                yield DataTable(id="events")
        yield Static(id="error")
        yield Footer()

    def on_mount(self: Self) -> None:
        self.sub_title = self._stack_name
        self.query_one("#parameters", DataTable).add_columns("Key", "Value")
        self.query_one("#outputs", DataTable).add_columns("Key", "Value", "Description")
        self.query_one("#resources", DataTable).add_columns(*RESOURCE_COLUMNS)
        self.query_one("#events", DataTable).add_columns(*EVENT_COLUMNS)
        for table in self.query(DataTable):
            table.cursor_type = "row"
        self.query_one("#tabs", TabbedContent).loading = True
        self._fetch_detail()

    @work(thread=True, exclusive=True, group="detail", exit_on_error=False)
    def _fetch_detail(self: Self) -> StackDetail:
        return self._gateway.get_stack_detail(self._stack_name)

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.worker.name == "_fetch_detail":
            self._handle_fetch(event)
        elif event.worker.name == "_request_delete":
            self._handle_delete(event)

    def _handle_fetch(self: Self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.SUCCESS:
            self._loaded = True
            self.query_one("#tabs", TabbedContent).loading = False
            detail = event.worker.result
            if detail is not None:
                self._render_detail(detail)
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, StackNotFoundError) and self._loaded:
                self.notify(f"Stack {self._stack_name} no longer exists.", title="Stack deleted")
                if self.app.screen is self:
                    self.app.pop_screen()
            elif isinstance(error, AwsError):
                self._show_error(error)
            elif error is not None:
                raise error

    def _show_error(self: Self, error: AwsError) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.loading = False
        if self._loaded:
            message = error.message if error.hint is None else f"{error.message} ({error.hint})"
            self.notify(message, title="Refresh failed", severity="error")
            return
        tabs.display = False
        self.set_focus(None)
        panel = self.query_one("#error", Static)
        panel.update(error.message if error.hint is None else f"{error.message}\n{error.hint}")
        panel.display = True

    def _render_detail(self: Self, detail: StackDetail) -> None:
        now = datetime.now(tz=UTC)
        self.query_one("#overview-info", Static).update(_overview_text(detail, now))
        parameters = self.query_one("#parameters", DataTable)
        parameters.clear()
        for parameter in detail.parameters:
            parameters.add_row(parameter.key, parameter.value)
        self._toggle_none(parameters, "#parameters-none", empty=not detail.parameters)
        outputs = self.query_one("#outputs", DataTable)
        outputs.clear()
        for output in detail.outputs:
            outputs.add_row(output.key, output.value, output.description or "")
        self._toggle_none(outputs, "#outputs-none", empty=not detail.outputs)
        self._render_resources(detail)
        self._render_events(detail, now)

    def _render_resources(self: Self, detail: StackDetail) -> None:
        table = self.query_one("#resources", DataTable)
        table.clear()
        for resource in detail.resources:
            table.add_row(
                resource.logical_id,
                resource.physical_id or "",
                resource.resource_type,
                Text(resource.status, style=status_style(resource.status)),
            )

    def _render_events(self: Self, detail: StackDetail, now: datetime) -> None:
        table = self.query_one("#events", DataTable)
        table.clear()
        for stack_event in detail.events:
            table.add_row(
                relative_age(stack_event.timestamp, now),
                stack_event.logical_id,
                stack_event.resource_type,
                Text(stack_event.status, style=status_style(stack_event.status)),
                stack_event.reason or "",
            )

    def _toggle_none(self: Self, table: DataTable, none_selector: str, *, empty: bool) -> None:
        table.display = not empty
        self.query_one(none_selector, Static).display = empty

    def action_back(self: Self) -> None:
        self.app.pop_screen()

    def action_refresh(self: Self) -> None:
        self.query_one("#error", Static).display = False
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.display = True
        if not self._loaded:
            tabs.loading = True
        self._fetch_detail()

    def action_delete(self: Self) -> None:
        question = f"Delete stack {self._stack_name}? This cannot be undone."
        self.app.push_screen(ConfirmScreen(question), self._on_delete_confirmed)

    def _on_delete_confirmed(self: Self, confirmed: bool | None) -> None:  # noqa: FBT001
        if confirmed:
            self._request_delete()

    @work(thread=True, exclusive=True, group="delete", exit_on_error=False)
    def _request_delete(self: Self) -> None:
        self._gateway.delete_stack(self._stack_name)

    def _handle_delete(self: Self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.SUCCESS:
            self.notify("Delete requested — press r to check progress.", title=self._stack_name)
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, AwsError):
                message = error.message if error.hint is None else f"{error.message} ({error.hint})"
                self.notify(message, title="Delete failed", severity="error")
            elif error is not None:
                raise error


def _overview_text(detail: StackDetail, now: datetime) -> Text:
    text = Text()
    text.append("Status       ")
    text.append(detail.status, style=status_style(detail.status))
    if detail.status_reason:
        text.append(f"\nReason       {detail.status_reason}")
    if detail.description:
        text.append(f"\nDescription  {detail.description}")
    text.append(f"\nCreated      {relative_age(detail.created, now)}")
    text.append(f"\nUpdated      {relative_age(detail.updated, now)}")
    text.append(f"\nStack ID     {detail.stack_id}")
    return text
