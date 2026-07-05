"""Tests for the confirmation modal."""

from typing import Self

import pytest
from textual.app import App
from textual.widgets import Static

from awst.screens.confirm import ConfirmScreen


class ConfirmApp(App[None]):
    """Harness that opens the confirmation modal and records the answer."""

    def __init__(self: Self) -> None:
        super().__init__()
        self.answers: list[bool | None] = []

    def on_mount(self: Self) -> None:
        self.push_screen(ConfirmScreen("Delete stack alpha? This cannot be undone."), self.answers.append)


@pytest.mark.asyncio
async def test_shows_the_question() -> None:
    app = ConfirmApp()

    async with app.run_test() as pilot:
        await pilot.pause()

        assert "Delete stack alpha?" in str(app.screen.query_one("#question", Static).content)


@pytest.mark.asyncio
async def test_y_key_confirms() -> None:
    app = ConfirmApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        assert app.answers == [True]


@pytest.mark.asyncio
async def test_n_key_cancels() -> None:
    app = ConfirmApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()

        assert app.answers == [False]


@pytest.mark.asyncio
async def test_escape_cancels() -> None:
    app = ConfirmApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.answers == [False]


@pytest.mark.asyncio
async def test_yes_button_confirms() -> None:
    app = ConfirmApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#yes")
        await pilot.pause()

        assert app.answers == [True]


@pytest.mark.asyncio
async def test_no_button_cancels() -> None:
    app = ConfirmApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#no")
        await pilot.pause()

        assert app.answers == [False]
