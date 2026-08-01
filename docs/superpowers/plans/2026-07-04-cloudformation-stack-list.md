# CloudFormation Stack List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-07-04-cloudformation-stack-list-design.md`

**Goal:** A read-only CloudFormation stack list TUI page behind a service-menu home screen, establishing awst's navigation, AWS-gateway, and testing foundations.

**Architecture:** Sync boto3 behind a per-service gateway (`CloudFormationGateway`) that returns frozen dataclasses and raises a single domain exception (`AwsError`); Textual screens load data via thread workers and never import boto3/botocore. Home screen (`OptionList` service menu) pushes the stack list screen (`DataTable` with local filter, manual refresh, color-coded statuses).

**Tech Stack:** Python >=3.14, Textual >=8.2.8, boto3, moto (dev, gateway tests), pytest + pytest-asyncio (Textual `run_test()` pilot), uv, ruff, ty.

## Global Constraints

- Python >=3.14; line length 120 (ruff).
- All commands via `uv run --frozen ...`; the full local check is `make test` (= `make lint` + `make unit`).
- `make lint` = `ruff check` + `ruff format --check` + `ty check`. Run it before every commit; fix what it reports.
- Ruff is strict (see `[tool.ruff.lint]` in `pyproject.toml`). Notably: annotate every function including `self: Self` (existing style); no local imports inside functions (PLC0415) — use module-level or `if TYPE_CHECKING:` blocks; type-only imports must live under `if TYPE_CHECKING:` (TCH rules); boolean arguments must be passed by keyword (FBT); timezone-aware datetimes only (DTZ) — use `datetime.now(tz=UTC)`.
- Tests may use `assert`, magic values, and local imports (per-file ignores).
- Coverage gate: >=75% (`make coverage` fails under it).
- Screens must never import `boto3` or `botocore` — AWS types stay behind `awst.aws`.
- Commit messages: conventional prefix (`feat:`, `test:`, `docs:`, `chore:`) + the repository's standard Claude trailers.
- pytest-asyncio runs in strict mode: every async test needs `@pytest.mark.asyncio`.

## File Structure

```
src/awst/
├── __init__.py           # main() → AwstApp().run()          (Task 9, modified)
├── app.py                # AwstApp: owns session/gateways     (Task 9, created)
├── skeleton_app.py       #                                    (Task 9, DELETED)
├── aws/
│   ├── __init__.py       # empty package marker               (Task 1, created)
│   ├── models.py         # StackSummary, AwsError             (Task 1, created)
│   ├── errors.py         # botocore → AwsError mapping        (Task 3, created)
│   └── cloudformation.py # CloudFormationGateway              (Task 2, created)
└── screens/
    ├── __init__.py       # empty package marker               (Task 5, created)
    ├── formatting.py     # relative_age, status_style         (Task 4, created)
    ├── home.py           # HomeScreen service menu            (Task 9, created)
    └── stacks.py         # StackListScreen                    (Tasks 5-8)
tests/
├── conftest.py           # fake AWS env credentials           (Task 2, created)
├── fakes.py              # FakeCloudFormationGateway          (Task 5, created)
├── test_skeleton_app.py  #                                    (Task 9, DELETED)
├── test_models.py        # Task 1
├── test_cloudformation_gateway.py  # Tasks 2-3
├── test_errors.py        # Task 3
├── test_formatting.py    # Task 4
├── test_stack_list_screen.py       # Tasks 5-8
└── test_app.py           # Task 9
```

---

### Task 1: Dependencies + AWS data models

**Files:**
- Modify: `pyproject.toml` (via `uv add`)
- Create: `src/awst/aws/__init__.py`
- Create: `src/awst/aws/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `StackSummary(name: str, status: str, created: datetime, updated: datetime, description: str | None)` — frozen, slotted dataclass. `AwsError(message: str, hint: str | None = None)` — exception with `.message` and `.hint` attributes; `str(error)` is the message.

- [ ] **Step 1: Add dependencies**

```bash
uv add boto3
uv add --dev "boto3-stubs[cloudformation]" "moto[cloudformation]"
```

Expected: `pyproject.toml` gains `boto3` under `dependencies` and the two dev packages under `[dependency-groups].dev`; `uv.lock` updated.

- [ ] **Step 2: Write the failing test**

Create `tests/test_models.py`:

```python
"""Tests for the AWS data models."""

from datetime import UTC, datetime

import pytest

from awst.aws.models import AwsError, StackSummary


def test_aws_error_carries_message_and_hint() -> None:
    error = AwsError("access denied", hint="check your IAM role")

    assert str(error) == "access denied"
    assert error.message == "access denied"
    assert error.hint == "check your IAM role"


def test_aws_error_hint_defaults_to_none() -> None:
    assert AwsError("boom").hint is None


def test_stack_summary_is_immutable() -> None:
    stack = StackSummary(
        name="stack-a",
        status="CREATE_COMPLETE",
        created=datetime(2026, 1, 1, tzinfo=UTC),
        updated=datetime(2026, 1, 2, tzinfo=UTC),
        description=None,
    )

    with pytest.raises(AttributeError):
        stack.name = "other"  # type: ignore[misc]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awst.aws'`

- [ ] **Step 4: Write minimal implementation**

Create `src/awst/aws/__init__.py` (empty file), then `src/awst/aws/models.py`:

```python
"""Plain data models and errors for the AWS layer."""

from dataclasses import dataclass
from datetime import datetime


class AwsError(Exception):
    """A user-presentable AWS failure with an optional remediation hint."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass(frozen=True, slots=True)
class StackSummary:
    """A CloudFormation stack, reduced to what the UI needs."""

    name: str
    status: str
    created: datetime
    updated: datetime
    description: str | None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --frozen pytest tests/test_models.py -v`
Expected: 3 PASSED

- [ ] **Step 6: Lint and commit**

```bash
make lint
git add pyproject.toml uv.lock src/awst/aws tests/test_models.py
git commit -m "feat: add AWS data models and boto3/moto dependencies"
```

---

### Task 2: CloudFormation gateway (happy path, moto)

**Files:**
- Create: `src/awst/aws/cloudformation.py`
- Create: `tests/conftest.py`
- Test: `tests/test_cloudformation_gateway.py`

**Interfaces:**
- Consumes: `StackSummary` from `awst.aws.models`.
- Produces: `CloudFormationGateway(client: CloudFormationClient)` with `list_stacks() -> list[StackSummary]` (all pages, sorted by name, deleted stacks excluded because `DescribeStacks` is used). Module-level `_to_summary(stack) -> StackSummary` maps one API dict.

- [ ] **Step 1: Write test credentials fixture**

Create `tests/conftest.py`:

```python
"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _aws_test_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake AWS credentials so no test can ever touch a real account."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_cloudformation_gateway.py`:

```python
"""Tests for the CloudFormation gateway."""

from datetime import UTC, datetime
import json

import boto3
from moto import mock_aws

from awst.aws.cloudformation import CloudFormationGateway, _to_summary

TEMPLATE = json.dumps(
    {
        "Description": "a test stack",
        "Resources": {"Topic": {"Type": "AWS::SNS::Topic"}},
    }
)


def _gateway() -> CloudFormationGateway:
    return CloudFormationGateway(boto3.client("cloudformation", region_name="eu-west-1"))


@mock_aws
def test_list_stacks_returns_all_stacks_sorted_by_name() -> None:
    client = boto3.client("cloudformation", region_name="eu-west-1")
    for name in ("gamma", "alpha", "beta"):
        client.create_stack(StackName=name, TemplateBody=TEMPLATE)

    stacks = _gateway().list_stacks()

    assert [stack.name for stack in stacks] == ["alpha", "beta", "gamma"]


@mock_aws
def test_list_stacks_maps_fields() -> None:
    client = boto3.client("cloudformation", region_name="eu-west-1")
    client.create_stack(StackName="alpha", TemplateBody=TEMPLATE)

    stack = _gateway().list_stacks()[0]

    assert stack.name == "alpha"
    assert stack.status == "CREATE_COMPLETE"
    assert stack.description == "a test stack"
    assert stack.created.tzinfo is not None
    assert stack.updated == stack.created  # never updated -> falls back to creation time


def test_to_summary_uses_last_updated_time_when_present() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    updated = datetime(2026, 2, 2, tzinfo=UTC)

    summary = _to_summary(
        {
            "StackName": "alpha",
            "StackStatus": "UPDATE_COMPLETE",
            "CreationTime": created,
            "LastUpdatedTime": updated,
        }
    )

    assert summary.created == created
    assert summary.updated == updated
    assert summary.description is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_cloudformation_gateway.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awst.aws.cloudformation'`

- [ ] **Step 4: Write minimal implementation**

Create `src/awst/aws/cloudformation.py`:

```python
"""Gateway to the CloudFormation API."""

from typing import TYPE_CHECKING, Self

from awst.aws.models import StackSummary

if TYPE_CHECKING:
    from mypy_boto3_cloudformation import CloudFormationClient
    from mypy_boto3_cloudformation.type_defs import StackTypeDef


class CloudFormationGateway:
    """Read-only access to CloudFormation, returning plain data models."""

    def __init__(self: Self, client: "CloudFormationClient") -> None:
        self._client = client

    def list_stacks(self: Self) -> list[StackSummary]:
        """Return every stack in the account/region, sorted by name."""
        paginator = self._client.get_paginator("describe_stacks")
        stacks = [_to_summary(stack) for page in paginator.paginate() for stack in page["Stacks"]]
        return sorted(stacks, key=lambda stack: stack.name)


def _to_summary(stack: "StackTypeDef") -> StackSummary:
    created = stack["CreationTime"]
    return StackSummary(
        name=stack["StackName"],
        status=stack["StackStatus"],
        created=created,
        updated=stack.get("LastUpdatedTime", created),
        description=stack.get("Description"),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_cloudformation_gateway.py -v`
Expected: 3 PASSED

- [ ] **Step 6: Lint and commit**

```bash
make lint
git add src/awst/aws/cloudformation.py tests/conftest.py tests/test_cloudformation_gateway.py
git commit -m "feat: add CloudFormation gateway with moto-backed tests"
```

---

### Task 3: Gateway error mapping

**Files:**
- Create: `src/awst/aws/errors.py`
- Modify: `src/awst/aws/cloudformation.py` (wrap `list_stacks` body)
- Test: `tests/test_errors.py`, extend `tests/test_cloudformation_gateway.py`

**Interfaces:**
- Consumes: `AwsError` from `awst.aws.models`.
- Produces: `map_botocore_error(error: Exception) -> AwsError` in `awst.aws.errors` (reusable by future gateways). `CloudFormationGateway.list_stacks` now raises `AwsError` for any botocore failure.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_errors.py`:

```python
"""Tests for botocore -> AwsError mapping."""

from typing import NoReturn, cast

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    TokenRetrievalError,
)
import pytest

from awst.aws.cloudformation import CloudFormationGateway
from awst.aws.errors import map_botocore_error
from awst.aws.models import AwsError


def test_missing_credentials_get_a_credentials_hint() -> None:
    error = map_botocore_error(NoCredentialsError())

    assert "credentials" in error.message.lower()
    assert error.hint is not None
    assert "aws sso login" in error.hint


def test_expired_sso_token_gets_a_credentials_hint() -> None:
    error = map_botocore_error(TokenRetrievalError(provider="sso", error_msg="token expired"))

    assert error.hint is not None
    assert "aws sso login" in error.hint


def test_connection_error_gets_a_network_hint() -> None:
    error = map_botocore_error(EndpointConnectionError(endpoint_url="https://cloudformation.example"))

    assert error.hint is not None
    assert "network" in error.hint.lower()


def test_client_error_uses_the_service_message() -> None:
    client_error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "User is not authorized"}},
        "DescribeStacks",
    )

    error = map_botocore_error(client_error)

    assert error.message == "User is not authorized"


def test_unknown_botocore_error_falls_back_to_str() -> None:
    error = map_botocore_error(BotoCoreError())

    assert error.message == str(BotoCoreError())


class _ExplodingClient:
    def get_paginator(self, _operation_name: str) -> NoReturn:
        raise NoCredentialsError


def test_list_stacks_raises_aws_error() -> None:
    from mypy_boto3_cloudformation import CloudFormationClient

    gateway = CloudFormationGateway(cast(CloudFormationClient, _ExplodingClient()))

    with pytest.raises(AwsError) as excinfo:
        gateway.list_stacks()

    assert excinfo.value.hint is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awst.aws.errors'`

- [ ] **Step 3: Write the mapping**

Create `src/awst/aws/errors.py`:

```python
"""Translate botocore failures into user-presentable AwsError values."""

from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    SSOTokenLoadError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)

from awst.aws.models import AwsError

_CREDENTIALS_HINT = "Check AWS_PROFILE, or run `aws sso login` if you use SSO."
_NETWORK_HINT = "Check your network connection and AWS region."

_CREDENTIAL_ERRORS = (NoCredentialsError, SSOTokenLoadError, TokenRetrievalError, UnauthorizedSSOTokenError)
_NETWORK_ERRORS = (EndpointConnectionError, ConnectTimeoutError)


def map_botocore_error(error: Exception) -> AwsError:
    """Return the AwsError equivalent of a botocore exception."""
    if isinstance(error, _CREDENTIAL_ERRORS):
        return AwsError("No valid AWS credentials found.", hint=_CREDENTIALS_HINT)
    if isinstance(error, _NETWORK_ERRORS):
        return AwsError(str(error), hint=_NETWORK_HINT)
    if isinstance(error, ClientError):
        return AwsError(error.response["Error"]["Message"])
    return AwsError(str(error))
```

- [ ] **Step 4: Wrap the gateway call**

In `src/awst/aws/cloudformation.py`, add imports and wrap `list_stacks`:

```python
from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
```

Replace the `list_stacks` method with:

```python
    def list_stacks(self: Self) -> list[StackSummary]:
        """Return every stack in the account/region, sorted by name.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            paginator = self._client.get_paginator("describe_stacks")
            stacks = [_to_summary(stack) for page in paginator.paginate() for stack in page["Stacks"]]
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        return sorted(stacks, key=lambda stack: stack.name)
```

(`ClientError` does not inherit from `BotoCoreError`; both must be caught.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_errors.py tests/test_cloudformation_gateway.py -v`
Expected: all PASSED (Task 2 tests still green)

- [ ] **Step 6: Lint and commit**

```bash
make lint
git add src/awst/aws/errors.py src/awst/aws/cloudformation.py tests/test_errors.py
git commit -m "feat: map botocore failures to AwsError with remediation hints"
```

---

### Task 4: Formatting helpers

**Files:**
- Create: `src/awst/screens/__init__.py`
- Create: `src/awst/screens/formatting.py`
- Test: `tests/test_formatting.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `relative_age(moment: datetime, now: datetime) -> str` ("just now", "5m ago", "2h ago", "3d ago"); `status_style(status: str) -> str` (Rich style name: "green" / "yellow" / "red" / "").

- [ ] **Step 1: Write the failing tests**

Create `tests/test_formatting.py`:

```python
"""Tests for presentation formatting helpers."""

from datetime import UTC, datetime, timedelta

import pytest

from awst.screens.formatting import relative_age, status_style

NOW = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(seconds=30), "just now"),
        (timedelta(minutes=5), "5m ago"),
        (timedelta(hours=2), "2h ago"),
        (timedelta(days=3), "3d ago"),
        (timedelta(days=400), "400d ago"),
    ],
)
def test_relative_age(age: timedelta, expected: str) -> None:
    assert relative_age(NOW - age, NOW) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("CREATE_COMPLETE", "green"),
        ("UPDATE_COMPLETE", "green"),
        ("UPDATE_IN_PROGRESS", "yellow"),
        ("CREATE_FAILED", "red"),
        ("ROLLBACK_IN_PROGRESS", "red"),
        ("UPDATE_ROLLBACK_COMPLETE", "red"),
        ("REVIEW_IN_PROGRESS", "yellow"),
    ],
)
def test_status_style(status: str, expected: str) -> None:
    assert status_style(status) == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_formatting.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awst.screens'`

- [ ] **Step 3: Write minimal implementation**

Create `src/awst/screens/__init__.py` (empty file), then `src/awst/screens/formatting.py`:

```python
"""Pure formatting helpers for presenting AWS data."""

from datetime import datetime

_MINUTE = 60
_HOUR = 3600
_DAY = 86400


def relative_age(moment: datetime, now: datetime) -> str:
    """Render how long ago ``moment`` was, e.g. "2h ago"."""
    seconds = int((now - moment).total_seconds())
    if seconds < _MINUTE:
        return "just now"
    if seconds < _HOUR:
        return f"{seconds // _MINUTE}m ago"
    if seconds < _DAY:
        return f"{seconds // _HOUR}h ago"
    return f"{seconds // _DAY}d ago"


def status_style(status: str) -> str:
    """Rich style for a CloudFormation stack status (rollbacks/failures win)."""
    if "ROLLBACK" in status or "FAILED" in status:
        return "red"
    if status.endswith("_IN_PROGRESS"):
        return "yellow"
    if status.endswith("_COMPLETE"):
        return "green"
    return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_formatting.py -v`
Expected: 12 PASSED

- [ ] **Step 5: Lint and commit**

```bash
make lint
git add src/awst/screens tests/test_formatting.py
git commit -m "feat: add relative-age and stack-status formatting helpers"
```

---

### Task 5: Stack list screen — initial load

**Files:**
- Create: `src/awst/screens/stacks.py`
- Create: `tests/fakes.py`
- Test: `tests/test_stack_list_screen.py`

**Interfaces:**
- Consumes: `StackSummary`, `relative_age`, `status_style` from earlier tasks.
- Produces: `StackLister` Protocol (`list_stacks() -> list[StackSummary]`) — the seam both the real gateway and the fake satisfy; `StackListScreen(gateway: StackLister)` — a `Screen[None]` that fetches on mount in a thread worker and renders a `DataTable` (columns Name/Status/Created/Updated, rows keyed by stack name) plus a `#count` Static. `FakeCloudFormationGateway(stacks=..., error=...)` with a `calls` counter in `tests/fakes.py`.

- [ ] **Step 1: Write the fake gateway**

Create `tests/fakes.py`:

```python
"""Test fakes for AWS gateways."""

from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from awst.aws.models import AwsError, StackSummary


class FakeCloudFormationGateway:
    """In-memory stand-in for the real CloudFormation gateway."""

    def __init__(
        self: Self,
        stacks: "list[StackSummary] | None" = None,
        error: "AwsError | None" = None,
    ) -> None:
        self.stacks = stacks or []
        self.error = error
        self.calls = 0

    def list_stacks(self: Self) -> "list[StackSummary]":
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.stacks)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_stack_list_screen.py`:

```python
"""Tests for the CloudFormation stack list screen."""

from datetime import UTC, datetime
from typing import Self

import pytest
from rich.text import Text
from textual.app import App
from textual.widgets import DataTable, Static

from awst.aws.models import StackSummary
from awst.screens.stacks import StackListScreen
from tests.fakes import FakeCloudFormationGateway


def _stack(name: str, status: str = "CREATE_COMPLETE") -> StackSummary:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    return StackSummary(name=name, status=status, created=created, updated=created, description=None)


class StackScreenApp(App[None]):
    """Minimal harness that opens the stack list screen directly."""

    def __init__(self: Self, gateway: FakeCloudFormationGateway) -> None:
        super().__init__()
        self.gateway = gateway

    def on_mount(self: Self) -> None:
        self.push_screen(StackListScreen(self.gateway))


async def _settle(app: App[None]) -> None:
    """Wait for the fetch worker and let its messages be processed."""
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_renders_one_row_per_stack_sorted_input_preserved() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("prod-api"), _stack("prod-network")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.query_one(DataTable)

        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "prod-api"
        assert table.get_row_at(1)[0] == "prod-network"


@pytest.mark.asyncio
async def test_count_header_shows_total() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("a"), _stack("b"), _stack("c")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert "3 stacks" in str(app.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_status_cell_is_styled() -> None:
    gateway = FakeCloudFormationGateway(
        stacks=[_stack("ok"), _stack("bad", status="ROLLBACK_COMPLETE")],
    )
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.query_one(DataTable)
        ok_cell = table.get_row_at(0)[1]
        bad_cell = table.get_row_at(1)[1]

        assert isinstance(ok_cell, Text)
        assert str(ok_cell.style) == "green"
        assert isinstance(bad_cell, Text)
        assert str(bad_cell.style) == "red"


@pytest.mark.asyncio
async def test_escape_pops_back() -> None:
    app = StackScreenApp(FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert isinstance(app.screen, StackListScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, StackListScreen)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_stack_list_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awst.screens.stacks'`

- [ ] **Step 4: Write the screen**

Create `src/awst/screens/stacks.py`:

```python
"""CloudFormation stack list screen."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, Self

from rich.text import Text
from textual import work
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static
from textual.worker import WorkerState

from awst.screens.formatting import relative_age, status_style

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.worker import Worker

    from awst.aws.models import StackSummary

COLUMNS = ("Name", "Status", "Created", "Updated")


class StackLister(Protocol):
    """The slice of the CloudFormation gateway this screen needs."""

    def list_stacks(self: Self) -> "list[StackSummary]": ...


class StackListScreen(Screen[None]):
    """Read-only list of the account's CloudFormation stacks."""

    TITLE = "CloudFormation stacks"

    BINDINGS = [("escape", "back", "Back")]

    DEFAULT_CSS = """
    #count { height: 1; padding: 0 1; color: $text-muted; }
    """

    def __init__(self: Self, gateway: StackLister) -> None:
        super().__init__()
        self._gateway = gateway
        self._all_stacks: list[StackSummary] = []

    def compose(self: Self) -> "ComposeResult":
        yield Static(id="count")
        yield DataTable(id="stacks")
        yield Footer()

    def on_mount(self: Self) -> None:
        table = self.query_one("#stacks", DataTable)
        table.cursor_type = "row"
        table.add_columns(*COLUMNS)
        table.loading = True
        table.focus()
        self._fetch_stacks()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_stacks(self: Self) -> "list[StackSummary]":
        return self._gateway.list_stacks()

    def on_worker_state_changed(self: Self, event: "Worker.StateChanged") -> None:
        if event.worker.name != "_fetch_stacks":
            return
        if event.state == WorkerState.SUCCESS:
            self._all_stacks = event.worker.result or []
            self.query_one("#stacks", DataTable).loading = False
            self._render_rows()
        elif event.state == WorkerState.ERROR and event.worker.error is not None:
            raise event.worker.error

    def _render_rows(self: Self) -> None:
        table = self.query_one("#stacks", DataTable)
        table.clear()
        now = datetime.now(tz=UTC)
        for stack in self._all_stacks:
            table.add_row(
                stack.name,
                Text(stack.status, style=status_style(stack.status)),
                relative_age(stack.created, now),
                relative_age(stack.updated, now),
                key=stack.name,
            )
        self.query_one("#count", Static).update(f"{len(self._all_stacks)} stacks")

    def action_back(self: Self) -> None:
        self.app.pop_screen()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_stack_list_screen.py -v`
Expected: 4 PASSED

- [ ] **Step 6: Lint and commit**

```bash
make lint
git add src/awst/screens/stacks.py tests/fakes.py tests/test_stack_list_screen.py
git commit -m "feat: add stack list screen with thread-worker loading"
```

---

### Task 6: Stack list screen — filtering

**Files:**
- Modify: `src/awst/screens/stacks.py`
- Test: extend `tests/test_stack_list_screen.py`

**Interfaces:**
- Consumes: Task 5's screen.
- Produces: `/` focuses a `#filter` Input; live case-insensitive substring filter on stack name; count shows "n of m stacks" while filtering; `escape` clears the filter (when it has focus or a value) before it means "back".

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stack_list_screen.py` (add `Input` to the existing `textual.widgets` import):

```python
@pytest.mark.asyncio
async def test_filter_narrows_rows_live() -> None:
    gateway = FakeCloudFormationGateway(
        stacks=[_stack("prod-api"), _stack("prod-network"), _stack("staging-api")],
    )
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()
        table = app.query_one(DataTable)

        assert table.row_count == 2
        assert "2 of 3 stacks" in str(app.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_filter_is_case_insensitive() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("Prod-API"), _stack("staging")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()

        assert app.query_one(DataTable).row_count == 1


@pytest.mark.asyncio
async def test_escape_clears_filter_before_going_back() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("prod-api"), _stack("staging-api")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, StackListScreen)  # still here: escape only cleared the filter
        assert app.query_one("#filter", Input).value == ""
        assert app.query_one(DataTable).row_count == 2
        assert app.query_one(DataTable).has_focus

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, StackListScreen)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_stack_list_screen.py -v`
Expected: the 3 new tests FAIL (no `#filter` widget); the 4 old tests PASS

- [ ] **Step 3: Implement filtering**

In `src/awst/screens/stacks.py`, add `Input` to the `textual.widgets` import, then:

Replace `BINDINGS` with:

```python
    BINDINGS = [
        ("escape", "back_or_clear", "Back"),
        ("slash", "focus_filter", "Filter"),
    ]
```

Replace `compose` with:

```python
    def compose(self: Self) -> "ComposeResult":
        yield Static(id="count")
        yield Input(placeholder="filter stacks by name", id="filter")
        yield DataTable(id="stacks")
        yield Footer()
```

Replace `_render_rows` with:

```python
    def _render_rows(self: Self) -> None:
        table = self.query_one("#stacks", DataTable)
        query = self.query_one("#filter", Input).value.strip().lower()
        visible = [stack for stack in self._all_stacks if query in stack.name.lower()]
        table.clear()
        now = datetime.now(tz=UTC)
        for stack in visible:
            table.add_row(
                stack.name,
                Text(stack.status, style=status_style(stack.status)),
                relative_age(stack.created, now),
                relative_age(stack.updated, now),
                key=stack.name,
            )
        total = len(self._all_stacks)
        count = f"{len(visible)} of {total} stacks" if query else f"{total} stacks"
        self.query_one("#count", Static).update(count)
```

Replace `action_back` with these three methods:

```python
def on_input_changed(self: Self, event: Input.Changed) -> None:
    if event.input.id == "filter":
        self._render_rows()


def action_focus_filter(self: Self) -> None:
    self.query_one("#filter", Input).focus()


def action_back_or_clear(self: Self) -> None:
    filter_input = self.query_one("#filter", Input)
    if filter_input.has_focus or filter_input.value:
        filter_input.value = ""
        self.query_one("#stacks", DataTable).focus()
    else:
        self.app.pop_screen()
```

(`Input` doesn't consume `escape`, so the screen binding fires while the filter has focus; printable keys like `r` and `/` are consumed by the focused `Input`, so those bindings only apply from the table.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_stack_list_screen.py -v`
Expected: 7 PASSED

- [ ] **Step 5: Lint and commit**

```bash
make lint
git add src/awst/screens/stacks.py tests/test_stack_list_screen.py
git commit -m "feat: add live name filter to stack list"
```

---

### Task 7: Stack list screen — refresh + cursor preservation

**Files:**
- Modify: `src/awst/screens/stacks.py`
- Test: extend `tests/test_stack_list_screen.py`

**Interfaces:**
- Consumes: Tasks 5-6.
- Produces: `r` re-fetches via the same exclusive worker (old rows stay visible; count shows "refreshing…" during a refresh); cursor stays on the same stack name across re-renders when that stack is still visible.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stack_list_screen.py`:

```python
@pytest.mark.asyncio
async def test_refresh_refetches_and_updates_rows() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("alpha")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 1

        gateway.stacks = [_stack("alpha"), _stack("beta")]
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert gateway.calls == 2
        assert app.query_one(DataTable).row_count == 2


@pytest.mark.asyncio
async def test_cursor_stays_on_same_stack_after_refresh() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("alpha"), _stack("beta"), _stack("gamma")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        await pilot.press("down")  # cursor: alpha -> beta
        await pilot.pause()

        gateway.stacks = [_stack("alnew"), _stack("alpha"), _stack("beta"), _stack("gamma")]
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()
        table = app.query_one(DataTable)

        assert table.get_row_at(table.cursor_row)[0] == "beta"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_stack_list_screen.py -v`
Expected: the 2 new tests FAIL (`r` not bound / cursor resets to row 0); the 7 old tests PASS

- [ ] **Step 3: Implement refresh**

In `src/awst/screens/stacks.py`:

Add the `r` binding to `BINDINGS`:

```python
    BINDINGS = [
        ("escape", "back_or_clear", "Back"),
        ("r", "refresh", "Refresh"),
        ("slash", "focus_filter", "Filter"),
    ]
```

Add the action and the cursor helper:

```python
def action_refresh(self: Self) -> None:
    if self._all_stacks:
        self.query_one("#count", Static).update("refreshing…")
    else:
        self.query_one("#stacks", DataTable).loading = True
    self._fetch_stacks()


def _cursor_stack_name(self: Self, table: DataTable) -> str | None:
    if table.row_count == 0:
        return None
    row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
    return row_key.value
```

In `_render_rows`, capture the cursor before `table.clear()` and restore it after the add-row loop. The full method becomes:

```python
    def _render_rows(self: Self) -> None:
        table = self.query_one("#stacks", DataTable)
        query = self.query_one("#filter", Input).value.strip().lower()
        visible = [stack for stack in self._all_stacks if query in stack.name.lower()]
        previous = self._cursor_stack_name(table)
        table.clear()
        now = datetime.now(tz=UTC)
        for stack in visible:
            table.add_row(
                stack.name,
                Text(stack.status, style=status_style(stack.status)),
                relative_age(stack.created, now),
                relative_age(stack.updated, now),
                key=stack.name,
            )
        names = [stack.name for stack in visible]
        if previous in names:
            table.move_cursor(row=names.index(previous))
        total = len(self._all_stacks)
        count = f"{len(visible)} of {total} stacks" if query else f"{total} stacks"
        self.query_one("#count", Static).update(count)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_stack_list_screen.py -v`
Expected: 9 PASSED

- [ ] **Step 5: Lint and commit**

```bash
make lint
git add src/awst/screens/stacks.py tests/test_stack_list_screen.py
git commit -m "feat: add manual refresh with cursor preservation to stack list"
```

---

### Task 8: Stack list screen — error handling

**Files:**
- Modify: `src/awst/screens/stacks.py`
- Test: extend `tests/test_stack_list_screen.py`

**Interfaces:**
- Consumes: Tasks 5-7; `AwsError` from `awst.aws.models`.
- Produces: initial-load `AwsError` → table hidden, `#error` Static shows message + hint, `r` retries; refresh `AwsError` after a successful load → stale rows kept, toast via `notify()`. Non-`AwsError` worker failures re-raise (crash loudly).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stack_list_screen.py` (add `from awst.aws.models import AwsError` to the imports — extend the existing `awst.aws.models` import line):

```python
@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    gateway = FakeCloudFormationGateway(error=AwsError("no credentials", hint="run `aws sso login`"))
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.query_one("#error", Static)

        assert panel.display is True
        assert "no credentials" in str(panel.content)
        assert "aws sso login" in str(panel.content)
        assert app.query_one(DataTable).display is False


@pytest.mark.asyncio
async def test_retry_after_initial_failure_recovers() -> None:
    gateway = FakeCloudFormationGateway(error=AwsError("boom"))
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.error = None
        gateway.stacks = [_stack("alpha")]
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert app.query_one("#error", Static).display is False
        assert app.query_one(DataTable).display is True
        assert app.query_one(DataTable).row_count == 1


@pytest.mark.asyncio
async def test_refresh_failure_keeps_stale_rows_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeCloudFormationGateway(stacks=[_stack("alpha")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.error = AwsError("throttled")
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()
        table = app.query_one(DataTable)

        assert table.display is True
        assert table.row_count == 1  # stale rows kept
        assert toasts == ["throttled"]
        assert "1 stacks" in str(app.query_one("#count", Static).content)  # "refreshing…" cleared
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_stack_list_screen.py -v`
Expected: the 3 new tests FAIL (no `#error` widget / worker error re-raised); earlier tests PASS

- [ ] **Step 3: Implement error handling**

In `src/awst/screens/stacks.py`:

Change the models import to a runtime import (isinstance needs it) and drop `StackSummary` from the `TYPE_CHECKING` block:

```python
from awst.aws.models import AwsError

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.worker import Worker

    from awst.aws.models import StackSummary
```

Add `self._loaded = False` to `__init__`:

```python
    def __init__(self: Self, gateway: StackLister) -> None:
        super().__init__()
        self._gateway = gateway
        self._all_stacks: list[StackSummary] = []
        self._loaded = False
```

Extend `DEFAULT_CSS`:

```python
    DEFAULT_CSS = """
    #count { height: 1; padding: 0 1; color: $text-muted; }
    #error { display: none; padding: 1 2; color: $text-error; }
    """
```

Add the error panel to `compose`:

```python
    def compose(self: Self) -> "ComposeResult":
        yield Static(id="count")
        yield Input(placeholder="filter stacks by name", id="filter")
        yield DataTable(id="stacks")
        yield Static(id="error")
        yield Footer()
```

Replace `on_worker_state_changed` and add `_show_error`:

```python
def on_worker_state_changed(self: Self, event: "Worker.StateChanged") -> None:
    if event.worker.name != "_fetch_stacks":
        return
    if event.state == WorkerState.SUCCESS:
        self._all_stacks = event.worker.result or []
        self._loaded = True
        self.query_one("#stacks", DataTable).loading = False
        self._render_rows()
    elif event.state == WorkerState.ERROR:
        error = event.worker.error
        if isinstance(error, AwsError):
            self._show_error(error)
        elif error is not None:
            raise error


def _show_error(self: Self, error: AwsError) -> None:
    if self._loaded:
        self.notify(error.message, title="Refresh failed", severity="error")
        self._render_rows()  # restores the count text over "refreshing…"
        return
    table = self.query_one("#stacks", DataTable)
    table.loading = False
    table.display = False
    panel = self.query_one("#error", Static)
    panel.update(error.message if error.hint is None else f"{error.message}\n{error.hint}")
    panel.display = True
```

Replace `action_refresh` so retry hides the panel first:

```python
    def action_refresh(self: Self) -> None:
        self.query_one("#error", Static).display = False
        table = self.query_one("#stacks", DataTable)
        table.display = True
        if self._loaded:
            self.query_one("#count", Static).update("refreshing…")
        else:
            table.loading = True
        self._fetch_stacks()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_stack_list_screen.py -v`
Expected: 12 PASSED

- [ ] **Step 5: Lint and commit**

```bash
make lint
git add src/awst/screens/stacks.py tests/test_stack_list_screen.py
git commit -m "feat: handle AWS errors in stack list with panel and toast"
```

---

### Task 9: Home screen, AwstApp, entry point

**Files:**
- Create: `src/awst/screens/home.py`
- Create: `src/awst/app.py`
- Modify: `src/awst/__init__.py`
- Delete: `src/awst/skeleton_app.py`, `tests/test_skeleton_app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `StackListScreen`, `StackLister` from `awst.screens.stacks`; `CloudFormationGateway` from `awst.aws.cloudformation`.
- Produces: `HomeScreen` (service `OptionList`; selecting `cloudformation` pushes the stack list); `AwstApp(cloudformation_gateway: StackLister | None = None)` with a lazy `cloudformation_gateway` property (real boto3 session on first use; tests inject the fake); `main()` runs `AwstApp`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app.py`:

```python
"""Tests for the app shell and home-screen navigation."""

import pytest
from textual.widgets import DataTable, OptionList

from awst.app import AwstApp
from awst.screens.home import HomeScreen
from awst.screens.stacks import StackListScreen
from tests.fakes import FakeCloudFormationGateway
from tests.test_stack_list_screen import _stack


@pytest.mark.asyncio
async def test_home_screen_lists_services_with_only_cloudformation_enabled() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        options = app.query_one(OptionList)

        assert isinstance(app.screen, HomeScreen)
        assert options.option_count == 3
        assert options.get_option("cloudformation").disabled is False
        assert options.get_option("s3").disabled is True
        assert options.get_option("sqs").disabled is True


@pytest.mark.asyncio
async def test_disabled_services_are_skipped_by_navigation() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        options = app.query_one(OptionList)
        assert options.highlighted == 0

        await pilot.press("down")
        await pilot.pause()

        assert options.highlighted == 0  # nowhere to go: everything below is disabled


@pytest.mark.asyncio
async def test_enter_opens_stack_list_and_escape_returns_home() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("prod-api")])
    app = AwstApp(cloudformation_gateway=gateway)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, StackListScreen)
        assert app.query_one(DataTable).row_count == 1

        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, HomeScreen)


@pytest.mark.asyncio
async def test_q_quits_from_home() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()

    assert app.return_code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awst.app'`

- [ ] **Step 3: Write the home screen**

Create `src/awst/screens/home.py`:

```python
"""Home screen: pick an AWS service."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, cast

from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from awst.screens.stacks import StackListScreen

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from awst.app import AwstApp


@dataclass(frozen=True, slots=True)
class ServiceEntry:
    """One row in the service menu."""

    option_id: str
    name: str
    resource: str
    enabled: bool


SERVICES = (
    ServiceEntry(option_id="cloudformation", name="CloudFormation", resource="Stacks", enabled=True),
    ServiceEntry(option_id="s3", name="S3", resource="Buckets", enabled=False),
    ServiceEntry(option_id="sqs", name="SQS", resource="Queues", enabled=False),
)


def _prompt(entry: ServiceEntry) -> str:
    suffix = "" if entry.enabled else "  (soon)"
    return f"{entry.name:<18}{entry.resource}{suffix}"


class HomeScreen(Screen[None]):
    """Service picker; the app's landing screen."""

    TITLE = "awst"

    BINDINGS = [("q", "app.quit", "Quit")]

    DEFAULT_CSS = """
    #prompt { padding: 1 2 0 2; color: $text-muted; }
    #services { margin: 1 2; }
    """

    def compose(self: Self) -> "ComposeResult":
        yield Static("Select a service", id="prompt")
        yield OptionList(
            *[Option(_prompt(entry), id=entry.option_id, disabled=not entry.enabled) for entry in SERVICES],
            id="services",
        )
        yield Footer()

    def on_option_list_option_selected(self: Self, event: OptionList.OptionSelected) -> None:
        if event.option.id == "cloudformation":
            app = cast("AwstApp", self.app)
            app.push_screen(StackListScreen(app.cloudformation_gateway))
```

- [ ] **Step 4: Write the app and entry point**

Create `src/awst/app.py`:

```python
"""The awst Textual application."""

from typing import TYPE_CHECKING, Self

import boto3
from textual.app import App

from awst.aws.cloudformation import CloudFormationGateway
from awst.screens.home import HomeScreen

if TYPE_CHECKING:
    from awst.screens.stacks import StackLister


class AwstApp(App[None]):
    """AWS console terminal UI."""

    def __init__(self: Self, cloudformation_gateway: "StackLister | None" = None) -> None:
        super().__init__()
        self._cloudformation_gateway = cloudformation_gateway

    @property
    def cloudformation_gateway(self: Self) -> "StackLister":
        """The CloudFormation gateway, built on first use from the default credential chain."""
        if self._cloudformation_gateway is None:
            session = boto3.Session()
            self._cloudformation_gateway = CloudFormationGateway(session.client("cloudformation"))
        return self._cloudformation_gateway

    def on_mount(self: Self) -> None:
        self.push_screen(HomeScreen())
```

Replace `src/awst/__init__.py` with:

```python
from awst.app import AwstApp


def main() -> None:
    """Run the awst terminal UI."""
    AwstApp().run()
```

Delete the skeleton:

```bash
git rm src/awst/skeleton_app.py tests/test_skeleton_app.py
```

- [ ] **Step 5: Run the full suite to verify it passes**

Run: `uv run --frozen pytest -v`
Expected: all tests PASS (Tasks 1-8 suites + 4 new app tests); no import errors from the deleted skeleton.

- [ ] **Step 6: Lint and commit**

```bash
make lint
git add -A src tests
git commit -m "feat: add home screen service menu and app shell, drop skeleton"
```

---

### Task 10: Docs + final verification

**Files:**
- Modify: `CLAUDE.md` (architecture section)

**Interfaces:**
- Consumes: everything above.
- Produces: accurate project docs; verified green `make test` and coverage gate.

- [ ] **Step 1: Update CLAUDE.md**

In `CLAUDE.md`, replace the "Project overview" sentence about the skeleton:

> The project is in early skeleton stage — `src/awst/skeleton_app.py` contains a placeholder `SkeletonApp` that will be replaced/expanded as the real console UI is built.

with:

> The app opens on a service-menu home screen; CloudFormation (read-only stack list) is the first implemented service.

Replace the whole "## Architecture" section with:

```markdown
## Architecture

- `src/awst/__init__.py` exposes `main()`, the console-script entry point (`awst` command, see `[project.scripts]` in `pyproject.toml`), which runs `AwstApp` (`src/awst/app.py`).
- `AwstApp` owns AWS access: it lazily builds gateways (e.g. `CloudFormationGateway`) from a `boto3.Session` using the default credential chain, and hands them to screens. Screens never import boto3/botocore.
- `src/awst/aws/` is the AWS layer: `models.py` (frozen dataclasses + `AwsError`), `errors.py` (botocore → `AwsError` mapping, reusable by future gateways), and one gateway module per service (`cloudformation.py`).
- `src/awst/screens/` holds one Textual `Screen` per page (`home.py`, `stacks.py`) and pure presentation helpers (`formatting.py`). Screens load data with thread workers (`@work(thread=True, exclusive=True, exit_on_error=False)`) and handle results in `on_worker_state_changed`.
- Adding a service = one new gateway module + one new screen module + an entry in `SERVICES` in `screens/home.py`.
- Tests: UI tests drive the app headlessly with pytest-asyncio + Textual's `run_test()` pilot, injecting `FakeCloudFormationGateway` (`tests/fakes.py`); gateway tests use moto's `mock_aws` (no network). `tests/conftest.py` sets fake AWS credentials for every test.
```

- [ ] **Step 2: Run the full check**

Run: `make test`
Expected: ruff check, ruff format --check, ty check all clean; full pytest suite PASSED.

- [ ] **Step 3: Verify coverage**

Run: `make coverage`
Expected: PASSED with total coverage >= 75% (uncovered lines should be limited to `main()` and the real-gateway branch of `AwstApp.cloudformation_gateway`).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: describe stack-list architecture in CLAUDE.md"
```
