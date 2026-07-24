"""Tests for the empty-bucket progress modal."""

import contextlib
import threading
from typing import TYPE_CHECKING, Self

import pytest
from textual.app import App
from textual.widgets import Static
from textual.worker import WorkerCancelled, WorkerFailed

from awst.aws.models import AwsError
from awst.screens.empty_bucket import EmptyBucketScreen
from tests.fakes import FakeS3Gateway

if TYPE_CHECKING:
    from textual.pilot import Pilot


@pytest.fixture
def toasts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record notifications instead of rendering toasts."""
    messages: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        messages.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    return messages


class EmptyBucketApp(App[None]):
    """Harness that opens the progress modal directly and records its dismissal."""

    def __init__(self: Self, gateway: FakeS3Gateway) -> None:
        super().__init__()
        self.gateway = gateway
        self.results: list[None] = []

    def on_mount(self: Self) -> None:
        self.push_screen(EmptyBucketScreen(self.gateway, "assets", "eu-west-1"), self.results.append)


async def _until_dismissed(app: EmptyBucketApp, pilot: Pilot[None]) -> None:
    """Let the delete worker run to completion, tolerating cancelled/failed workers."""
    for _ in range(100):
        with contextlib.suppress(WorkerFailed, WorkerCancelled):
            await app.workers.wait_for_complete()
        await pilot.pause()
        if app.results:
            return
    pytest.fail("modal never dismissed")


async def _until_progress_shows(app: EmptyBucketApp, pilot: Pilot[None], text: str) -> None:
    for _ in range(100):
        await pilot.pause()
        if text in str(app.screen.query_one("#progress", Static).content):
            return
    pytest.fail(f"progress never showed {text!r}")


@pytest.mark.asyncio
async def test_success_empties_bucket_and_toasts_final_count(toasts: list[str]) -> None:
    gateway = FakeS3Gateway(empty_batches=[500, 1234])
    app = EmptyBucketApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert gateway.emptied == [("assets", "eu-west-1")]
    assert toasts == ["1,234 objects deleted."]


@pytest.mark.asyncio
async def test_already_empty_bucket_reports_zero(toasts: list[str]) -> None:
    app = EmptyBucketApp(FakeS3Gateway(empty_batches=[]))

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert toasts == ["0 objects deleted."]


@pytest.mark.asyncio
async def test_progress_label_updates_per_batch(toasts: list[str]) -> None:
    gate = threading.Event()
    gateway = FakeS3Gateway(empty_batches=[500, 600], empty_gate=gate)
    app = EmptyBucketApp(gateway)

    async with app.run_test() as pilot:
        await _until_progress_shows(app, pilot, "500 objects deleted")

        gate.set()
        await _until_dismissed(app, pilot)

    assert toasts == ["600 objects deleted."]


@pytest.mark.asyncio
async def test_escape_cancels_and_reports_partial_count(toasts: list[str]) -> None:
    gate = threading.Event()
    gateway = FakeS3Gateway(empty_batches=[500, 600], empty_gate=gate)
    app = EmptyBucketApp(gateway)

    async with app.run_test() as pilot:
        await _until_progress_shows(app, pilot, "500 objects deleted")

        await pilot.press("escape")
        gate.set()  # release the frozen worker thread so it can observe the cancel
        await _until_dismissed(app, pilot)

    assert toasts == ["At least 500 objects already deleted."]


@pytest.mark.asyncio
async def test_gateway_error_toasts_and_dismisses(toasts: list[str]) -> None:
    gateway = FakeS3Gateway(empty_error=AwsError("Access Denied"))
    app = EmptyBucketApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert toasts == ["Access Denied"]
