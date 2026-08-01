# Bucket browsing — design

**Date:** 2026-07-19
**Status:** Approved

## Goal

Let the user press Enter on a bucket in the S3 bucket list and browse its
contents: see objects and folder prefixes, drill into folders, and go back up.
Read-only — no object actions (view, download, delete) in this version.

## Decisions

- **Scope:** read-only navigation only.
- **Pagination:** fetch one page (1,000 keys) per folder level; a `More`
  binding loads the next page. Never paginate to exhaustion.
- **Navigation:** push a new screen per folder level; escape pops back up.
- **Regions:** browsing works for buckets in any region via lazily created
  per-region S3 clients. The bucket's region comes from the bucket listing
  (`BucketSummary.region`), never a per-bucket `GetBucketLocation` call.

## Data model (`src/awst/aws/models.py`)

```python
@dataclass(frozen=True, slots=True)
class ObjectSummary:
    key: str  # full key
    size: int  # bytes
    modified: datetime


@dataclass(frozen=True, slots=True)
class ObjectPage:
    folders: tuple[str, ...]  # common prefixes, each ending "/"
    objects: tuple[ObjectSummary, ...]
    continuation_token: str | None  # None when this is the last page
```

## Gateway (`src/awst/aws/s3.py`)

New method:

```python
def list_objects(
    self, bucket: str, region: str, prefix: str = "",
    continuation_token: str | None = None,
) -> ObjectPage
```

- One `list_objects_v2` call with `Delimiter="/"`, `Prefix=prefix`,
  `MaxKeys=1000`, and `ContinuationToken` when given. Exactly one API call per
  screen load or load-more — no per-item calls.
- Folders come from `CommonPrefixes`, objects from `Contents`. S3 returns each
  lexicographically sorted; no re-sorting needed. When `prefix` is non-empty,
  S3 includes the zero-byte "folder marker" object equal to the prefix itself
  if one exists; it is filtered out of `objects`.
- Errors map through the existing `map_botocore_error` and raise `AwsError`.

Cross-region routing:

- `S3Gateway.__init__` gains an optional
  `regional_client_factory: Callable[[str], S3Client] | None = None` and keeps
  a `dict[str, S3Client]` cache.
- A private `_client_for(region)` returns the base client when `region` is
  empty, equals `client.meta.region_name`, or no factory was injected;
  otherwise it returns (building and caching on first use) a client from the
  factory.
- `list_buckets` and `empty_bucket` are unchanged and keep using the base
  client. (Follow-up, out of scope: route `empty_bucket` through
  `_client_for` so emptying cross-region buckets works too.)
- `AwstApp.s3_gateway` passes
  `regional_client_factory=lambda region: boto3.Session().client("s3", region_name=region)`.

## Base screen extension (`src/awst/screens/resource_list.py`)

Opt-in load-more support; existing subclasses are unaffected:

- Two new overridables: `_has_more(self) -> bool` (default `False`) and
  `_list_more(self) -> list[ItemT]` (fetches the next page; called on a worker
  thread). The base never calls `_list_more` unless `_has_more()` is true.
- New binding `("m", "load_more", "More")`, hidden via `check_action` unless
  `_has_more()`.
- `action_load_more` runs a worker in the same exclusive group as
  `_fetch_items`; on success its result is **appended** to `_all_items` and
  rows re-render (cursor preserved by the existing name-based restore).
- The count line appends `+` while `_has_more()` is true, e.g.
  `1000+ objects`.
- `action_refresh` is unchanged: it calls `_list`, which subclasses treat as
  "first page from scratch".

## Object list screen (`src/awst/screens/objects.py`)

`ObjectListScreen(ResourceListScreen[...])`, constructed with
`(gateway, bucket, region, prefix="")`.

- **Items:** a screen-level entry union of folder prefixes and
  `ObjectSummary`, folders first. Row key / `_item_name` is the full prefix or
  key (unique within a bucket).
- **Title:** set per-instance to `bucket/prefix` so the header shows the
  current location.
- **Columns:** Name, Size, Modified.
  - Name is relative to the current prefix; folders keep their trailing `/`.
  - Size uses a new `human_size(bytes) -> str` helper in
    `screens/formatting.py`; Modified uses the existing `relative_age`.
  - Folders leave Size and Modified blank.
- **Paging:** `_list` fetches the first page and stores the continuation
  token on the instance; `_has_more()` reports whether a token is stored;
  `_list_more()` fetches the next page with it and stores the new token.
- **Enter** (`on_data_table_row_selected`): on a folder, push a new
  `ObjectListScreen` with the deeper prefix; on an object, do nothing.
- **Escape** pops the screen (base behavior), returning to the parent folder
  or the bucket list, with cursor and filter preserved per level.
- Filter, error panel, refresh, and the SSO-login offer are inherited from
  the base unchanged.
- `NOUN = "object"` (folders count as objects in the count line; precision
  here is not worth a special case).

An `ObjectLister` protocol in `screens/objects.py` declares the
`list_objects` slice; `BucketGateway` in `screens/buckets.py` extends it.

## Bucket list change (`src/awst/screens/buckets.py`)

- New `on_data_table_row_selected`: look up the selected `BucketSummary` and
  push `ObjectListScreen(self._gateway, bucket.name, bucket.region)`.
- `BucketGateway` protocol now extends `BucketLister`, `BucketEmptier`, and
  `ObjectLister`.

## Error handling

All gateway failures raise `AwsError` via `map_botocore_error`, so the object
screen inherits the base behavior: full-screen error panel on first load,
toast on refresh failure, and the `l` SSO-login offer for credential errors on
SSO profiles.

## Testing

- **Gateway** (moto `mock_aws`): folder/object splitting at the root and
  under a prefix; folder-marker filtering; pagination (`MaxKeys` +
  continuation token round-trip); regional routing via an injected fake
  factory (base client used for the base region and empty region, factory
  used and cached for others).
- **UI** (pilot + fakes): extend `tests/fakes.py`'s fake S3 gateway with a
  paged `list_objects`; tests for bucket → object list on Enter, folder
  drill-down and escape back up, `m` binding hidden when no more pages /
  visible and appending when there are, `+` count suffix, and Enter on a
  plain object being a no-op.
