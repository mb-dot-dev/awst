"""Tests for the Lambda function list screen."""

from typing import Self

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from awst.aws.models import AwsError
from awst.screens.functions import FunctionListScreen
from tests.fakes import FakeLambdaGateway, make_function


class FunctionScreenApp(App[None]):
    """Minimal harness that opens the function list screen directly."""

    def __init__(self: Self, gateway: FakeLambdaGateway) -> None:
        super().__init__()
        self.gateway = gateway

    def on_mount(self: Self) -> None:
        self.push_screen(FunctionListScreen(self.gateway))


async def _settle(app: App[None]) -> None:
    """Wait for the fetch worker and let its messages be processed."""
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_renders_one_row_per_function_with_formatted_cells() -> None:
    gateway = FakeLambdaGateway(functions=[make_function("resize-images"), make_function("send-mail")])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "resize-images"
        assert table.get_row_at(0)[1] == "python3.14"
        assert table.get_row_at(0)[2] == "128 MB"
        assert table.get_row_at(0)[3] == "30s"


@pytest.mark.asyncio
async def test_image_function_renders_blank_runtime() -> None:
    gateway = FakeLambdaGateway(functions=[make_function("containerised", runtime="")])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).get_row_at(0)[1] == ""


@pytest.mark.asyncio
async def test_empty_account_renders_zero_rows_with_function_noun() -> None:
    gateway = FakeLambdaGateway(functions=[])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 0
        assert "0 functions" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_filter_narrows_rows_live() -> None:
    gateway = FakeLambdaGateway(
        functions=[make_function("prod-resize"), make_function("prod-mail"), make_function("staging-resize")],
    )
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 2
        assert "2 of 3 functions" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_escape_pops_back() -> None:
    app = FunctionScreenApp(FakeLambdaGateway())

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert isinstance(app.screen, FunctionListScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, FunctionListScreen)


@pytest.mark.asyncio
async def test_refresh_refetches_and_updates_rows() -> None:
    gateway = FakeLambdaGateway(functions=[make_function("resize-images")])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert app.screen.query_one(DataTable).row_count == 1

        gateway.functions = [make_function("resize-images"), make_function("send-mail")]
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert gateway.calls == 2
        assert app.screen.query_one(DataTable).row_count == 2


@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    gateway = FakeLambdaGateway(error=AwsError("no credentials", hint="run `aws sso login`"))
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.screen.query_one("#error", Static)

        assert panel.display is True
        assert "no credentials" in str(panel.content)
        assert app.screen.query_one(DataTable).display is False


@pytest.mark.asyncio
async def test_refresh_failure_keeps_stale_rows_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeLambdaGateway(functions=[make_function("resize-images")])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.error = AwsError("throttled")
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 1  # stale rows kept
        assert toasts == ["throttled"]
        assert str(app.screen.query_one("#count", Static).content) == "1 function"


@pytest.mark.asyncio
async def test_enter_on_row_does_nothing() -> None:
    gateway = FakeLambdaGateway(functions=[make_function("resize-images")])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, FunctionListScreen)  # no detail screen yet
