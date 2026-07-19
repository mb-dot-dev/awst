"""Modal that empties one S3 bucket, showing live progress with cancel."""

from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from textual import work
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Static
from textual.worker import Worker, WorkerState, get_current_worker

from awst.aws.models import AwsError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from textual.app import ComposeResult
    from textual.binding import BindingType


class BucketEmptier(Protocol):
    """The slice of the S3 gateway this screen needs."""

    def empty_bucket(self: Self, name: str) -> Iterator[int]: ...


class EmptyBucketScreen(ModalScreen[None]):
    """Delete every object version in one bucket; dismisses once done, cancelled, or failed."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    EmptyBucketScreen { align: center middle; }
    #dialog {
        width: auto;
        max-width: 80;
        height: auto;
        padding: 1 2;
        background: $panel;
        border: thick $primary;
    }
    #dialog Static { width: auto; }
    #title { text-style: bold; }
    #progress { color: $text-muted; margin-top: 1; }
    """

    def __init__(self: Self, gateway: BucketEmptier, bucket_name: str) -> None:
        super().__init__()
        self._gateway = gateway
        self._bucket_name = bucket_name
        self._deleted = 0

    def compose(self: Self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"Emptying {self._bucket_name}", id="title")
            yield Static("Deleting… 0 objects deleted", id="progress")
        yield Footer()

    def on_mount(self: Self) -> None:
        self._empty()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _empty(self: Self) -> None:
        worker = get_current_worker()
        for count in self._gateway.empty_bucket(self._bucket_name):
            if worker.is_cancelled:
                return
            self.app.call_from_thread(self._update_progress, count)

    def _update_progress(self: Self, count: int) -> None:
        self._deleted = count
        if not self.is_attached:  # a late batch landed while the screen was dismissing
            return
        self.query_one("#progress", Static).update(f"Deleting… {self._count_text()} deleted")

    def _count_text(self: Self) -> str:
        noun = "object" if self._deleted == 1 else "objects"
        return f"{self._deleted:,} {noun}"

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.worker.name != "_empty":
            return
        if event.state == WorkerState.SUCCESS:
            self.notify(f"{self._count_text()} deleted.", title=f"Emptied {self._bucket_name}")
            self.dismiss(result=None)
        elif event.state == WorkerState.CANCELLED:
            self.notify(f"At least {self._count_text()} already deleted.", title="Cancelled", severity="warning")
            self.dismiss(result=None)
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, AwsError):
                message = error.message if error.hint is None else f"{error.message} ({error.hint})"
                self.notify(message, title="Empty bucket failed", severity="error")
                self.dismiss(result=None)
            elif error is not None:
                raise error

    def action_cancel(self: Self) -> None:
        self.workers.cancel_node(self)
