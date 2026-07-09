"""Home screen: pick an AWS service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Self, cast

from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from awst.screens.buckets import BucketListScreen
from awst.screens.functions import FunctionListScreen
from awst.screens.queues import QueueListScreen
from awst.screens.stacks import StackListScreen

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.app import ComposeResult
    from textual.binding import BindingType

    from awst.app import AwstApp


@dataclass(frozen=True, slots=True)
class ServiceEntry:
    """One row in the service menu."""

    option_id: str
    name: str
    resource: str
    enabled: bool
    screen_factory: Callable[[AwstApp], Screen[None]] | None


SERVICES = (
    ServiceEntry(
        option_id="cloudformation",
        name="CloudFormation",
        resource="Stacks",
        enabled=True,
        screen_factory=lambda app: StackListScreen(app.cloudformation_gateway),
    ),
    ServiceEntry(
        option_id="s3",
        name="S3",
        resource="Buckets",
        enabled=True,
        screen_factory=lambda app: BucketListScreen(app.s3_gateway),
    ),
    ServiceEntry(
        option_id="lambda",
        name="Lambda",
        resource="Functions",
        enabled=True,
        screen_factory=lambda app: FunctionListScreen(app.lambda_gateway),
    ),
    ServiceEntry(
        option_id="sqs",
        name="SQS",
        resource="Queues",
        enabled=True,
        screen_factory=lambda app: QueueListScreen(app.sqs_gateway),
    ),
)


def _prompt(entry: ServiceEntry) -> str:
    suffix = "" if entry.enabled else "  (soon)"
    return f"{entry.name:<18}{entry.resource}{suffix}"


class HomeScreen(Screen[None]):
    """Service picker; the app's landing screen."""

    TITLE = "awst"

    BINDINGS: ClassVar[list[BindingType]] = [("q", "app.quit", "Quit")]

    DEFAULT_CSS = """
    #prompt { padding: 1 2 0 2; color: $text-muted; }
    #services { margin: 1 2; }
    """

    def compose(self: Self) -> ComposeResult:
        yield Static("Select a service", id="prompt")
        yield OptionList(
            *[Option(_prompt(entry), id=entry.option_id, disabled=not entry.enabled) for entry in SERVICES],
            id="services",
        )
        yield Footer()

    def on_option_list_option_selected(self: Self, event: OptionList.OptionSelected) -> None:
        entry = next(entry for entry in SERVICES if entry.option_id == event.option.id)
        if entry.screen_factory is not None:
            self.app.push_screen(entry.screen_factory(cast("AwstApp", self.app)))
