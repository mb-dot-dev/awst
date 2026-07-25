"""Profile selection screen, shown at startup when no AWS profile is active."""

from typing import TYPE_CHECKING, ClassVar, Self

from awst.screens.filterable_select import FilterableSelectScreen

if TYPE_CHECKING:
    from textual.binding import BindingType


class ProfileSelectScreen(FilterableSelectScreen[str]):
    """Pick the AWS profile the whole app will use; dismisses with its name."""

    PROMPT = "Select an AWS profile"
    NOUN = "profile"

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "clear_or_cancel", "Clear"),
        ("ctrl+q", "app.quit", "Quit"),
    ]

    def _selected(self: Self, name: str) -> None:
        self.dismiss(name)
