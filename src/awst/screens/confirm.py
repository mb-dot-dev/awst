"""A reusable yes/no confirmation modal."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Self

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType


class ConfirmScreen(ModalScreen[bool]):
    """Ask a yes/no question; dismisses with True on confirm, False otherwise."""

    BINDINGS: ClassVar[list[BindingType]] = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    #dialog {
        width: auto;
        max-width: 60;
        height: auto;
        padding: 1 2;
        background: $panel;
        border: thick $primary;
    }
    #question { margin-bottom: 1; }
    #buttons { height: auto; align-horizontal: center; }
    #buttons Button { margin: 0 1; }
    """

    def __init__(self: Self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self: Self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._question, id="question")
            with Horizontal(id="buttons"):
                yield Button("Yes", variant="error", id="yes")
                yield Button("No", variant="primary", id="no")

    def on_mount(self: Self) -> None:
        self.query_one("#no", Button).focus()

    def on_button_pressed(self: Self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_confirm(self: Self) -> None:
        self.dismiss(result=True)

    def action_cancel(self: Self) -> None:
        self.dismiss(result=False)
