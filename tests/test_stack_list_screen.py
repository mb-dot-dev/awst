"""Tests for the CloudFormation stack list screen."""

from datetime import UTC, datetime
from typing import Self

import pytest
from rich.text import Text
from textual.app import App
from textual.widgets import DataTable, Input, Static

from awst.aws.models import StackSummary
from awst.screens.stacks import StackListScreen
from tests.fakes import FakeCloudFormationGateway


def _stack(name: str, status: str = "CREATE_COMPLETE") -> StackSummary:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    return StackSummary(name=name, status=status, created=created, updated=created, description=None)


class StackScreenApp(App[None]):
    """Minimal harness that opens the stack list screen directly."""

    def __init__(self: Self, gateway: FakeCloudFormationGateway) -> None:
        super().__init__()
        self.gateway = gateway

    def on_mount(self: Self) -> None:
        self.push_screen(StackListScreen(self.gateway))


async def _settle(app: App[None]) -> None:
    """Wait for the fetch worker and let its messages be processed."""
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_renders_one_row_per_stack_sorted_input_preserved() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("prod-api"), _stack("prod-network")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "prod-api"
        assert table.get_row_at(1)[0] == "prod-network"


@pytest.mark.asyncio
async def test_count_header_shows_total() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("a"), _stack("b"), _stack("c")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert "3 stacks" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_status_cell_is_styled() -> None:
    gateway = FakeCloudFormationGateway(
        stacks=[_stack("ok"), _stack("bad", status="ROLLBACK_COMPLETE")],
    )
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)
        ok_cell = table.get_row_at(0)[1]
        bad_cell = table.get_row_at(1)[1]

        assert isinstance(ok_cell, Text)
        assert str(ok_cell.style) == "green"
        assert isinstance(bad_cell, Text)
        assert str(bad_cell.style) == "red"


@pytest.mark.asyncio
async def test_escape_pops_back() -> None:
    app = StackScreenApp(FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert isinstance(app.screen, StackListScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, StackListScreen)


@pytest.mark.asyncio
async def test_filter_narrows_rows_live() -> None:
    gateway = FakeCloudFormationGateway(
        stacks=[_stack("prod-api"), _stack("prod-network"), _stack("staging-api")],
    )
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert "2 of 3 stacks" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_filter_is_case_insensitive() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("Prod-API"), _stack("staging")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 1


@pytest.mark.asyncio
async def test_escape_clears_filter_before_going_back() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("prod-api"), _stack("staging-api")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, StackListScreen)  # still here: escape only cleared the filter
        assert app.screen.query_one("#filter", Input).value == ""
        assert app.screen.query_one(DataTable).row_count == 2
        assert app.screen.query_one(DataTable).has_focus

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, StackListScreen)


@pytest.mark.asyncio
async def test_refresh_refetches_and_updates_rows() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("alpha")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert app.screen.query_one(DataTable).row_count == 1

        gateway.stacks = [_stack("alpha"), _stack("beta")]
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert gateway.calls == 2
        assert app.screen.query_one(DataTable).row_count == 2


@pytest.mark.asyncio
async def test_cursor_stays_on_same_stack_after_refresh() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("alpha"), _stack("beta"), _stack("gamma")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        await pilot.press("down")  # cursor: alpha -> beta
        await pilot.pause()

        gateway.stacks = [_stack("alnew"), _stack("alpha"), _stack("beta"), _stack("gamma")]
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.get_row_at(table.cursor_row)[0] == "beta"
