"""Tests for the S3 bucket list screen."""

import contextlib
import threading
from typing import TYPE_CHECKING, Self

import pytest
from textual.app import App
from textual.widgets import DataTable, Input, Static
from textual.worker import WorkerCancelled, WorkerFailed

from awst.aws.models import AwsError
from awst.screens.buckets import BucketListScreen
from awst.screens.confirm import ConfirmScreen
from awst.screens.empty_bucket import EmptyBucketScreen
from awst.screens.objects import ObjectListScreen
from tests.fakes import FakeS3Gateway, make_bucket

if TYPE_CHECKING:
    from textual.pilot import Pilot


class BucketScreenApp(App[None]):
    """Minimal harness that opens the bucket list screen directly."""

    def __init__(self: Self, gateway: FakeS3Gateway) -> None:
        super().__init__()
        self.gateway = gateway

    def on_mount(self: Self) -> None:
        self.push_screen(BucketListScreen(self.gateway))


async def _settle(app: App[None]) -> None:
    """Wait for the fetch worker and let its messages be processed."""
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_renders_one_row_per_bucket_with_name_and_region() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets", region="eu-west-1"), make_bucket("logs", region="")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "assets"
        assert table.get_row_at(0)[1] == "eu-west-1"
        assert table.get_row_at(1)[0] == "logs"
        assert table.get_row_at(1)[1] == ""


@pytest.mark.asyncio
async def test_empty_account_renders_zero_rows() -> None:
    gateway = FakeS3Gateway(buckets=[])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 0
        assert "0 buckets" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_count_header_uses_singular_for_one_bucket() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert str(app.screen.query_one("#count", Static).content) == "1 bucket"


@pytest.mark.asyncio
async def test_escape_pops_back() -> None:
    app = BucketScreenApp(FakeS3Gateway())

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert isinstance(app.screen, BucketListScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, BucketListScreen)


@pytest.mark.asyncio
async def test_filter_narrows_rows_live() -> None:
    gateway = FakeS3Gateway(
        buckets=[make_bucket("prod-assets"), make_bucket("prod-logs"), make_bucket("staging-assets")],
    )
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert "2 of 3 buckets" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_escape_clears_filter_before_going_back() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("prod-assets"), make_bucket("staging-assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, BucketListScreen)  # still here: escape only cleared the filter
        assert app.screen.query_one("#filter", Input).value == ""
        assert app.screen.query_one(DataTable).row_count == 2
        assert app.screen.query_one(DataTable).has_focus

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, BucketListScreen)


@pytest.mark.asyncio
async def test_refresh_refetches_and_updates_rows() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert app.screen.query_one(DataTable).row_count == 1

        gateway.buckets = [make_bucket("assets"), make_bucket("logs")]
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert gateway.calls == 2
        assert app.screen.query_one(DataTable).row_count == 2


@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    gateway = FakeS3Gateway(error=AwsError("no credentials", hint="run `aws sso login`"))
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.screen.query_one("#error", Static)

        assert panel.display is True
        assert "no credentials" in str(panel.content)
        assert "aws sso login" in str(panel.content)
        assert app.screen.query_one(DataTable).display is False


@pytest.mark.asyncio
async def test_retry_after_initial_failure_recovers() -> None:
    gateway = FakeS3Gateway(error=AwsError("boom"))
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.error = None
        gateway.buckets = [make_bucket("assets")]
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one("#error", Static).display is False
        assert app.screen.query_one(DataTable).display is True
        assert app.screen.query_one(DataTable).row_count == 1


@pytest.mark.asyncio
async def test_refresh_failure_keeps_stale_rows_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.error = AwsError("throttled")
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.display is True
        assert table.row_count == 1  # stale rows kept
        assert toasts == ["throttled"]
        assert str(app.screen.query_one("#count", Static).content) == "1 bucket"  # "refreshing…" cleared


@pytest.mark.asyncio
async def test_enter_on_bucket_opens_the_object_browser_in_its_region() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets", region="eu-central-1")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("enter")
        await _settle(app)
        await pilot.pause()

        assert isinstance(app.screen, ObjectListScreen)
        assert app.screen.sub_title == "assets/"
        assert gateway.object_calls == [("assets", "eu-central-1", "", None)]


@pytest.mark.asyncio
async def test_enter_with_no_rows_does_nothing() -> None:
    app = BucketScreenApp(FakeS3Gateway(buckets=[]))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, BucketListScreen)


async def _until_back_on_list(app: App[None], pilot: Pilot[None]) -> None:
    for _ in range(100):
        with contextlib.suppress(WorkerFailed, WorkerCancelled):
            await app.workers.wait_for_complete()
        await pilot.pause()
        if isinstance(app.screen, BucketListScreen):
            return
    pytest.fail("never returned to the bucket list")


@pytest.mark.asyncio
async def test_e_on_row_asks_for_confirmation_naming_the_bucket() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        assert "assets" in str(app.screen.query_one("#question", Static).content)
        assert gateway.emptied == []  # nothing deleted yet


@pytest.mark.asyncio
async def test_e_with_no_rows_does_nothing() -> None:
    app = BucketScreenApp(FakeS3Gateway(buckets=[]))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        assert isinstance(app.screen, BucketListScreen)


@pytest.mark.asyncio
async def test_declining_confirmation_deletes_nothing() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()

        assert isinstance(app.screen, BucketListScreen)
        assert gateway.emptied == []
        assert gateway.calls == 1  # no refresh either


@pytest.mark.asyncio
async def test_confirming_empties_the_bucket_and_refreshes() -> None:
    gate = threading.Event()
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")], empty_batches=[1, 2], empty_gate=gate)
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        assert isinstance(app.screen, EmptyBucketScreen)  # gate holds the worker before its second batch

        gate.set()
        await _until_back_on_list(app, pilot)

        assert gateway.emptied == ["assets"]
        assert gateway.calls == 2  # the list refreshed after emptying
