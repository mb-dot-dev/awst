"""S3 object list screen: one prefix level of one bucket."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Self

from textual.widgets import DataTable  # noqa: TC002 -- needed at runtime: Textual inspects handler annotations
from textual.worker import get_current_worker

from awst.aws.models import ObjectSummary
from awst.screens.formatting import human_size, relative_age
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from datetime import datetime

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


@dataclass(frozen=True, slots=True)
class FolderEntry:
    """A common prefix one level below the current one."""

    prefix: str  # the full prefix, ending "/"


type ObjectEntry = FolderEntry | ObjectSummary


class ObjectListScreen(ResourceListScreen[ObjectEntry]):
    """Read-only listing of one prefix level; Enter drills into folders, m loads more."""

    TITLE = "S3 objects"
    COLUMNS = ("Name", "Size", "Modified")
    NOUN = "object"

    def __init__(self: Self, gateway: ObjectLister, bucket: str, region: str, prefix: str = "") -> None:
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
