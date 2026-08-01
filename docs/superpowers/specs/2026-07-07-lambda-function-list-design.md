# Lambda function list — design

Date: 2026-07-07
Status: approved

## Goal

Add a Lambda service to awst: a read-only list of the account's Lambda functions,
reachable from the home screen. List only — no detail page (that can be a later
feature, as it was for CloudFormation).

Because this would be the third nearly-identical list screen (stacks, buckets,
functions), the work includes extracting a shared list-screen base class and
refactoring the two existing list screens onto it.

## AWS layer

New model in `src/awst/aws/models.py`:

```python
@dataclass(frozen=True, slots=True)
class FunctionSummary:
    """A Lambda function, reduced to what the UI needs."""

    name: str
    runtime: str  # "" for container-image functions (no Runtime field)
    memory_mb: int
    timeout_s: int
    modified: datetime
```

New gateway `src/awst/aws/lambda_.py` (trailing underscore: `lambda` is a
Python keyword):

- `LambdaGateway(client)` holding a boto3 `lambda` client, same shape as
  `S3Gateway`.
- `list_functions() -> list[FunctionSummary]`: uses the `list_functions`
  paginator, maps `BotoCoreError`/`ClientError` through the existing
  `map_botocore_error`, returns summaries sorted by name.
- Lambda returns `LastModified` as an ISO-8601 **string** (e.g.
  `2026-01-01T12:00:00.000+0000`), unlike S3/CloudFormation which return
  datetimes; the gateway parses it into an aware `datetime` so the model stays
  consistent with `BucketSummary`/`StackSummary`.

`AwstApp` gains a `lambda_gateway` property, lazily built from
`boto3.Session()` on first use, mirroring `cloudformation_gateway` and
`s3_gateway`. Screens never import boto3.

## Shared base: `src/awst/screens/resource_list.py`

`ResourceListScreen[ItemT](Screen[None])` absorbs everything `StackListScreen`
and `BucketListScreen` currently duplicate:

- compose layout: count line, filter `Input`, `DataTable`, error `Static`,
  `Footer`, plus the shared `DEFAULT_CSS`
- bindings: `escape` (back or clear filter), `r` (refresh), `/` (focus filter)
- the `@work(thread=True, exclusive=True, exit_on_error=False)` fetch worker
  and `on_worker_state_changed` handling
- error handling: full-screen error panel before first load, `notify` toast on
  failed refresh after a successful load; non-`AwsError` exceptions re-raise
- substring name filtering, "N of M \<noun\>s" count text, cursor restoration
  across re-renders

Subclasses provide only:

- class attributes: `TITLE`, `COLUMNS`, the resource noun (used for the count
  text and the filter placeholder)
- `_list(self) -> list[ItemT]` — call the gateway
- `_row(self, item, now) -> tuple` — the cells for one row
- `_item_name(self, item) -> str` (renamed from _name: Textual's DOMNode sets a _name instance attribute) — the filter key / row key

Row selection stays a subclass concern: the base does nothing on Enter.
`StackListScreen` keeps its `on_data_table_row_selected` (push detail screen)
and `on_screen_resume` refresh.

`buckets.py` and `stacks.py` shrink to thin subclasses. Public names
(`BucketListScreen`, `StackListScreen`) and the Protocols (`BucketLister`,
`StackLister`, `StackGateway`) stay where they are so `app.py` and `home.py`
imports don't churn. Existing screen tests must pass unchanged — they are the
safety net for the refactor.

## Lambda screen and wiring

`src/awst/screens/functions.py`: `FunctionListScreen(ResourceListScreen[FunctionSummary])`
with a `FunctionLister` Protocol (the gateway slice it needs).

Columns: **Name, Runtime, Memory, Timeout, Modified**.

- Runtime: as reported (e.g. `python3.13`); blank for container-image functions
- Memory: `128 MB`
- Timeout: `30s`
- Modified: relative age via the existing `relative_age` helper

`home.py`: new enabled `SERVICES` entry (name `Lambda`, resource `Functions`)
pushing `FunctionListScreen(app.lambda_gateway)`; SQS stays the trailing
"(soon)" row.

## Error handling

Unchanged pattern: the gateway raises `AwsError` (via `map_botocore_error`);
the base screen renders the error panel on first load or a toast on refresh.

## Testing

- `tests/test_lambda_gateway.py`: moto `mock_aws`. Moto's Lambda backend
  validates the execution role, so tests create an IAM role first, then
  functions. Covers: empty account, multiple functions sorted by name,
  `LastModified` parsing, image-packaged function (no Runtime → `""`).
- `FakeLambdaGateway` in `tests/fakes.py`, same shape as the existing fakes.
- `tests/test_function_list_screen.py`: load, filter, clear-filter/back via
  escape, refresh, error panel before load, error toast after load — same
  coverage shape as `test_bucket_list_screen.py`.
- Home screen test: selecting Lambda pushes `FunctionListScreen`.
- Existing `test_stack_list_screen.py` and `test_bucket_list_screen.py` run
  unchanged to prove the base extraction preserved behavior.

`make test` (ruff + ty + pytest) must pass; coverage stays ≥ 75%.
