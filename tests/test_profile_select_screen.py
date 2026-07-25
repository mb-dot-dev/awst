"""Tests for the startup profile selector."""

import os
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList, Static

from awst.app import AwstApp
from awst.screens.home import HomeScreen
from awst.screens.profiles import ProfileSelectScreen
from tests.fakes import FakeCloudFormationGateway

_CONFIG = """\
[profile dev]
sso_start_url = https://legacy.awsapps.com/start
sso_region = eu-west-1

[profile prod]
region = eu-west-1
"""


def _write_config() -> None:
    Path(os.environ["AWS_CONFIG_FILE"]).write_text(_CONFIG)


@pytest.mark.asyncio
async def test_picker_shows_when_no_profile_is_active() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ProfileSelectScreen)
        options = app.screen.query_one(OptionList)
        assert options.option_count == 2
        assert options.get_option("dev") is not None
        assert options.get_option("prod") is not None


@pytest.mark.asyncio
async def test_selecting_a_profile_sets_it_and_opens_home() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # first option: dev
        await pilot.pause()

        assert os.environ["AWS_PROFILE"] == "dev"
        assert isinstance(app.screen, HomeScreen)
        assert app.sub_title == "dev @ eu-west-1"


@pytest.mark.asyncio
async def test_picker_skipped_when_profile_env_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config()
    monkeypatch.setenv("AWS_PROFILE", "prod")
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()

        assert isinstance(app.screen, HomeScreen)
        assert app.sub_title == "prod @ eu-west-1"


@pytest.mark.asyncio
async def test_picker_skipped_when_no_profiles_exist() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()

        assert isinstance(app.screen, HomeScreen)
        assert app.sub_title == "eu-west-1"


@pytest.mark.asyncio
async def test_typing_narrows_the_profiles() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "r", "o")
        await pilot.pause()

        options = app.screen.query_one(OptionList)
        assert options.option_count == 1
        assert options.get_option_at_index(0).id == "prod"
        assert str(app.screen.query_one("#prompt", Static).content) == "1 of 2 profiles"


@pytest.mark.asyncio
async def test_enter_selects_the_filtered_profile() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "r", "o")
        await pilot.press("enter")
        await pilot.pause()

        assert os.environ["AWS_PROFILE"] == "prod"
        assert isinstance(app.screen, HomeScreen)


@pytest.mark.asyncio
async def test_escape_clears_the_filter() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "r", "o")
        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, ProfileSelectScreen)
        assert app.screen.query_one("#filter", Input).value == ""
        assert app.screen.query_one(OptionList).option_count == 2
        assert str(app.screen.query_one("#prompt", Static).content) == "Select an AWS profile"


@pytest.mark.asyncio
async def test_no_matches_reports_it_and_enter_does_nothing() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("z", "z", "z")
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ProfileSelectScreen)
        assert app.screen.query_one(OptionList).option_count == 0
        assert str(app.screen.query_one("#prompt", Static).content) == "no profiles match"


@pytest.mark.asyncio
async def test_ctrl_q_quits_from_picker() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()

    assert app.return_code == 0
