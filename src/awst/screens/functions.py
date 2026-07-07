"""Lambda function list screen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self

from awst.aws.models import FunctionSummary
from awst.screens.formatting import relative_age
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from datetime import datetime


class FunctionLister(Protocol):
    """The slice of the Lambda gateway this screen needs."""

    def list_functions(self: Self) -> list[FunctionSummary]: ...


class FunctionListScreen(ResourceListScreen[FunctionSummary]):
    """Read-only list of the region's Lambda functions."""

    TITLE = "Lambda functions"
    COLUMNS = ("Name", "Runtime", "Memory", "Timeout", "Modified")
    NOUN = "function"

    def __init__(self: Self, gateway: FunctionLister) -> None:
        super().__init__()
        self._gateway = gateway

    def _list(self: Self) -> list[FunctionSummary]:
        return self._gateway.list_functions()

    def _row(self: Self, item: FunctionSummary, now: datetime) -> tuple[str, ...]:
        return (
            item.name,
            item.runtime,
            f"{item.memory_mb} MB",
            f"{item.timeout_s}s",
            relative_age(item.modified, now),
        )

    def _item_name(self: Self, item: FunctionSummary) -> str:
        return item.name
