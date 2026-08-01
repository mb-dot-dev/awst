# Lambda Function List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Lambda function list screen to awst, extracting a shared `ResourceListScreen` base class that the existing stack and bucket list screens also adopt.

**Architecture:** awst is a Textual TUI. Screens (`src/awst/screens/`) never touch boto3; they call gateway objects (`src/awst/aws/`) that return frozen dataclasses and raise `AwsError`. Data loads on thread workers (`@work(thread=True)`), results are handled in `on_worker_state_changed`. This plan first extracts the duplicated list-screen machinery into `ResourceListScreen[ItemT]` (guarded by the existing tests), then adds the Lambda gateway, screen, and home-screen wiring on top of it.

**Tech Stack:** Python >=3.14, Textual, boto3, uv + Makefile, pytest + pytest-asyncio (Textual `run_test()` pilot), moto (`mock_aws`), ruff, ty.

**Spec:** `docs/superpowers/specs/2026-07-07-lambda-function-list-design.md`

## Global Constraints

- Python >=3.14; PEP 695 generics (`class Foo[T]: ...`) are fine.
- All commands run through `uv`; prefer `make` targets. Tests: `uv run --frozen pytest ...`.
- `make lint` (ruff check + ruff format --check + ty check) must pass before every commit.
- Ruff line length is 120; a broad rule set is enabled (annotations, bandit, bugbear, pathlib, datetimez...). Every function needs type annotations, including `self: Self`.
- Coverage must stay >= 75% (`make coverage`).
- Existing tests in `tests/test_stack_list_screen.py` and `tests/test_bucket_list_screen.py` must pass **unchanged** — they are the safety net for the base-class refactor.
- End every commit message with:

  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Qzj9NUnWXEPTRVrLQGCHCH
  ```

---

### Task 1: Extract `ResourceListScreen` base; refactor `BucketListScreen` onto it

This is a behavior-preserving refactor: the existing bucket-screen tests are the red/green gate. No test files change in this task.

**Files:**
- Create: `src/awst/screens/resource_list.py`
- Modify: `src/awst/screens/buckets.py` (full rewrite, keep `BucketLister` and `BucketListScreen` names)
- Test (existing, unchanged): `tests/test_bucket_list_screen.py`

**Interfaces:**
- Consumes: `BucketSummary` from `awst.aws.models`, `relative_age` from `awst.screens.formatting` (both exist).
- Produces: `ResourceListScreen[ItemT](Screen[None])` in `awst.screens.resource_list` with:
  - class attributes subclasses set: `TITLE: str`, `COLUMNS: tuple[str, ...]`, `NOUN: str`
  - hooks subclasses implement: `_list(self) -> list[ItemT]`, `_row(self, item: ItemT, now: datetime) -> tuple[str | Text, ...]`, `_item_name(self, item: ItemT) -> str`
  - protected state subclasses may read: `self._loaded: bool`
  - `action_refresh()` (public to subclasses, used by the stack screen's `on_screen_resume`)
  - widget ids: `#count`, `#filter`, `#items` (the DataTable), `#error` — tests query `DataTable` without an id, so renaming the table id from `#buckets`/`#stacks` to `#items` is safe.

- [ ] **Step 1: Baseline — run the bucket screen tests and confirm they pass**

Run: `uv run --frozen pytest tests/test_bucket_list_screen.py -v`
Expected: 11 tests PASS.

- [ ] **Step 2: Create `src/awst/screens/resource_list.py`**

This is `buckets.py`'s logic verbatim, with bucket-specifics replaced by the three hooks and class attributes:

```python
"""Shared base for read-only, filterable AWS resource list screens."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Self

from textual import work
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static
from textual.worker import WorkerState

from awst.aws.models import AwsError

if TYPE_CHECKING:
    from rich.text import Text
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.worker import Worker


class ResourceListScreen[ItemT](Screen[None]):
    """A filterable, refreshable table of one kind of AWS resource.

    Subclasses set TITLE, COLUMNS, and NOUN, and implement _list, _row, and _item_name.
    Row selection is a subclass concern: the base does nothing on Enter.
    """

    COLUMNS: ClassVar[tuple[str, ...]]
    NOUN: ClassVar[str]

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "back_or_clear", "Back"),
        ("r", "refresh", "Refresh"),
        ("slash", "focus_filter", "Filter"),
    ]

    DEFAULT_CSS = """
    #count { height: 1; padding: 0 1; color: $text-muted; }
    #error { display: none; padding: 1 2; color: $text-error; }
    """

    def __init__(self: Self) -> None:
        super().__init__()
        self._all_items: list[ItemT] = []
        self._loaded = False

    def _list(self: Self) -> list[ItemT]:
        """Fetch every item from the gateway; called on a worker thread."""
        raise NotImplementedError

    def _row(self: Self, item: ItemT, now: datetime) -> tuple[str | Text, ...]:
        """The table cells for one item, in COLUMNS order."""
        raise NotImplementedError

    def _item_name(self: Self, item: ItemT) -> str:
        """The item's unique name, used as the row key and filter target."""
        raise NotImplementedError

    def compose(self: Self) -> ComposeResult:
        yield Static(id="count")
        yield Input(placeholder=f"filter {self.NOUN}s by name", id="filter")
        yield DataTable(id="items")
        yield Static(id="error")
        yield Footer()

    def on_mount(self: Self) -> None:
        table = self.query_one("#items", DataTable)
        table.cursor_type = "row"
        table.add_columns(*self.COLUMNS)
        table.loading = True
        table.focus()
        self._fetch_items()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_items(self: Self) -> list[ItemT]:
        return self._list()

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.worker.name != "_fetch_items":
            return
        if event.state == WorkerState.SUCCESS:
            was_loaded = self._loaded
            self._loaded = True
            self._all_items = event.worker.result or []
            table = self.query_one("#items", DataTable)
            table.loading = False
            self._render_rows()
            if not was_loaded:
                table.focus()
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, AwsError):
                self._show_error(error)
            elif error is not None:
                raise error

    def _show_error(self: Self, error: AwsError) -> None:
        if self._loaded:
            message = error.message if error.hint is None else f"{error.message} ({error.hint})"
            self.notify(message, title="Refresh failed", severity="error")
            self._render_rows()  # restores the count text over "refreshing…"
            return
        table = self.query_one("#items", DataTable)
        table.loading = False
        table.display = False
        self.query_one("#filter", Input).display = False
        self.query_one("#count", Static).display = False
        self.set_focus(None)
        panel = self.query_one("#error", Static)
        panel.update(error.message if error.hint is None else f"{error.message}\n{error.hint}")
        panel.display = True

    def _render_rows(self: Self) -> None:
        table = self.query_one("#items", DataTable)
        query = self.query_one("#filter", Input).value.strip().lower()
        visible = [item for item in self._all_items if query in self._item_name(item).lower()]
        previous = self._cursor_name(table)
        table.clear()
        now = datetime.now(tz=UTC)
        for item in visible:
            table.add_row(*self._row(item, now), key=self._item_name(item))
        names = [self._item_name(item) for item in visible]
        if previous in names:
            table.move_cursor(row=names.index(previous))
        total = len(self._all_items)
        noun = self.NOUN if total == 1 else f"{self.NOUN}s"
        count = f"{len(visible)} of {total} {noun}" if query else f"{total} {noun}"
        self.query_one("#count", Static).update(count)

    def _cursor_name(self: Self, table: DataTable) -> str | None:
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        return row_key.value

    def on_input_changed(self: Self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._render_rows()

    def action_focus_filter(self: Self) -> None:
        self.query_one("#filter", Input).focus()

    def action_refresh(self: Self) -> None:
        self.query_one("#error", Static).display = False
        table = self.query_one("#items", DataTable)
        table.display = True
        self.query_one("#filter", Input).display = True
        self.query_one("#count", Static).display = True
        if self._loaded:
            self.query_one("#count", Static).update("refreshing…")
        else:
            table.loading = True
        self._fetch_items()

    def action_back_or_clear(self: Self) -> None:
        filter_input = self.query_one("#filter", Input)
        if filter_input.has_focus or filter_input.value:
            filter_input.value = ""
            self.query_one("#items", DataTable).focus()
        else:
            self.app.pop_screen()
```

- [ ] **Step 3: Rewrite `src/awst/screens/buckets.py` as a thin subclass**

Replace the whole file with:

```python
"""S3 bucket list screen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self

from awst.aws.models import BucketSummary
from awst.screens.formatting import relative_age
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from datetime import datetime


class BucketLister(Protocol):
    """The slice of the S3 gateway this screen needs."""

    def list_buckets(self: Self) -> list[BucketSummary]: ...


class BucketListScreen(ResourceListScreen[BucketSummary]):
    """Read-only list of the account's S3 buckets."""

    TITLE = "S3 buckets"
    COLUMNS = ("Name", "Region", "Created")
    NOUN = "bucket"

    def __init__(self: Self, gateway: BucketLister) -> None:
        super().__init__()
        self._gateway = gateway

    def _list(self: Self) -> list[BucketSummary]:
        return self._gateway.list_buckets()

    def _row(self: Self, item: BucketSummary, now: datetime) -> tuple[str, ...]:
        return (item.name, item.region, relative_age(item.created, now))

    def _item_name(self: Self, item: BucketSummary) -> str:
        return item.name
```

Note: `BucketSummary` moves out of the `TYPE_CHECKING` block — it is now used at runtime in the base-class subscript.

- [ ] **Step 4: Run the bucket screen tests unchanged**

Run: `uv run --frozen pytest tests/test_bucket_list_screen.py -v`
Expected: 11 tests PASS. If any fail, fix `resource_list.py` — do not touch the tests.

- [ ] **Step 5: Run the full suite and lint**

Run: `make test`
Expected: lint clean, all tests PASS (stack screens are untouched so far).

- [ ] **Step 6: Commit**

```bash
git add src/awst/screens/resource_list.py src/awst/screens/buckets.py
git commit -m "$(cat <<'EOF'
Extract ResourceListScreen base from the bucket list screen

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Qzj9NUnWXEPTRVrLQGCHCH
EOF
)"
```

---

### Task 2: Refactor `StackListScreen` onto `ResourceListScreen`

Also a behavior-preserving refactor guarded by existing tests. The stack screen keeps its two extras as overrides: Enter pushes the detail screen, and returning from the detail refreshes the list.

**Files:**
- Modify: `src/awst/screens/stacks.py` (full rewrite, keep `StackLister`, `StackGateway`, `StackListScreen` names — `app.py` and `home.py` import them)
- Test (existing, unchanged): `tests/test_stack_list_screen.py`

**Interfaces:**
- Consumes: `ResourceListScreen` from Task 1 (attributes `TITLE`/`COLUMNS`/`NOUN`, hooks `_list`/`_row`/`_item_name`, state `self._loaded`, action `action_refresh()`).
- Produces: unchanged public API — `StackListScreen(gateway: StackGateway)`, `StackLister`, `StackGateway`.

- [ ] **Step 1: Baseline — run the stack screen tests and confirm they pass**

Run: `uv run --frozen pytest tests/test_stack_list_screen.py -v`
Expected: 19 tests PASS.

- [ ] **Step 2: Rewrite `src/awst/screens/stacks.py`**

Replace the whole file with:

```python
"""CloudFormation stack list screen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self

from rich.text import Text

from awst.aws.models import StackSummary
from awst.screens.formatting import relative_age, status_style
from awst.screens.resource_list import ResourceListScreen
from awst.screens.stack_detail import StackDetailScreen, StackInspector

if TYPE_CHECKING:
    from datetime import datetime

    from textual.widgets import DataTable


class StackLister(Protocol):
    """The slice of the CloudFormation gateway this screen needs."""

    def list_stacks(self: Self) -> list[StackSummary]: ...


class StackGateway(StackLister, StackInspector, Protocol):
    """Everything the stack screens collectively need from CloudFormation."""


class StackListScreen(ResourceListScreen[StackSummary]):
    """Read-only list of the account's CloudFormation stacks."""

    TITLE = "CloudFormation stacks"
    COLUMNS = ("Name", "Status", "Created", "Updated")
    NOUN = "stack"

    def __init__(self: Self, gateway: StackGateway) -> None:
        super().__init__()
        self._gateway = gateway

    def _list(self: Self) -> list[StackSummary]:
        return self._gateway.list_stacks()

    def _row(self: Self, item: StackSummary, now: datetime) -> tuple[str | Text, ...]:
        return (
            item.name,
            Text(item.status, style=status_style(item.status)),
            relative_age(item.created, now),
            relative_age(item.updated, now),
        )

    def _item_name(self: Self, item: StackSummary) -> str:
        return item.name

    def on_data_table_row_selected(self: Self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        if name is not None:
            self.app.push_screen(StackDetailScreen(self._gateway, name))

    def on_screen_resume(self: Self) -> None:
        if self._loaded:  # skip the initial push; on_mount already fetches
            self.action_refresh()
```

- [ ] **Step 3: Run the stack screen tests unchanged**

Run: `uv run --frozen pytest tests/test_stack_list_screen.py -v`
Expected: 19 tests PASS. If any fail, fix `stacks.py`/`resource_list.py` — do not touch the tests.

- [ ] **Step 4: Run the full suite and lint**

Run: `make test`
Expected: lint clean, all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/awst/screens/stacks.py
git commit -m "$(cat <<'EOF'
Move the stack list screen onto ResourceListScreen

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Qzj9NUnWXEPTRVrLQGCHCH
EOF
)"
```

---

### Task 3: `FunctionSummary` model and `LambdaGateway`

TDD: dependencies first, then failing gateway tests, then the model + gateway.

**Files:**
- Modify: `pyproject.toml` (dev dependency extras), `uv.lock` (via `uv sync`)
- Modify: `src/awst/aws/models.py` (add `FunctionSummary` after `BucketSummary`)
- Create: `src/awst/aws/lambda_.py`
- Create: `tests/test_lambda_gateway.py`

**Interfaces:**
- Consumes: `map_botocore_error(error: Exception) -> AwsError` from `awst.aws.errors`; `AwsError` from `awst.aws.models`.
- Produces:
  - `FunctionSummary(name: str, runtime: str, memory_mb: int, timeout_s: int, modified: datetime)` — frozen dataclass in `awst.aws.models`; `runtime` is `""` for container-image functions.
  - `LambdaGateway(client)` in `awst.aws.lambda_` with `list_functions(self) -> list[FunctionSummary]` (sorted by name, raises `AwsError`).

- [ ] **Step 1: Add Lambda extras to the dev dependencies**

In `pyproject.toml`, change the two dev-dependency lines:

```toml
    "boto3-stubs[cloudformation,lambda,s3]>=1.43.40",
    "moto[awslambda,cloudformation,s3]>=5.2.2",
```

Then run: `uv sync`
Expected: resolves and installs `mypy-boto3-lambda` (and moto's lambda deps); `uv.lock` is updated.

- [ ] **Step 2: Write the failing gateway tests**

Create `tests/test_lambda_gateway.py`:

```python
"""Tests for the Lambda gateway."""

from datetime import UTC, datetime
import io
import zipfile

import boto3
from botocore.stub import Stubber
from moto import mock_aws
import pytest

from awst.aws.lambda_ import LambdaGateway, _to_summary
from awst.aws.models import AwsError


def _gateway() -> LambdaGateway:
    return LambdaGateway(boto3.client("lambda", region_name="eu-west-1"))


def _role_arn() -> str:
    """Create an IAM role; moto's Lambda backend requires one that exists."""
    iam = boto3.client("iam", region_name="eu-west-1")
    document = '{"Version": "2012-10-17", "Statement": []}'
    return iam.create_role(RoleName="lambda-role", AssumeRolePolicyDocument=document)["Role"]["Arn"]


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("handler.py", "def handler(event, context):\n    return None\n")
    return buffer.getvalue()


def _create_function(name: str, role_arn: str) -> None:
    client = boto3.client("lambda", region_name="eu-west-1")
    client.create_function(
        FunctionName=name,
        Runtime="python3.12",
        Role=role_arn,
        Handler="handler.handler",
        Code={"ZipFile": _zip_bytes()},
        Timeout=30,
        MemorySize=256,
    )


@mock_aws
def test_list_functions_returns_all_functions_sorted_by_name() -> None:
    role_arn = _role_arn()
    for name in ("gamma", "alpha", "beta"):
        _create_function(name, role_arn)

    functions = _gateway().list_functions()

    assert [function.name for function in functions] == ["alpha", "beta", "gamma"]


@mock_aws
def test_list_functions_maps_fields() -> None:
    _create_function("alpha", _role_arn())

    function = _gateway().list_functions()[0]

    assert function.name == "alpha"
    assert function.runtime == "python3.12"
    assert function.memory_mb == 256
    assert function.timeout_s == 30
    assert function.modified.tzinfo is not None


@mock_aws
def test_list_functions_returns_empty_list_for_empty_account() -> None:
    assert _gateway().list_functions() == []


def test_to_summary_parses_last_modified_string() -> None:
    # Lambda returns LastModified as an ISO-8601 string, not a datetime
    summary = _to_summary(
        {
            "FunctionName": "alpha",
            "Runtime": "python3.12",
            "MemorySize": 128,
            "Timeout": 3,
            "LastModified": "2026-01-01T12:00:00.000+0000",
        }
    )

    assert summary.modified == datetime(2026, 1, 1, 12, tzinfo=UTC)


def test_to_summary_defaults_runtime_to_empty_for_image_functions() -> None:
    # container-image functions have no Runtime field; the UI renders a blank cell
    summary = _to_summary(
        {
            "FunctionName": "img",
            "MemorySize": 512,
            "Timeout": 60,
            "LastModified": "2026-01-01T12:00:00.000+0000",
        }
    )

    assert summary.runtime == ""


def test_list_functions_maps_client_error_to_aws_error() -> None:
    client = boto3.client("lambda", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_client_error("list_functions", service_error_code="AccessDenied", service_message="Access Denied")

        with pytest.raises(AwsError) as excinfo:
            LambdaGateway(client).list_functions()

    assert excinfo.value.message == "Access Denied"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_lambda_gateway.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'awst.aws.lambda_'`.

- [ ] **Step 4: Add `FunctionSummary` to `src/awst/aws/models.py`**

Insert after the `BucketSummary` dataclass (keep the existing imports; `datetime` is already imported under `TYPE_CHECKING`):

```python
@dataclass(frozen=True, slots=True)
class FunctionSummary:
    """A Lambda function, reduced to what the UI needs."""

    name: str
    runtime: str  # "" for container-image functions
    memory_mb: int
    timeout_s: int
    modified: datetime
```

- [ ] **Step 5: Create `src/awst/aws/lambda_.py`**

```python
"""Gateway to the Lambda API."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import FunctionSummary

if TYPE_CHECKING:
    from mypy_boto3_lambda import LambdaClient
    from mypy_boto3_lambda.type_defs import FunctionConfigurationTypeDef


class LambdaGateway:
    """Access to Lambda, returning plain data models."""

    def __init__(self: Self, client: LambdaClient) -> None:
        self._client = client

    def list_functions(self: Self) -> list[FunctionSummary]:
        """Return every function in the region, sorted by name.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            paginator = self._client.get_paginator("list_functions")
            functions = [_to_summary(function) for page in paginator.paginate() for function in page["Functions"]]
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        return sorted(functions, key=lambda function: function.name)


def _to_summary(function: FunctionConfigurationTypeDef) -> FunctionSummary:
    # LastModified is an ISO-8601 string (e.g. "2026-01-01T12:00:00.000+0000"), unlike S3/CFN datetimes
    return FunctionSummary(
        name=function["FunctionName"],
        runtime=function.get("Runtime", ""),
        memory_mb=function["MemorySize"],
        timeout_s=function["Timeout"],
        modified=datetime.fromisoformat(function["LastModified"]),
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_lambda_gateway.py -v`
Expected: 6 tests PASS.

- [ ] **Step 7: Full suite and lint**

Run: `make test`
Expected: lint clean, all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/awst/aws/models.py src/awst/aws/lambda_.py tests/test_lambda_gateway.py
git commit -m "$(cat <<'EOF'
Add Lambda gateway with function listing

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Qzj9NUnWXEPTRVrLQGCHCH
EOF
)"
```

---

### Task 4: `FunctionListScreen` and its tests

TDD: fake + failing screen tests first, then the (small) screen.

**Files:**
- Modify: `tests/fakes.py` (add `make_function` and `FakeLambdaGateway` at the end)
- Create: `tests/test_function_list_screen.py`
- Create: `src/awst/screens/functions.py`

**Interfaces:**
- Consumes: `ResourceListScreen` (Task 1), `FunctionSummary` (Task 3), `relative_age` from `awst.screens.formatting`.
- Produces:
  - `FunctionLister` Protocol with `list_functions(self) -> list[FunctionSummary]` and `FunctionListScreen(gateway: FunctionLister)` in `awst.screens.functions` (Task 5 imports both).
  - `FakeLambdaGateway(functions: list[FunctionSummary] | None = None, error: AwsError | None = None)` with a `calls: int` counter, and `make_function(name: str, runtime: str = "python3.14") -> FunctionSummary` in `tests.fakes` (Task 5's app tests use them).

- [ ] **Step 1: Add the fake and factory to `tests/fakes.py`**

Add `FunctionSummary` to the existing `from awst.aws.models import (...)` import block (it is sorted alphabetically), then append at the end of the file:

```python
def make_function(name: str, runtime: str = "python3.14") -> FunctionSummary:
    """A function summary with sensible defaults for list-screen tests."""
    return FunctionSummary(name=name, runtime=runtime, memory_mb=128, timeout_s=30, modified=_CREATED)


class FakeLambdaGateway:
    """In-memory stand-in for the real Lambda gateway."""

    def __init__(self: Self, functions: list[FunctionSummary] | None = None, error: AwsError | None = None) -> None:
        self.functions = functions or []
        self.error = error
        self.calls = 0

    def list_functions(self: Self) -> list[FunctionSummary]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.functions)
```

- [ ] **Step 2: Write the failing screen tests**

Create `tests/test_function_list_screen.py`:

```python
"""Tests for the Lambda function list screen."""

from typing import Self

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from awst.aws.models import AwsError
from awst.screens.functions import FunctionListScreen
from tests.fakes import FakeLambdaGateway, make_function


class FunctionScreenApp(App[None]):
    """Minimal harness that opens the function list screen directly."""

    def __init__(self: Self, gateway: FakeLambdaGateway) -> None:
        super().__init__()
        self.gateway = gateway

    def on_mount(self: Self) -> None:
        self.push_screen(FunctionListScreen(self.gateway))


async def _settle(app: App[None]) -> None:
    """Wait for the fetch worker and let its messages be processed."""
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_renders_one_row_per_function_with_formatted_cells() -> None:
    gateway = FakeLambdaGateway(functions=[make_function("resize-images"), make_function("send-mail")])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "resize-images"
        assert table.get_row_at(0)[1] == "python3.14"
        assert table.get_row_at(0)[2] == "128 MB"
        assert table.get_row_at(0)[3] == "30s"


@pytest.mark.asyncio
async def test_image_function_renders_blank_runtime() -> None:
    gateway = FakeLambdaGateway(functions=[make_function("containerised", runtime="")])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).get_row_at(0)[1] == ""


@pytest.mark.asyncio
async def test_empty_account_renders_zero_rows_with_function_noun() -> None:
    gateway = FakeLambdaGateway(functions=[])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 0
        assert "0 functions" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_filter_narrows_rows_live() -> None:
    gateway = FakeLambdaGateway(
        functions=[make_function("prod-resize"), make_function("prod-mail"), make_function("staging-resize")],
    )
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 2
        assert "2 of 3 functions" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_escape_pops_back() -> None:
    app = FunctionScreenApp(FakeLambdaGateway())

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert isinstance(app.screen, FunctionListScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, FunctionListScreen)


@pytest.mark.asyncio
async def test_refresh_refetches_and_updates_rows() -> None:
    gateway = FakeLambdaGateway(functions=[make_function("resize-images")])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert app.screen.query_one(DataTable).row_count == 1

        gateway.functions = [make_function("resize-images"), make_function("send-mail")]
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert gateway.calls == 2
        assert app.screen.query_one(DataTable).row_count == 2


@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    gateway = FakeLambdaGateway(error=AwsError("no credentials", hint="run `aws sso login`"))
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.screen.query_one("#error", Static)

        assert panel.display is True
        assert "no credentials" in str(panel.content)
        assert app.screen.query_one(DataTable).display is False


@pytest.mark.asyncio
async def test_refresh_failure_keeps_stale_rows_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeLambdaGateway(functions=[make_function("resize-images")])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.error = AwsError("throttled")
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 1  # stale rows kept
        assert toasts == ["throttled"]
        assert str(app.screen.query_one("#count", Static).content) == "1 function"


@pytest.mark.asyncio
async def test_enter_on_row_does_nothing() -> None:
    gateway = FakeLambdaGateway(functions=[make_function("resize-images")])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, FunctionListScreen)  # no detail screen yet
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_function_list_screen.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'awst.screens.functions'`.

- [ ] **Step 4: Create `src/awst/screens/functions.py`**

```python
"""Lambda function list screen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Self

from awst.aws.models import FunctionSummary
from awst.screens.formatting import relative_age
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from datetime import datetime


class FunctionLister(Protocol):
    """The slice of the Lambda gateway this screen needs."""

    def list_functions(self: Self) -> list[FunctionSummary]: ...


class FunctionListScreen(ResourceListScreen[FunctionSummary]):
    """Read-only list of the region's Lambda functions."""

    TITLE = "Lambda functions"
    COLUMNS = ("Name", "Runtime", "Memory", "Timeout", "Modified")
    NOUN = "function"

    def __init__(self: Self, gateway: FunctionLister) -> None:
        super().__init__()
        self._gateway = gateway

    def _list(self: Self) -> list[FunctionSummary]:
        return self._gateway.list_functions()

    def _row(self: Self, item: FunctionSummary, now: datetime) -> tuple[str, ...]:
        return (
            item.name,
            item.runtime,
            f"{item.memory_mb} MB",
            f"{item.timeout_s}s",
            relative_age(item.modified, now),
        )

    def _item_name(self: Self, item: FunctionSummary) -> str:
        return item.name
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_function_list_screen.py -v`
Expected: 9 tests PASS.

- [ ] **Step 6: Full suite and lint**

Run: `make test`
Expected: lint clean, all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/fakes.py tests/test_function_list_screen.py src/awst/screens/functions.py
git commit -m "$(cat <<'EOF'
Add Lambda function list screen

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Qzj9NUnWXEPTRVrLQGCHCH
EOF
)"
```

---

### Task 5: Wire Lambda into the app and home screen

TDD: update/add the app-level tests first (they fail against the current wiring), then wire. The Lambda entry goes between S3 and SQS, so two existing home-screen tests need their expectations updated — that is expected, unlike the list-screen tests which must not change.

**Files:**
- Modify: `tests/test_app.py` (two updated tests, one new test)
- Modify: `src/awst/app.py` (constructor param + `lambda_gateway` property)
- Modify: `src/awst/screens/home.py` (new `SERVICES` entry)
- Modify: `CLAUDE.md` (architecture notes: mention `resource_list.py` base and the Lambda service)

**Interfaces:**
- Consumes: `FunctionListScreen`, `FunctionLister` (Task 4), `LambdaGateway` (Task 3), `FakeLambdaGateway`/`make_function` (Task 4).
- Produces: `AwstApp(cloudformation_gateway=..., s3_gateway=..., lambda_gateway=...)` and `AwstApp.lambda_gateway` property; home screen order: cloudformation, s3, lambda, sqs (disabled).

- [ ] **Step 1: Update the two home-screen tests in `tests/test_app.py`**

Replace `test_home_screen_lists_services_with_sqs_still_disabled` with:

```python
@pytest.mark.asyncio
async def test_home_screen_lists_services_with_sqs_still_disabled() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        options = app.screen.query_one(OptionList)

        assert isinstance(app.screen, HomeScreen)
        assert options.option_count == 4
        assert options.get_option("cloudformation").disabled is False
        assert options.get_option("s3").disabled is False
        assert options.get_option("lambda").disabled is False
        assert options.get_option("sqs").disabled is True
```

Replace `test_navigation_skips_disabled_sqs_and_wraps` with:

```python
@pytest.mark.asyncio
async def test_navigation_skips_disabled_sqs_and_wraps() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        options = app.screen.query_one(OptionList)
        assert options.highlighted == 0

        await pilot.press("down")
        await pilot.pause()
        assert options.highlighted == 1  # s3

        await pilot.press("down")
        await pilot.pause()
        assert options.highlighted == 2  # lambda

        await pilot.press("down")
        await pilot.pause()
        assert options.highlighted == 0  # skips disabled sqs, wraps to the top
```

- [ ] **Step 2: Add the navigation test to `tests/test_app.py`**

Extend the `tests.fakes` import line to include `FakeLambdaGateway` and `make_function`, add `from awst.screens.functions import FunctionListScreen` to the imports, and add after `test_selecting_s3_opens_bucket_list`:

```python
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
```

- [ ] **Step 3: Run the app tests to verify they fail**

Run: `uv run --frozen pytest tests/test_app.py -v`
Expected: FAIL — `option_count == 4` assertion fails, `AwstApp` rejects the `lambda_gateway` keyword (TypeError).

- [ ] **Step 4: Wire the gateway into `src/awst/app.py`**

Add the imports:

```python
from awst.aws.lambda_ import LambdaGateway
```

and in the `TYPE_CHECKING` block:

```python
    from awst.screens.functions import FunctionLister
```

Replace the constructor with:

```python
    def __init__(
        self: Self,
        cloudformation_gateway: StackGateway | None = None,
        s3_gateway: BucketLister | None = None,
        lambda_gateway: FunctionLister | None = None,
    ) -> None:
        super().__init__()
        self._cloudformation_gateway = cloudformation_gateway
        self._s3_gateway = s3_gateway
        self._lambda_gateway = lambda_gateway
```

Add after the `s3_gateway` property:

```python
    @property
    def lambda_gateway(self: Self) -> FunctionLister:
        """The Lambda gateway, built on first use from the default credential chain."""
        if self._lambda_gateway is None:
            session = boto3.Session()
            self._lambda_gateway = LambdaGateway(session.client("lambda"))
        return self._lambda_gateway
```

- [ ] **Step 5: Add the service entry to `src/awst/screens/home.py`**

Add the import:

```python
from awst.screens.functions import FunctionListScreen
```

In `SERVICES`, insert between the s3 and sqs entries:

```python
(
    ServiceEntry(
        option_id="lambda",
        name="Lambda",
        resource="Functions",
        enabled=True,
        screen_factory=lambda app: FunctionListScreen(app.lambda_gateway),
    ),
)
```

- [ ] **Step 6: Run the app tests to verify they pass**

Run: `uv run --frozen pytest tests/test_app.py -v`
Expected: 7 tests PASS.

- [ ] **Step 7: Update `CLAUDE.md` architecture notes**

In the Architecture section: in the `src/awst/aws/` bullet change `(cloudformation.py)` to `(cloudformation.py, s3.py, lambda_.py)`, and in the `src/awst/screens/` bullet mention the shared base, e.g. change "holds one Textual `Screen` per page (`home.py`, `stacks.py`)" to "holds one Textual `Screen` per page (`home.py`, `stacks.py`, `buckets.py`, `functions.py`), with list screens subclassing `ResourceListScreen` (`resource_list.py`)". Also update the project-overview sentence "CloudFormation (read-only stack list) is the first implemented service" to name the implemented services (CloudFormation, S3, Lambda).

- [ ] **Step 8: Full suite, lint, and coverage**

Run: `make test`
Expected: lint clean, all tests PASS.

Run: `make coverage`
Expected: coverage >= 75% (it should rise: the new screen is mostly shared, tested code).

- [ ] **Step 9: Commit**

```bash
git add tests/test_app.py src/awst/app.py src/awst/screens/home.py CLAUDE.md
git commit -m "$(cat <<'EOF'
Wire Lambda function list into the app and home screen

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Qzj9NUnWXEPTRVrLQGCHCH
EOF
)"
```
