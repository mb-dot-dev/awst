# S3 Bucket List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the S3 entry on the home screen with a read-only bucket list (Name, Region, Created), mirroring the existing CloudFormation stack list.

**Architecture:** One new gateway module (`aws/s3.py`) wrapping a single paginated `ListBuckets` call, one new screen module (`screens/buckets.py`) closely modeled on `screens/stacks.py`, plus app wiring (lazy `s3_gateway` property, enabled `SERVICES` entry). Screens never import boto3; the screen depends on a `BucketLister` protocol and tests inject a fake.

**Tech Stack:** Python >=3.14, Textual, boto3/botocore (pinned 1.43.40 — supports the `list_buckets` paginator and `BucketRegion`), moto for gateway tests, pytest-asyncio + Textual `run_test()` pilot for screen tests. All commands via `uv` / `make`.

**Spec:** `docs/superpowers/specs/2026-07-05-s3-bucket-list-design.md`

## Global Constraints

- Run everything through uv: `uv run --frozen pytest ...`; full check is `make test` (lint + unit).
- Lint must pass: `make lint` runs `ruff check`, `ruff format --check`, and `ty check`. 120-char lines, `from __future__ import annotations`, `Self` annotations on methods, imports used only in annotations go under `if TYPE_CHECKING:`.
- Selecting a bucket does nothing (no detail screen in this feature).
- Region comes only from `ListBuckets`' `BucketRegion` field; missing field maps to `""` (moto 5.2.2 omits it — verified). No per-bucket API calls.
- Coverage gate is 75% (`make coverage`); the tests in this plan keep new code fully covered.

---

### Task 1: `BucketSummary` model + `S3Gateway`

**Files:**
- Modify: `pyproject.toml:17-18` (dev extras)
- Modify: `src/awst/aws/models.py` (add `BucketSummary`)
- Create: `src/awst/aws/s3.py`
- Test: `tests/test_s3_gateway.py`

**Interfaces:**
- Consumes: `awst.aws.errors.map_botocore_error(error: Exception) -> AwsError` (exists).
- Produces: `BucketSummary(name: str, region: str, created: datetime)` frozen dataclass in `awst.aws.models`; `S3Gateway(client: S3Client)` with `list_buckets() -> list[BucketSummary]` (sorted by name, raises `AwsError`) in `awst.aws.s3`. Tasks 2 and 3 rely on these exact names.

- [ ] **Step 1: Add s3 extras to dev dependencies**

In `pyproject.toml`, change the two dev-group lines:

```toml
    "boto3-stubs[cloudformation,s3]>=1.43.40",
    "moto[cloudformation,s3]>=5.2.2",
```

Then run:

```bash
uv lock && make install-dev
```

Expected: lockfile updated, sync succeeds (adds `mypy-boto3-s3`).

- [ ] **Step 2: Write the failing gateway tests**

Create `tests/test_s3_gateway.py`:

```python
"""Tests for the S3 gateway."""

from datetime import UTC, datetime

import boto3
from botocore.stub import Stubber
from moto import mock_aws
import pytest

from awst.aws.models import AwsError
from awst.aws.s3 import S3Gateway, _to_summary


def _gateway() -> S3Gateway:
    return S3Gateway(boto3.client("s3", region_name="eu-west-1"))


def _create_bucket(name: str) -> None:
    client = boto3.client("s3", region_name="eu-west-1")
    client.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})


@mock_aws
def test_list_buckets_returns_all_buckets_sorted_by_name() -> None:
    for name in ("gamma", "alpha", "beta"):
        _create_bucket(name)

    buckets = _gateway().list_buckets()

    assert [bucket.name for bucket in buckets] == ["alpha", "beta", "gamma"]


@mock_aws
def test_list_buckets_maps_fields() -> None:
    _create_bucket("alpha")

    bucket = _gateway().list_buckets()[0]

    assert bucket.name == "alpha"
    assert bucket.created.tzinfo is not None


@mock_aws
def test_list_buckets_returns_empty_list_for_empty_account() -> None:
    assert _gateway().list_buckets() == []


def test_to_summary_maps_bucket_region_when_present() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)

    summary = _to_summary({"Name": "alpha", "CreationDate": created, "BucketRegion": "eu-west-1"})

    assert summary.name == "alpha"
    assert summary.region == "eu-west-1"
    assert summary.created == created


def test_to_summary_defaults_region_to_empty_when_missing() -> None:
    # moto (and older endpoints) omit BucketRegion; the UI renders a blank cell
    summary = _to_summary({"Name": "alpha", "CreationDate": datetime(2026, 1, 1, tzinfo=UTC)})

    assert summary.region == ""


def test_list_buckets_maps_client_error_to_aws_error() -> None:
    client = boto3.client("s3", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_client_error("list_buckets", service_error_code="AccessDenied", service_message="Access Denied")

        with pytest.raises(AwsError) as excinfo:
            S3Gateway(client).list_buckets()

    assert excinfo.value.message == "Access Denied"
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run --frozen pytest tests/test_s3_gateway.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'awst.aws.s3'`.

- [ ] **Step 4: Add the model**

Append to `src/awst/aws/models.py` (after `StackNotFoundError`, before `StackSummary`, matching the file's model-per-service grouping — placement anywhere after the errors is fine):

```python
@dataclass(frozen=True, slots=True)
class BucketSummary:
    """An S3 bucket, reduced to what the UI needs."""

    name: str
    region: str
    created: datetime
```

- [ ] **Step 5: Implement the gateway**

Create `src/awst/aws/s3.py`:

```python
"""Gateway to the S3 API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import BucketSummary

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import BucketTypeDef


class S3Gateway:
    """Access to S3, returning plain data models."""

    def __init__(self: Self, client: S3Client) -> None:
        self._client = client

    def list_buckets(self: Self) -> list[BucketSummary]:
        """Return every bucket in the account, sorted by name.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            paginator = self._client.get_paginator("list_buckets")
            buckets = [_to_summary(bucket) for page in paginator.paginate() for bucket in page["Buckets"]]
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        return sorted(buckets, key=lambda bucket: bucket.name)


def _to_summary(bucket: BucketTypeDef) -> BucketSummary:
    return BucketSummary(
        name=bucket["Name"],
        region=bucket.get("BucketRegion", ""),
        created=bucket["CreationDate"],
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run --frozen pytest tests/test_s3_gateway.py -v
```

Expected: 6 passed.

- [ ] **Step 7: Lint and run the full suite**

```bash
make lint && make unit
```

Expected: no lint/type errors, all tests pass.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/awst/aws/models.py src/awst/aws/s3.py tests/test_s3_gateway.py
git commit -m "$(cat <<'EOF'
Add S3 gateway with bucket list

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LuRTnmJqeJRb5hJ1qpzTFe
EOF
)"
```

---

### Task 2: `FakeS3Gateway` + `BucketListScreen`

**Files:**
- Modify: `tests/fakes.py` (add `make_bucket` + `FakeS3Gateway`)
- Create: `src/awst/screens/buckets.py`
- Test: `tests/test_bucket_list_screen.py`

**Interfaces:**
- Consumes: `BucketSummary` from `awst.aws.models` (Task 1); `AwsError` from `awst.aws.models`; `relative_age(moment: datetime, now: datetime) -> str` from `awst.screens.formatting` (exists).
- Produces: `BucketLister` protocol (`list_buckets() -> list[BucketSummary]`) and `BucketListScreen(gateway: BucketLister)` in `awst.screens.buckets`; `FakeS3Gateway(buckets: list[BucketSummary] | None = None, error: AwsError | None = None)` with mutable `.buckets`, `.error`, and a `.calls` counter, plus `make_bucket(name: str, region: str = "eu-west-1") -> BucketSummary`, in `tests.fakes`. Task 3 relies on all of these.

- [ ] **Step 1: Add the fake gateway and factory**

In `tests/fakes.py`, add `BucketSummary` to the runtime import from `awst.aws.models`:

```python
from awst.aws.models import (
    BucketSummary,
    StackDetail,
    StackEvent,
    StackNotFoundError,
    StackOutput,
    StackParameter,
    StackResource,
    StackSummary,
)
```

Then append at the end of the file:

```python
def make_bucket(name: str, region: str = "eu-west-1") -> BucketSummary:
    """A bucket summary with sensible defaults for list-screen tests."""
    return BucketSummary(name=name, region=region, created=_CREATED)


class FakeS3Gateway:
    """In-memory stand-in for the real S3 gateway."""

    def __init__(self: Self, buckets: list[BucketSummary] | None = None, error: AwsError | None = None) -> None:
        self.buckets = buckets or []
        self.error = error
        self.calls = 0

    def list_buckets(self: Self) -> list[BucketSummary]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.buckets)
```

- [ ] **Step 2: Write the failing screen tests**

Create `tests/test_bucket_list_screen.py`:

```python
"""Tests for the S3 bucket list screen."""

from typing import Self

import pytest
from textual.app import App
from textual.widgets import DataTable, Input, Static

from awst.aws.models import AwsError
from awst.screens.buckets import BucketListScreen
from tests.fakes import FakeS3Gateway, make_bucket


class BucketScreenApp(App[None]):
    """Minimal harness that opens the bucket list screen directly."""

    def __init__(self: Self, gateway: FakeS3Gateway) -> None:
        super().__init__()
        self.gateway = gateway

    def on_mount(self: Self) -> None:
        self.push_screen(BucketListScreen(self.gateway))


async def _settle(app: App[None]) -> None:
    """Wait for the fetch worker and let its messages be processed."""
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_renders_one_row_per_bucket_with_name_and_region() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets", region="eu-west-1"), make_bucket("logs", region="")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "assets"
        assert table.get_row_at(0)[1] == "eu-west-1"
        assert table.get_row_at(1)[0] == "logs"
        assert table.get_row_at(1)[1] == ""


@pytest.mark.asyncio
async def test_empty_account_renders_zero_rows() -> None:
    gateway = FakeS3Gateway(buckets=[])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 0
        assert "0 buckets" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_count_header_uses_singular_for_one_bucket() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert str(app.screen.query_one("#count", Static).content) == "1 bucket"


@pytest.mark.asyncio
async def test_escape_pops_back() -> None:
    app = BucketScreenApp(FakeS3Gateway())

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert isinstance(app.screen, BucketListScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, BucketListScreen)


@pytest.mark.asyncio
async def test_filter_narrows_rows_live() -> None:
    gateway = FakeS3Gateway(
        buckets=[make_bucket("prod-assets"), make_bucket("prod-logs"), make_bucket("staging-assets")],
    )
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert "2 of 3 buckets" in str(app.screen.query_one("#count", Static).content)


@pytest.mark.asyncio
async def test_escape_clears_filter_before_going_back() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("prod-assets"), make_bucket("staging-assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        await pilot.press("slash")
        await pilot.press(*"prod")
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, BucketListScreen)  # still here: escape only cleared the filter
        assert app.screen.query_one("#filter", Input).value == ""
        assert app.screen.query_one(DataTable).row_count == 2
        assert app.screen.query_one(DataTable).has_focus

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, BucketListScreen)


@pytest.mark.asyncio
async def test_refresh_refetches_and_updates_rows() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert app.screen.query_one(DataTable).row_count == 1

        gateway.buckets = [make_bucket("assets"), make_bucket("logs")]
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert gateway.calls == 2
        assert app.screen.query_one(DataTable).row_count == 2


@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    gateway = FakeS3Gateway(error=AwsError("no credentials", hint="run `aws sso login`"))
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.screen.query_one("#error", Static)

        assert panel.display is True
        assert "no credentials" in str(panel.content)
        assert "aws sso login" in str(panel.content)
        assert app.screen.query_one(DataTable).display is False


@pytest.mark.asyncio
async def test_retry_after_initial_failure_recovers() -> None:
    gateway = FakeS3Gateway(error=AwsError("boom"))
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.error = None
        gateway.buckets = [make_bucket("assets")]
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one("#error", Static).display is False
        assert app.screen.query_one(DataTable).display is True
        assert app.screen.query_one(DataTable).row_count == 1


@pytest.mark.asyncio
async def test_refresh_failure_keeps_stale_rows_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.error = AwsError("throttled")
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.display is True
        assert table.row_count == 1  # stale rows kept
        assert toasts == ["throttled"]
        assert str(app.screen.query_one("#count", Static).content) == "1 bucket"  # "refreshing…" cleared


@pytest.mark.asyncio
async def test_enter_on_row_does_nothing() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, BucketListScreen)  # no detail screen yet
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run --frozen pytest tests/test_bucket_list_screen.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'awst.screens.buckets'`.

- [ ] **Step 4: Implement the screen**

Create `src/awst/screens/buckets.py`. This intentionally mirrors `screens/stacks.py` (same worker/error/filter machinery) minus status styling, row selection, and refresh-on-resume; a shared base class is deliberately deferred until a third service clarifies the variation points (see the spec's Approach section).

```python
"""S3 bucket list screen."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from textual import work
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static
from textual.worker import WorkerState

from awst.aws.models import AwsError
from awst.screens.formatting import relative_age

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.worker import Worker

    from awst.aws.models import BucketSummary

COLUMNS = ("Name", "Region", "Created")


class BucketLister(Protocol):
    """The slice of the S3 gateway this screen needs."""

    def list_buckets(self: Self) -> list[BucketSummary]: ...


class BucketListScreen(Screen[None]):
    """Read-only list of the account's S3 buckets."""

    TITLE = "S3 buckets"

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "back_or_clear", "Back"),
        ("r", "refresh", "Refresh"),
        ("slash", "focus_filter", "Filter"),
    ]

    DEFAULT_CSS = """
    #count { height: 1; padding: 0 1; color: $text-muted; }
    #error { display: none; padding: 1 2; color: $text-error; }
    """

    def __init__(self: Self, gateway: BucketLister) -> None:
        super().__init__()
        self._gateway = gateway
        self._all_buckets: list[BucketSummary] = []
        self._loaded = False

    def compose(self: Self) -> ComposeResult:
        yield Static(id="count")
        yield Input(placeholder="filter buckets by name", id="filter")
        yield DataTable(id="buckets")
        yield Static(id="error")
        yield Footer()

    def on_mount(self: Self) -> None:
        table = self.query_one("#buckets", DataTable)
        table.cursor_type = "row"
        table.add_columns(*COLUMNS)
        table.loading = True
        table.focus()
        self._fetch_buckets()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_buckets(self: Self) -> list[BucketSummary]:
        return self._gateway.list_buckets()

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.worker.name != "_fetch_buckets":
            return
        if event.state == WorkerState.SUCCESS:
            was_loaded = self._loaded
            self._loaded = True
            self._all_buckets = event.worker.result or []
            table = self.query_one("#buckets", DataTable)
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
        table = self.query_one("#buckets", DataTable)
        table.loading = False
        table.display = False
        self.query_one("#filter", Input).display = False
        self.query_one("#count", Static).display = False
        self.set_focus(None)
        panel = self.query_one("#error", Static)
        panel.update(error.message if error.hint is None else f"{error.message}\n{error.hint}")
        panel.display = True

    def _render_rows(self: Self) -> None:
        table = self.query_one("#buckets", DataTable)
        query = self.query_one("#filter", Input).value.strip().lower()
        visible = [bucket for bucket in self._all_buckets if query in bucket.name.lower()]
        previous = self._cursor_bucket_name(table)
        table.clear()
        now = datetime.now(tz=UTC)
        for bucket in visible:
            table.add_row(bucket.name, bucket.region, relative_age(bucket.created, now), key=bucket.name)
        names = [bucket.name for bucket in visible]
        if previous in names:
            table.move_cursor(row=names.index(previous))
        total = len(self._all_buckets)
        noun = "bucket" if total == 1 else "buckets"
        count = f"{len(visible)} of {total} {noun}" if query else f"{total} {noun}"
        self.query_one("#count", Static).update(count)

    def _cursor_bucket_name(self: Self, table: DataTable) -> str | None:
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
        table = self.query_one("#buckets", DataTable)
        table.display = True
        self.query_one("#filter", Input).display = True
        self.query_one("#count", Static).display = True
        if self._loaded:
            self.query_one("#count", Static).update("refreshing…")
        else:
            table.loading = True
        self._fetch_buckets()

    def action_back_or_clear(self: Self) -> None:
        filter_input = self.query_one("#filter", Input)
        if filter_input.has_focus or filter_input.value:
            filter_input.value = ""
            self.query_one("#buckets", DataTable).focus()
        else:
            self.app.pop_screen()
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run --frozen pytest tests/test_bucket_list_screen.py -v
```

Expected: 11 passed.

- [ ] **Step 6: Lint and run the full suite**

```bash
make lint && make unit
```

Expected: no lint/type errors, all tests pass.

- [ ] **Step 7: Commit**

```bash
git add tests/fakes.py src/awst/screens/buckets.py tests/test_bucket_list_screen.py
git commit -m "$(cat <<'EOF'
Add S3 bucket list screen

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LuRTnmJqeJRb5hJ1qpzTFe
EOF
)"
```

---

### Task 3: App wiring — enable S3 on the home screen

**Files:**
- Modify: `src/awst/app.py` (add `s3_gateway` param + property)
- Modify: `src/awst/screens/home.py:42` (enable the S3 entry)
- Test: `tests/test_app.py` (two existing tests change behavior; one new test)

**Interfaces:**
- Consumes: `BucketLister`, `BucketListScreen` from `awst.screens.buckets` (Task 2); `S3Gateway` from `awst.aws.s3` (Task 1); `FakeS3Gateway`, `make_bucket` from `tests.fakes` (Task 2).
- Produces: `AwstApp(cloudformation_gateway=None, s3_gateway=None)` with an `s3_gateway: BucketLister` property. Nothing later depends on this task.

- [ ] **Step 1: Update the app tests (two behavior changes + one new test)**

In `tests/test_app.py`, add imports:

```python
from awst.screens.buckets import BucketListScreen
```

and extend the fakes import:

```python
from tests.fakes import FakeCloudFormationGateway, FakeS3Gateway, make_bucket, make_detail, make_stack
```

Replace `test_home_screen_lists_services_with_only_cloudformation_enabled` with:

```python
@pytest.mark.asyncio
async def test_home_screen_lists_services_with_sqs_still_disabled() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        options = app.screen.query_one(OptionList)

        assert isinstance(app.screen, HomeScreen)
        assert options.option_count == 3
        assert options.get_option("cloudformation").disabled is False
        assert options.get_option("s3").disabled is False
        assert options.get_option("sqs").disabled is True
```

Replace `test_disabled_services_are_skipped_by_navigation` with:

```python
@pytest.mark.asyncio
async def test_disabled_services_are_skipped_by_navigation() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        options = app.screen.query_one(OptionList)
        assert options.highlighted == 0

        await pilot.press("down")
        await pilot.pause()
        assert options.highlighted == 1  # s3 is enabled now

        await pilot.press("down")
        await pilot.pause()
        assert options.highlighted == 1  # nowhere to go: sqs below is disabled
```

Add a new test after `test_enter_opens_stack_list_and_escape_returns_home`:

```python
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
```

- [ ] **Step 2: Run the app tests to verify the new/changed ones fail**

```bash
uv run --frozen pytest tests/test_app.py -v
```

Expected: `test_selecting_s3_opens_bucket_list` fails with `TypeError: AwstApp.__init__() got an unexpected keyword argument 's3_gateway'`; the two rewritten tests fail on the s3-disabled assertions.

- [ ] **Step 3: Wire the S3 gateway into the app**

Replace the full contents of `src/awst/app.py` with:

```python
"""The awst Textual application."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

import boto3
from textual.app import App

from awst.aws.cloudformation import CloudFormationGateway
from awst.aws.s3 import S3Gateway
from awst.screens.home import HomeScreen

if TYPE_CHECKING:
    from awst.screens.buckets import BucketLister
    from awst.screens.stacks import StackGateway


class AwstApp(App[None]):
    """AWS console terminal UI."""

    def __init__(
        self: Self,
        cloudformation_gateway: StackGateway | None = None,
        s3_gateway: BucketLister | None = None,
    ) -> None:
        super().__init__()
        self._cloudformation_gateway = cloudformation_gateway
        self._s3_gateway = s3_gateway

    @property
    def cloudformation_gateway(self: Self) -> StackGateway:
        """The CloudFormation gateway, built on first use from the default credential chain."""
        if self._cloudformation_gateway is None:
            session = boto3.Session()
            self._cloudformation_gateway = CloudFormationGateway(session.client("cloudformation"))
        return self._cloudformation_gateway

    @property
    def s3_gateway(self: Self) -> BucketLister:
        """The S3 gateway, built on first use from the default credential chain."""
        if self._s3_gateway is None:
            session = boto3.Session()
            self._s3_gateway = S3Gateway(session.client("s3"))
        return self._s3_gateway

    def on_mount(self: Self) -> None:
        self.push_screen(HomeScreen())
```

- [ ] **Step 4: Enable the S3 entry on the home screen**

In `src/awst/screens/home.py`, add the import after the existing `StackListScreen` import:

```python
from awst.screens.buckets import BucketListScreen
```

and replace the S3 `ServiceEntry` line with:

```python
(
    ServiceEntry(
        option_id="s3",
        name="S3",
        resource="Buckets",
        enabled=True,
        screen_factory=lambda app: BucketListScreen(app.s3_gateway),
    ),
)
```

- [ ] **Step 5: Run the app tests to verify they pass**

```bash
uv run --frozen pytest tests/test_app.py -v
```

Expected: all pass (6 tests).

- [ ] **Step 6: Full check — lint, tests, coverage**

```bash
make test && make coverage
```

Expected: lint clean, all tests pass, coverage ≥ 75%.

- [ ] **Step 7: Commit**

```bash
git add src/awst/app.py src/awst/screens/home.py tests/test_app.py
git commit -m "$(cat <<'EOF'
Enable S3 bucket list on the home screen

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LuRTnmJqeJRb5hJ1qpzTFe
EOF
)"
```
