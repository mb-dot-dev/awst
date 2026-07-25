"""Tests for the region selection screen."""

from typing import Self

import pytest
from textual.app import App
from textual.widgets import Input, OptionList, Static

from awst.screens.regions import RegionSelectScreen

_REGIONS = ["eu-central-1", "eu-west-1", "us-east-1"]


class RegionApp(App[None]):
    """Harness that opens the region picker and records the answer."""

    def __init__(self: Self, current: str | None = "eu-west-1") -> None:
        super().__init__()
        self._current = current
        self.answers: list[str | None] = []

    def on_mount(self: Self) -> None:
        self.push_screen(RegionSelectScreen(list(_REGIONS), self._current), self.answers.append)


@pytest.mark.asyncio
async def test_lists_all_regions() -> None:
    app = RegionApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        options = app.screen.query_one(OptionList)

        assert options.option_count == 3
        assert options.get_option("eu-central-1") is not None
        assert options.get_option("us-east-1") is not None


@pytest.mark.asyncio
async def test_current_region_is_preselected() -> None:
    app = RegionApp(current="eu-west-1")

    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.screen.query_one(OptionList).highlighted == 1


@pytest.mark.asyncio
async def test_unknown_current_region_defaults_to_the_top() -> None:
    app = RegionApp(current=None)

    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.screen.query_one(OptionList).highlighted == 0


@pytest.mark.asyncio
async def test_enter_dismisses_with_the_highlighted_region() -> None:
    app = RegionApp(current="eu-west-1")

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.answers == ["eu-west-1"]


@pytest.mark.asyncio
async def test_escape_dismisses_with_none() -> None:
    app = RegionApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.answers == [None]


@pytest.mark.asyncio
async def test_typing_narrows_the_regions() -> None:
    app = RegionApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u", "s")
        await pilot.pause()

        options = app.screen.query_one(OptionList)
        assert options.option_count == 1
        assert options.get_option_at_index(0).id == "us-east-1"
        assert str(app.screen.query_one("#prompt", Static).content) == "1 of 3 regions"


@pytest.mark.asyncio
async def test_enter_dismisses_with_the_filtered_region() -> None:
    app = RegionApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u", "s")
        await pilot.press("enter")
        await pilot.pause()

        assert app.answers == ["us-east-1"]


@pytest.mark.asyncio
async def test_escape_clears_the_filter_before_cancelling() -> None:
    app = RegionApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u", "s")
        await pilot.press("escape")
        await pilot.pause()

        assert app.answers == []
        assert app.screen.query_one("#filter", Input).value == ""
        assert app.screen.query_one(OptionList).option_count == 3

        await pilot.press("escape")
        await pilot.pause()

        assert app.answers == [None]
