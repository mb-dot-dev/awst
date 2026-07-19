"""S3 bucket list screen."""

from functools import partial
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from textual.widgets import DataTable

from awst.aws.models import BucketSummary
from awst.screens.confirm import ConfirmScreen
from awst.screens.empty_bucket import BucketEmptier, EmptyBucketScreen
from awst.screens.formatting import relative_age
from awst.screens.objects import ObjectLister, ObjectListScreen
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from datetime import datetime

    from textual.binding import BindingType


class BucketLister(Protocol):
    """The slice of the S3 gateway the list itself needs."""

    def list_buckets(self: Self) -> list[BucketSummary]: ...


class BucketGateway(BucketLister, BucketEmptier, ObjectLister, Protocol):
    """Everything the bucket screens collectively need from S3."""


class BucketListScreen(ResourceListScreen[BucketSummary]):
    """List of the account's S3 buckets; `e` empties the highlighted bucket."""

    TITLE = "S3 buckets"
    COLUMNS = ("Name", "Region", "Created")
    NOUN = "bucket"

    BINDINGS: ClassVar[list[BindingType]] = [("e", "empty", "Empty")]

    def __init__(self: Self, gateway: BucketGateway) -> None:
        super().__init__()
        self._gateway = gateway

    def _list(self: Self) -> list[BucketSummary]:
        return self._gateway.list_buckets()

    def _row(self: Self, item: BucketSummary, now: datetime) -> tuple[str, ...]:
        return (item.name, item.region, relative_age(item.created, now))

    def _item_name(self: Self, item: BucketSummary) -> str:
        return item.name

    def on_data_table_row_selected(self: Self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        bucket = next((item for item in self._all_items if item.name == name), None)
        if bucket is not None:
            self.app.push_screen(ObjectListScreen(self._gateway, bucket.name, bucket.region))

    def action_empty(self: Self) -> None:
        name = self._cursor_name(self.query_one("#items", DataTable))
        if name is None:
            return
        question = f"Permanently delete all objects, versions, and delete markers in {name}?"
        self.app.push_screen(ConfirmScreen(question), partial(self._on_empty_confirmed, name))

    def _on_empty_confirmed(self: Self, name: str, confirmed: bool | None) -> None:  # noqa: FBT001
        if not confirmed:
            return
        self.app.push_screen(EmptyBucketScreen(self._gateway, name), self._on_empty_finished)

    def _on_empty_finished(self: Self, result: None) -> None:  # noqa: ARG002
        self.action_refresh()
