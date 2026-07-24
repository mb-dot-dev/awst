# S3 Object Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `d` on a row of the S3 object browser permanently delete that object — or that whole folder prefix — including every version and delete marker.

**Architecture:** The gateway's delete-while-listing loop is extracted into one private helper that takes a key-matching predicate; `delete_object`, `delete_prefix`, and `empty_bucket` become thin wrappers over it. The existing `EmptyBucketScreen` progress modal is generalised into `DeleteObjectsScreen`, which picks the gateway call from a `target` string (`""` = whole bucket, trailing `/` = prefix, otherwise an object key). `ObjectListScreen` gains a `d` binding that confirms, pushes the modal, and refreshes on dismiss.

**Tech Stack:** Python 3.14, Textual (TUI), boto3/botocore, pytest + pytest-asyncio + Textual `run_test()` pilot, moto `mock_aws` and botocore `Stubber` for gateway tests, `uv` for dependency management, ruff + `ty` for lint and type checking.

**Spec:** `docs/superpowers/specs/2026-07-24-s3-object-delete-design.md`

**Branch:** `feature/s3-delete-object` (already created, spec already committed).

## Global Constraints

- Every task must end with `make test` passing — that runs `ruff check`, `ruff format --check`, `ty check`, and `pytest`.
- Line length limit is 120 characters (ruff).
- Coverage floor is 75% (`make coverage`); do not let a task drop below it.
- Screens must never import `boto3` or `botocore` — they depend on `Protocol` slices only.
- Every method takes an explicit `self: Self` annotation (flake8-annotations is enabled).
- Tests never touch the network. Gateway tests use moto's `mock_aws`, or botocore `Stubber` where moto cannot express the case.
- Public methods and classes need docstrings; follow the surrounding style (a one-line summary, then a blank line and detail where the behaviour is non-obvious).
- Every git commit message ends with these two trailers, separated from the body by a blank line:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01J9prQc9PMaDZHMTuAYFsCx
  ```
- Commit subject style follows the repo: imperative mood, capitalised, no `feat:`/`fix:` prefix.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `src/awst/aws/s3.py` | Modify | `_delete_versions` helper plus `delete_object`, `delete_prefix`, region-aware `empty_bucket` |
| `src/awst/screens/empty_bucket.py` | Rename → `src/awst/screens/delete_objects.py` | The progress modal, generalised to all three deletes; hosts the `BucketEmptier`, `ObjectDeleter`, `DeleteGateway` protocols |
| `src/awst/screens/objects.py` | Modify | `d` binding, confirm wording, push modal, refresh; `ObjectBrowserGateway` protocol |
| `src/awst/screens/buckets.py` | Modify | Pass the bucket's region into the modal; widen `BucketGateway` |
| `tests/fakes.py` | Modify | `FakeS3Gateway` records and simulates all three deletes |
| `tests/test_s3_gateway.py` | Modify | Gateway behaviour |
| `tests/test_empty_bucket_screen.py` | Rename → `tests/test_delete_objects_screen.py` | Modal behaviour for all three targets |
| `tests/test_object_list_screen.py` | Modify | The `d` flow |
| `tests/test_bucket_list_screen.py` | Modify | Region wiring for empty-bucket |
| `CLAUDE.md` | Modify | Document the delete action (Task 4) |

---

### Task 1: Gateway — shared delete loop, `delete_object`, `delete_prefix`

Adds the two new gateway methods and folds the existing `empty_bucket` body into the shared helper. `empty_bucket`'s signature does **not** change in this task, so nothing else in the repo needs touching and the app stays green.

**Files:**
- Modify: `src/awst/aws/s3.py:87-114` (replace the body of `empty_bucket`, add the helper and wrappers)
- Test: `tests/test_s3_gateway.py`

**Interfaces:**
- Consumes: existing `S3Gateway._client_for(region)`, `S3Gateway._delete_batch(client, name, keys)`, `map_botocore_error`.
- Produces:
  - `S3Gateway._delete_versions(bucket: str, region: str, prefix: str, match: Callable[[str], bool]) -> Iterator[int]`
  - `S3Gateway.delete_object(bucket: str, region: str, key: str) -> Iterator[int]`
  - `S3Gateway.delete_prefix(bucket: str, region: str, prefix: str) -> Iterator[int]`
  - All three are lazy: nothing is requested until the iterator is consumed. Each yields the **cumulative** deleted count after each batch of up to 1000 keys.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_s3_gateway.py` (the `_gateway`, `_create_bucket`, `mock_aws`, `Stubber`, and `AwsError` helpers are already imported at the top of that file):

```python
@mock_aws
def test_delete_object_removes_every_version_and_marker_of_the_exact_key() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    client.put_bucket_versioning(Bucket="alpha", VersioningConfiguration={"Status": "Enabled"})
    client.put_object(Bucket="alpha", Key="file.txt", Body=b"v1")
    client.put_object(Bucket="alpha", Key="file.txt", Body=b"v2")
    client.delete_object(Bucket="alpha", Key="file.txt")  # adds a delete marker
    client.put_object(Bucket="alpha", Key="file.txt.bak", Body=b"keep me")

    counts = list(_gateway().delete_object("alpha", "eu-west-1", "file.txt"))

    assert counts == [3]  # two versions + one delete marker
    # the sibling key that merely starts with "file.txt" survives
    assert [obj["Key"] for obj in client.list_objects_v2(Bucket="alpha")["Contents"]] == ["file.txt.bak"]


@mock_aws
def test_delete_object_on_a_missing_key_yields_nothing() -> None:
    _create_bucket("alpha")

    assert list(_gateway().delete_object("alpha", "eu-west-1", "gone.txt")) == []


@mock_aws
def test_delete_prefix_removes_everything_beneath_it_and_nothing_else() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    for key in ("docs/guide.md", "docs/2026/notes.md", "docs.txt", "readme.md"):
        client.put_object(Bucket="alpha", Key=key, Body=b"hi")

    counts = list(_gateway().delete_prefix("alpha", "eu-west-1", "docs/"))

    assert counts == [2]
    remaining = [obj["Key"] for obj in client.list_objects_v2(Bucket="alpha")["Contents"]]
    assert remaining == ["docs.txt", "readme.md"]


@mock_aws
def test_delete_prefix_on_an_empty_prefix_yields_nothing() -> None:
    _create_bucket("alpha")
    boto3.client("s3", region_name="eu-west-1").put_object(Bucket="alpha", Key="readme.md", Body=b"hi")

    assert list(_gateway().delete_prefix("alpha", "eu-west-1", "nothing/")) == []


@mock_aws
def test_delete_prefix_deletes_in_batches_of_1000() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    for index in range(1050):
        client.put_object(Bucket="alpha", Key=f"logs/key-{index:04}", Body=b"")

    counts = list(_gateway().delete_prefix("alpha", "eu-west-1", "logs/"))

    assert counts == [1000, 1050]
    assert client.list_objects_v2(Bucket="alpha")["KeyCount"] == 0


@mock_aws
def test_delete_prefix_maps_missing_bucket_to_aws_error() -> None:
    deletions = _gateway().delete_prefix("missing", "eu-west-1", "docs/")  # lazy: nothing raises until iterated

    with pytest.raises(AwsError):
        list(deletions)


@mock_aws
def test_delete_object_uses_the_regional_client_for_other_regions() -> None:
    regions_built: list[str] = []

    def factory(region: str):  # noqa: ANN202 -- returns a boto3 S3 client
        regions_built.append(region)
        return boto3.client("s3", region_name=region)

    gateway = S3Gateway(boto3.client("s3", region_name="eu-west-1"), regional_client_factory=factory)
    remote = boto3.client("s3", region_name="us-east-2")
    remote.create_bucket(Bucket="remote", CreateBucketConfiguration={"LocationConstraint": "us-east-2"})
    remote.put_object(Bucket="remote", Key="a.txt", Body=b"hi")

    counts = list(gateway.delete_object("remote", "us-east-2", "a.txt"))

    assert regions_built == ["us-east-2"]
    assert counts == [1]
    assert "Contents" not in remote.list_objects_v2(Bucket="remote")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --frozen pytest tests/test_s3_gateway.py -k "delete_object or delete_prefix" -v
```

Expected: 7 failures, each `AttributeError: 'S3Gateway' object has no attribute 'delete_object'` (or `delete_prefix`).

- [ ] **Step 3: Implement the helper and wrappers**

In `src/awst/aws/s3.py`, replace the whole `empty_bucket` method (currently lines 87–114, ending just before `def _delete_batch`) with:

```python
    def delete_object(self: Self, bucket: str, region: str, key: str) -> Iterator[int]:
        """Delete every version and delete marker of one object key.

        Yields the cumulative deleted count after each batch of up to 1000
        keys; a key that does not exist yields nothing. Raises AwsError for any
        credential, network, or API failure.
        """
        # Prefix=key also returns keys that merely extend it ("file.txt.bak"), so match exactly.
        # S3 returns keys in lexicographic order and key sorts before anything extending it,
        # so a page holding no exact match means this key's versions are exhausted.
        return self._delete_versions(bucket, region, key, lambda candidate: candidate == key)

    def delete_prefix(self: Self, bucket: str, region: str, prefix: str) -> Iterator[int]:
        """Delete every version and delete marker of every key beneath the prefix.

        Yields the cumulative deleted count after each batch of up to 1000
        keys; a prefix holding no keys yields nothing. Raises AwsError for any
        credential, network, or API failure.
        """
        return self._delete_versions(bucket, region, prefix, lambda _: True)

    def empty_bucket(self: Self, name: str) -> Iterator[int]:
        """Delete every object version and delete marker in the bucket.

        Yields the cumulative deleted-object count after each batch of up to
        1000 keys; an already-empty bucket yields nothing. Raises AwsError for
        any credential, network, or API failure, including per-key failures
        reported by DeleteObjects.
        """
        return self._delete_versions(name, "", "", lambda _: True)

    def _delete_versions(
        self: Self,
        bucket: str,
        region: str,
        prefix: str,
        match: Callable[[str], bool],
    ) -> Iterator[int]:
        """Delete every version and delete marker under prefix whose key satisfies match."""
        client = self._client_for(region)
        deleted = 0
        try:
            # Re-list from the start after each batch instead of paginating with
            # markers: resuming from a just-deleted key breaks under moto, and
            # restarting is the standard pattern for delete-while-listing anyway.
            # Each round deletes everything it matched (or raises), so the loop
            # always makes progress.
            while True:
                page = client.list_object_versions(Bucket=bucket, Prefix=prefix, MaxKeys=1000)
                items = [*page.get("Versions", []), *page.get("DeleteMarkers", [])]
                keys: list[ObjectIdentifierTypeDef] = [
                    {"Key": item["Key"], "VersionId": item["VersionId"]} for item in items if match(item["Key"])
                ]
                if not keys:
                    break
                self._delete_batch(client, bucket, keys)
                deleted += len(keys)
                yield deleted
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
```

`Callable`, `Iterator`, and `ObjectIdentifierTypeDef` are already imported in the `TYPE_CHECKING` block at the top of the file — no import changes are needed.

Note that `delete_object`, `delete_prefix`, and `empty_bucket` are now plain functions returning the generator produced by `_delete_versions`, so they stay lazy exactly as before: the existing `test_empty_bucket_maps_missing_bucket_to_aws_error` and `test_empty_bucket_raises_on_partial_failure` tests rely on that.

- [ ] **Step 4: Run the full gateway suite**

```bash
uv run --frozen pytest tests/test_s3_gateway.py -v
```

Expected: PASS, including all pre-existing `empty_bucket` tests (they still call `empty_bucket("alpha")` with one argument).

- [ ] **Step 5: Run the full check**

```bash
make test
```

Expected: ruff, `ty`, and the whole pytest suite all pass.

- [ ] **Step 6: Commit**

```bash
git add src/awst/aws/s3.py tests/test_s3_gateway.py
git commit -m "$(cat <<'EOF'
Add delete_object and delete_prefix to the S3 gateway

Extract the delete-while-listing loop into a shared _delete_versions helper
taking a key predicate, and rewrite empty_bucket as one of three wrappers
over it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01J9prQc9PMaDZHMTuAYFsCx
EOF
)"
```

---

### Task 2: Make `empty_bucket` region-aware

Standalone bug fix: emptying a bucket outside the session's home region currently fails, because `empty_bucket` never resolves a regional client. The helper from Task 1 already takes a region, so this task just threads one through.

**Files:**
- Modify: `src/awst/aws/s3.py` (the `empty_bucket` wrapper added in Task 1)
- Modify: `src/awst/screens/empty_bucket.py:20-23` (protocol), `:46-50` (constructor), `:64` (gateway call)
- Modify: `src/awst/screens/buckets.py:77-87` (`action_empty` and `_on_empty_confirmed`)
- Modify: `tests/fakes.py:167` (`emptied`), `:193-200` (`empty_bucket`)
- Test: `tests/test_s3_gateway.py`, `tests/test_empty_bucket_screen.py`, `tests/test_bucket_list_screen.py`

**Interfaces:**
- Consumes: `S3Gateway._delete_versions` from Task 1.
- Produces:
  - `S3Gateway.empty_bucket(name: str, region: str) -> Iterator[int]`
  - `BucketEmptier.empty_bucket(name: str, region: str) -> Iterator[int]`
  - `EmptyBucketScreen(gateway: BucketEmptier, bucket_name: str, region: str)`
  - `FakeS3Gateway.emptied: list[tuple[str, str]]` — `(name, region)` pairs, replacing the old list of bare names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_s3_gateway.py`:

```python
@mock_aws
def test_empty_bucket_uses_the_regional_client_for_other_regions() -> None:
    regions_built: list[str] = []

    def factory(region: str):  # noqa: ANN202 -- returns a boto3 S3 client
        regions_built.append(region)
        return boto3.client("s3", region_name=region)

    gateway = S3Gateway(boto3.client("s3", region_name="eu-west-1"), regional_client_factory=factory)
    remote = boto3.client("s3", region_name="us-east-2")
    remote.create_bucket(Bucket="remote", CreateBucketConfiguration={"LocationConstraint": "us-east-2"})
    remote.put_object(Bucket="remote", Key="a.txt", Body=b"hi")

    counts = list(gateway.empty_bucket("remote", "us-east-2"))

    assert regions_built == ["us-east-2"]
    assert counts == [1]
    assert "Contents" not in remote.list_objects_v2(Bucket="remote")
```

Append to `tests/test_bucket_list_screen.py`:

```python
@pytest.mark.asyncio
async def test_emptying_passes_the_buckets_own_region() -> None:
    gateway = FakeS3Gateway(buckets=[make_bucket("assets", region="us-east-2")], empty_batches=[1])
    app = BucketScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("e")
        await pilot.pause()
        await pilot.press("y")
        await _until_back_on_list(app, pilot)

        assert gateway.emptied == [("assets", "us-east-2")]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --frozen pytest tests/test_s3_gateway.py::test_empty_bucket_uses_the_regional_client_for_other_regions \
  tests/test_bucket_list_screen.py::test_emptying_passes_the_buckets_own_region -v
```

Expected: both FAIL — the gateway test with `TypeError: empty_bucket() takes 2 positional arguments but 3 were given`, the screen test with an assertion error showing `['assets']` instead of `[('assets', 'us-east-2')]`.

- [ ] **Step 3: Add the region parameter to the gateway**

In `src/awst/aws/s3.py`, change the `empty_bucket` wrapper written in Task 1 to:

```python
    def empty_bucket(self: Self, name: str, region: str) -> Iterator[int]:
        """Delete every object version and delete marker in the bucket.

        Yields the cumulative deleted-object count after each batch of up to
        1000 keys; an already-empty bucket yields nothing. Raises AwsError for
        any credential, network, or API failure, including per-key failures
        reported by DeleteObjects.
        """
        return self._delete_versions(name, region, "", lambda _: True)
```

- [ ] **Step 4: Thread the region through the modal**

In `src/awst/screens/empty_bucket.py`, change the protocol:

```python
class BucketEmptier(Protocol):
    """The slice of the S3 gateway this screen needs."""

    def empty_bucket(self: Self, name: str, region: str) -> Iterator[int]: ...
```

the constructor:

```python
    def __init__(self: Self, gateway: BucketEmptier, bucket_name: str, region: str) -> None:
        super().__init__()
        self._gateway = gateway
        self._bucket_name = bucket_name
        self._region = region
        self._deleted = 0
```

and the worker's call:

```python
        for count in self._gateway.empty_bucket(self._bucket_name, self._region):
```

- [ ] **Step 5: Look the bucket's region up at the call site**

In `src/awst/screens/buckets.py`, replace `action_empty` and `_on_empty_confirmed` (lines 77–87) with:

```python
    def action_empty(self: Self) -> None:
        name = self._cursor_name(self.query_one("#items", DataTable))
        bucket = next((item for item in self._all_items if item.name == name), None)
        if bucket is None:
            return
        question = f"Permanently delete all objects, versions, and delete markers in {bucket.name}?"
        self.app.push_screen(ConfirmScreen(question), partial(self._on_empty_confirmed, bucket))

    def _on_empty_confirmed(self: Self, bucket: BucketSummary, confirmed: bool | None) -> None:  # noqa: FBT001
        if not confirmed:
            return
        screen = EmptyBucketScreen(self._gateway, bucket.name, bucket.region)
        self.app.push_screen(screen, self._on_empty_finished)
```

`BucketSummary` is already imported at runtime in that module (line 9), and `partial` on line 3.

- [ ] **Step 6: Update the fake**

In `tests/fakes.py`, change the `emptied` attribute initialiser (line 167) to:

```python
        self.emptied: list[tuple[str, str]] = []
```

and the `empty_bucket` method (lines 193–200) to:

```python
    def empty_bucket(self: Self, name: str, region: str) -> Iterator[int]:
        self.emptied.append((name, region))
        for index, count in enumerate(self.empty_batches):
            if index > 0 and self.empty_gate is not None:
                self.empty_gate.wait(timeout=5)  # lets tests freeze the worker mid-delete
            yield count
        if self.empty_error is not None:
            raise self.empty_error
```

- [ ] **Step 7: Update the existing call sites in tests**

In `tests/test_s3_gateway.py`, add the region argument to the five existing `empty_bucket` calls:

- line 103: `list(_gateway().empty_bucket("alpha", "eu-west-1"))`
- line 118: `list(_gateway().empty_bucket("alpha", "eu-west-1"))`
- line 130: `list(_gateway().empty_bucket("alpha", "eu-west-1")) == []`
- line 140: `list(_gateway().empty_bucket("alpha", "eu-west-1"))`
- line 148: `_gateway().empty_bucket("missing", "eu-west-1")`
- line 166: `S3Gateway(client).empty_bucket("alpha", "eu-west-1")`

In `tests/test_empty_bucket_screen.py`, update the harness (line 41):

```python
        self.push_screen(EmptyBucketScreen(self.gateway, "assets", "eu-west-1"), self.results.append)
```

and the assertion in `test_success_empties_bucket_and_toasts_final_count` (line 71):

```python
    assert gateway.emptied == [("assets", "eu-west-1")]
```

In `tests/test_bucket_list_screen.py`, update the assertion in `test_confirming_empties_the_bucket_and_refreshes`:

```python
        assert gateway.emptied == [("assets", "eu-west-1")]
```

(`make_bucket` defaults to region `"eu-west-1"`.)

- [ ] **Step 8: Run the full check**

```bash
make test
```

Expected: everything passes, including the two new tests from Step 1.

- [ ] **Step 9: Commit**

```bash
git add src/awst/aws/s3.py src/awst/screens/empty_bucket.py src/awst/screens/buckets.py \
  tests/fakes.py tests/test_s3_gateway.py tests/test_empty_bucket_screen.py tests/test_bucket_list_screen.py
git commit -m "$(cat <<'EOF'
Empty buckets through the bucket's own region

empty_bucket only ever used the home-region client, so emptying a bucket in
another region failed with S3's PermanentRedirect. The bucket list now looks
up the row's summary and passes its region down to the gateway.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01J9prQc9PMaDZHMTuAYFsCx
EOF
)"
```

---

### Task 3: Generalise the modal into `DeleteObjectsScreen`

Pure refactor plus new capability in the modal: no user-visible change to empty-bucket, but the screen can now delete one key or one prefix. Nothing calls those paths yet — Task 4 wires the UI.

**Files:**
- Rename: `src/awst/screens/empty_bucket.py` → `src/awst/screens/delete_objects.py`
- Modify: `src/awst/screens/buckets.py` (imports, `BucketGateway`, `_on_empty_confirmed`)
- Modify: `tests/fakes.py` (`FakeS3Gateway`)
- Rename: `tests/test_empty_bucket_screen.py` → `tests/test_delete_objects_screen.py`
- Modify: `tests/test_bucket_list_screen.py` (import and one assertion)

**Interfaces:**
- Consumes: `S3Gateway.delete_object` / `delete_prefix` / `empty_bucket` from Tasks 1–2.
- Produces:
  - `BucketEmptier` — `empty_bucket(name: str, region: str) -> Iterator[int]`
  - `ObjectDeleter` — `delete_object(bucket: str, region: str, key: str) -> Iterator[int]` and `delete_prefix(bucket: str, region: str, prefix: str) -> Iterator[int]`
  - `DeleteGateway(BucketEmptier, ObjectDeleter, Protocol)`
  - `DeleteObjectsScreen(gateway: DeleteGateway, bucket: str, region: str, target: str = "")` — a `ModalScreen[None]`. `target` is `""` for the whole bucket, a prefix ending `/` for a folder, or an object key.
  - `FakeS3Gateway` constructor params renamed `empty_batches`/`empty_error`/`empty_gate` → `delete_batches`/`delete_error`/`delete_gate`; new attribute `deleted: list[tuple[str, str, str, str]]` recording `(method, bucket, region, target)` for `delete_object` and `delete_prefix`, where `method` is `"object"` or `"prefix"` so a test can tell them apart, not just their arguments.

- [ ] **Step 1: Rename the module and its test with git**

```bash
git mv src/awst/screens/empty_bucket.py src/awst/screens/delete_objects.py
git mv tests/test_empty_bucket_screen.py tests/test_delete_objects_screen.py
```

- [ ] **Step 2: Write the failing tests**

Replace the whole contents of `tests/test_delete_objects_screen.py` with:

```python
"""Tests for the object-delete progress modal."""

import contextlib
import threading
from typing import TYPE_CHECKING, Self

import pytest
from textual.app import App
from textual.widgets import Static
from textual.worker import WorkerCancelled, WorkerFailed

from awst.aws.models import AwsError
from awst.screens.delete_objects import DeleteObjectsScreen
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


class DeleteObjectsApp(App[None]):
    """Harness that opens the progress modal directly and records its dismissal."""

    def __init__(self: Self, gateway: FakeS3Gateway, target: str = "") -> None:
        super().__init__()
        self.gateway = gateway
        self.target = target
        self.results: list[None] = []

    def on_mount(self: Self) -> None:
        screen = DeleteObjectsScreen(self.gateway, "assets", "eu-west-1", self.target)
        self.push_screen(screen, self.results.append)


async def _until_dismissed(app: DeleteObjectsApp, pilot: Pilot[None]) -> None:
    """Let the delete worker run to completion, tolerating cancelled/failed workers."""
    for _ in range(100):
        with contextlib.suppress(WorkerFailed, WorkerCancelled):
            await app.workers.wait_for_complete()
        await pilot.pause()
        if app.results:
            return
    pytest.fail("modal never dismissed")


async def _until_progress_shows(app: DeleteObjectsApp, pilot: Pilot[None], text: str) -> None:
    for _ in range(100):
        await pilot.pause()
        if text in str(app.screen.query_one("#progress", Static).content):
            return
    pytest.fail(f"progress never showed {text!r}")


@pytest.mark.asyncio
async def test_empty_target_empties_the_bucket_and_toasts_final_count(toasts: list[str]) -> None:
    gateway = FakeS3Gateway(delete_batches=[500, 1234])
    app = DeleteObjectsApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert gateway.emptied == [("assets", "eu-west-1")]
    assert gateway.deleted == []
    assert toasts == ["1,234 objects deleted."]


@pytest.mark.asyncio
async def test_already_empty_bucket_reports_zero(toasts: list[str]) -> None:
    app = DeleteObjectsApp(FakeS3Gateway(delete_batches=[]))

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert toasts == ["0 objects deleted."]


@pytest.mark.asyncio
async def test_object_key_target_calls_delete_object(toasts: list[str]) -> None:
    gateway = FakeS3Gateway(delete_batches=[1])
    app = DeleteObjectsApp(gateway, target="docs/readme.md")

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert gateway.deleted == [("object", "assets", "eu-west-1", "docs/readme.md")]
    assert gateway.emptied == []
    assert toasts == ["1 object deleted."]


@pytest.mark.asyncio
async def test_prefix_target_calls_delete_prefix(toasts: list[str]) -> None:
    gateway = FakeS3Gateway(delete_batches=[7])
    app = DeleteObjectsApp(gateway, target="docs/2026/")

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert gateway.deleted == [("prefix", "assets", "eu-west-1", "docs/2026/")]
    assert gateway.emptied == []
    assert toasts == ["7 objects deleted."]


@pytest.mark.asyncio
async def test_title_names_the_bucket_when_emptying() -> None:
    gate = threading.Event()
    app = DeleteObjectsApp(FakeS3Gateway(delete_batches=[1, 2], delete_gate=gate))

    async with app.run_test() as pilot:
        await _until_progress_shows(app, pilot, "1 object deleted")

        assert str(app.screen.query_one("#title", Static).content) == "Emptying assets"

        gate.set()
        await _until_dismissed(app, pilot)


@pytest.mark.asyncio
async def test_title_names_the_target_when_deleting() -> None:
    gate = threading.Event()
    app = DeleteObjectsApp(FakeS3Gateway(delete_batches=[1, 2], delete_gate=gate), target="docs/2026/")

    async with app.run_test() as pilot:
        await _until_progress_shows(app, pilot, "1 object deleted")

        assert str(app.screen.query_one("#title", Static).content) == "Deleting docs/2026/"

        gate.set()
        await _until_dismissed(app, pilot)


@pytest.mark.asyncio
async def test_progress_label_updates_per_batch(toasts: list[str]) -> None:
    gate = threading.Event()
    gateway = FakeS3Gateway(delete_batches=[500, 600], delete_gate=gate)
    app = DeleteObjectsApp(gateway)

    async with app.run_test() as pilot:
        await _until_progress_shows(app, pilot, "500 objects deleted")

        gate.set()
        await _until_dismissed(app, pilot)

    assert toasts == ["600 objects deleted."]


@pytest.mark.asyncio
async def test_escape_cancels_and_reports_partial_count(toasts: list[str]) -> None:
    gate = threading.Event()
    gateway = FakeS3Gateway(delete_batches=[500, 600], delete_gate=gate)
    app = DeleteObjectsApp(gateway)

    async with app.run_test() as pilot:
        await _until_progress_shows(app, pilot, "500 objects deleted")

        await pilot.press("escape")
        gate.set()  # release the frozen worker thread so it can observe the cancel
        await _until_dismissed(app, pilot)

    assert toasts == ["At least 500 objects already deleted."]


@pytest.mark.asyncio
async def test_gateway_error_toasts_and_dismisses(toasts: list[str]) -> None:
    gateway = FakeS3Gateway(delete_error=AwsError("Access Denied"))
    app = DeleteObjectsApp(gateway, target="docs/")

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert toasts == ["Access Denied"]
```

- [ ] **Step 3: Update the fake**

In `tests/fakes.py`, replace the `FakeS3Gateway` constructor parameters and the delete methods. The constructor becomes:

```python
    def __init__(  # noqa: PLR0913
        self: Self,
        buckets: list[BucketSummary] | None = None,
        error: AwsError | None = None,
        bucket_pages: dict[str | None, Page[BucketSummary]] | None = None,
        delete_batches: list[int] | None = None,
        delete_error: AwsError | None = None,
        delete_gate: threading.Event | None = None,
        object_pages: dict[tuple[str, str | None], ObjectPage] | None = None,
        objects_error: AwsError | None = None,
        objects_gate: threading.Event | None = None,
    ) -> None:
        self.buckets = buckets or []
        self.error = error
        self.bucket_pages = bucket_pages
        self.delete_batches = delete_batches or []
        self.delete_error = delete_error
        self.delete_gate = delete_gate
        self.object_pages = object_pages or {}
        self.objects_error = objects_error
        self.objects_gate = objects_gate
        self.object_calls: list[tuple[str, str, str, str | None]] = []
        self.calls = 0
        self.next_tokens: list[str | None] = []
        self.emptied: list[tuple[str, str]] = []
        # First element records which gateway method was called ("object" vs "prefix") so a
        # test can tell delete_object and delete_prefix calls apart, not just their arguments.
        self.deleted: list[tuple[str, str, str, str]] = []
```

and `empty_bucket` is replaced by these three methods plus one shared generator:

```python
    def empty_bucket(self: Self, name: str, region: str) -> Iterator[int]:
        self.emptied.append((name, region))
        return self._deletions()

    def delete_object(self: Self, bucket: str, region: str, key: str) -> Iterator[int]:
        self.deleted.append(("object", bucket, region, key))
        return self._deletions()

    def delete_prefix(self: Self, bucket: str, region: str, prefix: str) -> Iterator[int]:
        self.deleted.append(("prefix", bucket, region, prefix))
        return self._deletions()

    def _deletions(self: Self) -> Iterator[int]:
        for index, count in enumerate(self.delete_batches):
            if index > 0 and self.delete_gate is not None:
                self.delete_gate.wait(timeout=5)  # lets tests freeze the worker mid-delete
            yield count
        if self.delete_error is not None:
            raise self.delete_error
```

- [ ] **Step 4: Update the other fake call sites**

In `tests/test_bucket_list_screen.py`, the two constructions using the old names become:

- `FakeS3Gateway(buckets=[make_bucket("assets")], empty_batches=[1, 2], empty_gate=gate)` → `FakeS3Gateway(buckets=[make_bucket("assets")], delete_batches=[1, 2], delete_gate=gate)`
- `FakeS3Gateway(buckets=[make_bucket("assets", region="us-east-2")], empty_batches=[1])` → `... delete_batches=[1])` (the test added in Task 2)

Find any others with:

```bash
grep -rn "empty_batches\|empty_error\|empty_gate" tests/
```

Expected after the edits: no matches.

- [ ] **Step 5: Generalise the screen**

Replace the whole contents of `src/awst/screens/delete_objects.py` with:

```python
"""Modal that deletes S3 objects — one key, one prefix, or a whole bucket — with live progress."""

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
    """The slice of the S3 gateway used to empty a whole bucket."""

    def empty_bucket(self: Self, name: str, region: str) -> Iterator[int]: ...


class ObjectDeleter(Protocol):
    """The slice of the S3 gateway used to delete one object or one prefix."""

    def delete_object(self: Self, bucket: str, region: str, key: str) -> Iterator[int]: ...

    def delete_prefix(self: Self, bucket: str, region: str, prefix: str) -> Iterator[int]: ...


class DeleteGateway(BucketEmptier, ObjectDeleter, Protocol):
    """Everything the progress modal may call."""


class DeleteObjectsScreen(ModalScreen[None]):
    """Delete S3 objects with live progress; dismisses once done, cancelled, or failed.

    `target` selects what to delete: "" is the whole bucket, a value ending "/"
    is every key beneath that prefix, and anything else is a single object key.
    Every version and delete marker goes in all three cases.
    """

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    DeleteObjectsScreen { align: center middle; }
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

    def __init__(self: Self, gateway: DeleteGateway, bucket: str, region: str, target: str = "") -> None:
        super().__init__()
        self._gateway = gateway
        self._bucket = bucket
        self._region = region
        self._target = target
        self._deleted = 0

    def compose(self: Self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._title(), id="title")
            yield Static("Deleting… 0 objects deleted", id="progress")
        yield Footer()

    def _title(self: Self) -> str:
        return f"Deleting {self._target}" if self._target else f"Emptying {self._bucket}"

    def _done_title(self: Self) -> str:
        return f"Deleted {self._target}" if self._target else f"Emptied {self._bucket}"

    def _failed_title(self: Self) -> str:
        return "Delete failed" if self._target else "Empty bucket failed"

    def _deletions(self: Self) -> Iterator[int]:
        """The gateway call for this target; the delimiter tells a folder from an object key."""
        if not self._target:
            return self._gateway.empty_bucket(self._bucket, self._region)
        if self._target.endswith("/"):
            return self._gateway.delete_prefix(self._bucket, self._region, self._target)
        return self._gateway.delete_object(self._bucket, self._region, self._target)

    def on_mount(self: Self) -> None:
        self._delete()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _delete(self: Self) -> None:
        worker = get_current_worker()
        for count in self._deletions():
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
        if event.worker.name != "_delete":
            return
        if event.state == WorkerState.SUCCESS:
            self.notify(f"{self._count_text()} deleted.", title=self._done_title())
            self.dismiss(result=None)
        elif event.state == WorkerState.CANCELLED:
            self.notify(f"At least {self._count_text()} already deleted.", title="Cancelled", severity="warning")
            self.dismiss(result=None)
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, AwsError):
                message = error.message if error.hint is None else f"{error.message} ({error.hint})"
                self.notify(message, title=self._failed_title(), severity="error")
                self.dismiss(result=None)
            elif error is not None:
                raise error

    def action_cancel(self: Self) -> None:
        """Cancel the in-flight delete worker; the escape binding above triggers this."""
        self.workers.cancel_node(self)
```

The `toasts` fixture in the test file above records `(message, title)` pairs so both
failure-title branches stay pinned; assert tuples, not bare message strings.

- [ ] **Step 6: Update the bucket screen**

In `src/awst/screens/buckets.py`, change the import (line 11) from

```python
from awst.screens.empty_bucket import BucketEmptier, EmptyBucketScreen
```

to

```python
from awst.screens.delete_objects import BucketEmptier, DeleteObjectsScreen, ObjectDeleter
```

widen `BucketGateway`:

```python
class BucketGateway(BucketLister, BucketEmptier, ObjectDeleter, ObjectLister, Protocol):
    """Everything the bucket screens collectively need from S3."""
```

and change the push in `_on_empty_confirmed`:

```python
        screen = DeleteObjectsScreen(self._gateway, bucket.name, bucket.region, "")
        self.app.push_screen(screen, self._on_empty_finished)
```

- [ ] **Step 7: Update the bucket list test's import**

In `tests/test_bucket_list_screen.py`, replace

```python
from awst.screens.empty_bucket import EmptyBucketScreen
```

with

```python
from awst.screens.delete_objects import DeleteObjectsScreen
```

and the assertion inside `test_confirming_empties_the_bucket_and_refreshes`:

```python
        assert isinstance(app.screen, DeleteObjectsScreen)  # gate holds the worker before its second batch
```

- [ ] **Step 8: Run the full check**

```bash
make test
```

Expected: everything passes. If `ty` reports that `FakeS3Gateway` no longer satisfies `BucketGateway`, the fake is missing `delete_object`/`delete_prefix` from Step 3.

- [ ] **Step 9: Commit**

```bash
git add -A src/awst/screens tests/
git commit -m "$(cat <<'EOF'
Generalise the empty-bucket modal into DeleteObjectsScreen

The modal now deletes a whole bucket, one prefix, or one object key, choosing
the gateway call from its target argument. Renaming it avoids a second,
near-identical progress modal for object deletes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01J9prQc9PMaDZHMTuAYFsCx
EOF
)"
```

---

### Task 4: `d` deletes the highlighted object or folder

The user-facing feature.

**Files:**
- Modify: `src/awst/screens/objects.py`
- Modify: `CLAUDE.md` (project overview sentence about S3)
- Test: `tests/test_object_list_screen.py`

**Interfaces:**
- Consumes: `DeleteObjectsScreen(gateway, bucket, region, target)` and `ObjectDeleter` from Task 3; the existing `ConfirmScreen(question)` from `awst.screens.confirm`, which dismisses `True` on `y` and `False` on `n`/`escape`; `ResourceListScreen._cursor_name(table)`, which returns `None` when the table is empty; `ResourceListScreen.action_refresh()`.
- Produces: `ObjectBrowserGateway(ObjectLister, DeleteGateway, Protocol)` and a `d` binding on `ObjectListScreen`.

- [ ] **Step 1: Write the failing tests**

Add to the top of `tests/test_object_list_screen.py`, after the existing imports, the toast fixture and a "back on the list" helper:

```python
@pytest.fixture
def toasts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record notifications instead of rendering toasts."""
    messages: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        messages.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    return messages


async def _until_back_on_list(app: ObjectScreenApp, pilot: Pilot[None]) -> None:
    """Let the delete modal finish and pop, tolerating cancelled/failed workers."""
    for _ in range(100):
        with contextlib.suppress(WorkerFailed, WorkerCancelled):
            await app.workers.wait_for_complete()
        await pilot.pause()
        if isinstance(app.screen, ObjectListScreen):
            return
    pytest.fail("never returned to the object list")
```

That helper needs `Pilot` imported under `TYPE_CHECKING`. Add to the top of the file:

```python
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from textual.pilot import Pilot
```

(`contextlib`, `threading`, `App`, `WorkerCancelled`, and `WorkerFailed` are already imported in this file.)

Then append the tests:

```python
@pytest.mark.asyncio
async def test_d_on_an_object_confirms_then_deletes_that_key() -> None:
    page = ObjectPage(folders=(), objects=(make_object("docs/readme.md"),), continuation_token=None)
    gateway = FakeS3Gateway(object_pages={("docs/", None): page}, delete_batches=[1])
    app = ObjectScreenApp(gateway, prefix="docs/")

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)

        await pilot.press("y")
        await _until_back_on_list(app, pilot)

        assert gateway.deleted == [("object", "assets", "eu-west-1", "docs/readme.md")]


@pytest.mark.asyncio
async def test_d_on_a_folder_deletes_the_whole_prefix() -> None:
    page = ObjectPage(folders=("docs/2026/",), objects=(), continuation_token=None)
    gateway = FakeS3Gateway(object_pages={("docs/", None): page}, delete_batches=[4])
    app = ObjectScreenApp(gateway, prefix="docs/")

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await _until_back_on_list(app, pilot)

        assert gateway.deleted == [("prefix", "assets", "eu-west-1", "docs/2026/")]


@pytest.mark.asyncio
async def test_declining_the_confirmation_deletes_nothing() -> None:
    page = ObjectPage(folders=(), objects=(make_object("readme.md"),), continuation_token=None)
    gateway = FakeS3Gateway(object_pages={("", None): page}, delete_batches=[1])
    app = ObjectScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()

        assert isinstance(app.screen, ObjectListScreen)
        assert gateway.deleted == []
        assert len(gateway.object_calls) == 1  # no refresh either


@pytest.mark.asyncio
async def test_d_with_no_rows_does_nothing() -> None:
    gateway = FakeS3Gateway(object_pages={})
    app = ObjectScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        screen = app.screen

        await pilot.press("d")
        await pilot.pause()

        assert app.screen is screen
        assert gateway.deleted == []


@pytest.mark.asyncio
async def test_finishing_a_delete_refreshes_the_listing() -> None:
    page = ObjectPage(folders=(), objects=(make_object("readme.md"),), continuation_token=None)
    gateway = FakeS3Gateway(object_pages={("", None): page}, delete_batches=[1])
    app = ObjectScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await _until_back_on_list(app, pilot)
        await _settle(app)
        await pilot.pause()

        assert gateway.object_calls == [
            ("assets", "eu-west-1", "", None),
            ("assets", "eu-west-1", "", None),
        ]


@pytest.mark.asyncio
async def test_delete_failure_toasts_and_returns_to_the_list(toasts: list[str]) -> None:
    page = ObjectPage(folders=(), objects=(make_object("readme.md"),), continuation_token=None)
    gateway = FakeS3Gateway(object_pages={("", None): page}, delete_error=AwsError("Access Denied"))
    app = ObjectScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await _until_back_on_list(app, pilot)

        assert "Access Denied" in toasts
```

Add `ConfirmScreen` to the imports at the top of the file:

```python
from awst.screens.confirm import ConfirmScreen
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --frozen pytest tests/test_object_list_screen.py -k "delete or test_d_" -v
```

Expected: FAIL — `d` is not bound, so no `ConfirmScreen` is pushed and `gateway.deleted` stays empty.

- [ ] **Step 3: Add the binding and the delete flow**

In `src/awst/screens/objects.py`, extend the imports at the top:

```python
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from textual.widgets import DataTable  # noqa: TC002 -- needed at runtime: Textual inspects handler annotations
from textual.worker import get_current_worker

from awst.aws.models import ObjectSummary
from awst.screens.confirm import ConfirmScreen
from awst.screens.delete_objects import DeleteObjectsScreen, ObjectDeleter
from awst.screens.formatting import human_size, relative_age
from awst.screens.resource_list import ResourceListScreen

if TYPE_CHECKING:
    from datetime import datetime

    from textual.binding import BindingType

    from awst.aws.models import ObjectPage
```

Add the combined protocol just below the existing `ObjectLister` protocol:

```python
class ObjectBrowserGateway(ObjectLister, DeleteGateway, Protocol):
    """Everything the object browser needs from S3; note it never empties a whole bucket."""
```

Change the class docstring, add the binding, and widen the constructor's gateway type:

```python
class ObjectListScreen(ResourceListScreen[ObjectEntry]):
    """One prefix level; Enter drills into folders, d deletes a row, m loads more."""

    TITLE = "S3 objects"
    COLUMNS = ("Name", "Size", "Modified")
    NOUN = "object"

    BINDINGS: ClassVar[list[BindingType]] = [("d", "delete", "Delete")]

    def __init__(self: Self, gateway: ObjectBrowserGateway, bucket: str, region: str, prefix: str = "") -> None:
```

Append the delete flow at the end of the class, after `on_data_table_row_selected`:

```python
    def action_delete(self: Self) -> None:
        target = self._cursor_name(self.query_one("#items", DataTable))
        if target is None:
            return
        if target.endswith("/"):
            question = f"Permanently delete everything under {target}, including all versions?"
        else:
            question = f"Permanently delete {target} and all its versions?"
        self.app.push_screen(ConfirmScreen(question), partial(self._on_delete_confirmed, target))

    def _on_delete_confirmed(self: Self, target: str, confirmed: bool | None) -> None:  # noqa: FBT001
        if not confirmed:
            return
        screen = DeleteObjectsScreen(self._gateway, self._bucket, self._region, target)
        self.app.push_screen(screen, self._on_delete_finished)

    def _on_delete_finished(self: Self, result: None) -> None:  # noqa: ARG002
        self.action_refresh()
```

- [ ] **Step 4: Run the object list suite**

```bash
uv run --frozen pytest tests/test_object_list_screen.py -v
```

Expected: PASS, including the pre-existing browsing tests.

- [ ] **Step 5: Update the project overview**

In `CLAUDE.md`, in the "Project overview" paragraph, replace this fragment:

```
S3 (bucket list with an empty-bucket action and a read-only object browser: Enter drills into buckets and folders, `m` loads the next page, cross-region buckets use per-region clients)
```

with:

```
S3 (bucket list with an empty-bucket action and an object browser: Enter drills into buckets and folders, `d` permanently deletes the highlighted object or folder prefix including all versions, `m` loads the next page, cross-region buckets use per-region clients)
```

Then, in the "Architecture" section, replace the `empty_bucket.py` mention:

```
`empty_bucket.py` for the empty-bucket progress modal
```

with:

```
`delete_objects.py` for the delete progress modal shared by empty-bucket and object/prefix deletes
```

- [ ] **Step 6: Run the full check**

```bash
make test
```

Expected: ruff, `ty`, and pytest all pass.

- [ ] **Step 7: Check coverage has not regressed**

```bash
make coverage
```

Expected: PASS, total coverage at or above 75%.

- [ ] **Step 8: Commit**

```bash
git add src/awst/screens/objects.py tests/test_object_list_screen.py CLAUDE.md
git commit -m "$(cat <<'EOF'
Delete S3 objects and folders with d

d on a row of the object browser confirms, then permanently deletes that
object key or everything beneath that folder prefix, versions and delete
markers included, and refreshes the level afterwards.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01J9prQc9PMaDZHMTuAYFsCx
EOF
)"
```

---

## Manual verification

After Task 4, exercise the feature against a real (or moto-backed) bucket if credentials are available:

```bash
uv run --frozen awst
```

Check: S3 → a bucket → Enter into a folder → `d` on an object shows "Permanently delete `<full key>` and all its versions?" → `y` → progress modal → the row is gone. Then `d` on a folder row shows the recursive wording and removes the whole prefix. `escape` on the confirm cancels without deleting.

## Out of scope

Do not add, even if it seems natural: object download or upload, version browsing, restore, multi-row selection, or refreshing parent levels of the browse stack after a delete.
