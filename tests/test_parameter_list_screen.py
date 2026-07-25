"""Tests for the SSM parameter list screen."""

from typing import Self

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from awst.aws.models import AwsError, Page
from awst.screens.parameter_detail import ParameterDetailScreen
from awst.screens.parameters import ParameterListScreen
from tests.fakes import FakeSsmGateway, make_parameter


class ParameterScreenApp(App[None]):
    """Minimal harness that opens the parameter list screen directly."""

    def __init__(self: Self, gateway: FakeSsmGateway) -> None:
        super().__init__()
        self.gateway = gateway

    def on_mount(self: Self) -> None:
        self.push_screen(ParameterListScreen(self.gateway))


async def _settle(app: App[None]) -> None:
    """Wait for the fetch worker and let its messages be processed."""
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_renders_one_row_per_parameter_with_name_type_and_tier() -> None:
    gateway = FakeSsmGateway(
        parameters=[
            make_parameter("/app/prod/api-key", param_type="SecureString", tier="Advanced"),
            make_parameter("/app/prod/db-url"),
        ],
    )
    app = ParameterScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert table.get_row_at(0)[:3] == ["/app/prod/api-key", "SecureString", "Advanced"]
        assert table.get_row_at(1)[:3] == ["/app/prod/db-url", "String", "Standard"]


@pytest.mark.asyncio
async def test_modified_column_renders_a_relative_age() -> None:
    gateway = FakeSsmGateway(parameters=[make_parameter("/app/prod/db-url")])
    app = ParameterScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).get_row_at(0)[3].endswith("ago")


@pytest.mark.asyncio
async def test_empty_region_renders_zero_rows_with_parameter_noun() -> None:
    gateway = FakeSsmGateway(parameters=[])
    app = ParameterScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 0
        assert "0 parameters" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_filter_narrows_rows_live() -> None:
    gateway = FakeSsmGateway(
        parameters=[
            make_parameter("/app/prod/db-url"),
            make_parameter("/app/prod/api-key"),
            make_parameter("/app/staging/db-url"),
        ],
    )
    app = ParameterScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"/prod/")
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 2
        assert "2 of 3 parameters" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    gateway = FakeSsmGateway(error=AwsError("no credentials", hint="run `aws sso login`"))
    app = ParameterScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.screen.query_one("#error", Static)

        assert panel.display is True
        assert "no credentials" in str(panel.content)
        assert app.screen.query_one(DataTable).display is False


@pytest.mark.asyncio
async def test_enter_on_row_opens_the_detail_screen() -> None:
    gateway = FakeSsmGateway(parameters=[make_parameter("/app/prod/db-url")])
    app = ParameterScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("enter")
        await _settle(app)
        await pilot.pause()

        assert isinstance(app.screen, ParameterDetailScreen)
        assert gateway.detail_calls == ["/app/prod/db-url"]


@pytest.mark.asyncio
async def test_renders_rows_sorted_by_name_even_when_gateway_order_differs() -> None:
    gateway = FakeSsmGateway(parameters=[make_parameter("/shared/vpc-id"), make_parameter("/app/prod/db-url")])
    app = ParameterScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.get_row_at(0)[0] == "/app/prod/db-url"
        assert table.get_row_at(1)[0] == "/shared/vpc-id"


@pytest.mark.asyncio
async def test_m_appends_and_resorts_the_next_page() -> None:
    first = Page(items=(make_parameter("/shared/vpc-id"),), next_token="t1")
    second = Page(items=(make_parameter("/app/prod/db-url"),), next_token=None)
    gateway = FakeSsmGateway(pages={None: first, "t1": second})
    app = ParameterScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert str(app.screen.query_one("#count", Static).content) == "1+ parameter"

        await pilot.press("m")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert gateway.next_tokens == [None, "t1"]
        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "/app/prod/db-url"
        assert table.get_row_at(1)[0] == "/shared/vpc-id"


@pytest.mark.asyncio
async def test_filter_fetches_remaining_pages_to_find_matches_beyond_the_first_page() -> None:
    first = Page(items=(make_parameter("/shared/vpc-id"),), next_token="t1")
    second = Page(items=(make_parameter("/app/prod/db-url"),), next_token=None)
    gateway = FakeSsmGateway(pages={None: first, "t1": second})
    app = ParameterScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"db-url")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert gateway.next_tokens == [None, "t1"]
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "/app/prod/db-url"
