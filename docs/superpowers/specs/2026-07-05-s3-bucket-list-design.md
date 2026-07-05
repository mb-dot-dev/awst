# S3 bucket list — design

**Date:** 2026-07-05
**Status:** Approved

## Goal

Enable the S3 entry on the home screen with a read-only bucket list, mirroring how
CloudFormation started with just the stack list. Selecting a bucket does nothing yet;
bucket detail and object browsing are future features.

## Scope

- List all buckets in the account with columns **Name, Region, Created**.
- All data comes from a single paginated `ListBuckets` call (bucket name, creation
  date, and `BucketRegion` are all returned natively). No per-bucket calls, no
  size/object counts.
- Same list-screen affordances as the stack list: name filter (`/`), refresh (`r`),
  back (`escape`), count line, loading state, error handling.

## Approach

Mirror the CloudFormation pattern exactly (per CLAUDE.md: one new gateway module, one
new screen module, one `SERVICES` entry). Do **not** extract a shared list-screen base
class yet — with only two list screens the variation points aren't clear, and the
stack list has behaviors buckets don't (detail push, refresh-on-resume).

## AWS layer

- `src/awst/aws/models.py`: add `BucketSummary`, a frozen slotted dataclass with
  `name: str`, `region: str`, `created: datetime`.
- `src/awst/aws/s3.py`: new `S3Gateway` taking an `S3Client` in the constructor
  (same shape as `CloudFormationGateway`). One method:
  - `list_buckets() -> list[BucketSummary]` — uses the `list_buckets` paginator
    (supported by the pinned botocore 1.43.40), maps `BucketRegion` via
    `bucket.get("BucketRegion", "")` so a missing field (moto, older endpoints)
    yields an empty region rather than a crash, catches
    `BotoCoreError | ClientError` and re-raises via the existing
    `map_botocore_error`, and returns buckets sorted by name.

## App wiring

- `AwstApp` gains an optional `s3_gateway` constructor parameter and a lazy
  `s3_gateway` property mirroring `cloudformation_gateway` (its own
  `boto3.Session`; no shared-session refactor).
- `screens/home.py`: flip the S3 `ServiceEntry` to `enabled=True` with
  `screen_factory=lambda app: BucketListScreen(app.s3_gateway)`.

## Screen

`src/awst/screens/buckets.py`:

- `BucketLister` protocol: `list_buckets() -> list[BucketSummary]`. This is the
  type the screen and `AwstApp.s3_gateway` are annotated with.
- `BucketListScreen(Screen[None])`, closely modeled on `StackListScreen`:
  - Columns: Name, Region, Created (`relative_age` for Created).
  - Count line ("N buckets" / "M of N buckets" when filtered), filter `Input`,
    `DataTable` with row cursor, hidden error `Static`, `Footer`.
  - Bindings: `escape` back-or-clear-filter, `r` refresh, `slash` focus filter.
  - Data loads in a thread worker (`@work(thread=True, exclusive=True,
    exit_on_error=False)`), handled in `on_worker_state_changed`.
  - Error handling identical to the stack list: full-screen error panel on
    first-load failure, `notify` toast on refresh failure; non-`AwsError`
    worker errors re-raise.
- Differences from the stack list (intentional):
  - No status column and no `status_style` usage.
  - No `on_data_table_row_selected` handler — selecting a bucket is a no-op.
  - No `on_screen_resume` refresh — nothing is ever pushed on top of this screen.

## Dependencies

- Dev group: `boto3-stubs[cloudformation,s3]` and `moto[cloudformation,s3]`,
  then re-lock (`uv lock`) and sync.

## Testing

- `tests/fakes.py`: `FakeS3Gateway` in the same style as
  `FakeCloudFormationGateway` — canned `list[BucketSummary]` or a raisable error.
- `tests/test_s3_gateway.py`: moto `mock_aws` tests — create buckets, assert
  field mapping and name sorting; assert botocore errors surface as `AwsError`
  (mirroring `tests/test_cloudformation_gateway.py`).
- `tests/test_bucket_list_screen.py`: headless pilot tests — rows rendered,
  filter narrows rows and updates count, refresh re-fetches, first-load failure
  shows the error panel, refresh failure notifies.
- `tests/test_app.py`: selecting S3 on the home screen pushes the bucket list.

## Error handling

All failures reach the user as `AwsError` (message + optional hint) through the
existing `map_botocore_error`; the screen renders them exactly as the stack list
does. No new error types are needed — there is no per-bucket lookup that could
"not find" anything.
