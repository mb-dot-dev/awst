# List pagination — design

**Date:** 2026-07-22
**Status:** Approved

## Goal

Extend the incremental-load pattern already used by the S3 object browser
(`ObjectListScreen`: fetch one page up front, `m` loads the next page) to the
four list screens that currently exhaust the AWS paginator internally before
showing a single row: CloudFormation stacks, S3 buckets, Lambda functions, and
SQS queues. Large accounts should see the first page immediately instead of
blocking until every page has been fetched.

Out of scope: the CloudFormation stack-detail screen's nested
resources/events tables, and the S3 object browser's own paging (already
done).

## Decisions

- **Page size:** whatever a single underlying API call returns for each
  service; no artificial `MaxItems`/`PageSize` override. CloudFormation's
  `DescribeStacks` does not even accept one.
- **Sort order:** preserve the existing "sorted by name" guarantee across
  incremental loads by re-sorting the accumulated list after every fetch,
  rather than relying on API order (which AWS does not guarantee is
  alphabetical for any of these four operations).
- **Page-result model:** one generic `Page[T]` dataclass shared by all four
  gateways, rather than four near-identical ones. `ObjectPage` stays separate
  since its shape genuinely differs (folders + objects).
- **Filtering:** a non-empty filter value on a paginated screen triggers
  fetching every remaining page in the background, so substring search always
  covers the whole account/region rather than silently missing matches on
  pages not yet loaded. This does **not** apply to the S3 object browser,
  where a prefix can hold millions of keys.
- **Branch:** implemented on a feature branch (e.g. `feature/list-pagination`).

## Data model (`src/awst/aws/models.py`)

```python
@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of a paginated listing."""

    items: tuple[T, ...]
    next_token: str | None  # None when this is the last page
```

## Gateway changes

Each of the four `list_*` methods changes from "loop the paginator to
exhaustion, sort, return `list[T]`" to "make exactly one API call for one
page, return `Page[T]`". They call the client method directly (not
`get_paginator(...).paginate()`, which is designed to be exhausted, not
resumed from a caller-held token) — matching what `S3Gateway.list_objects`
already does. The `sorted(...)` call is removed from every gateway; sorting
moves to the screen layer (see below).

- **`cloudformation.py`** — `list_stacks(self, next_token: str | None = None) -> Page[StackSummary]`.
  Calls `describe_stacks(NextToken=next_token)` when a token is given, else
  `describe_stacks()`. Reads the response's `NextToken`.
- **`s3.py`** — `list_buckets(self, next_token: str | None = None) -> Page[BucketSummary]`.
  Calls `list_buckets(ContinuationToken=next_token)` when a token is given,
  else `list_buckets()`. Reads the response's `ContinuationToken`.
- **`lambda_.py`** — `list_functions(self, next_token: str | None = None) -> Page[FunctionSummary]`.
  Calls `list_functions(Marker=next_token)` when a token is given, else
  `list_functions()`. Reads the response's `NextMarker`.
- **`sqs.py`** — `list_queues(self, next_token: str | None = None) -> Page[QueueSummary]`.
  Calls `list_queues(NextToken=next_token)` when a token is given, else
  `list_queues()`. Reads the response's `NextToken`.

All four keep raising `AwsError` via `map_botocore_error` for any
credential/network/API failure, unchanged.

## Base screen extension (`src/awst/screens/resource_list.py`)

Two new overridable hooks, both defaulting to today's behavior so
`ObjectListScreen` needs no changes beyond one explicit opt-out:

```python
def _sort_key(self) -> Callable[[ItemT], Any] | None:
    """Key to keep _all_items sorted after every fetch; None means don't re-sort."""
    return None


def _auto_fetch_on_filter(self) -> bool:
    """Whether a non-empty filter should trigger fetching every remaining page."""
    return True
```

- After `self._all_items` is set (fresh load) or extended (load-more) in
  `on_worker_state_changed`, if `_sort_key()` returns non-`None` the base
  re-sorts `self._all_items` with it before rendering.
- A new worker, `_fetch_remaining`, loops calling `_list_more()` while
  `_has_more()` is true, accumulating every page into one result list within a
  single background thread (so token-state updates stay sequential and safe,
  same as the existing cancellation-guarded pattern in `objects.py`).
  `on_worker_state_changed` treats its result like a load-more result
  (appended, then re-sorted if `_sort_key()` is set).
- A shared check runs after `on_input_changed` fires with a non-empty filter
  value, and again after any successful page load while a filter is already
  active: if `_auto_fetch_on_filter()` is true, `_has_more()` is true, and
  nothing is already loading, it starts `_fetch_remaining()`. This covers both
  "user filters an already-partially-loaded list" and "user typed a filter
  before the first page even finished loading."
- While remaining pages load, the count line shows a "searching…" message
  (reusing the existing "loading more…" treatment).
- `ObjectListScreen` overrides `_auto_fetch_on_filter` to return `False` and
  does not override `_sort_key` (unchanged natural S3 key order).

No changes to `_render_rows()`'s filtering logic itself — it already scans
`self._all_items`; the above just guarantees that set is complete before a
paginated screen's filter is trusted.

## The four list screens

`stacks.py`, `buckets.py`, `functions.py`, `queues.py` each gain the same
shape already used by `ObjectListScreen`:

- An instance attribute `self._next_token: str | None = None`.
- `_has_more(self) -> bool` returning `self._next_token is not None`.
- `_list(self)` / `_list_more(self)` call the gateway with the current token,
  and — guarded by `get_current_worker().is_cancelled`, mirroring the
  existing race-safety comment in `objects.py` — stash the new token before
  returning `list(page.items)`.
- `_sort_key(self)` returning a key on the item's `name` (e.g.
  `attrgetter("name")`).

The `m` binding, the "N of M+" count suffix, and the "loading more…"/
"searching…" indicators all come from the base class — no new per-screen UI
code.

`StackLister`, `BucketLister`, `FunctionLister`, `QueueLister` protocols
update their `list_*` signature/return type to match.

## Error handling

Unchanged from the existing generic behavior: a failed first page shows the
full-screen error panel (with the SSO login offer where applicable); a failed
load-more or fetch-remaining page shows a transient notification and leaves
already-loaded rows in place, restoring the loading flag so the user can
retry.

## Testing

- **Gateway tests** (moto `mock_aws` / Stubber where moto lacks support):
  assert one page is returned per call, the caller's `next_token` is
  forwarded to the correct request parameter, the response's page-token field
  becomes `Page.next_token`, and a single call no longer loops through every
  page itself.
- **Fakes** (`tests/fakes.py`): each `Fake*Gateway`'s list method takes
  `next_token` and returns `Page[T]`. Default behavior with no explicit
  multi-page setup returns everything as one page with `next_token=None`, so
  existing tests that just pass a flat list keep working unchanged; a
  dict-of-pages-by-token option (mirroring `FakeS3Gateway.object_pages`) lets
  new tests exercise multi-page scenarios.
- **Screen tests**: extend the existing "second page arrives on `m`" coverage
  (currently only on `ObjectListScreen`) to stacks/buckets/functions/queues.
  Add cases for: an item that sorts earlier alphabetically arriving on a
  later page still lands in the correct visual position; typing a filter on
  a partially-loaded list triggers fetching the rest and then filters over
  everything; the S3 object browser's filter behavior is unchanged (no
  auto-fetch).
- Full `make test` / `make coverage` run at the end, per CLAUDE.md.
