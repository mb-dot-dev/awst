# SSM Parameter Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a detail screen for a single SSM parameter, opened with Enter from the parameter list, showing the parameter's value — masked when it is a `SecureString` until the user reveals it.

**Architecture:** Follows the existing gateway + screen split. `SsmGateway` gains a `get_parameter` call returning a new `ParameterDetail` model; a new `ParameterDetailScreen` renders it, taking the already-loaded `ParameterSummary` alongside the gateway so `Tier` (which `get_parameter` does not return) needs no second API call. `parameters.py` gains a row-selected handler, exactly as `stacks.py` opens `stack_detail.py`.

**Tech Stack:** Python 3.14, Textual 8.x, boto3/botocore, pytest + pytest-asyncio, moto + botocore `Stubber`, ruff + ty, uv.

**Spec:** `docs/superpowers/specs/2026-07-25-ssm-parameter-detail-design.md`

## Global Constraints

- Branch: `ssm-parameter-details`. All commits land there.
- Requires Python >=3.14; every command runs through `uv` (`uv run --frozen ...`) or the `Makefile`.
- Screens never import boto3/botocore. Gateways never import Textual.
- Ruff: 120-char lines, flake8-annotations (annotate every parameter and return, including `self: Self`), docstrings on every module/class/public function, `TYPE_CHECKING` imports for typing-only names.
- Type checking is `ty check`, run as part of `make lint`.
- Coverage gate: 75% minimum (`make coverage`).
- Commit message style matches the repo's history: imperative, sentence case, no `feat:`/`fix:` prefix.
- Completion gate for the whole plan: `make test` (lint + unit) passes.

---

### Task 1: `ParameterDetail` model and `SsmGateway.get_parameter`

**Files:**
- Modify: `src/awst/aws/models.py` (add after `ParameterSummary`, around line 130)
- Modify: `src/awst/aws/ssm.py`
- Test: `tests/test_ssm_gateway.py` (extend)

**Interfaces:**
- Consumes: `map_botocore_error` from `awst.aws.errors`, `AwsError` from `awst.aws.models` (both already used by this module).
- Produces:
  - `ParameterDetail(name: str, param_type: str, value: str, version: int, arn: str, data_type: str, modified: datetime)` — frozen, slotted dataclass in `awst.aws.models`.
  - `SsmGateway.get_parameter(self, name: str) -> ParameterDetail`.
  - `_to_detail(parameter: ParameterTypeDef) -> ParameterDetail` — module-level in `awst.aws.ssm`, tested directly like the existing `_to_summary`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ssm_gateway.py`:

```python
@mock_aws
def test_get_parameter_maps_every_field() -> None:
    _put("/app/prod/db-url")

    detail = _gateway().get_parameter("/app/prod/db-url")

    assert detail.name == "/app/prod/db-url"
    assert detail.param_type == "String"
    assert detail.value == "value"
    assert detail.version == 1
    assert detail.arn.endswith(":parameter/app/prod/db-url")
    assert detail.data_type == "text"
    assert detail.modified.tzinfo is not None


@mock_aws
def test_get_parameter_decrypts_secure_strings() -> None:
    _put("/app/prod/api-key", param_type="SecureString")

    detail = _gateway().get_parameter("/app/prod/api-key")

    # Without WithDecryption the API returns the value prefixed by its KMS key.
    assert detail.value == "value"
    assert detail.param_type == "SecureString"


def test_get_parameter_requests_decryption() -> None:
    client = boto3.client("ssm", region_name="eu-west-1")
    response = {
        "Parameter": {
            "Name": "/app/prod/api-key",
            "Type": "SecureString",
            "Value": "s3cret",
            "Version": 3,
            "ARN": "arn:aws:ssm:eu-west-1:123456789012:parameter/app/prod/api-key",
            "DataType": "text",
            "LastModifiedDate": datetime(2026, 1, 1, tzinfo=UTC),
        },
    }
    with Stubber(client) as stubber:
        stubber.add_response("get_parameter", response, {"Name": "/app/prod/api-key", "WithDecryption": True})

        detail = SsmGateway(client).get_parameter("/app/prod/api-key")

    assert detail.value == "s3cret"
    assert detail.version == 3


def test_to_detail_defaults_missing_optional_fields() -> None:
    detail = _to_detail({"Name": "/alpha", "LastModifiedDate": datetime(2026, 1, 1, tzinfo=UTC)})

    assert detail.param_type == ""
    assert detail.value == ""
    assert detail.version == 0
    assert detail.arn == ""
    assert detail.data_type == ""


def test_get_parameter_maps_access_denied_to_aws_error() -> None:
    client = boto3.client("ssm", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "get_parameter",
            service_error_code="AccessDeniedException",
            service_message="not authorized to perform kms:Decrypt",
        )

        with pytest.raises(AwsError) as excinfo:
            SsmGateway(client).get_parameter("/app/prod/api-key")

    assert "kms:Decrypt" in excinfo.value.message


def test_get_parameter_maps_parameter_not_found_to_aws_error() -> None:
    client = boto3.client("ssm", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "get_parameter",
            service_error_code="ParameterNotFound",
            service_message="Parameter /gone not found.",
        )

        with pytest.raises(AwsError):
            SsmGateway(client).get_parameter("/gone")
```

Update the import at the top of the file from `from awst.aws.ssm import SsmGateway, _to_summary` to:

```python
from awst.aws.ssm import SsmGateway, _to_detail, _to_summary
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_ssm_gateway.py -v`
Expected: FAIL — `ImportError: cannot import name '_to_detail' from 'awst.aws.ssm'`

- [ ] **Step 3: Add the model**

In `src/awst/aws/models.py`, directly after the `ParameterSummary` dataclass:

```python
@dataclass(frozen=True, slots=True)
class ParameterDetail:
    """One SSM parameter, including its value. Unlike ParameterSummary, this does hold a secret."""

    name: str
    param_type: str  # "String", "StringList", or "SecureString"
    value: str  # decrypted for SecureString parameters
    version: int
    arn: str
    data_type: str  # "text", "aws:ec2:image", ...
    modified: datetime
```

- [ ] **Step 4: Add the gateway call**

In `src/awst/aws/ssm.py`, extend the `TYPE_CHECKING` block and the imports:

```python
from awst.aws.models import Page, ParameterDetail, ParameterSummary

if TYPE_CHECKING:
    from mypy_boto3_ssm import SSMClient
    from mypy_boto3_ssm.type_defs import ParameterMetadataTypeDef, ParameterTypeDef
```

Add this method to `SsmGateway`, after `list_parameters`:

```python
    def get_parameter(self: Self, name: str) -> ParameterDetail:
        """Return one parameter including its value, decrypting SecureString values.

        Raises AwsError for any credential, network, or API failure — including the
        AccessDeniedException raised when the caller cannot decrypt the parameter's KMS key.
        """
        try:
            response = self._client.get_parameter(Name=name, WithDecryption=True)
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        return _to_detail(response["Parameter"])
```

And this module-level function after `_to_summary`:

```python
def _to_detail(parameter: ParameterTypeDef) -> ParameterDetail:
    # Every get_parameter field except Name is optional in the API model, though all are
    # present in practice; default the cosmetic ones so a sparse response still renders.
    return ParameterDetail(
        name=parameter["Name"],
        param_type=parameter.get("Type", ""),
        value=parameter.get("Value", ""),
        version=parameter.get("Version", 0),
        arn=parameter.get("ARN", ""),
        data_type=parameter.get("DataType", ""),
        modified=parameter["LastModifiedDate"],
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_ssm_gateway.py tests/test_models.py -v`
Expected: PASS

- [ ] **Step 6: Lint**

Run: `make lint`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add src/awst/aws/models.py src/awst/aws/ssm.py tests/test_ssm_gateway.py
git commit -m "Add SSM get_parameter gateway call and ParameterDetail model"
```

---

### Task 2: `mask_value` formatting helper

**Files:**
- Modify: `src/awst/screens/formatting.py`
- Test: `tests/test_formatting.py` (extend)

**Interfaces:**
- Consumes: nothing.
- Produces: `mask_value(value: str, param_type: str, *, revealed: bool) -> str` in `awst.screens.formatting`.

The mask is a fixed eight bullets regardless of the value's length, so it does not leak how long the secret is. (Ruff accepts the `•` character in this codebase's configuration — verified.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_formatting.py`:

```python
def test_mask_value_hides_a_secure_string_when_not_revealed() -> None:
    assert mask_value("s3cret", "SecureString", revealed=False) == "••••••••"


def test_mask_value_shows_a_secure_string_when_revealed() -> None:
    assert mask_value("s3cret", "SecureString", revealed=True) == "s3cret"


def test_mask_value_never_masks_plain_types() -> None:
    assert mask_value("postgres://db", "String", revealed=False) == "postgres://db"
    assert mask_value("a,b,c", "StringList", revealed=False) == "a,b,c"


def test_mask_value_mask_width_does_not_depend_on_value_length() -> None:
    short = mask_value("x", "SecureString", revealed=False)
    long = mask_value("x" * 200, "SecureString", revealed=False)

    assert short == long
```

Add `mask_value` to the existing `from awst.screens.formatting import ...` line at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_formatting.py -v`
Expected: FAIL — `ImportError: cannot import name 'mask_value'`

- [ ] **Step 3: Implement**

In `src/awst/screens/formatting.py`, add the constant next to `_KIB`:

```python
_MASK = "•" * 8
```

and this function after `relative_age`:

```python
def mask_value(value: str, param_type: str, *, revealed: bool) -> str:
    """Hide a SecureString value behind a fixed-width mask unless it has been revealed.

    The mask's width is constant so it does not leak the secret's length.
    """
    if param_type == "SecureString" and not revealed:
        return _MASK
    return value
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_formatting.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/awst/screens/formatting.py tests/test_formatting.py
git commit -m "Add mask_value helper for hiding SecureString values"
```

---

### Task 3: `ParameterDetailScreen`

**Files:**
- Create: `src/awst/screens/parameter_detail.py`
- Create: `tests/test_parameter_detail_screen.py`
- Modify: `tests/fakes.py` (add `make_parameter_detail`; extend `FakeSsmGateway`)

**Interfaces:**
- Consumes: `ParameterDetail`, `ParameterSummary`, `AwsError` from `awst.aws.models`; `mask_value`, `relative_age` from `awst.screens.formatting`; `SsmGateway.get_parameter` from Task 1.
- Produces:
  - `ParameterInspector` — `Protocol` with `get_parameter(self, name: str) -> ParameterDetail`.
  - `ParameterDetailScreen(gateway: ParameterInspector, summary: ParameterSummary)` — a `Screen[None]`.
  - `make_parameter_detail(name: str = "/app/prod/api-key", param_type: str = "SecureString", value: str = "s3cret") -> ParameterDetail` in `tests.fakes`.
  - `FakeSsmGateway(..., detail: ParameterDetail | None = None, detail_error: AwsError | None = None)` with a `get_parameter` method and a `detail_calls: list[str]` attribute.

- [ ] **Step 1: Extend the fakes**

In `tests/fakes.py`, add `ParameterDetail` to the `from awst.aws.models import (...)` block, then add after `make_parameter`:

```python
def make_parameter_detail(
    name: str = "/app/prod/api-key",
    param_type: str = "SecureString",
    value: str = "s3cret",
) -> ParameterDetail:
    """A parameter detail with sensible defaults for detail-screen tests."""
    return ParameterDetail(
        name=name,
        param_type=param_type,
        value=value,
        version=3,
        arn=f"arn:aws:ssm:eu-west-1:123456789012:parameter{name}",
        data_type="text",
        modified=_CREATED,
    )
```

Then change `FakeSsmGateway.__init__` to accept and store the detail hooks, and add `get_parameter`:

```python
class FakeSsmGateway:
    """In-memory stand-in for the real SSM gateway."""

    def __init__(
        self: Self,
        parameters: list[ParameterSummary] | None = None,
        error: AwsError | None = None,
        pages: dict[str | None, Page[ParameterSummary]] | None = None,
        detail: ParameterDetail | None = None,
        detail_error: AwsError | None = None,
    ) -> None:
        self.parameters = parameters or []
        self.error = error
        self.pages = pages
        self.calls = 0
        self.next_tokens: list[str | None] = []
        self.detail = detail
        self.detail_error = detail_error
        self.detail_calls: list[str] = []

    def list_parameters(self: Self, next_token: str | None = None) -> Page[ParameterSummary]:
        self.calls += 1
        self.next_tokens.append(next_token)
        if self.error is not None:
            raise self.error
        if self.pages is not None:
            return self.pages.get(next_token, Page(items=(), next_token=None))
        return Page(items=tuple(self.parameters), next_token=None)

    def get_parameter(self: Self, name: str) -> ParameterDetail:
        self.detail_calls.append(name)
        if self.detail_error is not None:
            raise self.detail_error
        return self.detail if self.detail is not None else make_parameter_detail(name)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_parameter_detail_screen.py`:

```python
"""Tests for the SSM parameter detail screen."""

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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_parameter_detail_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awst.screens.parameter_detail'`

- [ ] **Step 4: Implement the screen**

Create `src/awst/screens/parameter_detail.py`:

```python
"""SSM parameter detail screen."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from rich.text import Text
from textual import work
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static
from textual.worker import Worker, WorkerState

from awst.aws.models import AwsError
from awst.screens.formatting import mask_value, relative_age

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType

    from awst.aws.models import ParameterDetail, ParameterSummary


class ParameterInspector(Protocol):
    """The slice of the SSM gateway this screen needs."""

    def get_parameter(self: Self, name: str) -> ParameterDetail: ...


class ParameterDetailScreen(Screen[None]):
    """Detail view of one SSM parameter; SecureString values stay masked until revealed."""

    TITLE = "Parameter details"

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "back", "Back"),
        ("r", "refresh", "Refresh"),
        ("s", "toggle_reveal", "Reveal/hide"),
        ("c", "copy", "Copy value"),
    ]

    DEFAULT_CSS = """
    #overview-info { height: auto; padding: 1 2 0 2; }
    .heading { height: 1; padding: 0 2; margin-top: 1; text-style: bold; }
    #value { height: auto; padding: 0 2; }
    #error { display: none; padding: 1 2; color: $text-error; }
    """

    def __init__(self: Self, gateway: ParameterInspector, summary: ParameterSummary) -> None:
        super().__init__()
        self._gateway = gateway
        self._summary = summary
        self._detail: ParameterDetail | None = None
        self._revealed = False
        self._loaded = False

    def compose(self: Self) -> ComposeResult:
        with VerticalScroll(id="body"):
            yield Static(id="overview-info")
            yield Static("Value", classes="heading")
            yield Static(id="value")
        yield Static(id="error")
        yield Footer()

    def on_mount(self: Self) -> None:
        self.sub_title = self._summary.name
        self.query_one("#body", VerticalScroll).loading = True
        self._fetch_detail()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_detail(self: Self) -> ParameterDetail:
        return self._gateway.get_parameter(self._summary.name)

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.SUCCESS:
            self._loaded = True
            self.query_one("#body", VerticalScroll).loading = False
            detail = event.worker.result
            if detail is not None:
                self._detail = detail
                self._render_detail(detail)
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, AwsError):
                self._show_error(error)
            elif error is not None:
                raise error

    def _show_error(self: Self, error: AwsError) -> None:
        body = self.query_one("#body", VerticalScroll)
        body.loading = False
        if self._loaded:
            message = error.message if error.hint is None else f"{error.message} ({error.hint})"
            self.notify(message, title="Refresh failed", severity="error")
            return
        body.display = False
        self.set_focus(None)
        panel = self.query_one("#error", Static)
        panel.update(error.message if error.hint is None else f"{error.message}\n{error.hint}")
        panel.display = True

    def _render_detail(self: Self, detail: ParameterDetail) -> None:
        now = datetime.now(tz=UTC)
        self.query_one("#overview-info", Static).update(_overview_text(detail, self._summary.tier, now))
        self._render_value()

    def _render_value(self: Self) -> None:
        if self._detail is None:
            return
        # Text(), not a bare str: values often contain square brackets that Rich would
        # otherwise parse as console markup.
        shown = mask_value(self._detail.value, self._detail.param_type, revealed=self._revealed)
        self.query_one("#value", Static).update(Text(shown))

    def action_back(self: Self) -> None:
        self.app.pop_screen()

    def action_refresh(self: Self) -> None:
        self._revealed = False
        self.query_one("#error", Static).display = False
        body = self.query_one("#body", VerticalScroll)
        body.display = True
        if not self._loaded:
            body.loading = True
        self._fetch_detail()

    def action_toggle_reveal(self: Self) -> None:
        if self._detail is None:
            return
        self._revealed = not self._revealed
        self._render_value()

    def action_copy(self: Self) -> None:
        if self._detail is None:
            return
        self.app.copy_to_clipboard(self._detail.value)
        self.notify("Value copied to clipboard.", title=self._summary.name)


def _overview_text(detail: ParameterDetail, tier: str, now: datetime) -> str:
    return (
        f"Type       {detail.param_type}\n"
        f"Tier       {tier}\n"
        f"Version    {detail.version}\n"
        f"Data type  {detail.data_type}\n"
        f"Modified   {relative_age(detail.modified, now)}\n"
        f"ARN        {detail.arn}"
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_parameter_detail_screen.py -v`
Expected: PASS

If `test_refresh_refetches_and_remasks` fails because the refresh worker's result is discarded, check that `_fetch_detail` is declared `exclusive=True` — the second call must be allowed to replace the first, which has already finished.

- [ ] **Step 6: Lint**

Run: `make lint`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add src/awst/screens/parameter_detail.py tests/test_parameter_detail_screen.py tests/fakes.py
git commit -m "Add SSM parameter detail screen with masked SecureString values"
```

---

### Task 4: Open the detail screen from the parameter list

**Files:**
- Modify: `src/awst/screens/parameters.py`
- Modify: `tests/test_parameter_list_screen.py:113-125` (replace `test_enter_on_row_does_nothing`)
- Modify: `CLAUDE.md` (the implemented-services sentence in "Project overview")

**Interfaces:**
- Consumes: `ParameterDetailScreen`, `ParameterInspector` from Task 3; `ResourceListScreen._all_items` (the base class's list of every loaded item, keyed in the table by `_item_name`).
- Produces: `ParameterGateway(ParameterLister, ParameterInspector, Protocol)` — the union `ParameterListScreen` now takes, mirroring `StackGateway` in `stacks.py`.

**No app wiring is needed.** `AwstApp.ssm_gateway` and the `SERVICES` entry in `screens/home.py` already exist from the parameter-list feature, and the concrete `SsmGateway` satisfies `ParameterGateway` as soon as Task 1's `get_parameter` lands. Do not add a gateway property or a menu entry.

- [ ] **Step 1: Replace the "does nothing" test with the failing one**

In `tests/test_parameter_list_screen.py`, delete `test_enter_on_row_does_nothing` (lines 113-125) and put this in its place:

```python
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
```

Add the import at the top of the file:

```python
from awst.screens.parameter_detail import ParameterDetailScreen
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --frozen pytest tests/test_parameter_list_screen.py::test_enter_on_row_opens_the_detail_screen -v`
Expected: FAIL — `assert isinstance(app.screen, ParameterDetailScreen)` is False; the list screen ignores Enter.

- [ ] **Step 3: Wire the list screen**

In `src/awst/screens/parameters.py`, add these imports:

```python
from textual.widgets import DataTable  # noqa: TC002 -- needed at runtime: Textual inspects handler annotations

from awst.screens.parameter_detail import ParameterDetailScreen, ParameterInspector
```

Add the union protocol after `ParameterLister`:

```python
class ParameterGateway(ParameterLister, ParameterInspector, Protocol):
    """Everything the parameter screens collectively need from SSM."""
```

Change the constructor's annotation from `ParameterLister` to `ParameterGateway`:

```python
    def __init__(self: Self, gateway: ParameterGateway) -> None:
```

And add this handler at the end of `ParameterListScreen`:

```python
    def on_data_table_row_selected(self: Self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        summary = next((item for item in self._all_items if item.name == name), None)
        if summary is not None:
            self.app.push_screen(ParameterDetailScreen(self._gateway, summary))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_parameter_list_screen.py tests/test_parameter_detail_screen.py -v`
Expected: PASS

- [ ] **Step 5: Update the docs**

In `CLAUDE.md`, in the "Project overview" paragraph, replace:

```
and SSM (Parameter Store list showing name, type, tier, and last-modified; metadata only, never values)
```

with:

```
and SSM (Parameter Store list showing name, type, tier, and last-modified; Enter opens a parameter's details, where `SecureString` values render masked until `s` reveals them, `c` copies the value to the clipboard, and `r` re-fetches)
```

- [ ] **Step 6: Run the full check**

Run: `make test`
Expected: lint clean, all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/awst/screens/parameters.py tests/test_parameter_list_screen.py CLAUDE.md
git commit -m "Open SSM parameter details with Enter from the parameter list"
```

---

## Verification

- [ ] `make test` passes (lint + unit).
- [ ] `make coverage` stays above the 75% gate.
- [ ] `git log --oneline main..ssm-parameter-details` shows the design-doc commit plus the four task commits.
