"""Tests for the SSO login modal."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Self
import webbrowser

import pytest
from textual.app import App
from textual.widgets import Static
from textual.worker import WorkerCancelled, WorkerFailed

from awst.aws.models import AwsError
from awst.screens.sso_login import SsoLoginScreen
from tests.fakes import FakeSsoLoginGateway, make_device_authorization, make_sso_config

if TYPE_CHECKING:
    from textual.pilot import Pilot


@pytest.fixture(autouse=True)
def opened_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record browser launches instead of opening a real browser."""
    urls: list[str] = []
    monkeypatch.setattr(webbrowser, "open", urls.append)
    return urls


class SsoModalApp(App[None]):
    """Harness that opens the login modal directly and records its result."""

    def __init__(self: Self, gateway: FakeSsoLoginGateway) -> None:
        super().__init__()
        self.gateway = gateway
        self.results: list[bool | None] = []

    def on_mount(self: Self) -> None:
        self.push_screen(SsoLoginScreen(self.gateway, make_sso_config()), self.results.append)


async def _until_dismissed(app: SsoModalApp, pilot: Pilot[None]) -> None:
    """Let the two chained workers (start, poll) run to completion.

    A worker that raises (e.g. an expired authorization or a poll error) leaves
    `wait_for_complete` raising WorkerFailed/WorkerCancelled even though the screen's
    own `on_worker_state_changed` handles the error and dismisses the modal just fine;
    that failure path is exercised via `app.results`, not via this helper's return.
    """
    for _ in range(100):
        with contextlib.suppress(WorkerFailed, WorkerCancelled):
            await app.workers.wait_for_complete()
        await pilot.pause()
        if app.results:
            return
    pytest.fail("modal never dismissed")


async def _until_code_shown(app: SsoModalApp, pilot: Pilot[None]) -> None:
    for _ in range(100):
        await pilot.pause()
        if str(app.screen.query_one("#code", Static).content):
            return
    pytest.fail("device code never rendered")


@pytest.mark.asyncio
async def test_shows_code_and_url_and_opens_browser(opened_urls: list[str]) -> None:
    authorization = make_device_authorization(interval=60)  # long interval: modal stays up
    gateway = FakeSsoLoginGateway(authorization=authorization, pending_polls=10**6)
    app = SsoModalApp(gateway)

    async with app.run_test() as pilot:
        await _until_code_shown(app, pilot)

        assert "ABCD-EFGH" in str(app.screen.query_one("#code", Static).content)
        assert str(app.screen.query_one("#url", Static).content) == authorization.verification_uri_complete
        assert opened_urls == [authorization.verification_uri_complete]

        # The content being set is not enough: a zero-size widget renders nothing.
        await pilot.pause()
        code_widget = app.screen.query_one("#code", Static)
        assert code_widget.region.width > 0
        assert code_widget.region.height > 0
        assert "ABCD-EFGH" in app.export_screenshot()


@pytest.mark.asyncio
async def test_successful_login_caches_token_and_dismisses_true() -> None:
    gateway = FakeSsoLoginGateway(pending_polls=2)
    app = SsoModalApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert app.results == [True]
    assert gateway.poll_calls == 3
    assert len(gateway.cached) == 1


@pytest.mark.asyncio
async def test_expired_authorization_dismisses_false_without_polling() -> None:
    gateway = FakeSsoLoginGateway(authorization=make_device_authorization(expires_in_s=-1))
    app = SsoModalApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert app.results == [False]
    assert gateway.poll_calls == 0
    assert gateway.cached == []


@pytest.mark.asyncio
async def test_start_failure_notifies_and_dismisses_false(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeSsoLoginGateway(start_error=AwsError("denied"))
    app = SsoModalApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert app.results == [False]
    assert toasts == ["denied"]


@pytest.mark.asyncio
async def test_poll_failure_dismisses_false() -> None:
    gateway = FakeSsoLoginGateway(poll_error=AwsError("expired"))
    app = SsoModalApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert app.results == [False]
    assert gateway.cached == []


@pytest.mark.asyncio
async def test_escape_cancels_and_dismisses_false() -> None:
    gateway = FakeSsoLoginGateway(authorization=make_device_authorization(interval=60), pending_polls=10**6)
    app = SsoModalApp(gateway)

    async with app.run_test() as pilot:
        await _until_code_shown(app, pilot)
        await pilot.press("escape")
        await pilot.pause()

    assert app.results == [False]
    assert gateway.cached == []
