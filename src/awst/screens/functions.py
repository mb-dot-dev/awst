"""Lambda function list screen."""

from typing import TYPE_CHECKING, Protocol, Self

from textual.worker import get_current_worker

from awst.aws.models import FunctionSummary, Page
from awst.screens.formatting import relative_age
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime


class FunctionLister(Protocol):
    """The slice of the Lambda gateway this screen needs."""

    def list_functions(self: Self, next_token: str | None = None) -> Page[FunctionSummary]: ...


class FunctionListScreen(ResourceListScreen[FunctionSummary]):
    """Read-only list of the region's Lambda functions."""

    TITLE = "Lambda functions"
    COLUMNS = ("Name", "Runtime", "Memory", "Timeout", "Modified")
    NOUN = "function"

    def __init__(self: Self, gateway: FunctionLister) -> None:
        super().__init__()
        self._gateway = gateway
        self._next_token: str | None = None

    def _list(self: Self) -> list[FunctionSummary]:
        page = self._gateway.list_functions()
        if not get_current_worker().is_cancelled:
            self._next_token = page.next_token
        return list(page.items)

    def _has_more(self: Self) -> bool:
        return self._next_token is not None

    def _list_more(self: Self) -> list[FunctionSummary]:
        page = self._gateway.list_functions(self._next_token)
        if not get_current_worker().is_cancelled:
            self._next_token = page.next_token
        return list(page.items)

    def _sort_key(self: Self) -> Callable[[FunctionSummary], str]:
        return lambda function: function.name

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
