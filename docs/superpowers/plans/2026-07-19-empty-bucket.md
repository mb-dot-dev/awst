# Empty Bucket Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `e` ("Empty") action to the S3 bucket list that permanently deletes every object, object version, and delete marker in the selected bucket, with confirmation, live progress, and cancellation.

**Architecture:** `S3Gateway.empty_bucket(name)` is a generator that paginates `list_object_versions`, deletes up to 1000 keys per `delete_objects` call, and yields a cumulative deleted count after each batch. `BucketListScreen` confirms via the existing reusable `ConfirmScreen`, then pushes a new `EmptyBucketScreen` modal whose thread worker iterates the generator, updates a progress label, and supports cancellation between batches. Screens never import boto3; the modal sees the gateway only through a `BucketEmptier` protocol.

**Tech Stack:** Python 3.14, Textual, boto3/botocore, moto (`mock_aws`) + botocore `Stubber` for gateway tests, pytest-asyncio + Textual `run_test()` pilot for UI tests, `uv` + `make` for tooling.

**Spec:** `docs/superpowers/specs/2026-07-19-empty-bucket-design.md`

## Global Constraints

- Python >= 3.14; no `from __future__ import annotations` (the repo removed it deliberately).
- All commands run through `uv`/`make`: `make lint`, `make unit`, or single tests via `uv run --frozen pytest <path>::<test> -v`.
- Ruff conventions used throughout the codebase: assign exception messages to a variable before `raise AwsError(message)`; `Self` annotations on methods; `# noqa: FBT001` on bool callback parameters; runtime `DataTable` import needs `# noqa: TC002` when it's only referenced in annotations, but a plain import when used at runtime.
- Screens must not import boto3/botocore; gateways must map botocore errors through `map_botocore_error` (`src/awst/aws/errors.py`).
- Coverage must stay >= 75% (`make coverage`); every new branch gets a test.
- Commit after each task with the trailer lines used in this repo (see task commit steps).

---

### Task 1: `S3Gateway.empty_bucket` generator

**Files:**
- Modify: `src/awst/aws/s3.py`
- Test: `tests/test_s3_gateway.py`

**Interfaces:**
- Consumes: existing `S3Gateway.__init__(client: S3Client)`, `map_botocore_error(error) -> AwsError` from `awst.aws.errors`, `AwsError` from `awst.aws.models`.
- Produces: `S3Gateway.empty_bucket(self, name: str) -> Iterator[int]` — generator yielding the cumulative deleted-object count after each batch; raises `AwsError` on any failure (including per-key `DeleteObjects` errors); yields nothing for an already-empty bucket.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_s3_gateway.py` (existing imports already cover `boto3`, `Stubber`, `mock_aws`, `pytest`, `AwsError`, `S3Gateway`):

```python
@mock_aws
def test_empty_bucket_deletes_all_objects_and_yields_cumulative_count() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    for index in range(3):
        client.put_object(Bucket="alpha", Key=f"key-{index}", Body=b"data")

    counts = list(_gateway().empty_bucket("alpha"))

    assert counts == [3]
    assert client.list_objects_v2(Bucket="alpha")["KeyCount"] == 0


@mock_aws
def test_empty_bucket_deletes_versions_and_delete_markers() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    client.put_bucket_versioning(Bucket="alpha", VersioningConfiguration={"Status": "Enabled"})
    client.put_object(Bucket="alpha", Key="doc", Body=b"v1")
    client.put_object(Bucket="alpha", Key="doc", Body=b"v2")
    client.delete_object(Bucket="alpha", Key="doc")  # adds a delete marker

    counts = list(_gateway().empty_bucket("alpha"))

    assert counts == [3]  # two versions + one delete marker
    versions = client.list_object_versions(Bucket="alpha")
    assert "Versions" not in versions
    assert "DeleteMarkers" not in versions


@mock_aws
def test_empty_bucket_on_already_empty_bucket_yields_nothing() -> None:
    _create_bucket("alpha")

    assert list(_gateway().empty_bucket("alpha")) == []


@mock_aws
def test_empty_bucket_deletes_in_batches_of_1000() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    for index in range(1050):
        client.put_object(Bucket="alpha", Key=f"key-{index:04}", Body=b"")

    counts = list(_gateway().empty_bucket("alpha"))

    assert counts == [1000, 1050]
    assert client.list_objects_v2(Bucket="alpha")["KeyCount"] == 0


@mock_aws
def test_empty_bucket_maps_missing_bucket_to_aws_error() -> None:
    with pytest.raises(AwsError):
        list(_gateway().empty_bucket("missing"))


def test_empty_bucket_raises_on_partial_failure() -> None:
    client = boto3.client("s3", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_response(
            "list_object_versions",
            {"Versions": [{"Key": "locked", "VersionId": "v1"}], "IsTruncated": False},
        )
        stubber.add_response(
            "delete_objects",
            {"Errors": [{"Key": "locked", "VersionId": "v1", "Code": "AccessDenied", "Message": "Access Denied"}]},
        )

        with pytest.raises(AwsError) as excinfo:
            list(S3Gateway(client).empty_bucket("alpha"))

    assert "locked" in excinfo.value.message
    assert "Access Denied" in excinfo.value.message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_s3_gateway.py -v`
Expected: the six new tests FAIL with `AttributeError: 'S3Gateway' object has no attribute 'empty_bucket'`; the existing tests still pass.

- [ ] **Step 3: Implement the generator**

In `src/awst/aws/s3.py`, extend the imports:

```python
from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import AwsError, BucketSummary

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import BucketTypeDef, ObjectIdentifierTypeDef
```

Add to `S3Gateway` (below `list_buckets`):

```python
    def empty_bucket(self: Self, name: str) -> Iterator[int]:
        """Delete every object version and delete marker in the bucket.

        Yields the cumulative deleted-object count after each batch of up to
        1000 keys; an already-empty bucket yields nothing. Raises AwsError for
        any credential, network, or API failure, including per-key failures
        reported by DeleteObjects.
        """
        deleted = 0
        try:
            paginator = self._client.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=name):
                items = [*page.get("Versions", []), *page.get("DeleteMarkers", [])]
                keys: list[ObjectIdentifierTypeDef] = [
                    {"Key": item["Key"], "VersionId": item["VersionId"]} for item in items
                ]
                if not keys:
                    continue
                self._delete_batch(name, keys)
                deleted += len(keys)
                yield deleted
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error

    def _delete_batch(self: Self, name: str, keys: list[ObjectIdentifierTypeDef]) -> None:
        response = self._client.delete_objects(Bucket=name, Delete={"Objects": keys, "Quiet": True})
        errors = response.get("Errors", [])
        if errors:
            first = errors[0]
            reason = first.get("Message", first.get("Code", "unknown error"))
            message = f"Could not delete {first.get('Key', 'an object')}: {reason}"
            raise AwsError(message)
```

Notes for the implementer:
- `list_object_versions` returns both current/noncurrent versions and delete markers, and works identically on never-versioned buckets (each object appears as one version with `VersionId` `"null"`), so one code path covers all bucket states.
- Pages cap at 1000 keys, which is exactly the `DeleteObjects` limit — no re-batching needed.
- `AwsError` raised inside `_delete_batch` is not a botocore error, so it propagates through the `except` clause untouched.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_s3_gateway.py -v`
Expected: all tests PASS (the 1050-object test takes a few seconds under moto).

- [ ] **Step 5: Lint**

Run: `make lint`
Expected: ruff check, ruff format --check, and ty check all pass with no findings.

- [ ] **Step 6: Commit**

```bash
git add src/awst/aws/s3.py tests/test_s3_gateway.py
git commit -m "Add S3Gateway.empty_bucket generator

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KQYXe2X8MJcasFZJUt5qJB"
```

---

### Task 2: `EmptyBucketScreen` progress modal

**Files:**
- Create: `src/awst/screens/empty_bucket.py`
- Modify: `tests/fakes.py` (extend `FakeS3Gateway`)
- Test: `tests/test_empty_bucket_screen.py`

**Interfaces:**
- Consumes: `S3Gateway.empty_bucket(name: str) -> Iterator[int]` (Task 1) via a new protocol; `AwsError` from `awst.aws.models`.
- Produces:
  - `BucketEmptier` protocol in `awst.screens.empty_bucket` with method `empty_bucket(self, name: str) -> Iterator[int]`.
  - `EmptyBucketScreen(gateway: BucketEmptier, bucket_name: str)`, a `ModalScreen[None]` that starts deleting on mount, shows a running count, cancels on `escape`, toasts the outcome, and always dismisses with `None`.
  - `FakeS3Gateway` gains constructor args `empty_batches: list[int] | None`, `empty_error: AwsError | None`, `empty_gate: threading.Event | None`, and attribute `emptied: list[str]`; its `empty_bucket(name)` generator records the name, yields each batch count (blocking on `empty_gate` before every yield after the first), then raises `empty_error` if set.

- [ ] **Step 1: Extend `FakeS3Gateway`**

In `tests/fakes.py`, add `import threading` to the top-level imports and `from collections.abc import Iterator` to the `TYPE_CHECKING` block, then replace the `FakeS3Gateway` class with:

```python
class FakeS3Gateway:
    """In-memory stand-in for the real S3 gateway."""

    def __init__(
        self: Self,
        buckets: list[BucketSummary] | None = None,
        error: AwsError | None = None,
        empty_batches: list[int] | None = None,
        empty_error: AwsError | None = None,
        empty_gate: threading.Event | None = None,
    ) -> None:
        self.buckets = buckets or []
        self.error = error
        self.empty_batches = empty_batches or []
        self.empty_error = empty_error
        self.empty_gate = empty_gate
        self.calls = 0
        self.emptied: list[str] = []

    def list_buckets(self: Self) -> list[BucketSummary]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.buckets)

    def empty_bucket(self: Self, name: str) -> Iterator[int]:
        self.emptied.append(name)
        for index, count in enumerate(self.empty_batches):
            if index > 0 and self.empty_gate is not None:
                self.empty_gate.wait(timeout=5)  # lets tests freeze the worker mid-delete
            yield count
        if self.empty_error is not None:
            raise self.empty_error
```

(The unquoted `Iterator[int]` annotation works with the `TYPE_CHECKING` import because Python 3.14's lazy annotations never evaluate it at runtime.)

- [ ] **Step 2: Write the failing UI tests**

Create `tests/test_empty_bucket_screen.py`:

```python
"""Tests for the empty-bucket progress modal."""

import contextlib
import threading
from typing import TYPE_CHECKING, Self

import pytest
from textual.app import App
from textual.widgets import Static
from textual.worker import WorkerCancelled, WorkerFailed

from awst.aws.models import AwsError
from awst.screens.empty_bucket import EmptyBucketScreen
from tests.fakes import FakeS3Gateway

if TYPE_CHECKING:
    from textual.pilot import Pilot


@pytest.fixture
def toasts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record notifications instead of rendering toasts."""
    messages: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        messages.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    return messages


class EmptyBucketApp(App[None]):
    """Harness that opens the progress modal directly and records its dismissal."""

    def __init__(self: Self, gateway: FakeS3Gateway) -> None:
        super().__init__()
        self.gateway = gateway
        self.results: list[None] = []

    def on_mount(self: Self) -> None:
        self.push_screen(EmptyBucketScreen(self.gateway, "assets"), self.results.append)


async def _until_dismissed(app: EmptyBucketApp, pilot: Pilot[None]) -> None:
    """Let the delete worker run to completion, tolerating cancelled/failed workers."""
    for _ in range(100):
        with contextlib.suppress(WorkerFailed, WorkerCancelled):
            await app.workers.wait_for_complete()
        await pilot.pause()
        if app.results:
            return
    pytest.fail("modal never dismissed")


async def _until_progress_shows(app: EmptyBucketApp, pilot: Pilot[None], text: str) -> None:
    for _ in range(100):
        await pilot.pause()
        if text in str(app.screen.query_one("#progress", Static).content):
            return
    pytest.fail(f"progress never showed {text!r}")


@pytest.mark.asyncio
async def test_success_empties_bucket_and_toasts_final_count(toasts: list[str]) -> None:
    gateway = FakeS3Gateway(empty_batches=[500, 1234])
    app = EmptyBucketApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert gateway.emptied == ["assets"]
    assert toasts == ["1,234 objects deleted."]


@pytest.mark.asyncio
async def test_already_empty_bucket_reports_zero(toasts: list[str]) -> None:
    app = EmptyBucketApp(FakeS3Gateway(empty_batches=[]))

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert toasts == ["0 objects deleted."]


@pytest.mark.asyncio
async def test_progress_label_updates_per_batch(toasts: list[str]) -> None:
    gate = threading.Event()
    gateway = FakeS3Gateway(empty_batches=[500, 600], empty_gate=gate)
    app = EmptyBucketApp(gateway)

    async with app.run_test() as pilot:
        await _until_progress_shows(app, pilot, "500 objects deleted")

        gate.set()
        await _until_dismissed(app, pilot)

    assert toasts == ["600 objects deleted."]


@pytest.mark.asyncio
async def test_escape_cancels_and_reports_partial_count(toasts: list[str]) -> None:
    gate = threading.Event()
    gateway = FakeS3Gateway(empty_batches=[500, 600], empty_gate=gate)
    app = EmptyBucketApp(gateway)

    async with app.run_test() as pilot:
        await _until_progress_shows(app, pilot, "500 objects deleted")

        await pilot.press("escape")
        gate.set()  # release the frozen worker thread so it can observe the cancel
        await _until_dismissed(app, pilot)

    assert toasts == ["500 objects were already deleted."]


@pytest.mark.asyncio
async def test_gateway_error_toasts_and_dismisses(toasts: list[str]) -> None:
    gateway = FakeS3Gateway(empty_error=AwsError("Access Denied"))
    app = EmptyBucketApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert toasts == ["Access Denied"]
```

Note: drop the `if True:` block above and put `from textual.pilot import Pilot` inside `from typing import TYPE_CHECKING` guard only if ruff requires it; tests are allowed local imports, so a plain top-level import of `Pilot` under `TYPE_CHECKING` matching `tests/test_sso_login_screen.py` is the style to copy.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_empty_bucket_screen.py -v`
Expected: FAIL at import time with `ModuleNotFoundError: No module named 'awst.screens.empty_bucket'`.

- [ ] **Step 4: Implement the modal**

Create `src/awst/screens/empty_bucket.py`:

```python
"""Modal that empties one S3 bucket, showing live progress with cancel."""

from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from textual import work
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Static
from textual.worker import Worker, WorkerState, get_current_worker

from awst.aws.models import AwsError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from textual.app import ComposeResult
    from textual.binding import BindingType


class BucketEmptier(Protocol):
    """The slice of the S3 gateway this screen needs."""

    def empty_bucket(self: Self, name: str) -> Iterator[int]: ...


class EmptyBucketScreen(ModalScreen[None]):
    """Delete every object version in one bucket; dismisses once done, cancelled, or failed."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    EmptyBucketScreen { align: center middle; }
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
    #progress { color: $text-muted; margin-top: 1; }
    """

    def __init__(self: Self, gateway: BucketEmptier, bucket_name: str) -> None:
        super().__init__()
        self._gateway = gateway
        self._bucket_name = bucket_name
        self._deleted = 0
        self._worker: Worker[None] | None = None

    def compose(self: Self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(f"Emptying {self._bucket_name}", id="title")
            yield Static("Deleting… 0 objects deleted", id="progress")
        yield Footer()

    def on_mount(self: Self) -> None:
        self._worker = self._empty()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _empty(self: Self) -> None:
        worker = get_current_worker()
        for count in self._gateway.empty_bucket(self._bucket_name):
            if worker.is_cancelled:
                return
            self.app.call_from_thread(self._update_progress, count)

    def _update_progress(self: Self, count: int) -> None:
        self._deleted = count
        if not self.is_attached:  # a late batch landed while the screen was dismissing
            return
        self.query_one("#progress", Static).update(f"Deleting… {self._count_text()} deleted")

    def _count_text(self: Self) -> str:
        noun = "object" if self._deleted == 1 else "objects"
        return f"{self._deleted:,} {noun}"

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.worker.name != "_empty":
            return
        if event.state == WorkerState.SUCCESS:
            self.notify(f"{self._count_text()} deleted.", title=f"Emptied {self._bucket_name}")
            self.dismiss(result=None)
        elif event.state == WorkerState.CANCELLED:
            self.notify(f"{self._count_text()} were already deleted.", title="Cancelled", severity="warning")
            self.dismiss(result=None)
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, AwsError):
                message = error.message if error.hint is None else f"{error.message} ({error.hint})"
                self.notify(message, title="Empty bucket failed", severity="error")
                self.dismiss(result=None)
            elif error is not None:
                raise error

    def action_cancel(self: Self) -> None:
        if self._worker is not None:
            self._worker.cancel()
```

Notes for the implementer:
- Cancelling a thread worker flips its state to `CANCELLED` immediately (the asyncio task wrapping the executor future is cancelled), while the thread itself finishes cooperatively — that's why `_empty` checks `is_cancelled` before every progress update and why `_update_progress` tolerates a detached screen.
- `call_from_thread` is synchronous, so `self._deleted` is always current when the state-change handler reads it.
- Do not `dismiss()` inside `action_cancel`; the `CANCELLED` state change handles it, keeping one exit path per outcome.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_empty_bucket_screen.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 6: Run the whole suite and lint**

Run: `make unit && make lint`
Expected: full suite passes (the extended `FakeS3Gateway` stays compatible with `tests/test_bucket_list_screen.py` and `tests/test_app.py`); lint clean.

- [ ] **Step 7: Commit**

```bash
git add src/awst/screens/empty_bucket.py tests/fakes.py tests/test_empty_bucket_screen.py
git commit -m "Add EmptyBucketScreen progress modal

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KQYXe2X8MJcasFZJUt5qJB"
```

---

### Task 3: Wire `e` into the bucket list

**Files:**
- Modify: `src/awst/screens/buckets.py`
- Test: `tests/test_bucket_list_screen.py`

**Interfaces:**
- Consumes: `ConfirmScreen(question: str)` (`awst.screens.confirm`, dismisses `bool`), `EmptyBucketScreen` and `BucketEmptier` (Task 2), `ResourceListScreen._cursor_name(table) -> str | None` and `action_refresh()` from the base class, `FakeS3Gateway.emptied` / `empty_batches` (Task 2).
- Produces: `BucketGateway(BucketLister, BucketEmptier, Protocol)` — the type `AwstApp` already satisfies with the real `S3Gateway`; `BucketListScreen.__init__` now takes `BucketGateway`; new binding `e` → `action_empty`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bucket_list_screen.py`. Add these imports: `import contextlib`, `from textual.worker import WorkerCancelled, WorkerFailed`, `from awst.screens.confirm import ConfirmScreen`, `from awst.screens.empty_bucket import EmptyBucketScreen`, and a new `TYPE_CHECKING` block (`from typing import TYPE_CHECKING, Self` replacing the current `from typing import Self`) containing `from textual.pilot import Pilot`:

```python
async def _until_back_on_list(app: App[None], pilot: Pilot[None]) -> None:
    for _ in range(100):
        with contextlib.suppress(WorkerFailed, WorkerCancelled):
            await app.workers.wait_for_complete()
        await pilot.pause()
        if isinstance(app.screen, BucketListScreen):
            return
    pytest.fail("never returned to the bucket list")


@pytest.mark.asyncio
async def test_e_on_row_asks_for_confirmation_naming_the_bucket() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        assert "assets" in str(app.screen.query_one("#question", Static).content)
        assert gateway.emptied == []  # nothing deleted yet


@pytest.mark.asyncio
async def test_e_with_no_rows_does_nothing() -> None:
    app = BucketScreenApp(FakeS3Gateway(buckets=[]))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()

        assert isinstance(app.screen, BucketListScreen)


@pytest.mark.asyncio
async def test_declining_confirmation_deletes_nothing() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()

        assert isinstance(app.screen, BucketListScreen)
        assert gateway.emptied == []
        assert gateway.calls == 1  # no refresh either


@pytest.mark.asyncio
async def test_confirming_empties_the_bucket_and_refreshes() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")], empty_batches=[2])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        assert isinstance(app.screen, EmptyBucketScreen)

        await _until_back_on_list(app, pilot)

        assert gateway.emptied == ["assets"]
        assert gateway.calls == 2  # the list refreshed after emptying
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_bucket_list_screen.py -v`
Expected: the four new tests FAIL — pressing `e` does nothing, so the `ConfirmScreen`/`EmptyBucketScreen` assertions and `gateway.emptied` checks fail; existing tests still pass.

- [ ] **Step 3: Implement the binding and flow**

Replace `src/awst/screens/buckets.py` with:

```python
"""S3 bucket list screen."""

from functools import partial
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from textual.widgets import DataTable

from awst.aws.models import BucketSummary
from awst.screens.confirm import ConfirmScreen
from awst.screens.empty_bucket import BucketEmptier, EmptyBucketScreen
from awst.screens.formatting import relative_age
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from datetime import datetime

    from textual.binding import BindingType


class BucketLister(Protocol):
    """The slice of the S3 gateway the list itself needs."""

    def list_buckets(self: Self) -> list[BucketSummary]: ...


class BucketGateway(BucketLister, BucketEmptier, Protocol):
    """Everything the bucket screens collectively need from S3."""


class BucketListScreen(ResourceListScreen[BucketSummary]):
    """List of the account's S3 buckets; `e` empties the highlighted bucket."""

    TITLE = "S3 buckets"
    COLUMNS = ("Name", "Region", "Created")
    NOUN = "bucket"

    BINDINGS: ClassVar[list[BindingType]] = [("e", "empty", "Empty")]

    def __init__(self: Self, gateway: BucketGateway) -> None:
        super().__init__()
        self._gateway = gateway

    def _list(self: Self) -> list[BucketSummary]:
        return self._gateway.list_buckets()

    def _row(self: Self, item: BucketSummary, now: datetime) -> tuple[str, ...]:
        return (item.name, item.region, relative_age(item.created, now))

    def _item_name(self: Self, item: BucketSummary) -> str:
        return item.name

    def action_empty(self: Self) -> None:
        name = self._cursor_name(self.query_one("#items", DataTable))
        if name is None:
            return
        question = f"Permanently delete all objects, versions, and delete markers in {name}?"
        self.app.push_screen(ConfirmScreen(question), partial(self._on_empty_confirmed, name))

    def _on_empty_confirmed(self: Self, name: str, confirmed: bool | None) -> None:  # noqa: FBT001
        if not confirmed:
            return
        self.app.push_screen(EmptyBucketScreen(self._gateway, name), self._on_empty_finished)

    def _on_empty_finished(self: Self, result: None) -> None:  # noqa: ARG002
        self.action_refresh()
```

Notes for the implementer:
- Textual merges `BINDINGS` across the class hierarchy, so `escape`/`r`/`slash`/`l` from `ResourceListScreen` remain active alongside `e`.
- `DataTable` is imported at runtime (no `TYPE_CHECKING` guard) because `query_one("#items", DataTable)` uses it as a value.
- The filter `Input` swallows printable keys while focused, so typing "e" into the filter cannot trigger `action_empty` (already covered by Textual's focus semantics — no extra code needed).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_bucket_list_screen.py -v`
Expected: all tests PASS, including the pre-existing ones.

- [ ] **Step 5: Run the whole suite and lint**

Run: `make test`
Expected: lint clean and full suite green (`tests/test_app.py` constructs `BucketListScreen` with the real `S3Gateway`, which now satisfies `BucketGateway` thanks to Task 1).

- [ ] **Step 6: Commit**

```bash
git add src/awst/screens/buckets.py tests/test_bucket_list_screen.py
git commit -m "Wire e (Empty bucket) into the S3 bucket list

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KQYXe2X8MJcasFZJUt5qJB"
```

---

### Task 4: Docs and final verification

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: the finished feature (Tasks 1-3).
- Produces: updated project docs; a verified green `make test` and `make coverage`.

- [ ] **Step 1: Update CLAUDE.md**

In the "Project overview" section, change:

```
Implemented services are CloudFormation (stack list), S3 (bucket list), Lambda (function list), and SQS (queue list).
```

to:

```
Implemented services are CloudFormation (stack list), S3 (bucket list with an empty-bucket action), Lambda (function list), and SQS (queue list).
```

In the Architecture bullet listing `src/awst/screens/`, add `empty_bucket.py` after `sso_login.py`:

```
`sso_login.py` for the SSO login modal, `empty_bucket.py` for the empty-bucket progress modal),
```

- [ ] **Step 2: Full verification**

Run: `make test && make coverage`
Expected: lint clean, all tests pass, coverage >= 75%.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "Document the S3 empty-bucket action

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KQYXe2X8MJcasFZJUt5qJB"
```
