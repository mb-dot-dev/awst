"""Region selection screen, opened from anywhere with ctrl+g."""

from typing import TYPE_CHECKING, ClassVar, Self

from awst.screens.filterable_select import FilterableSelectScreen

if TYPE_CHECKING:
    from textual.binding import BindingType


class RegionSelectScreen(FilterableSelectScreen[str | None]):
    """Pick the AWS region the whole app will use; dismisses with its name, or None to cancel."""

    PROMPT = "Select an AWS region"
    NOUN = "region"

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "clear_filter", "Clear"),
        ("escape", "cancel", "Back"),
    ]

    def __init__(self: Self, region_names: list[str], current: str | None) -> None:
        super().__init__(region_names)
        self._preferred = current

    def _selected(self: Self, name: str) -> None:
        self.dismiss(name)

    def action_cancel(self: Self) -> None:
        self.dismiss(None)
