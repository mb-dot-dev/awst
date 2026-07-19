# Empty bucket — design

2026-07-19

## Goal

Add the AWS console's "Empty bucket" capability to the S3 bucket list: permanently delete every
object, object version, and delete marker in the selected bucket. This is the app's first
destructive operation.

## Decisions

- **Scope:** delete all versions and delete markers (console-equivalent), so versioned buckets
  end up truly empty. `list_object_versions` handles versioned, unversioned, and
  versioning-suspended buckets uniformly.
- **Confirmation:** a simple yes/no modal (no typed phrase).
- **Progress:** the modal stays open during deletion, showing a running deleted-object count,
  and `escape` cancels between batches. Already-deleted objects stay deleted.
- **Gateway shape:** `empty_bucket` is a generator yielding progress, so the gateway owns all
  AWS orchestration and the screen owns only presentation and cancellation.

## Trigger & flow

`BucketListScreen` gains an `e` binding ("Empty") acting on the cursor row. It pushes the
existing reusable `ConfirmScreen` (already used for stack deletion) asking "Permanently delete
all objects, versions, and delete markers in {name}?" — `y` confirms, `escape` or `n` cancels.
On confirm, an `EmptyBucketScreen` progress modal (patterned on `SsoLoginScreen`) is pushed
and deletion starts. Nothing is deleted until `y`.

With no rows in the table, `e` does nothing. While the filter input has focus, keys go to the
input (standard Textual focus behavior), so typing "e" in the filter never triggers the action.

## Gateway

`S3Gateway.empty_bucket(name: str) -> Iterator[int]`:

- Paginates `list_object_versions` (returns both versions and delete markers).
- Calls `delete_objects` with up to 1000 keys per batch.
- Yields the cumulative deleted count after each batch.
- Maps botocore errors through the existing `map_botocore_error`.
- Per-key errors in the `delete_objects` response (partial failures) raise an `AwsError`
  naming the first failed key.
- An already-empty bucket completes with zero yields; the UI reports "0 objects deleted".

The `BucketLister` protocol in `screens/buckets.py` gains `empty_bucket`, keeping the screen
free of boto3 imports.

## Progress & cancel

After `y`, the modal swaps to a progress state ("Deleting… N objects deleted") driven by a
thread worker (`@work(thread=True, exclusive=True, exit_on_error=False)`) that iterates the
generator and updates the label via `call_from_thread` after each batch. `escape` during
progress cancels the worker; the loop checks `worker.is_cancelled` between batches, so
cancellation is clean at a batch boundary. Confirm bindings are disabled once deletion starts;
only cancel remains.

## Completion & errors

- **Success:** modal dismisses; the bucket list shows a toast
  ("Emptied my-bucket: 12,345 objects deleted") and refreshes.
- **Cancel mid-delete:** toast "Cancelled: N objects were already deleted", then refresh.
- **`AwsError`:** error toast using the existing message/hint format; modal dismisses.

## Testing

- **Gateway (moto `mock_aws`):** unversioned bucket, versioned bucket with delete markers,
  already-empty bucket, and batch pagination with >1000 objects. Partial-failure mapping via
  botocore `Stubber` if moto cannot simulate it.
- **UI (pytest-asyncio + `run_test()` pilot):** extend `tests/fakes.py` with a `FakeS3Gateway`
  whose `empty_bucket` yields scripted counts and can raise, driving: confirm flow,
  cancel-before-confirm, progress updates, cancel mid-delete, error toast, and post-success
  refresh.
