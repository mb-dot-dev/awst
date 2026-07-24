"""Tests for the object-delete progress modal."""

import contextlib
import threading
from typing import TYPE_CHECKING, Self

import pytest
from textual.app import App
from textual.widgets import Static
from textual.worker import WorkerCancelled, WorkerFailed

from awst.aws.models import AwsError
from awst.screens.delete_objects import DeleteObjectsScreen
from tests.fakes import FakeS3Gateway

if TYPE_CHECKING:
    from textual.pilot import Pilot


@pytest.fixture
def toasts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record (message, title) pairs instead of rendering toasts."""
    messages: list[tuple[str, str]] = []

    def record_notify(self: App[None], message: str, *, title: str = "", **kwargs: object) -> None:
        messages.append((message, title))

    monkeypatch.setattr(App, "notify", record_notify)
    return messages


class DeleteObjectsApp(App[None]):
    """Harness that opens the progress modal directly and records its dismissal."""

    def __init__(self: Self, gateway: FakeS3Gateway, target: str = "") -> None:
        super().__init__()
        self.gateway = gateway
        self.target = target
        self.results: list[None] = []

    def on_mount(self: Self) -> None:
        screen = DeleteObjectsScreen(self.gateway, "assets", "eu-west-1", self.target)
        self.push_screen(screen, self.results.append)


async def _until_dismissed(app: DeleteObjectsApp, pilot: Pilot[None]) -> None:
    """Let the delete worker run to completion, tolerating cancelled/failed workers."""
    for _ in range(100):
        with contextlib.suppress(WorkerFailed, WorkerCancelled):
            await app.workers.wait_for_complete()
        await pilot.pause()
        if app.results:
            return
    pytest.fail("modal never dismissed")


async def _until_progress_shows(app: DeleteObjectsApp, pilot: Pilot[None], text: str) -> None:
    for _ in range(100):
        await pilot.pause()
        if text in str(app.screen.query_one("#progress", Static).content):
            return
    pytest.fail(f"progress never showed {text!r}")


@pytest.mark.asyncio
async def test_empty_target_empties_the_bucket_and_toasts_final_count(toasts: list[tuple[str, str]]) -> None:
    gateway = FakeS3Gateway(delete_batches=[500, 1234])
    app = DeleteObjectsApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert gateway.emptied == [("assets", "eu-west-1")]
    assert gateway.deleted == []
    assert toasts == [("1,234 objects deleted.", "Emptied assets")]


@pytest.mark.asyncio
async def test_already_empty_bucket_reports_zero(toasts: list[tuple[str, str]]) -> None:
    app = DeleteObjectsApp(FakeS3Gateway(delete_batches=[]))

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert toasts == [("0 objects deleted.", "Emptied assets")]


@pytest.mark.asyncio
async def test_object_key_target_calls_delete_object(toasts: list[tuple[str, str]]) -> None:
    gateway = FakeS3Gateway(delete_batches=[1])
    app = DeleteObjectsApp(gateway, target="docs/readme.md")

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert gateway.deleted == [("assets", "eu-west-1", "docs/readme.md")]
    assert gateway.emptied == []
    assert toasts == [("1 object deleted.", "Deleted docs/readme.md")]


@pytest.mark.asyncio
async def test_prefix_target_calls_delete_prefix(toasts: list[tuple[str, str]]) -> None:
    gateway = FakeS3Gateway(delete_batches=[7])
    app = DeleteObjectsApp(gateway, target="docs/2026/")

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert gateway.deleted == [("assets", "eu-west-1", "docs/2026/")]
    assert gateway.emptied == []
    assert toasts == [("7 objects deleted.", "Deleted docs/2026/")]


@pytest.mark.asyncio
async def test_title_names_the_bucket_when_emptying() -> None:
    gate = threading.Event()
    app = DeleteObjectsApp(FakeS3Gateway(delete_batches=[1, 2], delete_gate=gate))

    async with app.run_test() as pilot:
        await _until_progress_shows(app, pilot, "1 object deleted")

        assert str(app.screen.query_one("#title", Static).content) == "Emptying assets"

        gate.set()
        await _until_dismissed(app, pilot)


@pytest.mark.asyncio
async def test_title_names_the_target_when_deleting() -> None:
    gate = threading.Event()
    app = DeleteObjectsApp(FakeS3Gateway(delete_batches=[1, 2], delete_gate=gate), target="docs/2026/")

    async with app.run_test() as pilot:
        await _until_progress_shows(app, pilot, "1 object deleted")

        assert str(app.screen.query_one("#title", Static).content) == "Deleting docs/2026/"

        gate.set()
        await _until_dismissed(app, pilot)


@pytest.mark.asyncio
async def test_progress_label_updates_per_batch(toasts: list[tuple[str, str]]) -> None:
    gate = threading.Event()
    gateway = FakeS3Gateway(delete_batches=[500, 600], delete_gate=gate)
    app = DeleteObjectsApp(gateway)

    async with app.run_test() as pilot:
        await _until_progress_shows(app, pilot, "500 objects deleted")

        gate.set()
        await _until_dismissed(app, pilot)

    assert toasts == [("600 objects deleted.", "Emptied assets")]


@pytest.mark.asyncio
async def test_escape_cancels_and_reports_partial_count(toasts: list[tuple[str, str]]) -> None:
    gate = threading.Event()
    gateway = FakeS3Gateway(delete_batches=[500, 600], delete_gate=gate)
    app = DeleteObjectsApp(gateway)

    async with app.run_test() as pilot:
        await _until_progress_shows(app, pilot, "500 objects deleted")

        await pilot.press("escape")
        gate.set()  # release the frozen worker thread so it can observe the cancel
        await _until_dismissed(app, pilot)

    assert toasts == [("At least 500 objects already deleted.", "Cancelled")]


@pytest.mark.asyncio
async def test_gateway_error_toasts_and_dismisses(toasts: list[tuple[str, str]]) -> None:
    gateway = FakeS3Gateway(delete_error=AwsError("Access Denied"))
    app = DeleteObjectsApp(gateway, target="docs/")

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert toasts == [("Access Denied", "Delete failed")]


@pytest.mark.asyncio
async def test_gateway_error_while_emptying_bucket_toasts_empty_bucket_failed(
    toasts: list[tuple[str, str]],
) -> None:
    gateway = FakeS3Gateway(delete_error=AwsError("Access Denied"))
    app = DeleteObjectsApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert toasts == [("Access Denied", "Empty bucket failed")]
