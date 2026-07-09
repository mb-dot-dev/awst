"""SQS queue list screen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self

from awst.aws.models import QueueSummary
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from datetime import datetime


class QueueLister(Protocol):
    """The slice of the SQS gateway this screen needs."""

    def list_queues(self: Self) -> list[QueueSummary]: ...


class QueueListScreen(ResourceListScreen[QueueSummary]):
    """Read-only list of the region's SQS queues."""

    TITLE = "SQS queues"
    COLUMNS = ("Name", "Type")
    NOUN = "queue"

    def __init__(self: Self, gateway: QueueLister) -> None:
        super().__init__()
        self._gateway = gateway

    def _list(self: Self) -> list[QueueSummary]:
        return self._gateway.list_queues()

    def _row(self: Self, item: QueueSummary, now: datetime) -> tuple[str, ...]:  # noqa: ARG002 - no timestamp column
        return (item.name, "FIFO" if item.is_fifo else "Standard")

    def _item_name(self: Self, item: QueueSummary) -> str:
        return item.name
