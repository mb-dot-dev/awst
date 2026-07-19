# Bucket Browsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pressing Enter on a bucket in the S3 bucket list opens a read-only object browser: objects and folder prefixes at one level per screen, Enter drills into folders, escape pops back, `m` loads the next 1,000-key page, and buckets in any region work via lazily cached per-region S3 clients.

**Architecture:** A new `list_objects` gateway method makes exactly one `list_objects_v2` call (`Delimiter="/"`, `MaxKeys=1000`) per screen load and returns an `ObjectPage` model. The shared `ResourceListScreen` base gains an opt-in load-more hook (`_has_more`/`_list_more` + an `m` binding). A new `ObjectListScreen` subclasses the base, one screen per prefix level, pushed onto the screen stack like the existing stacks → stack-detail drill-down.

**Tech Stack:** Python >=3.14, Textual, boto3, pytest + pytest-asyncio + Textual pilot, moto (`mock_aws`) and botocore `Stubber` for gateway tests, `uv` for everything.

**Spec:** `docs/superpowers/specs/2026-07-19-bucket-browsing-design.md`

## Global Constraints

- Python >=3.14; run everything through `uv` (`uv run --frozen pytest …`) or the `make` targets.
- Run `make lint` (ruff check + ruff format check + ty check) before every commit; a task is not done until it passes.
- Screens never import boto3/botocore; all AWS access goes through gateway classes in `src/awst/aws/`.
- List screens make exactly ONE list API call per load — never per-item attribute fetches (project rule, see memory + CLAUDE.md).
- Models are frozen slotted dataclasses in `src/awst/aws/models.py`; gateways map botocore errors via `map_botocore_error` and raise `AwsError`.
- Ruff line length is 120; `tests/**/*.py` may use `assert` and hardcoded values.
- Commit messages: short imperative sentence, capitalized, no conventional-commit prefix (matches repo history, e.g. "Add global region switcher on ctrl+g").

---

### Task 1: Object models and gateway `list_objects` with per-region clients

**Files:**
- Modify: `src/awst/aws/models.py` (append after `BucketSummary`, around line 75)
- Modify: `src/awst/aws/s3.py`
- Test: `tests/test_s3_gateway.py` (append)

**Interfaces:**
- Consumes: existing `map_botocore_error`, `AwsError`.
- Produces:
  - `ObjectSummary(key: str, size: int, modified: datetime)` — frozen dataclass.
  - `ObjectPage(folders: tuple[str, ...], objects: tuple[ObjectSummary, ...], continuation_token: str | None)` — frozen dataclass.
  - `S3Gateway.__init__(self, client: S3Client, regional_client_factory: Callable[[str], S3Client] | None = None)`.
  - `S3Gateway.list_objects(self, bucket: str, region: str, prefix: str = "", continuation_token: str | None = None) -> ObjectPage`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_s3_gateway.py` (note: the file already imports `boto3`, `Stubber`, `mock_aws`, `pytest`, `AwsError`, `S3Gateway`, and defines `_gateway()` and `_create_bucket()` at the top — reuse them):

```python
@mock_aws
def test_list_objects_splits_folders_and_objects() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    client.put_object(Bucket="alpha", Key="readme.md", Body=b"hi")
    client.put_object(Bucket="alpha", Key="docs/guide.md", Body=b"hi")
    client.put_object(Bucket="alpha", Key="logs/2026/app.log", Body=b"hi")

    page = _gateway().list_objects("alpha", "eu-west-1")

    assert page.folders == ("docs/", "logs/")
    assert [obj.key for obj in page.objects] == ["readme.md"]
    assert page.continuation_token is None


@mock_aws
def test_list_objects_under_prefix_returns_one_level() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    client.put_object(Bucket="alpha", Key="logs/2026/app.log", Body=b"hi")
    client.put_object(Bucket="alpha", Key="logs/readme.md", Body=b"hi")

    page = _gateway().list_objects("alpha", "eu-west-1", prefix="logs/")

    assert page.folders == ("logs/2026/",)
    assert [obj.key for obj in page.objects] == ["logs/readme.md"]


@mock_aws
def test_list_objects_filters_out_the_folder_marker() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    client.put_object(Bucket="alpha", Key="docs/", Body=b"")  # zero-byte "folder" object
    client.put_object(Bucket="alpha", Key="docs/guide.md", Body=b"hi")

    page = _gateway().list_objects("alpha", "eu-west-1", prefix="docs/")

    assert [obj.key for obj in page.objects] == ["docs/guide.md"]


@mock_aws
def test_list_objects_maps_fields() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    client.put_object(Bucket="alpha", Key="readme.md", Body=b"hello")

    obj = _gateway().list_objects("alpha", "eu-west-1").objects[0]

    assert obj.key == "readme.md"
    assert obj.size == 5
    assert obj.modified.tzinfo is not None


@mock_aws
def test_list_objects_paginates_with_continuation_token() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    for index in range(1005):
        client.put_object(Bucket="alpha", Key=f"key-{index:04}", Body=b"")

    first = _gateway().list_objects("alpha", "eu-west-1")
    second = _gateway().list_objects("alpha", "eu-west-1", continuation_token=first.continuation_token)

    assert len(first.objects) == 1000
    assert first.continuation_token is not None
    assert len(second.objects) == 5
    assert second.continuation_token is None


def test_list_objects_maps_client_error_to_aws_error() -> None:
    client = boto3.client("s3", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_client_error("list_objects_v2", service_error_code="AccessDenied", service_message="Access Denied")

        with pytest.raises(AwsError) as excinfo:
            S3Gateway(client).list_objects("alpha", "eu-west-1")

    assert excinfo.value.message == "Access Denied"


@mock_aws
def test_list_objects_uses_regional_client_for_other_regions_and_caches_it() -> None:
    regions_built: list[str] = []

    def factory(region: str):  # noqa: ANN202 -- returns a boto3 S3 client
        regions_built.append(region)
        return boto3.client("s3", region_name=region)

    gateway = S3Gateway(boto3.client("s3", region_name="eu-west-1"), regional_client_factory=factory)
    remote = boto3.client("s3", region_name="us-east-2")
    remote.create_bucket(Bucket="remote", CreateBucketConfiguration={"LocationConstraint": "us-east-2"})
    remote.put_object(Bucket="remote", Key="a.txt", Body=b"hi")

    gateway.list_objects("remote", "us-east-2")
    page = gateway.list_objects("remote", "us-east-2")

    assert regions_built == ["us-east-2"]  # built once, cached after
    assert [obj.key for obj in page.objects] == ["a.txt"]


@mock_aws
def test_list_objects_uses_base_client_for_home_and_unknown_regions() -> None:
    def factory(region: str):  # noqa: ANN202
        pytest.fail(f"factory should not be called, got region {region!r}")

    gateway = S3Gateway(boto3.client("s3", region_name="eu-west-1"), regional_client_factory=factory)
    _create_bucket("alpha")
    boto3.client("s3", region_name="eu-west-1").put_object(Bucket="alpha", Key="a.txt", Body=b"hi")

    assert [obj.key for obj in gateway.list_objects("alpha", "eu-west-1").objects] == ["a.txt"]
    assert [obj.key for obj in gateway.list_objects("alpha", "").objects] == ["a.txt"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_s3_gateway.py -v -k list_objects`
Expected: FAIL / ERROR with `AttributeError: 'S3Gateway' object has no attribute 'list_objects'` (and `TypeError` on the factory kwarg).

- [ ] **Step 3: Add the models**

In `src/awst/aws/models.py`, insert directly after the `BucketSummary` dataclass (after line 74):

```python
@dataclass(frozen=True, slots=True)
class ObjectSummary:
    """An S3 object, reduced to what the UI needs."""

    key: str  # the full key, including any prefix
    size: int  # bytes
    modified: datetime


@dataclass(frozen=True, slots=True)
class ObjectPage:
    """One page of one prefix level of a bucket listing."""

    folders: tuple[str, ...]  # common prefixes, each ending "/"
    objects: tuple[ObjectSummary, ...]
    continuation_token: str | None  # None when this is the last page
```

- [ ] **Step 4: Implement the gateway**

In `src/awst/aws/s3.py`:

Replace the imports block at the top of the file with:

```python
"""Gateway to the S3 API."""

from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import AwsError, BucketSummary, ObjectPage, ObjectSummary

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import BucketTypeDef, ObjectIdentifierTypeDef
```

Replace `S3Gateway.__init__` with:

```python
    def __init__(
        self: Self,
        client: S3Client,
        regional_client_factory: Callable[[str], S3Client] | None = None,
    ) -> None:
        self._client = client
        self._regional_client_factory = regional_client_factory
        self._regional_clients: dict[str, S3Client] = {}
```

Add these methods to `S3Gateway` (after `list_buckets`, before `empty_bucket`):

```python
    def list_objects(
        self: Self,
        bucket: str,
        region: str,
        prefix: str = "",
        continuation_token: str | None = None,
    ) -> ObjectPage:
        """Return one page (up to 1000 keys) of one prefix level of the bucket.

        Folders are the level's common prefixes; the zero-byte "folder marker"
        object equal to the prefix itself is filtered out. Raises AwsError for
        any credential, network, or API failure.
        """
        client = self._client_for(region)
        try:
            if continuation_token is None:
                page = client.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter="/", MaxKeys=1000)
            else:
                page = client.list_objects_v2(
                    Bucket=bucket,
                    Prefix=prefix,
                    Delimiter="/",
                    MaxKeys=1000,
                    ContinuationToken=continuation_token,
                )
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        folders = tuple(entry["Prefix"] for entry in page.get("CommonPrefixes", []) if "Prefix" in entry)
        objects = tuple(
            ObjectSummary(key=obj["Key"], size=obj["Size"], modified=obj["LastModified"])
            for obj in page.get("Contents", [])
            if obj["Key"] != prefix
        )
        return ObjectPage(folders=folders, objects=objects, continuation_token=page.get("NextContinuationToken"))

    def _client_for(self: Self, region: str) -> S3Client:
        """The base client for the home (or unknown) region, a cached regional client otherwise."""
        if not region or region == self._client.meta.region_name or self._regional_client_factory is None:
            return self._client
        if region not in self._regional_clients:
            self._regional_clients[region] = self._regional_client_factory(region)
        return self._regional_clients[region]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_s3_gateway.py -v`
Expected: all PASS (the pre-existing tests too).

- [ ] **Step 6: Lint**

Run: `make lint`
Expected: ruff check, ruff format check, and ty check all pass. Fix anything reported.

- [ ] **Step 7: Commit**

```bash
git add src/awst/aws/models.py src/awst/aws/s3.py tests/test_s3_gateway.py
git commit -m "Add S3 list_objects with per-region client routing"
```

---

### Task 2: `human_size` formatting helper

**Files:**
- Modify: `src/awst/screens/formatting.py` (append)
- Test: `tests/test_formatting.py` (append)

**Interfaces:**
- Produces: `human_size(size: int) -> str` — `512 B`, `1.5 KB`, `1.0 MB`, … up to `PB`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_formatting.py`, and add `human_size` to the existing import from `awst.screens.formatting`:

```python
@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1048576, "1.0 MB"),
        (5 * 1024**3, "5.0 GB"),
        (2 * 1024**4, "2.0 TB"),
        (1024**5, "1.0 PB"),
    ],
)
def test_human_size(size: int, expected: str) -> None:
    assert human_size(size) == expected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --frozen pytest tests/test_formatting.py -v -k human_size`
Expected: FAIL with `ImportError: cannot import name 'human_size'`.

- [ ] **Step 3: Implement**

Append to `src/awst/screens/formatting.py`:

```python
_KIB = 1024


def human_size(size: int) -> str:
    """Render a byte count for humans, e.g. "1.5 KB"."""
    if size < _KIB:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= _KIB
        if value < _KIB:
            return f"{value:.1f} {unit}"
    return f"{value / _KIB:.1f} PB"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --frozen pytest tests/test_formatting.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
make lint
git add src/awst/screens/formatting.py tests/test_formatting.py
git commit -m "Add human_size formatting helper"
```

---

### Task 3: Load-more support in `ResourceListScreen`

**Files:**
- Modify: `src/awst/screens/resource_list.py`
- Test: Create `tests/test_resource_list_paging.py`

**Interfaces:**
- Consumes: the existing `ResourceListScreen` internals (`_fetch_items`, `_all_items`, `_render_rows`, `check_action`).
- Produces (for Task 4's `ObjectListScreen`):
  - Overridable `_has_more(self) -> bool`, default `False`.
  - Overridable `_list_more(self) -> list[ItemT]`, default raises `NotImplementedError`; called on a worker thread, never called unless `_has_more()` is true.
  - Binding `("m", "load_more", "More")`, hidden via `check_action` when `_has_more()` is false.
  - The count line gains a `+` suffix while `_has_more()` is true (e.g. `1000+ objects`, `3 of 1000+ objects`).
  - Existing behavior unchanged: `action_refresh` re-runs `_list` (subclasses treat `_list` as "first page from scratch"); existing subclasses (stacks, buckets, functions, queues) are unaffected because `_has_more()` defaults to `False`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resource_list_paging.py`:

```python
"""Tests for the load-more support in ResourceListScreen."""

from datetime import datetime  # noqa: TC003
from typing import Self

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from awst.screens.resource_list import ResourceListScreen


class PagedScreen(ResourceListScreen[str]):
    """Minimal paged list: each page is a list of item names."""

    TITLE = "Paged"
    COLUMNS = ("Name",)
    NOUN = "thing"

    def __init__(self: Self, pages: list[list[str]]) -> None:
        super().__init__()
        self._pages = pages
        self._next = 0

    def _list(self: Self) -> list[str]:
        self._next = 1
        return list(self._pages[0])

    def _has_more(self: Self) -> bool:
        return self._next < len(self._pages)

    def _list_more(self: Self) -> list[str]:
        page = self._pages[self._next]
        self._next += 1
        return list(page)

    def _row(self: Self, item: str, now: datetime) -> tuple[str, ...]:
        return (item,)

    def _item_name(self: Self, item: str) -> str:
        return item


class PagedApp(App[None]):
    """Minimal harness that opens a PagedScreen directly."""

    def __init__(self: Self, pages: list[list[str]]) -> None:
        super().__init__()
        self.pages = pages

    def on_mount(self: Self) -> None:
        self.push_screen(PagedScreen(self.pages))


async def _settle(app: App[None]) -> None:
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_more_binding_hidden_when_there_is_no_more() -> None:
    app = PagedApp([["a", "b"]])

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.check_action("load_more", ()) is False
        assert str(app.screen.query_one("#count", Static).content) == "2 things"


@pytest.mark.asyncio
async def test_count_shows_plus_suffix_while_more_pages_remain() -> None:
    app = PagedApp([["a", "b"], ["c"]])

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.check_action("load_more", ()) is True
        assert str(app.screen.query_one("#count", Static).content) == "2+ things"


@pytest.mark.asyncio
async def test_m_appends_the_next_page() -> None:
    app = PagedApp([["a", "b"], ["c"]])

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("m")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 3
        assert table.get_row_at(2)[0] == "c"
        assert str(app.screen.query_one("#count", Static).content) == "3 things"
        assert app.screen.check_action("load_more", ()) is False


@pytest.mark.asyncio
async def test_m_with_no_more_pages_does_nothing() -> None:
    app = PagedApp([["a"]])

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("m")
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 1


@pytest.mark.asyncio
async def test_refresh_restarts_from_the_first_page() -> None:
    app = PagedApp([["a", "b"], ["c"]])

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        await pilot.press("m")
        await _settle(app)
        await pilot.pause()
        assert app.screen.query_one(DataTable).row_count == 3

        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one(DataTable).row_count == 2
        assert str(app.screen.query_one("#count", Static).content) == "2+ things"


@pytest.mark.asyncio
async def test_filtered_count_keeps_plus_suffix() -> None:
    app = PagedApp([["apple", "banana"], ["cherry"]])

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"app")
        await pilot.pause()

        assert str(app.screen.query_one("#count", Static).content) == "1 of 2+ things"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_resource_list_paging.py -v`
Expected: FAILs — `check_action("load_more", ())` returns `True` (base default), counts lack `+`, `m` does nothing.

- [ ] **Step 3: Implement in `src/awst/screens/resource_list.py`**

Add the binding to `BINDINGS` (after the `"l"`/login entry):

```python
        ("m", "load_more", "More"),
```

Add the two hooks right after `_item_name` (line 57):

```python
    def _has_more(self: Self) -> bool:
        """Whether _list_more can fetch another page; paged subclasses override both."""
        return False

    def _list_more(self: Self) -> list[ItemT]:
        """Fetch the next page; called on a worker thread, only when _has_more() is true."""
        raise NotImplementedError
```

Extend `check_action` to gate the new binding:

```python
    def check_action(self: Self, action: str, parameters: tuple[object, ...]) -> bool | None:  # noqa: ARG002
        if action == "login":
            return self._show_login
        if action == "load_more":
            return self._has_more()
        return True
```

Add the worker and action (after `_fetch_items`):

```python
    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_more(self: Self) -> list[ItemT]:
        return self._list_more()

    def action_load_more(self: Self) -> None:
        if not self._has_more():
            return
        self.query_one("#count", Static).update("loading more…")
        self._fetch_more()
```

Rework `on_worker_state_changed` to handle both workers (replace the whole method):

```python
    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.worker.name not in {"_fetch_items", "_fetch_more"}:
            return
        if event.state == WorkerState.SUCCESS:
            self._show_login = False
            was_loaded = self._loaded
            self._loaded = True
            result = event.worker.result or []
            if event.worker.name == "_fetch_more":
                self._all_items = [*self._all_items, *result]
            else:
                self._all_items = result
            self.refresh_bindings()
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
```

(Behavioral notes: `refresh_bindings()` moved before `_render_rows` and now runs on every success so the `More` binding appears/disappears; a failed `_fetch_more` lands in the existing `_show_error` loaded-path — a toast — and `_render_rows()` there restores the count text over "loading more…". Both `@work` decorators share the default worker group, so `exclusive=True` means a refresh cancels an in-flight load-more and vice versa; cancelled workers reach neither branch.)

Update the count line in `_render_rows` (replace the last three lines of the method):

```python
        total = len(self._all_items)
        noun = self.NOUN if total == 1 else f"{self.NOUN}s"
        suffix = "+" if self._has_more() else ""
        count = f"{len(visible)} of {total}{suffix} {noun}" if query else f"{total}{suffix} {noun}"
        self.query_one("#count", Static).update(count)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_resource_list_paging.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the whole suite — the base is shared by every list screen**

Run: `uv run --frozen pytest`
Expected: all PASS (stacks/buckets/functions/queues screens must be unaffected).

- [ ] **Step 6: Lint and commit**

```bash
make lint
git add src/awst/screens/resource_list.py tests/test_resource_list_paging.py
git commit -m "Add opt-in load-more paging to ResourceListScreen"
```

---

### Task 4: `ObjectListScreen` and test fakes

**Files:**
- Create: `src/awst/screens/objects.py`
- Modify: `tests/fakes.py`
- Test: Create `tests/test_object_list_screen.py`

**Interfaces:**
- Consumes: `ObjectPage`/`ObjectSummary` (Task 1), `human_size` (Task 2), base load-more hooks (Task 3), existing `relative_age`.
- Produces (for Task 5):
  - `ObjectLister` protocol with `list_objects(bucket: str, region: str, prefix: str = "", continuation_token: str | None = None) -> ObjectPage`.
  - `ObjectListScreen(gateway: ObjectLister, bucket: str, region: str, prefix: str = "")`.
  - `FakeS3Gateway` gains `object_pages: dict[tuple[str, str | None], ObjectPage]` (keyed by `(prefix, continuation_token)`), `objects_error: AwsError | None`, a `list_objects` method, and an `object_calls: list[tuple[str, str, str, str | None]]` call log.
  - `make_object(key: str, size: int = 2048) -> ObjectSummary` factory (modified = `_CREATED`).

- [ ] **Step 1: Extend the fakes**

In `tests/fakes.py`, add `ObjectPage` and `ObjectSummary` to the runtime import from `awst.aws.models` (the big import at the top). Then add after `make_bucket`:

```python
def make_object(key: str, size: int = 2048) -> ObjectSummary:
    """An object summary with sensible defaults for list-screen tests."""
    return ObjectSummary(key=key, size=size, modified=_CREATED)
```

Extend `FakeS3Gateway.__init__` with two new keyword parameters (after `empty_gate`) and two new attributes:

```python
        object_pages: dict[tuple[str, str | None], ObjectPage] | None = None,
        objects_error: AwsError | None = None,
```

and in the body:

```python
        self.object_pages = object_pages or {}
        self.objects_error = objects_error
        self.object_calls: list[tuple[str, str, str, str | None]] = []
```

Add the method to `FakeS3Gateway` (after `list_buckets`):

```python
    def list_objects(
        self: Self,
        bucket: str,
        region: str,
        prefix: str = "",
        continuation_token: str | None = None,
    ) -> ObjectPage:
        self.object_calls.append((bucket, region, prefix, continuation_token))
        if self.objects_error is not None:
            raise self.objects_error
        empty = ObjectPage(folders=(), objects=(), continuation_token=None)
        return self.object_pages.get((prefix, continuation_token), empty)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_object_list_screen.py`:

```python
"""Tests for the S3 object list screen."""

from typing import Self

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from awst.aws.models import AwsError, ObjectPage
from awst.screens.objects import ObjectListScreen
from tests.fakes import FakeS3Gateway, make_object


class ObjectScreenApp(App[None]):
    """Minimal harness that opens the object list for bucket "assets" directly."""

    def __init__(self: Self, gateway: FakeS3Gateway, prefix: str = "") -> None:
        super().__init__()
        self.gateway = gateway
        self.prefix = prefix

    def on_mount(self: Self) -> None:
        self.push_screen(ObjectListScreen(self.gateway, "assets", "eu-west-1", self.prefix))


async def _settle(app: App[None]) -> None:
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_renders_folders_first_with_sizes_and_blank_folder_cells() -> None:
    page = ObjectPage(folders=("docs/",), objects=(make_object("readme.md", size=1536),), continuation_token=None)
    app = ObjectScreenApp(FakeS3Gateway(object_pages={("", None): page}))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert table.get_row_at(0) == ["docs/", "", ""]
        assert table.get_row_at(1)[0] == "readme.md"
        assert table.get_row_at(1)[1] == "1.5 KB"
        assert app.gateway.object_calls == [("assets", "eu-west-1", "", None)]


@pytest.mark.asyncio
async def test_names_are_relative_to_the_prefix() -> None:
    page = ObjectPage(
        folders=("docs/2026/",),
        objects=(make_object("docs/guide.md"),),
        continuation_token=None,
    )
    app = ObjectScreenApp(FakeS3Gateway(object_pages={("docs/", None): page}), prefix="docs/")

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.get_row_at(0)[0] == "2026/"
        assert table.get_row_at(1)[0] == "guide.md"


@pytest.mark.asyncio
async def test_enter_on_folder_drills_down_and_escape_pops_back() -> None:
    root = ObjectPage(folders=("docs/",), objects=(), continuation_token=None)
    docs = ObjectPage(folders=(), objects=(make_object("docs/guide.md"),), continuation_token=None)
    app = ObjectScreenApp(FakeS3Gateway(object_pages={("", None): root, ("docs/", None): docs}))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        parent = app.screen

        await pilot.press("enter")
        await _settle(app)
        await pilot.pause()

        assert isinstance(app.screen, ObjectListScreen)
        assert app.screen is not parent
        assert app.gateway.object_calls[-1] == ("assets", "eu-west-1", "docs/", None)
        assert app.screen.query_one(DataTable).get_row_at(0)[0] == "guide.md"

        await pilot.press("escape")
        await pilot.pause()

        assert app.screen is parent


@pytest.mark.asyncio
async def test_enter_on_an_object_does_nothing() -> None:
    page = ObjectPage(folders=(), objects=(make_object("readme.md"),), continuation_token=None)
    app = ObjectScreenApp(FakeS3Gateway(object_pages={("", None): page}))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        screen = app.screen

        await pilot.press("enter")
        await pilot.pause()

        assert app.screen is screen
        assert app.gateway.object_calls == [("assets", "eu-west-1", "", None)]


@pytest.mark.asyncio
async def test_m_loads_the_next_page_with_the_token() -> None:
    first = ObjectPage(folders=(), objects=(make_object("a.txt"), make_object("b.txt")), continuation_token="t1")
    second = ObjectPage(folders=(), objects=(make_object("c.txt"),), continuation_token=None)
    app = ObjectScreenApp(FakeS3Gateway(object_pages={("", None): first, ("", "t1"): second}))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert str(app.screen.query_one("#count", Static).content) == "2+ objects"

        await pilot.press("m")
        await _settle(app)
        await pilot.pause()

        assert app.gateway.object_calls[-1] == ("assets", "eu-west-1", "", "t1")
        assert app.screen.query_one(DataTable).row_count == 3
        assert str(app.screen.query_one("#count", Static).content) == "3 objects"


@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    app = ObjectScreenApp(FakeS3Gateway(objects_error=AwsError("access denied")))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.screen.query_one("#error", Static)

        assert panel.display is True
        assert "access denied" in str(panel.content)


@pytest.mark.asyncio
async def test_sub_title_shows_bucket_and_prefix() -> None:
    app = ObjectScreenApp(FakeS3Gateway(), prefix="docs/")

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.sub_title == "assets/docs/"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_object_list_screen.py -v`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'awst.screens.objects'`.

- [ ] **Step 4: Implement the screen**

Create `src/awst/screens/objects.py`:

```python
"""S3 object list screen: one prefix level of one bucket."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Self

from textual.widgets import DataTable  # noqa: TC002 -- needed at runtime: Textual inspects handler annotations

from awst.aws.models import ObjectSummary
from awst.screens.formatting import human_size, relative_age
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from datetime import datetime

    from awst.aws.models import ObjectPage


class ObjectLister(Protocol):
    """The slice of the S3 gateway the object browser needs."""

    def list_objects(
        self: Self,
        bucket: str,
        region: str,
        prefix: str = "",
        continuation_token: str | None = None,
    ) -> ObjectPage: ...


@dataclass(frozen=True, slots=True)
class FolderEntry:
    """A common prefix one level below the current one."""

    prefix: str  # the full prefix, ending "/"


type ObjectEntry = FolderEntry | ObjectSummary


class ObjectListScreen(ResourceListScreen[ObjectEntry]):
    """Read-only listing of one prefix level; Enter drills into folders, m loads more."""

    TITLE = "S3 objects"
    COLUMNS = ("Name", "Size", "Modified")
    NOUN = "object"

    def __init__(self: Self, gateway: ObjectLister, bucket: str, region: str, prefix: str = "") -> None:
        super().__init__()
        self._gateway = gateway
        self._bucket = bucket
        self._region = region
        self._prefix = prefix
        self._continuation_token: str | None = None
        self.sub_title = f"{bucket}/{prefix}"

    def _list(self: Self) -> list[ObjectEntry]:
        page = self._gateway.list_objects(self._bucket, self._region, self._prefix)
        self._continuation_token = page.continuation_token
        return self._entries(page)

    def _has_more(self: Self) -> bool:
        return self._continuation_token is not None

    def _list_more(self: Self) -> list[ObjectEntry]:
        page = self._gateway.list_objects(self._bucket, self._region, self._prefix, self._continuation_token)
        self._continuation_token = page.continuation_token
        return self._entries(page)

    def _entries(self: Self, page: ObjectPage) -> list[ObjectEntry]:
        return [*(FolderEntry(prefix) for prefix in page.folders), *page.objects]

    def _row(self: Self, item: ObjectEntry, now: datetime) -> tuple[str, ...]:
        if isinstance(item, FolderEntry):
            return (item.prefix[len(self._prefix) :], "", "")
        return (item.key[len(self._prefix) :], human_size(item.size), relative_age(item.modified, now))

    def _item_name(self: Self, item: ObjectEntry) -> str:
        return item.prefix if isinstance(item, FolderEntry) else item.key

    def on_data_table_row_selected(self: Self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        # Folder row keys end with the delimiter; object keys at this level never do
        # (a key ending "/" rolls up into CommonPrefixes when listing with Delimiter="/").
        if name is not None and name.endswith("/"):
            self.app.push_screen(ObjectListScreen(self._gateway, self._bucket, self._region, name))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_object_list_screen.py -v`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
make lint
git add src/awst/screens/objects.py tests/fakes.py tests/test_object_list_screen.py
git commit -m "Add S3 object browser screen"
```

---

### Task 5: Wire Enter-on-bucket, the app's regional factory, and docs

**Files:**
- Modify: `src/awst/screens/buckets.py`
- Modify: `src/awst/app.py:61-66` (the `s3_gateway` property)
- Modify: `tests/test_bucket_list_screen.py` (replace `test_enter_on_row_does_nothing`, around line 223)
- Modify: `CLAUDE.md` (project-overview and screens-list sentences)

**Interfaces:**
- Consumes: `ObjectListScreen` and `ObjectLister` (Task 4), `S3Gateway(client, regional_client_factory=…)` (Task 1).
- Produces: Enter on a bucket row pushes `ObjectListScreen(gateway, bucket.name, bucket.region)`; `BucketGateway` protocol now also extends `ObjectLister`.

- [ ] **Step 1: Write the failing test**

In `tests/test_bucket_list_screen.py`, delete `test_enter_on_row_does_nothing` and add in its place (also add `from awst.screens.objects import ObjectListScreen` to the imports):

```python
@pytest.mark.asyncio
async def test_enter_on_bucket_opens_the_object_browser_in_its_region() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets", region="eu-central-1")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("enter")
        await _settle(app)
        await pilot.pause()

        assert isinstance(app.screen, ObjectListScreen)
        assert app.screen.sub_title == "assets/"
        assert gateway.object_calls == [("assets", "eu-central-1", "", None)]


@pytest.mark.asyncio
async def test_enter_with_no_rows_does_nothing() -> None:
    app = BucketScreenApp(FakeS3Gateway(buckets=[]))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, BucketListScreen)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_bucket_list_screen.py -v -k enter`
Expected: `test_enter_on_bucket_opens_the_object_browser_in_its_region` FAILS (still on `BucketListScreen`); the no-rows test passes.

- [ ] **Step 3: Implement the bucket-screen wiring**

In `src/awst/screens/buckets.py`:

Add the import (with the other `awst.screens` imports):

```python
from awst.screens.objects import ObjectLister, ObjectListScreen
```

Change the combined protocol:

```python
class BucketGateway(BucketLister, BucketEmptier, ObjectLister, Protocol):
    """Everything the bucket screens collectively need from S3."""
```

Add the handler to `BucketListScreen` (after `_item_name`, before `action_empty`):

```python
    def on_data_table_row_selected(self: Self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        bucket = next((item for item in self._all_items if item.name == name), None)
        if bucket is not None:
            self.app.push_screen(ObjectListScreen(self._gateway, bucket.name, bucket.region))
```

Note: `DataTable` is already imported at runtime in this file, which Textual's handler-annotation inspection needs.

- [ ] **Step 4: Wire the regional factory in the app**

In `src/awst/app.py`, replace the body of the `s3_gateway` property:

```python
    @property
    def s3_gateway(self: Self) -> BucketGateway:
        """The S3 gateway, built on first use from the default credential chain."""
        if self._s3_gateway is None:
            session = boto3.Session()
            self._s3_gateway = S3Gateway(
                session.client("s3"),
                regional_client_factory=lambda region: boto3.Session().client("s3", region_name=region),
            )
        return self._s3_gateway
```

(No new app test: the factory is a one-line boto3 construction; its routing/caching behavior is covered by the Task 1 gateway tests, and the protocol conformance by `ty check`.)

- [ ] **Step 5: Run the full suite**

Run: `uv run --frozen pytest`
Expected: all PASS.

- [ ] **Step 6: Update CLAUDE.md**

In the **Project overview** paragraph, change

> S3 (bucket list with an empty-bucket action)

to

> S3 (bucket list with an empty-bucket action and a read-only object browser: Enter drills into buckets and folders, `m` loads the next page, cross-region buckets use per-region clients)

In the **Architecture** bullet listing `src/awst/screens/`, add `objects.py` to the screen list, e.g. after `empty_bucket.py for the empty-bucket progress modal`:

> `objects.py` for the read-only S3 object browser (one screen per prefix level)

- [ ] **Step 7: Lint, full check, and commit**

```bash
make test
git add src/awst/screens/buckets.py src/awst/app.py tests/test_bucket_list_screen.py CLAUDE.md
git commit -m "Open the S3 object browser on Enter from the bucket list"
```

Expected: `make test` (lint + unit) fully green before committing.

---

## Verification checklist (after all tasks)

- [ ] `make test` passes (lint + full suite).
- [ ] `make coverage` stays above the 75% gate.
- [ ] Manual smoke (optional, needs AWS credentials): `uv run awst` → S3 → Enter a bucket → drill into a folder → escape back → `m` on a >1000-key level.
