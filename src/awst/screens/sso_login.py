"""Modal that walks the user through an AWS SSO device login."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
import time
from typing import TYPE_CHECKING, ClassVar, Protocol, Self, cast
import webbrowser

from textual import work
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Static
from textual.worker import WorkerState, get_current_worker

from awst.aws.models import AwsError, DeviceAuthorization, SlowDownError, SsoToken

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.worker import Worker

    from awst.aws.models import SsoConfig

_SLOW_DOWN_INCREMENT_S = 5
_CANCEL_POLL_S = 0.1


class SsoAuthorizer(Protocol):
    """The slice of the SSO login gateway this screen needs."""

    def start_device_authorization(self: Self, config: SsoConfig) -> DeviceAuthorization: ...

    def poll_token(self: Self, authorization: DeviceAuthorization) -> SsoToken | None: ...

    def write_token_cache(
        self: Self,
        config: SsoConfig,
        authorization: DeviceAuthorization,
        token: SsoToken,
    ) -> None: ...


class SsoLoginScreen(ModalScreen[bool]):
    """Run the SSO OIDC device flow; dismisses True once a token is cached."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    SsoLoginScreen { align: center middle; }
    #dialog {
        width: auto;
        max-width: 80;
        height: auto;
        padding: 1 2;
        background: $panel;
        border: thick $primary;
    }
    #dialog Static { width: auto; }
    #title { text-style: bold; }
    #status { color: $text-muted; margin-top: 1; }
    #code { text-style: bold; margin-top: 1; }
    """

    def __init__(self: Self, gateway: SsoAuthorizer, config: SsoConfig) -> None:
        super().__init__()
        self._gateway = gateway
        self._config = config
        self._authorization: DeviceAuthorization | None = None

    def compose(self: Self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("AWS SSO login", id="title")
            yield Static("Contacting AWS SSO…", id="status")
            yield Static(id="code")
            yield Static(id="url")
        yield Footer()

    def on_mount(self: Self) -> None:
        self._start_authorization()

    @work(thread=True, exclusive=True, group="sso-start", exit_on_error=False)
    def _start_authorization(self: Self) -> DeviceAuthorization:
        return self._gateway.start_device_authorization(self._config)

    @work(thread=True, exclusive=True, group="sso-poll", exit_on_error=False)
    def _await_token(self: Self, authorization: DeviceAuthorization) -> SsoToken:
        worker = get_current_worker()
        interval = authorization.interval
        while not worker.is_cancelled:
            if datetime.now(tz=UTC) >= authorization.expires_at:
                message = "The login request expired before it was approved."
                raise AwsError(message, hint="Press l to start again.")
            self._sleep(worker, interval)
            try:
                token = self._gateway.poll_token(authorization)
            except SlowDownError:
                interval += _SLOW_DOWN_INCREMENT_S
                continue
            if token is not None:
                return token
        message = "The login was cancelled."  # unreachable for the UI: cancellation discards the worker's result
        raise AwsError(message)

    def _sleep(self: Self, worker: Worker[SsoToken], seconds: int) -> None:
        deadline = time.monotonic() + seconds
        while not worker.is_cancelled and time.monotonic() < deadline:
            time.sleep(_CANCEL_POLL_S)

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.SUCCESS and event.worker.name == "_start_authorization":
            self._show_authorization(cast("DeviceAuthorization", event.worker.result))
        elif event.state == WorkerState.SUCCESS and event.worker.name == "_await_token":
            self._finish_login(cast("SsoToken", event.worker.result))
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, AwsError):
                self._fail(error)
            elif error is not None:
                raise error

    def _show_authorization(self: Self, authorization: DeviceAuthorization) -> None:
        self._authorization = authorization
        self.query_one("#status", Static).update("Approve the request in your browser, then return here.")
        self.query_one("#code", Static).update(f"Code: {authorization.user_code}")
        self.query_one("#url", Static).update(authorization.verification_uri_complete)
        with contextlib.suppress(webbrowser.Error):  # the URL on screen is enough (e.g. headless, SSH)
            webbrowser.open(authorization.verification_uri_complete)
        self._await_token(authorization)

    def _finish_login(self: Self, token: SsoToken) -> None:
        if self._authorization is None:  # pragma: no cover — polling only starts after authorization
            return
        try:
            self._gateway.write_token_cache(self._config, self._authorization, token)
        except OSError as error:
            message = f"Could not write the SSO token cache: {error}"
            self._fail(AwsError(message))
            return
        self.dismiss(result=True)

    def _fail(self: Self, error: AwsError) -> None:
        message = error.message if error.hint is None else f"{error.message} ({error.hint})"
        self.notify(message, title="Login failed", severity="error")
        self.dismiss(result=False)

    def action_cancel(self: Self) -> None:
        self.dismiss(result=False)
