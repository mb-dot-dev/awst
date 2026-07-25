"""SSM parameter list screen."""

from typing import TYPE_CHECKING, Protocol, Self

from textual.widgets import DataTable  # noqa: TC002 -- needed at runtime: Textual inspects handler annotations
from textual.worker import get_current_worker

from awst.aws.models import Page, ParameterSummary
from awst.screens.formatting import relative_age
from awst.screens.parameter_detail import ParameterDetailScreen, ParameterInspector
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime


class ParameterLister(Protocol):
    """The slice of the SSM gateway this screen needs."""

    def list_parameters(self: Self, next_token: str | None = None) -> Page[ParameterSummary]: ...


class ParameterGateway(ParameterLister, ParameterInspector, Protocol):
    """Everything the parameter screens collectively need from SSM."""


class ParameterListScreen(ResourceListScreen[ParameterSummary]):
    """Read-only list of the region's SSM parameters; metadata only, never values."""

    TITLE = "SSM parameters"
    COLUMNS = ("Name", "Type", "Tier", "Modified")
    NOUN = "parameter"

    def __init__(self: Self, gateway: ParameterGateway) -> None:
        super().__init__()
        self._gateway = gateway
        self._next_token: str | None = None

    def _list(self: Self) -> list[ParameterSummary]:
        page = self._gateway.list_parameters()
        if not get_current_worker().is_cancelled:
            self._next_token = page.next_token
        return list(page.items)

    def _has_more(self: Self) -> bool:
        return self._next_token is not None

    def _list_more(self: Self) -> list[ParameterSummary]:
        page = self._gateway.list_parameters(self._next_token)
        if not get_current_worker().is_cancelled:
            self._next_token = page.next_token
        return list(page.items)

    def _sort_key(self: Self) -> Callable[[ParameterSummary], str]:
        return lambda parameter: parameter.name

    def _row(self: Self, item: ParameterSummary, now: datetime) -> tuple[str, ...]:
        return (item.name, item.param_type, item.tier, relative_age(item.modified, now))

    def _item_name(self: Self, item: ParameterSummary) -> str:
        return item.name

    def on_data_table_row_selected(self: Self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        summary = next((item for item in self._all_items if item.name == name), None)
        if summary is not None:
            self.app.push_screen(ParameterDetailScreen(self._gateway, summary))
