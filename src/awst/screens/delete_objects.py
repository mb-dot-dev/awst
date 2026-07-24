"""Modal that deletes S3 objects — one key, one prefix, or a whole bucket — with live progress."""

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
    """The slice of the S3 gateway used to empty a whole bucket."""

    def empty_bucket(self: Self, name: str, region: str) -> Iterator[int]: ...


class ObjectDeleter(Protocol):
    """The slice of the S3 gateway used to delete one object or one prefix."""

    def delete_object(self: Self, bucket: str, region: str, key: str) -> Iterator[int]: ...

    def delete_prefix(self: Self, bucket: str, region: str, prefix: str) -> Iterator[int]: ...


class DeleteGateway(BucketEmptier, ObjectDeleter, Protocol):
    """Everything the progress modal may call."""


class DeleteObjectsScreen(ModalScreen[None]):
    """Delete S3 objects with live progress; dismisses once done, cancelled, or failed.

    `target` selects what to delete: "" is the whole bucket, a value ending "/"
    is every key beneath that prefix, and anything else is a single object key.
    Every version and delete marker goes in all three cases.
    """

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    DeleteObjectsScreen { align: center middle; }
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

    def __init__(self: Self, gateway: DeleteGateway, bucket: str, region: str, target: str = "") -> None:
        super().__init__()
        self._gateway = gateway
        self._bucket = bucket
        self._region = region
        self._target = target
        self._deleted = 0

    def compose(self: Self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._title(), id="title")
            yield Static("Deleting… 0 objects deleted", id="progress")
        yield Footer()

    def _title(self: Self) -> str:
        return f"Deleting {self._target}" if self._target else f"Emptying {self._bucket}"

    def _done_title(self: Self) -> str:
        return f"Deleted {self._target}" if self._target else f"Emptied {self._bucket}"

    def _failed_title(self: Self) -> str:
        return "Delete failed" if self._target else "Empty bucket failed"

    def _deletions(self: Self) -> Iterator[int]:
        """The gateway call for this target; the delimiter tells a folder from an object key."""
        if not self._target:
            return self._gateway.empty_bucket(self._bucket, self._region)
        if self._target.endswith("/"):
            return self._gateway.delete_prefix(self._bucket, self._region, self._target)
        return self._gateway.delete_object(self._bucket, self._region, self._target)

    def on_mount(self: Self) -> None:
        self._delete()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _delete(self: Self) -> None:
        worker = get_current_worker()
        for count in self._deletions():
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
        if event.worker.name != "_delete":
            return
        if event.state == WorkerState.SUCCESS:
            self.notify(f"{self._count_text()} deleted.", title=self._done_title())
            self.dismiss(result=None)
        elif event.state == WorkerState.CANCELLED:
            # Cancellation is cooperative: the thread only checks worker.is_cancelled between
            # batches, so it may still be finishing an in-flight delete_objects call when we
            # dismiss here. The caller's post-dismiss refresh can therefore briefly list rows
            # that this batch is about to delete; the "at least" wording sets that expectation.
            self.notify(f"At least {self._count_text()} already deleted.", title="Cancelled", severity="warning")
            self.dismiss(result=None)
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, AwsError):
                message = error.message if error.hint is None else f"{error.message} ({error.hint})"
                self.notify(message, title=self._failed_title(), severity="error")
                self.dismiss(result=None)
            elif error is not None:
                raise error

    def action_cancel(self: Self) -> None:
        """Cancel the in-flight delete worker; the escape binding above triggers this."""
        self.workers.cancel_node(self)
