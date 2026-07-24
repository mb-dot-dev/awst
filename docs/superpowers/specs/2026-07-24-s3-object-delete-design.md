# S3 object delete — design

2026-07-24

## Goal

Add a destructive delete to the read-only S3 object browser shipped in "bucket browsing":
`d` on a row permanently deletes either one object or an entire folder (common prefix),
including every version and delete marker. This is the app's third destructive operation,
after CloudFormation stack deletion and empty-bucket.

Object browsing itself is unchanged — no new navigation, no object detail view.

## Decisions

- **Target:** the cursor row, which is either an object key or a folder prefix. Folder delete
  is recursive over every key beneath the prefix.
- **Versioning:** purge all versions and delete markers, matching `empty_bucket`. A
  delete-marker-only delete would leave the row visible on a versioned bucket, which reads as
  a failed delete.
- **One flow for both targets:** confirm modal, then the same progress modal, whether the
  target is one key or a whole prefix. A single object usually completes in one batch and the
  modal just flashes; that is cheaper than maintaining two code paths and two error paths.
- **One progress modal for all three deletes.** `EmptyBucketScreen` is generalised into
  `DeleteObjectsScreen` rather than copied — the two would otherwise be ~90% identical.
- **One gateway loop.** The delete-while-listing loop moves into a private helper; the three
  public methods are thin wrappers over it.
- **`empty_bucket` becomes region-aware.** It currently uses only the home-region client, so
  emptying a cross-region bucket fails with S3's `PermanentRedirect`. The new helper already
  takes a region, so the fix is folded in here rather than left as an odd split where object
  delete honours the bucket's region and empty-bucket does not.

## Gateway — `src/awst/aws/s3.py`

A private helper holds the loop:

```python
def _delete_versions(
    self, bucket: str, region: str, prefix: str, match: Callable[[str], bool]
) -> Iterator[int]
```

- Resolves the client through the existing `_client_for(region)`.
- Repeatedly lists the first page of `list_object_versions(Bucket=bucket, Prefix=prefix,
  MaxKeys=1000)`, keeps the `Versions` and `DeleteMarkers` entries whose `Key` satisfies
  `match`, deletes them through `_delete_batch`, and yields the cumulative count — the same
  restart-after-each-batch strategy `empty_bucket` uses today. `_delete_batch` takes the
  resolved client as its first argument: it previously used `self._client` unconditionally,
  which would have routed the destructive `DeleteObjects` call to the home region even when
  the listing went to the bucket's own region.
- Stops when a page yields no matching items.
- Botocore errors map through `map_botocore_error`; per-key failures in the `delete_objects`
  response still raise `AwsError` naming the first failed key.

Three public wrappers replace the current `empty_bucket` body:

| Method | Prefix | Match |
| --- | --- | --- |
| `delete_object(bucket, region, key)` | `key` | `k == key` |
| `delete_prefix(bucket, region, prefix)` | `prefix` | everything |
| `empty_bucket(name, region)` | `""` | everything |

`delete_object` filters because `Prefix=key` also matches siblings that extend it
(`file.txt` also returns `file.txt.bak`). S3 returns keys lexicographically and `key` sorts
before any key extending it, so a page with no matching entries means the key's versions are
exhausted. That ordering assumption gets a comment in the code.

All three stay lazy generators: nothing is requested until iterated.

## Screens

### `screens/delete_objects.py` (renamed from `screens/empty_bucket.py`)

`EmptyBucketScreen` becomes `DeleteObjectsScreen(gateway, bucket, region, target)`. `target`
distinguishes the three deletes using the delimiter convention already relied on in
`objects.py` (folder row keys end in `/`, object keys at a level never do):

| `target` | Gateway call | Title |
| --- | --- | --- |
| `""` | `empty_bucket(bucket, region)` | `Emptying {bucket}` |
| ends `/` | `delete_prefix(bucket, region, target)` | `Deleting {target}` |
| otherwise | `delete_object(bucket, region, target)` | `Deleting {target}` |

Everything else carries over verbatim: the thread worker iterating the generator, progress
updates via `call_from_thread`, `escape` to cancel between batches, and the
`on_worker_state_changed` handling for success, cancellation, and `AwsError`.

Protocols follow the existing narrow-slice-plus-combination style. `delete_objects.py`
declares:

- `BucketEmptier` — `empty_bucket(name, region)`, carried over with the new signature.
- `ObjectDeleter` — `delete_object` and `delete_prefix`.
- `DeleteGateway(BucketEmptier, ObjectDeleter, Protocol)` — what the modal itself needs, since
  it may call any of the three.

### `screens/objects.py`

`ObjectListScreen` gains `BINDINGS = [("d", "delete", "Delete")]`.

`action_delete` reads the cursor row key via `self._cursor_name(...)` and returns early when
there is none — the same shape as `BucketListScreen.action_empty`, so no `check_action` gate
is added. It pushes the existing `ConfirmScreen`:

- object: `Permanently delete {key} and all its versions?`
- folder: `Permanently delete everything under {prefix}, including all versions?`

Both use the full key or prefix, not the name shown relative to the current level, so the
question is unambiguous.

On `y` it pushes `DeleteObjectsScreen(self._gateway, self._bucket, self._region, target)`;
when that dismisses it calls `action_refresh()`, which re-lists page one and resets the
continuation token. Screens further up the stack are deliberately not refreshed — deleting the
last object in a folder leaves a stale folder row on the parent screen until the user presses
`r` there.

The screen's gateway type widens from `ObjectLister` to
`ObjectBrowserGateway(ObjectLister, DeleteGateway, Protocol)`. That means it statically
promises `empty_bucket`, which the object browser itself never calls — the narrower
`ObjectLister + ObjectDeleter` cannot typecheck, because the shared modal selects its gateway
call from a runtime string and so must require all three methods. Making the modal take the
deletion source instead would let both sides stay precise; that is a deliberate follow-up, not
part of this change. `_on_delete_confirmed` carries a defensive guard refusing a falsy target,
since an empty target means "whole bucket" to the modal.

### `screens/buckets.py`

`BucketGateway` gains `ObjectDeleter` alongside its existing `BucketLister`, `BucketEmptier`,
and `ObjectLister` slices, so one gateway still satisfies every S3 screen. `action_empty` looks up
the cursor row's `BucketSummary` for its region — the way `on_data_table_row_selected` already
does — and pushes `DeleteObjectsScreen(self._gateway, bucket.name, bucket.region, "")`.
Confirmation wording and the post-dismiss refresh are unchanged.

## Errors and cancellation

No new error handling. Partial-failure and botocore errors surface as an error toast from the
modal's existing worker handler, which then dismisses back to the list.

`escape` cancels between batches, so a cancelled folder delete leaves the prefix partially
deleted. The existing "At least N objects already deleted" warning covers that case; the list
refresh on dismiss shows what survived.

## Testing

**Gateway** (`tests/test_s3_gateway.py`, moto `mock_aws` on a versioned bucket):

- `delete_object` removes every version of the exact key and leaves a sibling key that extends
  it (`file.txt.bak`) intact.
- `delete_prefix` removes everything beneath the prefix and leaves keys outside it intact.
- `delete_prefix` on a prefix with no keys yields nothing.
- Both yield cumulative counts and batch at 1000 keys.
- A cross-region call resolves through the regional client factory.
- Existing `empty_bucket` tests carry over with the new region argument.
- A `DeleteObjects` response containing `Errors` raises `AwsError` (botocore `Stubber`, as
  today).

**Screens** (`tests/test_object_list_screen.py`, plus the renamed
`tests/test_delete_objects_screen.py`):

- `d` on an object row confirms, then calls `delete_object` with the full key.
- `d` on a folder row calls `delete_prefix` with the full prefix.
- Declining the confirm calls neither.
- `d` with an empty table does nothing.
- Completing a delete refreshes the object list.
- A gateway `AwsError` surfaces as a notification and dismisses the modal.
- `DeleteObjectsScreen` with `target=""` still empties the bucket (existing tests, retargeted).

**Fakes** (`tests/fakes.py`): `FakeS3Gateway` records `delete_object` and `delete_prefix`
calls, reuses the existing batch/gate/error plumbing for progress and failure simulation, and
takes the new `empty_bucket(name, region)` signature.

`make test` (ruff, `ty`, pytest) must pass; coverage floor stays at 75%.

## Out of scope

- Object download, upload, rename, or copy.
- Restoring a deleted object or browsing versions.
- Multi-select delete of several rows in one action.
- Refreshing parent levels of the browse stack after a delete.
