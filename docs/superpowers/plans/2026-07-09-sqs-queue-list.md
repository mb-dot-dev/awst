# SQS Queue List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the stubbed SQS entry on the home screen with a names-only queue list screen.

**Architecture:** Follows the repo's established per-service pattern: one gateway module (`src/awst/aws/sqs.py`) that maps boto3 responses to a frozen dataclass, one screen module (`src/awst/screens/queues.py`) subclassing `ResourceListScreen`, wired up through a lazy gateway property on `AwstApp` and a `SERVICES` entry in the home screen. The listing uses **only** the paginated `list_queues` call — no per-queue `get_queue_attributes` (N+1 calls make long queue lists slow). The FIFO/Standard type is derived from the `.fifo` name suffix.

**Tech Stack:** Python >=3.14, Textual, boto3, uv, pytest + pytest-asyncio, moto (`mock_aws`), ruff, ty.

**Spec:** `docs/superpowers/specs/2026-07-09-sqs-queue-list-design.md`

## Global Constraints

- Python `>=3.14`; all commands run through `uv`, preferably via `make` targets.
- `make lint` = `ruff check` + `ruff format --check` + `ty check`; `make unit` = pytest; `make test` = both. Every task ends green.
- Line length 120. Strict annotation rules (ruff `ANN`): every function/method parameter and return is annotated, methods take `self: Self`.
- Data models are `@dataclass(frozen=True, slots=True)` in `src/awst/aws/models.py`.
- Screens never import boto3/botocore; they depend on a `Protocol` slice of a gateway.
- Gateways catch `(BotoCoreError, ClientError)` and re-raise via `map_botocore_error(error) from error`.
- Tests must not touch the network: gateway tests use moto's `mock_aws` / botocore `Stubber`; screen tests use fakes from `tests/fakes.py`. `tests/conftest.py` already sets fake credentials.
- Run a single test file with: `uv run --frozen pytest tests/<file>.py -v`.

---

### Task 1: `QueueSummary` model + SQS gateway

**Files:**
- Modify: `pyproject.toml:17-18` (add `sqs` extras to dev dependencies)
- Modify: `src/awst/aws/models.py` (add `QueueSummary` after `FunctionSummary`, before `StackSummary`)
- Create: `src/awst/aws/sqs.py`
- Test: `tests/test_sqs_gateway.py`

**Interfaces:**
- Consumes: `map_botocore_error(error: BotoCoreError | ClientError) -> AwsError` from `awst.aws.errors` (exists).
- Produces: `QueueSummary(name: str, is_fifo: bool)` frozen dataclass in `awst.aws.models`; `SqsGateway(client: SQSClient)` with `list_queues() -> list[QueueSummary]` in `awst.aws.sqs`. Tasks 2 and 3 rely on these exact names.

- [ ] **Step 1: Add SQS extras to dev dependencies**

In `pyproject.toml`, change the two dev-dependency lines:

```toml
    "boto3-stubs[cloudformation,lambda,s3,sqs]>=1.43.40",
    "moto[awslambda,cloudformation,s3,sqs]>=5.2.2",
```

Then re-lock and install:

```bash
uv lock && make install-dev
```

Expected: lockfile updates, `mypy-boto3-sqs` appears in the environment.

- [ ] **Step 2: Write the failing gateway tests**

Create `tests/test_sqs_gateway.py`:

```python
"""Tests for the SQS gateway."""

import boto3
from botocore.stub import Stubber
from moto import mock_aws
import pytest

from awst.aws.models import AwsError
from awst.aws.sqs import SqsGateway, _to_summary


def _gateway() -> SqsGateway:
    return SqsGateway(boto3.client("sqs", region_name="eu-west-1"))


def _create_queue(name: str) -> None:
    client = boto3.client("sqs", region_name="eu-west-1")
    attributes = {"FifoQueue": "true"} if name.endswith(".fifo") else {}
    client.create_queue(QueueName=name, Attributes=attributes)


@mock_aws
def test_list_queues_returns_all_queues_sorted_by_name() -> None:
    for name in ("gamma", "alpha", "beta"):
        _create_queue(name)

    queues = _gateway().list_queues()

    assert [queue.name for queue in queues] == ["alpha", "beta", "gamma"]


@mock_aws
def test_list_queues_marks_fifo_queues() -> None:
    _create_queue("orders.fifo")
    _create_queue("orders")

    queues = _gateway().list_queues()

    assert [(queue.name, queue.is_fifo) for queue in queues] == [("orders", False), ("orders.fifo", True)]


@mock_aws
def test_list_queues_returns_empty_list_for_empty_region() -> None:
    assert _gateway().list_queues() == []


def test_to_summary_takes_name_from_last_url_segment() -> None:
    summary = _to_summary("https://sqs.eu-west-1.amazonaws.com/123456789012/orders")

    assert summary.name == "orders"
    assert summary.is_fifo is False


def test_to_summary_detects_fifo_suffix() -> None:
    summary = _to_summary("https://sqs.eu-west-1.amazonaws.com/123456789012/orders.fifo")

    assert summary.name == "orders.fifo"
    assert summary.is_fifo is True


def test_list_queues_maps_client_error_to_aws_error() -> None:
    client = boto3.client("sqs", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_client_error("list_queues", service_error_code="AccessDenied", service_message="Access Denied")

        with pytest.raises(AwsError) as excinfo:
            SqsGateway(client).list_queues()

    assert excinfo.value.message == "Access Denied"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_sqs_gateway.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'awst.aws.sqs'`.

- [ ] **Step 4: Add the `QueueSummary` model**

In `src/awst/aws/models.py`, insert between `FunctionSummary` and `StackSummary`:

```python
@dataclass(frozen=True, slots=True)
class QueueSummary:
    """An SQS queue, reduced to what the UI needs."""

    name: str
    is_fifo: bool
```

- [ ] **Step 5: Implement the gateway**

Create `src/awst/aws/sqs.py`:

```python
"""Gateway to the SQS API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import QueueSummary

if TYPE_CHECKING:
    from mypy_boto3_sqs import SQSClient


class SqsGateway:
    """Access to SQS, returning plain data models."""

    def __init__(self: Self, client: SQSClient) -> None:
        self._client = client

    def list_queues(self: Self) -> list[QueueSummary]:
        """Return every queue in the region, sorted by name.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            paginator = self._client.get_paginator("list_queues")
            queues = [_to_summary(url) for page in paginator.paginate() for url in page.get("QueueUrls", [])]
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        return sorted(queues, key=lambda queue: queue.name)


def _to_summary(queue_url: str) -> QueueSummary:
    # A page with no queues omits the QueueUrls key entirely; list_queues returns only URLs,
    # so the name is the last path segment and FIFO-ness comes from the mandatory .fifo suffix.
    name = queue_url.rsplit("/", 1)[-1]
    return QueueSummary(name=name, is_fifo=name.endswith(".fifo"))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_sqs_gateway.py -v`
Expected: 6 passed.

- [ ] **Step 7: Lint**

Run: `make lint`
Expected: ruff check, ruff format --check, and ty check all pass.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/awst/aws/models.py src/awst/aws/sqs.py tests/test_sqs_gateway.py
git commit -m "Add SQS gateway with names-only queue listing"
```

---

### Task 2: Queue list screen

**Files:**
- Create: `src/awst/screens/queues.py`
- Modify: `tests/fakes.py` (add `make_queue` + `FakeSqsGateway` at the end, after `FakeLambdaGateway`; add `QueueSummary` to the models import)
- Test: `tests/test_queue_list_screen.py`

**Interfaces:**
- Consumes: `QueueSummary(name: str, is_fifo: bool)` from `awst.aws.models` (Task 1); `ResourceListScreen[ItemT]` from `awst.screens.resource_list` (exists — subclasses set `TITLE`/`COLUMNS`/`NOUN` and implement `_list`, `_row`, `_item_name`).
- Produces: `QueueLister` protocol and `QueueListScreen(gateway: QueueLister)` in `awst.screens.queues`; `FakeSqsGateway(queues: list[QueueSummary] | None = None, error: AwsError | None = None)` and `make_queue(name: str) -> QueueSummary` in `tests.fakes`. Task 3 relies on all four.

- [ ] **Step 1: Add the fake gateway and factory**

In `tests/fakes.py`, add `QueueSummary` to the `awst.aws.models` import block (alphabetical, after `FunctionSummary`):

```python
from awst.aws.models import (
    BucketSummary,
    FunctionSummary,
    QueueSummary,
    StackDetail,
    StackEvent,
    StackNotFoundError,
    StackOutput,
    StackParameter,
    StackResource,
    StackSummary,
)
```

Append at the end of the file:

```python
def make_queue(name: str) -> QueueSummary:
    """A queue summary whose FIFO flag follows the .fifo naming rule."""
    return QueueSummary(name=name, is_fifo=name.endswith(".fifo"))


class FakeSqsGateway:
    """In-memory stand-in for the real SQS gateway."""

    def __init__(self: Self, queues: list[QueueSummary] | None = None, error: AwsError | None = None) -> None:
        self.queues = queues or []
        self.error = error
        self.calls = 0

    def list_queues(self: Self) -> list[QueueSummary]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.queues)
```

- [ ] **Step 2: Write the failing screen tests**

Create `tests/test_queue_list_screen.py`. Base-class behaviors (refresh, escape handling, stale-row error toasts) are already covered by the bucket and function screen tests, so this file covers only queue-specific rendering plus the wiring of `_list`/`_item_name`:

```python
"""Tests for the SQS queue list screen."""

from typing import Self

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from awst.aws.models import AwsError
from awst.screens.queues import QueueListScreen
from tests.fakes import FakeSqsGateway, make_queue


class QueueScreenApp(App[None]):
    """Minimal harness that opens the queue list screen directly."""

    def __init__(self: Self, gateway: FakeSqsGateway) -> None:
        super().__init__()
        self.gateway = gateway

    def on_mount(self: Self) -> None:
        self.push_screen(QueueListScreen(self.gateway))


async def _settle(app: App[None]) -> None:
    """Wait for the fetch worker and let its messages be processed."""
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_renders_one_row_per_queue_with_name_and_type() -> None:
    gateway = FakeSqsGateway(queues=[make_queue("orders"), make_queue("orders.fifo")])
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "orders"
        assert table.get_row_at(0)[1] == "Standard"
        assert table.get_row_at(1)[0] == "orders.fifo"
        assert table.get_row_at(1)[1] == "FIFO"


@pytest.mark.asyncio
async def test_empty_region_renders_zero_rows_with_queue_noun() -> None:
    gateway = FakeSqsGateway(queues=[])
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 0
        assert "0 queues" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_filter_narrows_rows_live() -> None:
    gateway = FakeSqsGateway(
        queues=[make_queue("prod-orders"), make_queue("prod-mail"), make_queue("staging-orders")],
    )
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 2
        assert "2 of 3 queues" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    gateway = FakeSqsGateway(error=AwsError("no credentials", hint="run `aws sso login`"))
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.screen.query_one("#error", Static)

        assert panel.display is True
        assert "no credentials" in str(panel.content)
        assert app.screen.query_one(DataTable).display is False


@pytest.mark.asyncio
async def test_enter_on_row_does_nothing() -> None:
    gateway = FakeSqsGateway(queues=[make_queue("orders")])
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, QueueListScreen)  # no detail screen yet
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_queue_list_screen.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'awst.screens.queues'`.

- [ ] **Step 4: Implement the screen**

Create `src/awst/screens/queues.py`. Note the `_now` parameter name: queues have no timestamp column, so the base-class `now` argument is unused here, and the underscore prefix is what keeps ruff's ARG002 (unused method argument) quiet — do not rename it to `now`:

```python
"""SQS queue list screen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self

from awst.aws.models import QueueSummary
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from datetime import datetime


class QueueLister(Protocol):
    """The slice of the SQS gateway this screen needs."""

    def list_queues(self: Self) -> list[QueueSummary]: ...


class QueueListScreen(ResourceListScreen[QueueSummary]):
    """Read-only list of the region's SQS queues."""

    TITLE = "SQS queues"
    COLUMNS = ("Name", "Type")
    NOUN = "queue"

    def __init__(self: Self, gateway: QueueLister) -> None:
        super().__init__()
        self._gateway = gateway

    def _list(self: Self) -> list[QueueSummary]:
        return self._gateway.list_queues()

    def _row(self: Self, item: QueueSummary, _now: datetime) -> tuple[str, ...]:
        return (item.name, "FIFO" if item.is_fifo else "Standard")

    def _item_name(self: Self, item: QueueSummary) -> str:
        return item.name
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_queue_list_screen.py -v`
Expected: 5 passed.

- [ ] **Step 6: Lint**

Run: `make lint`
Expected: all three checks pass.

- [ ] **Step 7: Commit**

```bash
git add src/awst/screens/queues.py tests/fakes.py tests/test_queue_list_screen.py
git commit -m "Add SQS queue list screen"
```

---

### Task 3: Wire SQS into the app and home screen

**Files:**
- Modify: `src/awst/app.py` (constructor parameter + lazy `sqs_gateway` property)
- Modify: `src/awst/screens/home.py` (enable the SQS `SERVICES` entry)
- Test: `tests/test_app.py` (update the two tests that assert SQS is disabled; add an SQS navigation test)

**Interfaces:**
- Consumes: `SqsGateway` from `awst.aws.sqs` (Task 1); `QueueLister` and `QueueListScreen` from `awst.screens.queues`, `FakeSqsGateway`/`make_queue` from `tests.fakes` (Task 2).
- Produces: `AwstApp(cloudformation_gateway=..., s3_gateway=..., lambda_gateway=..., sqs_gateway=...)` constructor and an `AwstApp.sqs_gateway` property returning `QueueLister`.

- [ ] **Step 1: Update the home-screen tests**

In `tests/test_app.py`, extend the `tests.fakes` import:

```python
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
```

Add the screen import alongside the other screen imports:

```python
from awst.screens.queues import QueueListScreen
```

Replace `test_home_screen_lists_services_with_sqs_still_disabled` entirely with:

```python
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
```

Replace `test_navigation_skips_disabled_sqs_and_wraps` entirely with:

```python
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
```

Add after `test_selecting_lambda_opens_function_list`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_app.py -v`
Expected: FAIL — `test_selecting_sqs_opens_queue_list` errors with `TypeError` (unexpected keyword argument `sqs_gateway`), and the two updated tests fail on the still-disabled SQS option.

- [ ] **Step 3: Add the gateway property to the app**

In `src/awst/app.py`:

Add to the real imports:

```python
from awst.aws.sqs import SqsGateway
```

Add to the `TYPE_CHECKING` block:

```python
    from awst.screens.queues import QueueLister
```

Extend the constructor:

```python
    def __init__(
        self: Self,
        cloudformation_gateway: StackGateway | None = None,
        s3_gateway: BucketLister | None = None,
        lambda_gateway: FunctionLister | None = None,
        sqs_gateway: QueueLister | None = None,
    ) -> None:
        super().__init__()
        self._cloudformation_gateway = cloudformation_gateway
        self._s3_gateway = s3_gateway
        self._lambda_gateway = lambda_gateway
        self._sqs_gateway = sqs_gateway
```

Add after the `lambda_gateway` property:

```python
    @property
    def sqs_gateway(self: Self) -> QueueLister:
        """The SQS gateway, built on first use from the default credential chain."""
        if self._sqs_gateway is None:
            session = boto3.Session()
            self._sqs_gateway = SqsGateway(session.client("sqs"))
        return self._sqs_gateway
```

- [ ] **Step 4: Enable SQS on the home screen**

In `src/awst/screens/home.py`:

Add the import alongside the other screen imports:

```python
from awst.screens.queues import QueueListScreen
```

Replace the disabled SQS entry in `SERVICES`:

```python
    ServiceEntry(
        option_id="sqs",
        name="SQS",
        resource="Queues",
        enabled=True,
        screen_factory=lambda app: QueueListScreen(app.sqs_gateway),
    ),
```

- [ ] **Step 5: Run the app tests to verify they pass**

Run: `uv run --frozen pytest tests/test_app.py -v`
Expected: all pass.

- [ ] **Step 6: Run the full check**

Run: `make test`
Expected: lint clean, full suite passes.

- [ ] **Step 7: Commit**

```bash
git add src/awst/app.py src/awst/screens/home.py tests/test_app.py
git commit -m "Enable SQS queue list on the home screen"
```
