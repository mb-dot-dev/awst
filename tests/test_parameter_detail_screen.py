"""Tests for the SSM parameter detail screen."""

import threading
from typing import Self

import pytest
from textual.app import App
from textual.widgets import Static

from awst.aws.models import AwsError, ParameterSummary
from awst.screens.parameter_detail import ParameterDetailScreen
from tests.fakes import FakeSsmGateway, make_parameter, make_parameter_detail

_MASK = "••••••••"


class DetailScreenApp(App[None]):
    """Minimal harness that opens the parameter detail screen directly."""

    def __init__(self: Self, gateway: FakeSsmGateway, summary: ParameterSummary | None = None) -> None:
        super().__init__()
        self.gateway = gateway
        self.summary = summary or make_parameter("/app/prod/api-key", param_type="SecureString", tier="Advanced")

    def on_mount(self: Self) -> None:
        self.push_screen(ParameterDetailScreen(self.gateway, self.summary))


async def _settle(app: App[None]) -> None:
    """Wait for the fetch worker and let its messages be processed."""
    await app.workers.wait_for_complete()


def _overview(app: App[None]) -> str:
    return str(app.screen.query_one("#overview-info", Static).content)


def _value(app: App[None]) -> str:
    return str(app.screen.query_one("#value", Static).content)


@pytest.mark.asyncio
async def test_overview_shows_type_version_data_type_and_arn() -> None:
    app = DetailScreenApp(FakeSsmGateway(detail=make_parameter_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert "SecureString" in _overview(app)
        assert "Version    3" in _overview(app)
        assert "Data type  text" in _overview(app)
        assert ":parameter/app/prod/api-key" in _overview(app)
        assert "ago" in _overview(app)


@pytest.mark.asyncio
async def test_overview_shows_tier_from_the_summary() -> None:
    app = DetailScreenApp(FakeSsmGateway(detail=make_parameter_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert "Advanced" in _overview(app)


@pytest.mark.asyncio
async def test_secure_string_value_starts_masked() -> None:
    app = DetailScreenApp(FakeSsmGateway(detail=make_parameter_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert _value(app) == _MASK


@pytest.mark.asyncio
async def test_s_reveals_then_hides_the_value() -> None:
    app = DetailScreenApp(FakeSsmGateway(detail=make_parameter_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("s")
        await pilot.pause()
        assert _value(app) == "s3cret"

        await pilot.press("s")
        await pilot.pause()
        assert _value(app) == _MASK


@pytest.mark.asyncio
async def test_plain_string_value_is_never_masked() -> None:
    summary = make_parameter("/app/prod/db-url")
    gateway = FakeSsmGateway(detail=make_parameter_detail("/app/prod/db-url", "String", "postgres://db"))
    app = DetailScreenApp(gateway, summary)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert _value(app) == "postgres://db"


@pytest.mark.asyncio
async def test_value_with_markup_like_syntax_renders_literally() -> None:
    # A value that looks like Rich console markup must render as literal text, not be
    # interpreted as styling — that's why _render_value wraps it in Text() rather than
    # passing a bare str to Static.update(). str(widget.content) can't tell the two apart
    # (Text.plain and a bare str compare equal), so this asserts on the rendered output,
    # where markup parsing actually happens.
    markup = "[bold red]x[/bold red]"
    summary = make_parameter("/app/prod/db-url")
    gateway = FakeSsmGateway(detail=make_parameter_detail("/app/prod/db-url", "String", markup))
    app = DetailScreenApp(gateway, summary)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        value_widget = app.screen.query_one("#value", Static)
        assert value_widget.region.width > 0
        assert value_widget.region.height > 0

        # export_screenshot renders spaces as "&#160;" (non-breaking space) SVG entities.
        screenshot = app.export_screenshot().replace("&#160;", " ")

        assert markup in screenshot


@pytest.mark.asyncio
async def test_refresh_refetches_and_remasks() -> None:
    gateway = FakeSsmGateway(detail=make_parameter_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert _value(app) == "s3cret"

        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert gateway.detail_calls == ["/app/prod/api-key", "/app/prod/api-key"]
        assert _value(app) == _MASK


@pytest.mark.asyncio
async def test_failed_refresh_remasks_immediately_instead_of_leaving_plaintext() -> None:
    # Regression test: action_refresh used to set _revealed = False without re-rendering,
    # so a *failed* refresh left the plaintext value on screen (re-masking only happened on
    # the success path, when _render_detail ran). This reveals the value, fails the refetch,
    # and asserts the value is masked immediately rather than staying visible.
    gateway = FakeSsmGateway(detail=make_parameter_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert _value(app) == "s3cret"

        gateway.detail_error = AwsError("boom")
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert _value(app) == _MASK


@pytest.mark.asyncio
async def test_s_before_initial_load_does_not_reveal_or_crash() -> None:
    gate = threading.Event()
    app = DetailScreenApp(FakeSsmGateway(detail=make_parameter_detail(), detail_gate=gate))

    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()

        assert _value(app) == ""

        gate.set()
        await _settle(app)
        await pilot.pause()


@pytest.mark.asyncio
async def test_c_before_initial_load_does_not_copy_or_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    copied: list[str] = []

    def record_copy(self: App[None], text: str) -> None:
        copied.append(text)

    monkeypatch.setattr(App, "copy_to_clipboard", record_copy)
    gate = threading.Event()
    app = DetailScreenApp(FakeSsmGateway(detail=make_parameter_detail(), detail_gate=gate))

    async with app.run_test() as pilot:
        await pilot.press("c")
        await pilot.pause()

        assert copied == []

        gate.set()
        await _settle(app)
        await pilot.pause()


@pytest.mark.asyncio
async def test_copy_of_secure_string_names_it_as_decrypted(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    app = DetailScreenApp(FakeSsmGateway(detail=make_parameter_detail(param_type="SecureString")))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("c")
        await pilot.pause()

        assert toasts == ["Decrypted value copied to clipboard."]


@pytest.mark.asyncio
async def test_copy_of_plain_string_keeps_the_generic_wording(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    summary = make_parameter("/app/prod/db-url")
    gateway = FakeSsmGateway(detail=make_parameter_detail("/app/prod/db-url", "String", "postgres://db"))
    app = DetailScreenApp(gateway, summary)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("c")
        await pilot.pause()

        assert toasts == ["Value copied to clipboard."]


@pytest.mark.asyncio
async def test_c_copies_the_full_value_while_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    copied: list[str] = []

    # A def, not a lambda: ruff's ARG005 (unused lambda argument) is not waived for tests,
    # only ARG001. This mirrors record_notify in tests/test_stack_detail_screen.py.
    def record_copy(self: App[None], text: str) -> None:
        copied.append(text)

    monkeypatch.setattr(App, "copy_to_clipboard", record_copy)
    app = DetailScreenApp(FakeSsmGateway(detail=make_parameter_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("c")
        await pilot.pause()

        assert copied == ["s3cret"]
        assert _value(app) == _MASK


@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    gateway = FakeSsmGateway(detail_error=AwsError("Access Denied", hint="need kms:Decrypt"))
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.screen.query_one("#error", Static)

        assert panel.display is True
        assert "Access Denied" in str(panel.content)
        assert "kms:Decrypt" in str(panel.content)


@pytest.mark.asyncio
async def test_failure_after_a_successful_load_toasts_instead(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeSsmGateway(detail=make_parameter_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.detail_error = AwsError("boom")
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert any("boom" in toast for toast in toasts)
        assert app.screen.query_one("#error", Static).display is False


@pytest.mark.asyncio
async def test_escape_pops_back() -> None:
    app = DetailScreenApp(FakeSsmGateway(detail=make_parameter_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert isinstance(app.screen, ParameterDetailScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, ParameterDetailScreen)
