"""Tests for the CloudFormation stack detail screen."""

from datetime import UTC, datetime
from typing import Self

import pytest
from rich.text import Text
from textual.app import App
from textual.widgets import DataTable, Static

from awst.aws.models import (
    AwsError,
    StackDetail,
    StackEvent,
    StackNotFoundError,
    StackOutput,
    StackParameter,
    StackResource,
)
from awst.screens.confirm import ConfirmScreen
from awst.screens.stack_detail import StackDetailScreen
from tests.fakes import FakeCloudFormationGateway

CREATED = datetime(2026, 1, 1, tzinfo=UTC)
PARAMETERS = (StackParameter(key="Env", value="prod"),)
OUTPUTS = (StackOutput(key="Url", value="https://example.com", description="endpoint"),)


def _detail(
    parameters: tuple[StackParameter, ...] = PARAMETERS,
    outputs: tuple[StackOutput, ...] = OUTPUTS,
) -> StackDetail:
    return StackDetail(
        name="alpha",
        stack_id="arn:aws:cloudformation:eu-west-1:123456789012:stack/alpha/abc",
        status="CREATE_COMPLETE",
        status_reason=None,
        description="a test stack",
        created=CREATED,
        updated=CREATED,
        parameters=parameters,
        outputs=outputs,
        resources=(
            StackResource(
                logical_id="Topic",
                physical_id="arn:aws:sns:eu-west-1:123456789012:topic",
                resource_type="AWS::SNS::Topic",
                status="CREATE_COMPLETE",
            ),
        ),
        events=(
            StackEvent(
                timestamp=CREATED,
                logical_id="alpha",
                resource_type="AWS::CloudFormation::Stack",
                status="CREATE_COMPLETE",
                reason=None,
            ),
            StackEvent(
                timestamp=CREATED,
                logical_id="Topic",
                resource_type="AWS::SNS::Topic",
                status="CREATE_IN_PROGRESS",
                reason="Resource creation Initiated",
            ),
        ),
    )


class DetailScreenApp(App[None]):
    """Minimal harness that opens the stack detail screen directly."""

    def __init__(self: Self, gateway: FakeCloudFormationGateway) -> None:
        super().__init__()
        self.gateway = gateway

    def on_mount(self: Self) -> None:
        self.push_screen(StackDetailScreen(self.gateway, "alpha"))


async def _settle(app: App[None]) -> None:
    """Wait for workers and let their messages be processed."""
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_overview_shows_status_description_and_stack_id() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        info = str(app.screen.query_one("#overview-info", Static).content)

        assert "CREATE_COMPLETE" in info
        assert "a test stack" in info
        assert "stack/alpha/abc" in info


@pytest.mark.asyncio
async def test_overview_lists_parameters_and_outputs() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        parameters = app.screen.query_one("#parameters", DataTable)
        outputs = app.screen.query_one("#outputs", DataTable)

        assert parameters.get_row_at(0) == ["Env", "prod"]
        assert outputs.get_row_at(0) == ["Url", "https://example.com", "endpoint"]


@pytest.mark.asyncio
async def test_overview_shows_none_for_missing_parameters_and_outputs() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail(parameters=(), outputs=())))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one("#parameters", DataTable).display is False
        assert app.screen.query_one("#parameters-none", Static).display is True
        assert app.screen.query_one("#outputs", DataTable).display is False
        assert app.screen.query_one("#outputs-none", Static).display is True


@pytest.mark.asyncio
async def test_resources_tab_lists_resources_with_styled_status() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        row = app.screen.query_one("#resources", DataTable).get_row_at(0)

        assert row[0] == "Topic"
        assert row[2] == "AWS::SNS::Topic"
        assert isinstance(row[3], Text)
        assert str(row[3]) == "CREATE_COMPLETE"
        assert str(row[3].style) == "green"


@pytest.mark.asyncio
async def test_events_tab_lists_events_in_gateway_order() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        events = app.screen.query_one("#events", DataTable)

        assert events.row_count == 2
        assert events.get_row_at(0)[1] == "alpha"
        assert events.get_row_at(1)[1] == "Topic"
        assert events.get_row_at(1)[4] == "Resource creation Initiated"


@pytest.mark.asyncio
async def test_refresh_refetches_detail() -> None:
    gateway = FakeCloudFormationGateway(detail=_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert gateway.detail_calls == ["alpha", "alpha"]


@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    gateway = FakeCloudFormationGateway(detail_error=AwsError("no credentials", hint="run `aws sso login`"))
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.screen.query_one("#error", Static)

        assert panel.display is True
        assert "no credentials" in str(panel.content)
        assert "aws sso login" in str(panel.content)


@pytest.mark.asyncio
async def test_retry_after_initial_failure_recovers() -> None:
    gateway = FakeCloudFormationGateway(detail_error=AwsError("boom"))
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.detail_error = None
        gateway.detail = _detail()
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one("#error", Static).display is False
        assert "CREATE_COMPLETE" in str(app.screen.query_one("#overview-info", Static).content)


@pytest.mark.asyncio
async def test_escape_pops_back() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert isinstance(app.screen, StackDetailScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, StackDetailScreen)


@pytest.mark.asyncio
async def test_d_opens_confirmation_modal_naming_the_stack() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        assert "alpha" in str(app.screen.query_one("#question", Static).content)


@pytest.mark.asyncio
async def test_confirming_delete_calls_gateway_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeCloudFormationGateway(detail=_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await _settle(app)
        await pilot.pause()

        assert gateway.deleted == ["alpha"]
        assert any("Delete requested" in toast for toast in toasts)
        assert isinstance(app.screen, StackDetailScreen)  # stays put; refresh is manual


@pytest.mark.asyncio
async def test_cancelling_delete_does_not_call_gateway() -> None:
    gateway = FakeCloudFormationGateway(detail=_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("escape")
        await _settle(app)
        await pilot.pause()

        assert gateway.deleted == []
        assert isinstance(app.screen, StackDetailScreen)


@pytest.mark.asyncio
async def test_delete_failure_shows_error_toast(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeCloudFormationGateway(detail=_detail(), delete_error=AwsError("denied"))
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await _settle(app)
        await pilot.pause()

        assert gateway.deleted == []
        assert "denied" in toasts


@pytest.mark.asyncio
async def test_refresh_after_stack_deleted_notifies_and_pops_back(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeCloudFormationGateway(detail=_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.detail = None
        gateway.detail_error = StackNotFoundError("Stack alpha does not exist.")
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert not isinstance(app.screen, StackDetailScreen)
        assert any("no longer exists" in toast for toast in toasts)


@pytest.mark.asyncio
async def test_not_found_refresh_under_confirm_modal_does_not_pop_the_modal(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeCloudFormationGateway(detail=_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")  # confirm modal opens
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)

        gateway.detail = None
        gateway.detail_error = StackNotFoundError("Stack alpha does not exist.")
        screen = app.screen_stack[-2]
        assert isinstance(screen, StackDetailScreen)
        screen.action_refresh()  # refresh resolves while the modal is on top
        await pilot.pause()
        await _settle(app)
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)  # modal survived
        assert any("no longer exists" in toast for toast in toasts)


@pytest.mark.asyncio
async def test_refresh_failure_keeps_stale_detail_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeCloudFormationGateway(detail=_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.detail_error = AwsError("throttled")
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert isinstance(app.screen, StackDetailScreen)  # stayed put
        assert "throttled" in toasts
        assert "CREATE_COMPLETE" in str(app.screen.query_one("#overview-info", Static).content)  # stale data kept
