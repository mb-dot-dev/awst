"""Tests for the SQS queue list screen."""

from typing import Self

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from awst.aws.models import AwsError
from awst.screens.queues import QueueListScreen
from tests.fakes import FakeSqsGateway, make_queue


class QueueScreenApp(App[None]):
    """Minimal harness that opens the queue list screen directly."""

    def __init__(self: Self, gateway: FakeSqsGateway) -> None:
        super().__init__()
        self.gateway = gateway

    def on_mount(self: Self) -> None:
        self.push_screen(QueueListScreen(self.gateway))


async def _settle(app: App[None]) -> None:
    """Wait for the fetch worker and let its messages be processed."""
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_renders_one_row_per_queue_with_name_and_type() -> None:
    gateway = FakeSqsGateway(queues=[make_queue("orders"), make_queue("orders.fifo")])
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "orders"
        assert table.get_row_at(0)[1] == "Standard"
        assert table.get_row_at(1)[0] == "orders.fifo"
        assert table.get_row_at(1)[1] == "FIFO"


@pytest.mark.asyncio
async def test_empty_region_renders_zero_rows_with_queue_noun() -> None:
    gateway = FakeSqsGateway(queues=[])
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 0
        assert "0 queues" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_filter_narrows_rows_live() -> None:
    gateway = FakeSqsGateway(
        queues=[make_queue("prod-orders"), make_queue("prod-mail"), make_queue("staging-orders")],
    )
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 2
        assert "2 of 3 queues" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    gateway = FakeSqsGateway(error=AwsError("no credentials", hint="run `aws sso login`"))
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.screen.query_one("#error", Static)

        assert panel.display is True
        assert "no credentials" in str(panel.content)
        assert app.screen.query_one(DataTable).display is False


@pytest.mark.asyncio
async def test_enter_on_row_does_nothing() -> None:
    gateway = FakeSqsGateway(queues=[make_queue("orders")])
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, QueueListScreen)  # no detail screen yet
