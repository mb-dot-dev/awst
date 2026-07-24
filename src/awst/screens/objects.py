"""S3 object list screen: one prefix level of one bucket."""

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from textual.widgets import DataTable
from textual.worker import get_current_worker

from awst.aws.models import ObjectSummary
from awst.screens.confirm import ConfirmScreen
from awst.screens.delete_objects import DeleteGateway, DeleteObjectsScreen
from awst.screens.formatting import human_size, relative_age
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from datetime import datetime

    from textual.binding import BindingType

    from awst.aws.models import ObjectPage


class ObjectLister(Protocol):
    """The slice of the S3 gateway the object browser needs."""

    def list_objects(
        self: Self,
        bucket: str,
        region: str,
        prefix: str = "",
        continuation_token: str | None = None,
    ) -> ObjectPage: ...


class ObjectBrowserGateway(ObjectLister, DeleteGateway, Protocol):
    """Everything the object browser needs, plus what it hands to the shared delete modal."""


@dataclass(frozen=True, slots=True)
class FolderEntry:
    """A common prefix one level below the current one."""

    prefix: str  # the full prefix, ending "/"


type ObjectEntry = FolderEntry | ObjectSummary


class ObjectListScreen(ResourceListScreen[ObjectEntry]):
    """One prefix level; Enter drills into folders, d deletes a row, m loads more."""

    TITLE = "S3 objects"
    COLUMNS = ("Name", "Size", "Modified")
    NOUN = "object"

    BINDINGS: ClassVar[list[BindingType]] = [("d", "delete", "Delete")]

    def __init__(self: Self, gateway: ObjectBrowserGateway, bucket: str, region: str, prefix: str = "") -> None:
        super().__init__()
        self._gateway = gateway
        self._bucket = bucket
        self._region = region
        self._prefix = prefix
        self._continuation_token: str | None = None
        self.sub_title = f"{bucket}/{prefix}"

    def _list(self: Self) -> list[ObjectEntry]:
        page = self._gateway.list_objects(self._bucket, self._region, self._prefix)
        # A cancelled worker's result is discarded by the base anyway; skip the state write so a
        # zombie thread that outlives its cancellation can't clobber a token set by a later fetch.
        if not get_current_worker().is_cancelled:
            self._continuation_token = page.continuation_token
        return self._entries(page)

    def _has_more(self: Self) -> bool:
        return self._continuation_token is not None

    def _auto_fetch_on_filter(self: Self) -> bool:
        return False  # a prefix can hold millions of keys; stay scoped to loaded objects

    def _list_more(self: Self) -> list[ObjectEntry]:
        page = self._gateway.list_objects(self._bucket, self._region, self._prefix, self._continuation_token)
        if not get_current_worker().is_cancelled:
            self._continuation_token = page.continuation_token
        return self._entries(page)

    def _entries(self: Self, page: ObjectPage) -> list[ObjectEntry]:
        return [*(FolderEntry(prefix) for prefix in page.folders), *page.objects]

    def _row(self: Self, item: ObjectEntry, now: datetime) -> tuple[str, ...]:
        if isinstance(item, FolderEntry):
            return (item.prefix[len(self._prefix) :], "", "")
        return (item.key[len(self._prefix) :], human_size(item.size), relative_age(item.modified, now))

    def _item_name(self: Self, item: ObjectEntry) -> str:
        return item.prefix if isinstance(item, FolderEntry) else item.key

    def on_data_table_row_selected(self: Self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        # Folder row keys end with the delimiter; object keys at this level never do
        # (a key ending "/" rolls up into CommonPrefixes when listing with Delimiter="/").
        if name is not None and name.endswith("/"):
            self.app.push_screen(ObjectListScreen(self._gateway, self._bucket, self._region, name))

    def action_delete(self: Self) -> None:
        """Confirm, then permanently delete the highlighted object key or folder prefix."""
        target = self._cursor_name(self.query_one("#items", DataTable))
        if target is None:
            return
        if target.endswith("/"):
            question = f"Permanently delete everything under {target}, including all versions?"
        else:
            question = f"Permanently delete {target} and all its versions?"
        self.app.push_screen(ConfirmScreen(question), partial(self._on_delete_confirmed, target))

    def _on_delete_confirmed(self: Self, target: str, confirmed: bool | None) -> None:  # noqa: FBT001
        if not confirmed:
            return
        if not target:  # pragma: no cover -- unreachable: object keys are never empty; see action_delete
            # An empty target tells the modal to empty the whole bucket, with no confirmation
            # step of its own; the object browser must never request that.
            return
        screen = DeleteObjectsScreen(self._gateway, self._bucket, self._region, target)
        self.app.push_screen(screen, self._on_delete_finished)

    def _on_delete_finished(self: Self, result: None) -> None:  # noqa: ARG002
        self.action_refresh()
