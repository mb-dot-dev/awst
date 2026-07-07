"""S3 bucket list screen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self

from awst.aws.models import BucketSummary
from awst.screens.formatting import relative_age
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from datetime import datetime


class BucketLister(Protocol):
    """The slice of the S3 gateway this screen needs."""

    def list_buckets(self: Self) -> list[BucketSummary]: ...


class BucketListScreen(ResourceListScreen[BucketSummary]):
    """Read-only list of the account's S3 buckets."""

    TITLE = "S3 buckets"
    COLUMNS = ("Name", "Region", "Created")
    NOUN = "bucket"

    def __init__(self: Self, gateway: BucketLister) -> None:
        super().__init__()
        self._gateway = gateway

    def _list(self: Self) -> list[BucketSummary]:
        return self._gateway.list_buckets()

    def _row(self: Self, item: BucketSummary, now: datetime) -> tuple[str, ...]:
        return (item.name, item.region, relative_age(item.created, now))

    def _name(self: Self, item: BucketSummary) -> str:
        return item.name
