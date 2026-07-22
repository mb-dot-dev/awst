# List Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the incremental-load pattern already used by the S3 object browser (fetch one page up front, `m` loads the next page) to the four list screens that currently exhaust the AWS paginator internally before showing a row: CloudFormation stacks, S3 buckets, Lambda functions, and SQS queues.

**Architecture:** Each of the four gateways changes from "loop the paginator to exhaustion, sort, return a list" to "make one API call for one page, return a generic `Page[T]`". Each of the four list screens gains the same `_has_more`/`_list_more` shape `ObjectListScreen` already has. Two new hooks on the shared `ResourceListScreen` base — `_sort_key` (keeps the merged list alphabetical across pages) and `_auto_fetch_on_filter` (fetches every remaining page the moment someone filters, since large lists can't otherwise be searched — pressing `m` doesn't work while the filter `Input` has focus) — do the new work generically, so no screen needs bespoke sort/filter code.

**Tech Stack:** Python 3.14, Textual, boto3/botocore, moto (`mock_aws`) and `botocore.stub.Stubber` for gateway tests, pytest-asyncio + Textual's `run_test()` pilot for screen tests.

## Global Constraints

- Requires Python >=3.14; dependency management and packaging use `uv`.
- Run `make lint` (`ruff check`, `ruff format --check`, `ty check`) before considering any change complete; `make test` runs lint then the unit suite and mirrors CI.
- Coverage must stay at or above 75% (`make coverage`).
- Screens never import boto3/botocore directly — only gateway modules do.
- Ruff's rule set includes flake8-annotations, bandit, bugbear, complexity, and pathlib checks at a 120-char line length; `tests/**/*.py` may use `assert`, hardcoded values, local imports, and `print`.
- `moto`'s mocks do not implement pagination for `list_buckets`, `list_functions`, `describe_stacks`, or `list_queues` (verified: `MaxBuckets`/`MaxItems`/`MaxResults` are silently ignored and no continuation token is ever returned) — any test asserting real page-splitting or token forwarding must use `botocore.stub.Stubber`, matching the existing precedent for `sso-oidc` gateway tests.
- moto does **not** reorder results either — `list_buckets`/`describe_stacks`/`list_functions`/`list_queues` all return items in creation order (verified locally), which is what makes it possible to test "gateway no longer sorts, screen does" cleanly.
- Work happens on a feature branch, e.g. `feature/list-pagination`.

---

### Task 1: Generic `Page[T]` model

**Files:**
- Modify: `src/awst/aws/models.py:59-68` (insert after `SsoToken`, before `BucketSummary`)
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `Page[T]` — frozen, slotted, generic dataclass with `items: tuple[T, ...]` and `next_token: str | None` (`None` means last page). Used by every gateway/screen task below.

- [ ] **Step 1: Create the feature branch**

Run: `git checkout -b feature/list-pagination`
Expected: `Switched to a new branch 'feature/list-pagination'`

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_models.py` (needs `Page` added to the existing `from awst.aws.models import (...)` block):

```python
def test_page_is_immutable() -> None:
    page = Page(items=("a", "b"), next_token="t1")

    with pytest.raises(AttributeError):
        page.next_token = None  # type: ignore[misc]  # ty: ignore[invalid-assignment]


def test_page_next_token_is_none_for_the_last_page() -> None:
    page: Page[str] = Page(items=("a",), next_token=None)

    assert page.items == ("a",)
    assert page.next_token is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'Page' from 'awst.aws.models'`

- [ ] **Step 4: Implement `Page[T]`**

In `src/awst/aws/models.py`, insert between the `SsoToken` class and the `BucketSummary` class:

```python
@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of a paginated listing."""

    items: tuple[T, ...]
    next_token: str | None  # None when this is the last page
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 6: Lint and commit**

Run: `make lint`
Expected: no errors

```bash
git add src/awst/aws/models.py tests/test_models.py
git commit -m "Add generic Page[T] model for paginated gateway results"
```

---

### Task 2: Base screen support for stable sort and search-triggered full fetch

**Files:**
- Modify: `src/awst/screens/resource_list.py`
- Test: `tests/test_resource_list_paging.py`

**Interfaces:**
- Consumes: nothing new — builds on the existing `_has_more()`/`_list_more()` contract already used by `ObjectListScreen`.
- Produces (for every later task to override):
  - `_sort_key(self) -> Callable[[ItemT], Any] | None` — default `None` (no re-sort, today's behavior).
  - `_auto_fetch_on_filter(self) -> bool` — default `True`.
  - Both hooks are read generically by the base; no other file needs to call them directly.

- [ ] **Step 1: Write the failing tests**

In `tests/test_resource_list_paging.py`, add `TYPE_CHECKING`/`Callable` and a `sort` flag to the existing fixtures, then three new tests. First, update the imports and `PagedScreen`/`PagedApp`:

```python
from datetime import datetime  # noqa: TC003
import threading
from typing import TYPE_CHECKING, Self

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from awst.aws.models import AwsError
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from collections.abc import Callable


class PagedScreen(ResourceListScreen[str]):
    """Minimal paged list: each page is a list of item names.

    ``fail_more`` makes ``_list_more`` raise instead of returning a page; ``gate`` (set by
    ``started`` once entered) lets tests freeze a load-more mid-flight, mirroring the
    ``empty_gate`` precedent in ``tests/fakes.py``. ``sort`` opts into alphabetical
    re-sorting after every fetch, mirroring the paginated list screens.
    """

    TITLE = "Paged"
    COLUMNS = ("Name",)
    NOUN = "thing"

    def __init__(
        self: Self,
        pages: list[list[str]],
        fail_more: AwsError | None = None,
        gate: threading.Event | None = None,
        started: threading.Event | None = None,
        sort: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        super().__init__()
        self._pages = pages
        self._next = 0
        self._fail_more = fail_more
        self._gate = gate
        self._started = started
        self._sort = sort
        self.more_calls = 0

    def _list(self: Self) -> list[str]:
        self._next = 1
        return list(self._pages[0])

    def _has_more(self: Self) -> bool:
        return self._next < len(self._pages)

    def _list_more(self: Self) -> list[str]:
        self.more_calls += 1
        if self._started is not None:
            self._started.set()
        if self._fail_more is not None:
            raise self._fail_more
        if self._gate is not None:
            self._gate.wait(timeout=5)
        page = self._pages[self._next]
        self._next += 1
        return list(page)

    def _sort_key(self: Self) -> Callable[[str], str] | None:
        return (lambda item: item) if self._sort else None

    def _row(self: Self, item: str, now: datetime) -> tuple[str, ...]:  # noqa: ARG002
        return (item,)

    def _item_name(self: Self, item: str) -> str:
        return item


class PagedApp(App[None]):
    """Minimal harness that opens a PagedScreen directly."""

    def __init__(
        self: Self,
        pages: list[list[str]],
        fail_more: AwsError | None = None,
        gate: threading.Event | None = None,
        started: threading.Event | None = None,
        sort: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        super().__init__()
        self.pages = pages
        self.fail_more = fail_more
        self.gate = gate
        self.started = started
        self.sort = sort

    def on_mount(self: Self) -> None:
        self.push_screen(
            PagedScreen(
                self.pages, fail_more=self.fail_more, gate=self.gate, started=self.started, sort=self.sort
            )
        )
```

Then add these tests at the end of the file:

```python
@pytest.mark.asyncio
async def test_m_press_keeps_accumulated_items_sorted() -> None:
    app = PagedApp([["banana"], ["apple"]], sort=True)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("m")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "apple"
        assert table.get_row_at(1)[0] == "banana"


@pytest.mark.asyncio
async def test_typing_a_filter_fetches_every_remaining_page() -> None:
    app = PagedApp([["apple"], ["banana"], ["cherry"]])

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PagedScreen)

        await pilot.press("slash")
        await pilot.press(*"c")
        await _settle(app)
        await pilot.pause()

        assert screen.more_calls == 2  # both remaining pages fetched in one go
        assert screen.check_action("load_more", ()) is False
        table = app.screen.query_one(DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "cherry"


@pytest.mark.asyncio
async def test_count_shows_searching_while_fetching_remaining_pages() -> None:
    gate = threading.Event()
    started = threading.Event()
    app = PagedApp([["apple"], ["banana"]], gate=gate, started=started)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"a")
        assert started.wait(timeout=5)  # the worker has actually entered _list_more
        await pilot.pause()

        assert str(app.screen.query_one("#count", Static).content) == "searching…"

        gate.set()
        await _settle(app)
        await pilot.pause()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_resource_list_paging.py -v`
Expected: FAIL — `test_m_press_keeps_accumulated_items_sorted` fails on row order (no sorting yet); `test_typing_a_filter_fetches_every_remaining_page` fails because `more_calls == 0` (no auto-fetch yet); `test_count_shows_searching_while_fetching_remaining_pages` times out waiting on `started` because `_list_more` is never called on filter.

- [ ] **Step 3: Implement the base class changes**

In `src/awst/screens/resource_list.py`, update the imports:

```python
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Self

from textual import work
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Static
from textual.worker import Worker, WorkerState

from awst.aws.models import AwsError, CredentialsError

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.text import Text
    from textual.app import ComposeResult
    from textual.binding import BindingType
```

Add two new hooks right after `_list_more`:

```python
    def _list_more(self: Self) -> list[ItemT]:
        """Fetch the next page; called on a worker thread, only when _has_more() is true."""
        raise NotImplementedError

    def _sort_key(self: Self) -> Callable[[ItemT], Any] | None:
        """Key to keep _all_items sorted after every fetch; None (the default) means don't re-sort."""
        return None

    def _auto_fetch_on_filter(self: Self) -> bool:
        """Whether a non-empty filter should trigger fetching every remaining page."""
        return True
```

Add a new worker right after `_fetch_more`:

```python
    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_more(self: Self) -> list[ItemT]:
        return self._list_more()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _fetch_remaining(self: Self) -> list[ItemT]:
        items: list[ItemT] = []
        while self._has_more():
            items.extend(self._list_more())
        return items
```

Update `on_worker_state_changed` to recognize the new worker and apply the sort key:

```python
    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.worker.name not in {"_fetch_items", "_fetch_more", "_fetch_remaining"}:
            return
        is_more = event.worker.name in {"_fetch_more", "_fetch_remaining"}
        if event.state == WorkerState.SUCCESS:
            self._show_login = False
            was_loaded = self._loaded
            self._loaded = True
            result = event.worker.result or []
            if is_more:
                self._all_items = [*self._all_items, *result]
                self._loading_more = False
            else:
                self._all_items = result
            key = self._sort_key()
            if key is not None:
                self._all_items = sorted(self._all_items, key=key)
            self.refresh_bindings()
            table = self.query_one("#items", DataTable)
            table.loading = False
            self._render_rows()
            if not was_loaded:
                table.focus()
            self._maybe_fetch_remaining()
        elif event.state == WorkerState.ERROR:
            if is_more:
                self._loading_more = False
                self.refresh_bindings()
            error = event.worker.error
            if isinstance(error, AwsError):
                self._show_error(error)
            elif error is not None:
                raise error
        elif event.state == WorkerState.CANCELLED and is_more:
            # The load-more thread may still be running (cancellation is cooperative); clear the
            # flag now so the user can retry without waiting for the zombie thread to finish.
            self._loading_more = False
            self.refresh_bindings()
```

Add a helper and call it from `on_input_changed`:

```python
    def on_input_changed(self: Self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._render_rows()
            self._maybe_fetch_remaining()

    def _maybe_fetch_remaining(self: Self) -> None:
        query = self.query_one("#filter", Input).value.strip()
        if query and self._auto_fetch_on_filter() and self._has_more() and not self._loading_more:
            self._loading_more = True
            self.refresh_bindings()
            self.query_one("#count", Static).update("searching…")
            self._fetch_remaining()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_resource_list_paging.py -v`
Expected: PASS (all tests in the file, including the pre-existing ones)

- [ ] **Step 5: Lint and commit**

Run: `make lint`
Expected: no errors

```bash
git add src/awst/screens/resource_list.py tests/test_resource_list_paging.py
git commit -m "Add stable-sort and search-triggered full fetch to ResourceListScreen"
```

---

### Task 3: CloudFormation stack pagination

**Files:**
- Modify: `src/awst/aws/cloudformation.py:38-48`
- Modify: `src/awst/screens/stacks.py`
- Modify: `tests/fakes.py:82-121` (`FakeCloudFormationGateway`)
- Test: `tests/test_cloudformation_gateway.py`
- Test: `tests/test_stack_list_screen.py`

**Interfaces:**
- Consumes: `Page[T]` (Task 1); `_sort_key`/`_auto_fetch_on_filter`/`_has_more`/`_list_more` contract (Task 2).
- Produces: `CloudFormationGateway.list_stacks(next_token: str | None = None) -> Page[StackSummary]`; `FakeCloudFormationGateway.list_stacks` with the same signature, plus a `pages: dict[str | None, Page[StackSummary]] | None` constructor arg and a `next_tokens: list[str | None]` call log, for later tasks' tests to imitate.

- [ ] **Step 1: Write the failing gateway tests**

In `tests/test_cloudformation_gateway.py`, add `from botocore.stub import Stubber` to the imports and `Page` to the models import. Replace `test_list_stacks_returns_all_stacks_sorted_by_name` with:

```python
@mock_aws
def test_list_stacks_returns_stacks_in_api_order_unsorted() -> None:
    client = boto3.client("cloudformation", region_name="eu-west-1")
    for name in ("gamma", "alpha", "beta"):
        client.create_stack(StackName=name, TemplateBody=TEMPLATE)

    page = _gateway().list_stacks()

    assert [stack.name for stack in page.items] == ["gamma", "alpha", "beta"]
    assert page.next_token is None
```

Update `test_list_stacks_maps_fields` (`stack = _gateway().list_stacks()[0]` → `stack = _gateway().list_stacks().items[0]`) and `test_delete_stack_deletes_the_stack` (`assert _gateway().list_stacks() == []` → `assert _gateway().list_stacks().items == ()`).

Add:

```python
def test_list_stacks_forwards_next_token() -> None:
    client = boto3.client("cloudformation", region_name="eu-west-1")
    created = datetime(2026, 1, 1, tzinfo=UTC)
    with Stubber(client) as stubber:
        stubber.add_response(
            "describe_stacks",
            {
                "Stacks": [{"StackName": "alpha", "StackStatus": "CREATE_COMPLETE", "CreationTime": created}],
                "NextToken": "t1",
            },
            {},
        )
        stubber.add_response(
            "describe_stacks",
            {"Stacks": [{"StackName": "beta", "StackStatus": "CREATE_COMPLETE", "CreationTime": created}]},
            {"NextToken": "t1"},
        )

        first = CloudFormationGateway(client).list_stacks()
        second = CloudFormationGateway(client).list_stacks(first.next_token)

    assert first.next_token == "t1"
    assert [stack.name for stack in second.items] == ["beta"]
    assert second.next_token is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_cloudformation_gateway.py -v`
Expected: FAIL — `list_stacks` still returns a plain `list`, has no `next_token` param, and still sorts.

- [ ] **Step 3: Implement the gateway change**

In `src/awst/aws/cloudformation.py`, add `Page` to the `from awst.aws.models import (...)` block, then replace `list_stacks`:

```python
    def list_stacks(self: Self, next_token: str | None = None) -> Page[StackSummary]:
        """Return one page of stacks in the account/region.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            if next_token is None:
                response = self._client.describe_stacks()
            else:
                response = self._client.describe_stacks(NextToken=next_token)
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        stacks = tuple(_to_summary(stack) for stack in response["Stacks"])
        return Page(items=stacks, next_token=response.get("NextToken"))
```

- [ ] **Step 4: Run gateway tests to verify they pass**

Run: `uv run --frozen pytest tests/test_cloudformation_gateway.py -v`
Expected: PASS

- [ ] **Step 5: Update the fake gateway**

In `tests/fakes.py`, add `Page` to the `from awst.aws.models import (...)` block, then replace `FakeCloudFormationGateway`:

```python
class FakeCloudFormationGateway:
    """In-memory stand-in for the real CloudFormation gateway."""

    def __init__(  # noqa: PLR0913
        self: Self,
        stacks: list[StackSummary] | None = None,
        error: AwsError | None = None,
        detail: StackDetail | None = None,
        detail_error: AwsError | None = None,
        delete_error: AwsError | None = None,
        pages: dict[str | None, Page[StackSummary]] | None = None,
    ) -> None:
        self.stacks = stacks or []
        self.error = error
        self.detail = detail
        self.detail_error = detail_error
        self.delete_error = delete_error
        self.pages = pages
        self.calls = 0
        self.next_tokens: list[str | None] = []
        self.detail_calls: list[str] = []
        self.deleted: list[str] = []

    def list_stacks(self: Self, next_token: str | None = None) -> Page[StackSummary]:
        self.calls += 1
        self.next_tokens.append(next_token)
        if self.error is not None:
            raise self.error
        if self.pages is not None:
            return self.pages.get(next_token, Page(items=(), next_token=None))
        return Page(items=tuple(self.stacks), next_token=None)

    def get_stack_detail(self: Self, name: str) -> StackDetail:
        self.detail_calls.append(name)
        if self.detail_error is not None:
            raise self.detail_error
        if self.detail is None:
            message = f"Stack {name} does not exist."
            raise StackNotFoundError(message)
        return self.detail

    def delete_stack(self: Self, name: str) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(name)
```

- [ ] **Step 6: Update the stack list screen**

In `src/awst/screens/stacks.py`:

```python
"""CloudFormation stack list screen."""

from typing import TYPE_CHECKING, Protocol, Self

from rich.text import Text
from textual.widgets import DataTable  # noqa: TC002 -- needed at runtime: Textual inspects handler annotations
from textual.worker import get_current_worker

from awst.aws.models import Page, StackSummary
from awst.screens.formatting import relative_age, status_style
from awst.screens.resource_list import ResourceListScreen
from awst.screens.stack_detail import StackDetailScreen, StackInspector

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime


class StackLister(Protocol):
    """The slice of the CloudFormation gateway this screen needs."""

    def list_stacks(self: Self, next_token: str | None = None) -> Page[StackSummary]: ...


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
        self._next_token: str | None = None

    def _list(self: Self) -> list[StackSummary]:
        page = self._gateway.list_stacks()
        # A cancelled worker's result is discarded by the base anyway; skip the state write so a
        # zombie thread that outlives its cancellation can't clobber a token set by a later fetch.
        if not get_current_worker().is_cancelled:
            self._next_token = page.next_token
        return list(page.items)

    def _has_more(self: Self) -> bool:
        return self._next_token is not None

    def _list_more(self: Self) -> list[StackSummary]:
        page = self._gateway.list_stacks(self._next_token)
        if not get_current_worker().is_cancelled:
            self._next_token = page.next_token
        return list(page.items)

    def _sort_key(self: Self) -> Callable[[StackSummary], str]:
        return lambda stack: stack.name

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

- [ ] **Step 7: Write the failing screen tests**

In `tests/test_stack_list_screen.py`, add `Page` to the models import. Rename `test_renders_one_row_per_stack_in_gateway_order` to `test_renders_one_row_per_stack_with_name_and_status` (unchanged body — it's now a plain rendering test, not an order test). Add:

```python
@pytest.mark.asyncio
async def test_renders_rows_sorted_by_name_even_when_gateway_order_differs() -> None:
    gateway = FakeCloudFormationGateway(stacks=[make_stack("prod-network"), make_stack("prod-api")])
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.get_row_at(0)[0] == "prod-api"
        assert table.get_row_at(1)[0] == "prod-network"


@pytest.mark.asyncio
async def test_m_appends_and_resorts_the_next_page() -> None:
    first = Page(items=(make_stack("prod-network"),), next_token="t1")
    second = Page(items=(make_stack("prod-api"),), next_token=None)
    gateway = FakeCloudFormationGateway(pages={None: first, "t1": second})
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert str(app.screen.query_one("#count", Static).content) == "1+ stack"

        await pilot.press("m")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert gateway.next_tokens == [None, "t1"]
        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "prod-api"  # re-sorted after the second page arrived
        assert table.get_row_at(1)[0] == "prod-network"
        assert str(app.screen.query_one("#count", Static).content) == "2 stacks"


@pytest.mark.asyncio
async def test_filter_fetches_remaining_pages_to_find_matches_beyond_the_first_page() -> None:
    first = Page(items=(make_stack("prod-network"),), next_token="t1")
    second = Page(items=(make_stack("prod-api"),), next_token=None)
    gateway = FakeCloudFormationGateway(pages={None: first, "t1": second})
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"api")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert gateway.next_tokens == [None, "t1"]
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "prod-api"
        assert app.screen.check_action("load_more", ()) is False
```

- [ ] **Step 8: Run all stack tests to verify they pass**

Run: `uv run --frozen pytest tests/test_stack_list_screen.py tests/test_cloudformation_gateway.py -v`
Expected: PASS

- [ ] **Step 9: Lint and commit**

Run: `make lint`
Expected: no errors

```bash
git add src/awst/aws/cloudformation.py src/awst/screens/stacks.py tests/fakes.py \
        tests/test_cloudformation_gateway.py tests/test_stack_list_screen.py
git commit -m "Paginate CloudFormation stack listing"
```

---

### Task 4: S3 bucket pagination

**Files:**
- Modify: `src/awst/aws/s3.py:29-39`
- Modify: `src/awst/screens/buckets.py`
- Modify: `tests/fakes.py` (`FakeS3Gateway`)
- Test: `tests/test_s3_gateway.py`
- Test: `tests/test_bucket_list_screen.py`

**Interfaces:**
- Consumes: `Page[T]` (Task 1); base hooks (Task 2); same `pages`/`next_tokens` fake convention established in Task 3.
- Produces: `S3Gateway.list_buckets(next_token: str | None = None) -> Page[BucketSummary]`; `FakeS3Gateway.list_buckets` with the same signature plus a `bucket_pages: dict[str | None, Page[BucketSummary]] | None` constructor arg (named distinctly from the pre-existing `object_pages`, which is unrelated — it pages the *object* browser, not the bucket list).

- [ ] **Step 1: Write the failing gateway tests**

In `tests/test_s3_gateway.py`, add `Page` to the models import. Replace `test_list_buckets_returns_all_buckets_sorted_by_name` with:

```python
@mock_aws
def test_list_buckets_returns_buckets_in_api_order_unsorted() -> None:
    for name in ("gamma", "alpha", "beta"):
        _create_bucket(name)

    page = _gateway().list_buckets()

    assert [bucket.name for bucket in page.items] == ["gamma", "alpha", "beta"]
    assert page.next_token is None
```

Update `test_list_buckets_maps_fields` (`bucket = _gateway().list_buckets()[0]` → `bucket = _gateway().list_buckets().items[0]`) and `test_list_buckets_returns_empty_list_for_empty_account` (`assert _gateway().list_buckets() == []` → `assert _gateway().list_buckets().items == ()`).

Add:

```python
def test_list_buckets_forwards_continuation_token() -> None:
    client = boto3.client("s3", region_name="eu-west-1")
    created = datetime(2026, 1, 1, tzinfo=UTC)
    with Stubber(client) as stubber:
        stubber.add_response(
            "list_buckets", {"Buckets": [{"Name": "alpha", "CreationDate": created}], "ContinuationToken": "t1"}, {}
        )
        stubber.add_response(
            "list_buckets", {"Buckets": [{"Name": "beta", "CreationDate": created}]}, {"ContinuationToken": "t1"}
        )

        first = S3Gateway(client).list_buckets()
        second = S3Gateway(client).list_buckets(first.next_token)

    assert first.next_token == "t1"
    assert [bucket.name for bucket in second.items] == ["beta"]
    assert second.next_token is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_s3_gateway.py -v -k list_buckets`
Expected: FAIL

- [ ] **Step 3: Implement the gateway change**

In `src/awst/aws/s3.py`, add `Page` to the `from awst.aws.models import (...)` block, then replace `list_buckets`:

```python
    def list_buckets(self: Self, next_token: str | None = None) -> Page[BucketSummary]:
        """Return one page of buckets in the account.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            if next_token is None:
                response = self._client.list_buckets()
            else:
                response = self._client.list_buckets(ContinuationToken=next_token)
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        buckets = tuple(_to_summary(bucket) for bucket in response.get("Buckets", []))
        return Page(items=buckets, next_token=response.get("ContinuationToken"))
```

- [ ] **Step 4: Run gateway tests to verify they pass**

Run: `uv run --frozen pytest tests/test_s3_gateway.py -v -k list_buckets`
Expected: PASS

- [ ] **Step 5: Update the fake gateway**

In `tests/fakes.py`, add `Page` to the models import (already added in Task 3, no-op if so), then update `FakeS3Gateway`'s constructor and `list_buckets`:

```python
class FakeS3Gateway:
    """In-memory stand-in for the real S3 gateway."""

    def __init__(  # noqa: PLR0913
        self: Self,
        buckets: list[BucketSummary] | None = None,
        error: AwsError | None = None,
        bucket_pages: dict[str | None, Page[BucketSummary]] | None = None,
        empty_batches: list[int] | None = None,
        empty_error: AwsError | None = None,
        empty_gate: threading.Event | None = None,
        object_pages: dict[tuple[str, str | None], ObjectPage] | None = None,
        objects_error: AwsError | None = None,
        objects_gate: threading.Event | None = None,
    ) -> None:
        self.buckets = buckets or []
        self.error = error
        self.bucket_pages = bucket_pages
        self.empty_batches = empty_batches or []
        self.empty_error = empty_error
        self.empty_gate = empty_gate
        self.object_pages = object_pages or {}
        self.objects_error = objects_error
        self.objects_gate = objects_gate
        self.object_calls: list[tuple[str, str, str, str | None]] = []
        self.calls = 0
        self.next_tokens: list[str | None] = []
        self.emptied: list[str] = []

    def list_buckets(self: Self, next_token: str | None = None) -> Page[BucketSummary]:
        self.calls += 1
        self.next_tokens.append(next_token)
        if self.error is not None:
            raise self.error
        if self.bucket_pages is not None:
            return self.bucket_pages.get(next_token, Page(items=(), next_token=None))
        return Page(items=tuple(self.buckets), next_token=None)
```

(`list_objects` and `empty_bucket` below it are unchanged.)

- [ ] **Step 6: Update the bucket list screen**

In `src/awst/screens/buckets.py`, add imports and pagination support:

```python
"""S3 bucket list screen."""

from functools import partial
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from textual.widgets import DataTable
from textual.worker import get_current_worker

from awst.aws.models import BucketSummary, Page
from awst.screens.confirm import ConfirmScreen
from awst.screens.empty_bucket import BucketEmptier, EmptyBucketScreen
from awst.screens.formatting import relative_age
from awst.screens.objects import ObjectLister, ObjectListScreen
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from textual.binding import BindingType


class BucketLister(Protocol):
    """The slice of the S3 gateway the list itself needs."""

    def list_buckets(self: Self, next_token: str | None = None) -> Page[BucketSummary]: ...


class BucketGateway(BucketLister, BucketEmptier, ObjectLister, Protocol):
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
        self._next_token: str | None = None

    def _list(self: Self) -> list[BucketSummary]:
        page = self._gateway.list_buckets()
        if not get_current_worker().is_cancelled:
            self._next_token = page.next_token
        return list(page.items)

    def _has_more(self: Self) -> bool:
        return self._next_token is not None

    def _list_more(self: Self) -> list[BucketSummary]:
        page = self._gateway.list_buckets(self._next_token)
        if not get_current_worker().is_cancelled:
            self._next_token = page.next_token
        return list(page.items)

    def _sort_key(self: Self) -> Callable[[BucketSummary], str]:
        return lambda bucket: bucket.name

    def _row(self: Self, item: BucketSummary, now: datetime) -> tuple[str, ...]:
        return (item.name, item.region, relative_age(item.created, now))

    def _item_name(self: Self, item: BucketSummary) -> str:
        return item.name

    def on_data_table_row_selected(self: Self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        bucket = next((item for item in self._all_items if item.name == name), None)
        if bucket is not None:
            self.app.push_screen(ObjectListScreen(self._gateway, bucket.name, bucket.region))

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

- [ ] **Step 7: Write the failing screen tests**

In `tests/test_bucket_list_screen.py`, add `Page` to the models import, then add:

```python
@pytest.mark.asyncio
async def test_renders_rows_sorted_by_name_even_when_gateway_order_differs() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("prod-logs"), make_bucket("prod-assets")])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.get_row_at(0)[0] == "prod-assets"
        assert table.get_row_at(1)[0] == "prod-logs"


@pytest.mark.asyncio
async def test_m_appends_and_resorts_the_next_page() -> None:
    first = Page(items=(make_bucket("prod-logs"),), next_token="t1")
    second = Page(items=(make_bucket("prod-assets"),), next_token=None)
    gateway = FakeS3Gateway(bucket_pages={None: first, "t1": second})
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert str(app.screen.query_one("#count", Static).content) == "1+ bucket"

        await pilot.press("m")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert gateway.next_tokens == [None, "t1"]
        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "prod-assets"
        assert table.get_row_at(1)[0] == "prod-logs"


@pytest.mark.asyncio
async def test_filter_fetches_remaining_pages_to_find_matches_beyond_the_first_page() -> None:
    first = Page(items=(make_bucket("prod-logs"),), next_token="t1")
    second = Page(items=(make_bucket("prod-assets"),), next_token=None)
    gateway = FakeS3Gateway(bucket_pages={None: first, "t1": second})
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"assets")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert gateway.next_tokens == [None, "t1"]
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "prod-assets"
```

- [ ] **Step 8: Run all bucket tests to verify they pass**

Run: `uv run --frozen pytest tests/test_bucket_list_screen.py tests/test_s3_gateway.py tests/test_object_list_screen.py tests/test_empty_bucket_screen.py -v`
Expected: PASS

- [ ] **Step 9: Lint and commit**

Run: `make lint`
Expected: no errors

```bash
git add src/awst/aws/s3.py src/awst/screens/buckets.py tests/fakes.py \
        tests/test_s3_gateway.py tests/test_bucket_list_screen.py
git commit -m "Paginate S3 bucket listing"
```

---

### Task 5: Lambda function pagination

**Files:**
- Modify: `src/awst/aws/lambda_.py:22-33`
- Modify: `src/awst/screens/functions.py`
- Modify: `tests/fakes.py` (`FakeLambdaGateway`)
- Test: `tests/test_lambda_gateway.py`
- Test: `tests/test_function_list_screen.py`

**Interfaces:**
- Consumes: `Page[T]` (Task 1); base hooks (Task 2).
- Produces: `LambdaGateway.list_functions(next_token: str | None = None) -> Page[FunctionSummary]`; `FakeLambdaGateway.list_functions` with the same signature plus `pages`/`next_tokens`.

- [ ] **Step 1: Write the failing gateway tests**

In `tests/test_lambda_gateway.py`, add `Page` to the models import and `from botocore.stub import Stubber` (Stubber is already imported — reuse it). Replace `test_list_functions_returns_all_functions_sorted_by_name` with:

```python
@mock_aws
def test_list_functions_returns_functions_in_api_order_unsorted() -> None:
    role_arn = _role_arn()
    for name in ("gamma", "alpha", "beta"):
        _create_function(name, role_arn)

    page = _gateway().list_functions()

    assert [function.name for function in page.items] == ["gamma", "alpha", "beta"]
    assert page.next_token is None
```

Update `test_list_functions_maps_fields` (`function = _gateway().list_functions()[0]` → `function = _gateway().list_functions().items[0]`) and `test_list_functions_returns_empty_list_for_empty_account` (`assert _gateway().list_functions() == []` → `assert _gateway().list_functions().items == ()`).

Add:

```python
def test_list_functions_forwards_marker() -> None:
    client = boto3.client("lambda", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_response(
            "list_functions",
            {
                "Functions": [{"FunctionName": "alpha", "LastModified": "2026-01-01T12:00:00.000+0000"}],
                "NextMarker": "t1",
            },
            {},
        )
        stubber.add_response(
            "list_functions",
            {"Functions": [{"FunctionName": "beta", "LastModified": "2026-01-01T12:00:00.000+0000"}]},
            {"Marker": "t1"},
        )

        first = LambdaGateway(client).list_functions()
        second = LambdaGateway(client).list_functions(first.next_token)

    assert first.next_token == "t1"
    assert [function.name for function in second.items] == ["beta"]
    assert second.next_token is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_lambda_gateway.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the gateway change**

In `src/awst/aws/lambda_.py`, add `Page` to the `from awst.aws.models import FunctionSummary` line (`from awst.aws.models import FunctionSummary, Page`), then replace `list_functions`:

```python
    def list_functions(self: Self, next_token: str | None = None) -> Page[FunctionSummary]:
        """Return one page of functions in the region.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            if next_token is None:
                response = self._client.list_functions()
            else:
                response = self._client.list_functions(Marker=next_token)
            functions = tuple(_to_summary(function) for function in response.get("Functions", []))
        except (BotoCoreError, ClientError, ValueError) as error:
            # ValueError: _to_summary rejects an unparseable LastModified string
            raise map_botocore_error(error) from error
        return Page(items=functions, next_token=response.get("NextMarker"))
```

- [ ] **Step 4: Run gateway tests to verify they pass**

Run: `uv run --frozen pytest tests/test_lambda_gateway.py -v`
Expected: PASS

- [ ] **Step 5: Update the fake gateway**

In `tests/fakes.py`, replace `FakeLambdaGateway`:

```python
class FakeLambdaGateway:
    """In-memory stand-in for the real Lambda gateway."""

    def __init__(
        self: Self,
        functions: list[FunctionSummary] | None = None,
        error: AwsError | None = None,
        pages: dict[str | None, Page[FunctionSummary]] | None = None,
    ) -> None:
        self.functions = functions or []
        self.error = error
        self.pages = pages
        self.calls = 0
        self.next_tokens: list[str | None] = []

    def list_functions(self: Self, next_token: str | None = None) -> Page[FunctionSummary]:
        self.calls += 1
        self.next_tokens.append(next_token)
        if self.error is not None:
            raise self.error
        if self.pages is not None:
            return self.pages.get(next_token, Page(items=(), next_token=None))
        return Page(items=tuple(self.functions), next_token=None)
```

- [ ] **Step 6: Update the function list screen**

In `src/awst/screens/functions.py`:

```python
"""Lambda function list screen."""

from typing import TYPE_CHECKING, Protocol, Self

from textual.worker import get_current_worker

from awst.aws.models import FunctionSummary, Page
from awst.screens.formatting import relative_age
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime


class FunctionLister(Protocol):
    """The slice of the Lambda gateway this screen needs."""

    def list_functions(self: Self, next_token: str | None = None) -> Page[FunctionSummary]: ...


class FunctionListScreen(ResourceListScreen[FunctionSummary]):
    """Read-only list of the region's Lambda functions."""

    TITLE = "Lambda functions"
    COLUMNS = ("Name", "Runtime", "Memory", "Timeout", "Modified")
    NOUN = "function"

    def __init__(self: Self, gateway: FunctionLister) -> None:
        super().__init__()
        self._gateway = gateway
        self._next_token: str | None = None

    def _list(self: Self) -> list[FunctionSummary]:
        page = self._gateway.list_functions()
        if not get_current_worker().is_cancelled:
            self._next_token = page.next_token
        return list(page.items)

    def _has_more(self: Self) -> bool:
        return self._next_token is not None

    def _list_more(self: Self) -> list[FunctionSummary]:
        page = self._gateway.list_functions(self._next_token)
        if not get_current_worker().is_cancelled:
            self._next_token = page.next_token
        return list(page.items)

    def _sort_key(self: Self) -> Callable[[FunctionSummary], str]:
        return lambda function: function.name

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

- [ ] **Step 7: Write the failing screen tests**

In `tests/test_function_list_screen.py`, add `Page` to the models import, then add:

```python
@pytest.mark.asyncio
async def test_renders_rows_sorted_by_name_even_when_gateway_order_differs() -> None:
    gateway = FakeLambdaGateway(functions=[make_function("send-mail"), make_function("resize-images")])
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.get_row_at(0)[0] == "resize-images"
        assert table.get_row_at(1)[0] == "send-mail"


@pytest.mark.asyncio
async def test_m_appends_and_resorts_the_next_page() -> None:
    first = Page(items=(make_function("send-mail"),), next_token="t1")
    second = Page(items=(make_function("resize-images"),), next_token=None)
    gateway = FakeLambdaGateway(pages={None: first, "t1": second})
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert str(app.screen.query_one("#count", Static).content) == "1+ function"

        await pilot.press("m")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert gateway.next_tokens == [None, "t1"]
        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "resize-images"
        assert table.get_row_at(1)[0] == "send-mail"


@pytest.mark.asyncio
async def test_filter_fetches_remaining_pages_to_find_matches_beyond_the_first_page() -> None:
    first = Page(items=(make_function("send-mail"),), next_token="t1")
    second = Page(items=(make_function("resize-images"),), next_token=None)
    gateway = FakeLambdaGateway(pages={None: first, "t1": second})
    app = FunctionScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"resize")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert gateway.next_tokens == [None, "t1"]
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "resize-images"
```

- [ ] **Step 8: Run all function tests to verify they pass**

Run: `uv run --frozen pytest tests/test_function_list_screen.py tests/test_lambda_gateway.py -v`
Expected: PASS

- [ ] **Step 9: Lint and commit**

Run: `make lint`
Expected: no errors

```bash
git add src/awst/aws/lambda_.py src/awst/screens/functions.py tests/fakes.py \
        tests/test_lambda_gateway.py tests/test_function_list_screen.py
git commit -m "Paginate Lambda function listing"
```

---

### Task 6: SQS queue pagination

**Files:**
- Modify: `src/awst/aws/sqs.py:20-30`
- Modify: `src/awst/screens/queues.py`
- Modify: `tests/fakes.py` (`FakeSqsGateway`)
- Test: `tests/test_sqs_gateway.py`
- Test: `tests/test_queue_list_screen.py`

**Interfaces:**
- Consumes: `Page[T]` (Task 1); base hooks (Task 2).
- Produces: `SqsGateway.list_queues(next_token: str | None = None) -> Page[QueueSummary]`; `FakeSqsGateway.list_queues` with the same signature plus `pages`/`next_tokens`.

- [ ] **Step 1: Write the failing gateway tests**

In `tests/test_sqs_gateway.py`, add `Page` to the models import and `from botocore.stub import Stubber` (already imported). Replace `test_list_queues_returns_all_queues_sorted_by_name` with:

```python
@mock_aws
def test_list_queues_returns_queues_in_api_order_unsorted() -> None:
    for name in ("gamma", "alpha", "beta"):
        _create_queue(name)

    page = _gateway().list_queues()

    assert [queue.name for queue in page.items] == ["gamma", "alpha", "beta"]
    assert page.next_token is None
```

Update `test_list_queues_marks_fifo_queues` (`queues = _gateway().list_queues()` → `queues = _gateway().list_queues().items`) and `test_list_queues_returns_empty_list_for_empty_region` (`assert _gateway().list_queues() == []` → `assert _gateway().list_queues().items == ()`).

Add:

```python
def test_list_queues_forwards_next_token() -> None:
    client = boto3.client("sqs", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_response(
            "list_queues",
            {"QueueUrls": ["https://sqs.eu-west-1.amazonaws.com/123456789012/alpha"], "NextToken": "t1"},
            {},
        )
        stubber.add_response(
            "list_queues", {"QueueUrls": ["https://sqs.eu-west-1.amazonaws.com/123456789012/beta"]}, {"NextToken": "t1"}
        )

        first = SqsGateway(client).list_queues()
        second = SqsGateway(client).list_queues(first.next_token)

    assert first.next_token == "t1"
    assert [queue.name for queue in second.items] == ["beta"]
    assert second.next_token is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_sqs_gateway.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the gateway change**

In `src/awst/aws/sqs.py`, add `Page` to the `from awst.aws.models import QueueSummary` line (`from awst.aws.models import Page, QueueSummary`), then replace `list_queues`:

```python
    def list_queues(self: Self, next_token: str | None = None) -> Page[QueueSummary]:
        """Return one page of queues in the region.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            if next_token is None:
                response = self._client.list_queues()
            else:
                response = self._client.list_queues(NextToken=next_token)
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        queues = tuple(_to_summary(url) for url in response.get("QueueUrls", []))
        return Page(items=queues, next_token=response.get("NextToken"))
```

- [ ] **Step 4: Run gateway tests to verify they pass**

Run: `uv run --frozen pytest tests/test_sqs_gateway.py -v`
Expected: PASS

- [ ] **Step 5: Update the fake gateway**

In `tests/fakes.py`, replace `FakeSqsGateway`:

```python
class FakeSqsGateway:
    """In-memory stand-in for the real SQS gateway."""

    def __init__(
        self: Self,
        queues: list[QueueSummary] | None = None,
        error: AwsError | None = None,
        pages: dict[str | None, Page[QueueSummary]] | None = None,
    ) -> None:
        self.queues = queues or []
        self.error = error
        self.pages = pages
        self.calls = 0
        self.next_tokens: list[str | None] = []

    def list_queues(self: Self, next_token: str | None = None) -> Page[QueueSummary]:
        self.calls += 1
        self.next_tokens.append(next_token)
        if self.error is not None:
            raise self.error
        if self.pages is not None:
            return self.pages.get(next_token, Page(items=(), next_token=None))
        return Page(items=tuple(self.queues), next_token=None)
```

- [ ] **Step 6: Update the queue list screen**

In `src/awst/screens/queues.py`:

```python
"""SQS queue list screen."""

from typing import TYPE_CHECKING, Protocol, Self

from textual.worker import get_current_worker

from awst.aws.models import Page, QueueSummary
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime


class QueueLister(Protocol):
    """The slice of the SQS gateway this screen needs."""

    def list_queues(self: Self, next_token: str | None = None) -> Page[QueueSummary]: ...


class QueueListScreen(ResourceListScreen[QueueSummary]):
    """Read-only list of the region's SQS queues."""

    TITLE = "SQS queues"
    COLUMNS = ("Name", "Type")
    NOUN = "queue"

    def __init__(self: Self, gateway: QueueLister) -> None:
        super().__init__()
        self._gateway = gateway
        self._next_token: str | None = None

    def _list(self: Self) -> list[QueueSummary]:
        page = self._gateway.list_queues()
        if not get_current_worker().is_cancelled:
            self._next_token = page.next_token
        return list(page.items)

    def _has_more(self: Self) -> bool:
        return self._next_token is not None

    def _list_more(self: Self) -> list[QueueSummary]:
        page = self._gateway.list_queues(self._next_token)
        if not get_current_worker().is_cancelled:
            self._next_token = page.next_token
        return list(page.items)

    def _sort_key(self: Self) -> Callable[[QueueSummary], str]:
        return lambda queue: queue.name

    def _row(self: Self, item: QueueSummary, now: datetime) -> tuple[str, ...]:  # noqa: ARG002 - no timestamp column
        return (item.name, "FIFO" if item.is_fifo else "Standard")

    def _item_name(self: Self, item: QueueSummary) -> str:
        return item.name
```

- [ ] **Step 7: Write the failing screen tests**

In `tests/test_queue_list_screen.py`, add `Page` to the models import, then add:

```python
@pytest.mark.asyncio
async def test_renders_rows_sorted_by_name_even_when_gateway_order_differs() -> None:
    gateway = FakeSqsGateway(queues=[make_queue("prod-orders"), make_queue("prod-mail")])
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.get_row_at(0)[0] == "prod-mail"
        assert table.get_row_at(1)[0] == "prod-orders"


@pytest.mark.asyncio
async def test_m_appends_and_resorts_the_next_page() -> None:
    first = Page(items=(make_queue("prod-orders"),), next_token="t1")
    second = Page(items=(make_queue("prod-mail"),), next_token=None)
    gateway = FakeSqsGateway(pages={None: first, "t1": second})
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert str(app.screen.query_one("#count", Static).content) == "1+ queue"

        await pilot.press("m")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert gateway.next_tokens == [None, "t1"]
        assert table.row_count == 2
        assert table.get_row_at(0)[0] == "prod-mail"
        assert table.get_row_at(1)[0] == "prod-orders"


@pytest.mark.asyncio
async def test_filter_fetches_remaining_pages_to_find_matches_beyond_the_first_page() -> None:
    first = Page(items=(make_queue("prod-orders"),), next_token="t1")
    second = Page(items=(make_queue("prod-mail"),), next_token=None)
    gateway = FakeSqsGateway(pages={None: first, "t1": second})
    app = QueueScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"mail")
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert gateway.next_tokens == [None, "t1"]
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "prod-mail"
```

- [ ] **Step 8: Run all queue tests to verify they pass**

Run: `uv run --frozen pytest tests/test_queue_list_screen.py tests/test_sqs_gateway.py -v`
Expected: PASS

- [ ] **Step 9: Lint and commit**

Run: `make lint`
Expected: no errors

```bash
git add src/awst/aws/sqs.py src/awst/screens/queues.py tests/fakes.py \
        tests/test_sqs_gateway.py tests/test_queue_list_screen.py
git commit -m "Paginate SQS queue listing"
```

---

### Task 7: Opt the S3 object browser out of auto-fetch-on-filter

**Files:**
- Modify: `src/awst/screens/objects.py`
- Test: `tests/test_object_list_screen.py`

**Interfaces:**
- Consumes: `_auto_fetch_on_filter` hook (Task 2).
- Produces: `ObjectListScreen._auto_fetch_on_filter() -> False` — confirms the one deliberate exception to Task 2's default, since a prefix can hold millions of keys.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_object_list_screen.py`:

```python
@pytest.mark.asyncio
async def test_filtering_does_not_fetch_remaining_pages() -> None:
    first = ObjectPage(folders=(), objects=(make_object("a.txt"), make_object("b.txt")), continuation_token="t1")
    app = ObjectScreenApp(FakeS3Gateway(object_pages={("", None): first}))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"a")
        await _settle(app)
        await pilot.pause()

        assert app.gateway.object_calls == [("assets", "eu-west-1", "", None)]  # no auto-fetch of the next page
        assert app.screen.query_one(DataTable).row_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --frozen pytest tests/test_object_list_screen.py -v -k does_not_fetch_remaining`
Expected: FAIL — the base class default (`_auto_fetch_on_filter` returning `True`) makes it fetch the second page, so `object_calls` has two entries.

- [ ] **Step 3: Implement the opt-out**

In `src/awst/screens/objects.py`, add the override to `ObjectListScreen` (placed next to `_has_more`/`_list_more`):

```python
    def _has_more(self: Self) -> bool:
        return self._continuation_token is not None

    def _auto_fetch_on_filter(self: Self) -> bool:
        return False  # a prefix can hold millions of keys; stay scoped to loaded objects
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --frozen pytest tests/test_object_list_screen.py -v`
Expected: PASS (full file)

- [ ] **Step 5: Lint and commit**

Run: `make lint`
Expected: no errors

```bash
git add src/awst/screens/objects.py tests/test_object_list_screen.py
git commit -m "Keep the S3 object browser's filter scoped to loaded objects"
```

---

### Task 8: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `uv run --frozen pytest`
Expected: all tests PASS

- [ ] **Step 2: Run the full local check**

Run: `make test`
Expected: `ruff check`, `ruff format --check`, `ty check`, and the unit suite all pass

- [ ] **Step 3: Run coverage**

Run: `make coverage`
Expected: coverage report generated at `build/coverage.xml`, overall coverage at or above 75%

- [ ] **Step 4: Fix any issues found**

If any step above fails, fix the underlying issue (not by weakening assertions or skipping checks) and re-run the affected command until it passes. Commit any fixes:

```bash
git add -A
git commit -m "Fix lint/coverage issues found in full verification"
```

(Skip this step's commit if nothing needed fixing.)
