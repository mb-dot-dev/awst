"""Shared base for pickers that narrow a list of names with a filter input."""

from typing import TYPE_CHECKING, ClassVar, Self

from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType


class FilterableSelectScreen[ResultT](Screen[ResultT]):
    """An option list of names narrowed by an always-focused filter input.

    Subclasses set PROMPT and NOUN and implement _selected. Escape and quit
    bindings belong to the subclass, so each picker labels them for itself;
    both point at action_clear_or_cancel.
    """

    TITLE = "awst"

    PROMPT: ClassVar[str]
    NOUN: ClassVar[str]

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    DEFAULT_CSS = """
    #prompt { padding: 1 2 0 2; color: $text-muted; }
    #filter { margin: 1 2 0 2; }
    #options { margin: 0 2 1 2; }
    """

    def __init__(self: Self, names: list[str]) -> None:
        super().__init__()
        self._names = names
        self._preferred: str | None = None

    def _selected(self: Self, name: str) -> None:
        """Dismiss with the chosen name; subclasses type the result."""
        raise NotImplementedError

    def _cancel(self: Self) -> None:
        """Escape with an empty filter; a no-op unless the subclass can cancel."""

    def compose(self: Self) -> ComposeResult:
        yield Static(self.PROMPT, id="prompt")
        yield Input(placeholder=f"filter {self.NOUN}s by name", id="filter")
        yield OptionList(id="options")
        yield Footer()

    def on_mount(self: Self) -> None:
        self._render_options()
        self.query_one("#filter", Input).focus()

    def _render_options(self: Self) -> None:
        query = self.query_one("#filter", Input).value.strip().lower()
        visible = [name for name in self._names if query in name.lower()]
        options = self.query_one("#options", OptionList)
        wanted = self._highlighted_name(options) or self._preferred
        options.clear_options()
        options.add_options([Option(name, id=name) for name in visible])
        if visible:
            options.highlighted = visible.index(wanted) if wanted in visible else 0
        self._update_prompt(len(visible), filtering=bool(query))

    def _highlighted_name(self: Self, options: OptionList) -> str | None:
        highlighted = options.highlighted_option
        return None if highlighted is None else highlighted.id

    def _update_prompt(self: Self, shown: int, *, filtering: bool) -> None:
        total = len(self._names)
        if not filtering:
            text = self.PROMPT
        elif shown == 0:
            text = f"no {self.NOUN}s match"
        else:
            noun = self.NOUN if total == 1 else f"{self.NOUN}s"
            text = f"{shown} of {total} {noun}"
        self.query_one("#prompt", Static).update(text)

    def on_input_changed(self: Self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._render_options()

    def on_input_submitted(self: Self, event: Input.Submitted) -> None:
        if event.input.id == "filter":
            # No-op when nothing is highlighted, which is exactly the zero-match case.
            self.query_one("#options", OptionList).action_select()

    def on_option_list_option_selected(self: Self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self._selected(event.option.id)

    def action_cursor_up(self: Self) -> None:
        self.query_one("#options", OptionList).action_cursor_up()

    def action_cursor_down(self: Self) -> None:
        self.query_one("#options", OptionList).action_cursor_down()

    def action_clear_or_cancel(self: Self) -> None:
        filter_input = self.query_one("#filter", Input)
        if filter_input.value:
            filter_input.value = ""  # fires Input.Changed, which re-renders the options
        else:
            self._cancel()
