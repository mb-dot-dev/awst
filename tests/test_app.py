"""Tests for the app shell and home-screen navigation."""

import pytest
from textual.widgets import DataTable, OptionList

from awst.app import AwstApp
from awst.screens.buckets import BucketListScreen
from awst.screens.functions import FunctionListScreen
from awst.screens.home import HomeScreen
from awst.screens.queues import QueueListScreen
from awst.screens.stack_detail import StackDetailScreen
from awst.screens.stacks import StackListScreen
from tests.fakes import (
    FakeCloudFormationGateway,
    FakeLambdaGateway,
    FakeS3Gateway,
    FakeSqsGateway,
    make_bucket,
    make_detail,
    make_function,
    make_queue,
    make_stack,
)


@pytest.mark.asyncio
async def test_home_screen_lists_all_services_enabled() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        options = app.screen.query_one(OptionList)

        assert isinstance(app.screen, HomeScreen)
        assert options.option_count == 4
        assert options.get_option("cloudformation").disabled is False
        assert options.get_option("s3").disabled is False
        assert options.get_option("lambda").disabled is False
        assert options.get_option("sqs").disabled is False


@pytest.mark.asyncio
async def test_navigation_reaches_sqs_and_wraps() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        options = app.screen.query_one(OptionList)
        assert options.highlighted == 0

        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()
        assert options.highlighted == 3  # sqs, now enabled

        await pilot.press("down")
        await pilot.pause()
        assert options.highlighted == 0  # wraps to the top


@pytest.mark.asyncio
async def test_enter_opens_stack_list_and_escape_returns_home() -> None:
    gateway = FakeCloudFormationGateway(stacks=[make_stack("prod-api")])
    app = AwstApp(cloudformation_gateway=gateway)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, StackListScreen)
        assert app.screen.query_one(DataTable).row_count == 1

        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, HomeScreen)


@pytest.mark.asyncio
async def test_selecting_s3_opens_bucket_list() -> None:
    app = AwstApp(
        cloudformation_gateway=FakeCloudFormationGateway(),
        s3_gateway=FakeS3Gateway(buckets=[make_bucket("assets")]),
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")  # highlight s3
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, BucketListScreen)
        assert app.screen.query_one(DataTable).row_count == 1


@pytest.mark.asyncio
async def test_selecting_lambda_opens_function_list() -> None:
    app = AwstApp(
        cloudformation_gateway=FakeCloudFormationGateway(),
        lambda_gateway=FakeLambdaGateway(functions=[make_function("resize-images")]),
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")  # s3
        await pilot.press("down")  # lambda
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, FunctionListScreen)
        assert app.screen.query_one(DataTable).row_count == 1


@pytest.mark.asyncio
async def test_selecting_sqs_opens_queue_list() -> None:
    app = AwstApp(
        cloudformation_gateway=FakeCloudFormationGateway(),
        sqs_gateway=FakeSqsGateway(queues=[make_queue("orders")]),
    )

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")  # s3
        await pilot.press("down")  # lambda
        await pilot.press("down")  # sqs
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, QueueListScreen)
        assert app.screen.query_one(DataTable).row_count == 1


@pytest.mark.asyncio
async def test_enter_twice_drills_from_home_into_stack_details() -> None:
    gateway = FakeCloudFormationGateway(stacks=[make_stack("prod-api")], detail=make_detail())
    app = AwstApp(cloudformation_gateway=gateway)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, StackDetailScreen)


@pytest.mark.asyncio
async def test_q_quits_from_home() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()

    assert app.return_code == 0
