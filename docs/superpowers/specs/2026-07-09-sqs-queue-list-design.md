# SQS queue list — design

Date: 2026-07-09
Status: approved

## Goal

Complete the stubbed SQS entry on the home screen with a queue list screen, following the
established gateway + list-screen pattern used by CloudFormation, S3, and Lambda.

## Decisions

- **Names-only listing.** The screen is built from the single paginated `list_queues` call.
  No per-queue `get_queue_attributes` calls: with a long queue list, N+1 attribute fetches
  make loading slow. Richer data (message counts, timestamps) is out of scope and would
  belong on a future detail screen.
- **Type column derived from the name.** FIFO queue names always end in `.fifo`, so a
  Type column (FIFO/Standard) costs no extra API calls.

## Components

### Model (`src/awst/aws/models.py`)

`QueueSummary` — frozen, slotted dataclass:

- `name: str` — queue name (last path segment of the queue URL)
- `is_fifo: bool` — true when the name ends with `.fifo`

### Gateway (`src/awst/aws/sqs.py`)

`SqsGateway`, constructed with a boto3 SQS client (typed as `SQSClient`):

- `list_queues() -> list[QueueSummary]` — paginates the `list_queues` API, maps each
  queue URL to a `QueueSummary`, returns the list sorted by name.
- A page with no queues has no `QueueUrls` key; treat it as empty.
- `BotoCoreError`/`ClientError` are mapped to `AwsError` via the existing
  `map_botocore_error`.

### Screen (`src/awst/screens/queues.py`)

Mirrors `functions.py`:

- `QueueLister` — `Protocol` declaring `list_queues() -> list[QueueSummary]`.
- `QueueListScreen(ResourceListScreen[QueueSummary])` with:
  - `TITLE = "SQS queues"`
  - `COLUMNS = ("Name", "Type")`
  - `NOUN = "queue"`
  - `_row` renders the Type cell as `"FIFO"` or `"Standard"`.

Loading state, error display, and refresh behavior are inherited from
`ResourceListScreen`.

### Wiring

- `AwstApp` gains a lazy `sqs_gateway` property mirroring the existing gateway
  properties.
- The `SERVICES` entry for SQS in `screens/home.py` flips to `enabled=True` with
  `screen_factory=lambda app: QueueListScreen(app.sqs_gateway)`; the "(soon)" suffix
  disappears automatically.

## Error handling

All API failures surface as `AwsError` and render through the existing
`ResourceListScreen` error path. There are no write operations and no per-queue calls,
so no partial-failure cases exist.

## Testing

- **Gateway** (moto `mock_aws`, no network): standard and FIFO queues are listed with
  correct `name`/`is_fifo`; results sorted by name; empty region returns an empty list.
- **UI** (pytest-asyncio + Textual pilot): add `FakeSqsGateway` to `tests/fakes.py`;
  selecting SQS from the home screen shows queue rows; a gateway error renders the
  error message.
- Completion gate: `make test` (lint + unit) passes.
