"""SSM parameter detail screen."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from rich.text import Text
from textual import work
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static
from textual.worker import Worker, WorkerState

from awst.aws.models import AwsError
from awst.screens.formatting import mask_value, relative_age

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType

    from awst.aws.models import ParameterDetail, ParameterSummary


class ParameterInspector(Protocol):
    """The slice of the SSM gateway this screen needs."""

    def get_parameter(self: Self, name: str) -> ParameterDetail: ...


class ParameterDetailScreen(Screen[None]):
    """Detail view of one SSM parameter; SecureString values stay masked until revealed."""

    TITLE = "Parameter details"

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "back", "Back"),
        ("r", "refresh", "Refresh"),
        ("s", "toggle_reveal", "Reveal/hide"),
        ("c", "copy", "Copy value"),
    ]

    DEFAULT_CSS = """
    #overview-info { height: auto; padding: 1 2 0 2; }
    .heading { height: 1; padding: 0 2; margin-top: 1; text-style: bold; }
    #value { height: auto; padding: 0 2; }
    #error { display: none; padding: 1 2; color: $text-error; }
    """

    def __init__(self: Self, gateway: ParameterInspector, summary: ParameterSummary) -> None:
        super().__init__()
        self._gateway = gateway
        self._summary = summary
        self._detail: ParameterDetail | None = None
        self._revealed = False
        self._loaded = False

    def compose(self: Self) -> ComposeResult:
        with VerticalScroll(id="body"):
            yield Static(id="overview-info")
            yield Static("Value", classes="heading")
            yield Static(id="value")
        yield Static(id="error")
        yield Footer()

    def on_mount(self: Self) -> None:
        self.sub_title = self._summary.name
        self.query_one("#body", VerticalScroll).loading = True
        self._fetch_detail()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_detail(self: Self) -> ParameterDetail:
        return self._gateway.get_parameter(self._summary.name)

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.SUCCESS:
            self._loaded = True
            self.query_one("#body", VerticalScroll).loading = False
            detail = event.worker.result
            if detail is not None:
                self._detail = detail
                self._render_detail(detail)
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, AwsError):
                self._show_error(error)
            elif error is not None:
                raise error

    def _show_error(self: Self, error: AwsError) -> None:
        body = self.query_one("#body", VerticalScroll)
        body.loading = False
        if self._loaded:
            message = error.message if error.hint is None else f"{error.message} ({error.hint})"
            self.notify(message, title="Refresh failed", severity="error")
            return
        body.display = False
        self.set_focus(None)
        panel = self.query_one("#error", Static)
        panel.update(error.message if error.hint is None else f"{error.message}\n{error.hint}")
        panel.display = True

    def _render_detail(self: Self, detail: ParameterDetail) -> None:
        now = datetime.now(tz=UTC)
        self.query_one("#overview-info", Static).update(_overview_text(detail, self._summary.tier, now))
        self._render_value()

    def _render_value(self: Self) -> None:
        if self._detail is None:
            return
        # Text(), not a bare str: values often contain square brackets that Rich would
        # otherwise parse as console markup.
        shown = mask_value(self._detail.value, self._detail.param_type, revealed=self._revealed)
        self.query_one("#value", Static).update(Text(shown))

    def action_back(self: Self) -> None:
        self.app.pop_screen()

    def action_refresh(self: Self) -> None:
        self._revealed = False
        self.query_one("#error", Static).display = False
        body = self.query_one("#body", VerticalScroll)
        body.display = True
        if not self._loaded:
            body.loading = True
        self._fetch_detail()

    def action_toggle_reveal(self: Self) -> None:
        if self._detail is None:
            return
        self._revealed = not self._revealed
        self._render_value()

    def action_copy(self: Self) -> None:
        if self._detail is None:
            return
        self.app.copy_to_clipboard(self._detail.value)
        self.notify("Value copied to clipboard.", title=self._summary.name)


def _overview_text(detail: ParameterDetail, tier: str, now: datetime) -> str:
    return (
        f"Type       {detail.param_type}\n"
        f"Tier       {tier}\n"
        f"Version    {detail.version}\n"
        f"Data type  {detail.data_type}\n"
        f"Modified   {relative_age(detail.modified, now)}\n"
        f"ARN        {detail.arn}"
    )
