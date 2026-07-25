# SSM parameter list — design

Date: 2026-07-25
Status: approved

## Goal

Add SSM Parameter Store as the fifth service on the home screen, with a read-only,
paginated list of the region's parameters. Follows the established gateway +
`ResourceListScreen` pattern used by CloudFormation, S3, Lambda, and SQS.

## Decisions

- **Metadata only; no parameter values.** `describe_parameters` returns metadata but no
  values; fetching values needs separate `get_parameter`/`get_parameters` calls. Batching
  those would reintroduce the N+1 pattern the list screens deliberately avoid, and it would
  decrypt `SecureString` values onto the screen by default. Values are out of scope and
  would belong on a future detail screen behind an explicit keypress.
- **Flat list, not a path browser.** Parameter names are hierarchical (`/app/prod/db-url`),
  but sorting by name already groups each path together, and the existing type-to-filter
  matches on substring, so `/prod/` narrows as well as a tree would. A drill-down browser
  would need `get_parameters_by_path` per level plus a second screen class for no gain.
- **Client-side filtering, as with every other list screen.** Filtering triggers the
  inherited "fetch remaining pages" behaviour rather than SSM's server-side
  `ParameterFilters`, keeping the screen identical in feel to Lambda and SQS.

## Components

### Model (`src/awst/aws/models.py`)

`ParameterSummary` — frozen, slotted dataclass:

- `name: str` — the full parameter name, including any leading path
- `param_type: str` — `"String"`, `"StringList"`, or `"SecureString"` (named `param_type`
  because `type` shadows the builtin)
- `tier: str` — `"Standard"`, `"Advanced"`, or `"Intelligent-Tiering"`
- `modified: datetime` — `LastModifiedDate`, already timezone-aware from botocore

### Gateway (`src/awst/aws/ssm.py`)

`SsmGateway`, constructed with a boto3 SSM client (typed as `SSMClient` under
`TYPE_CHECKING`):

- `list_parameters(next_token: str | None = None) -> Page[ParameterSummary]` — calls
  `describe_parameters()` on the first page and `describe_parameters(NextToken=...)`
  thereafter, maps each entry through a module-level `_to_summary`, and returns a `Page`
  carrying the response's `NextToken` (`None` on the last page).
- Items are returned in API order; sorting is the screen's job, matching Lambda and SQS.
- A page with no parameters omits the `Parameters` key; treat it as empty.
- `BotoCoreError`/`ClientError` are mapped to `AwsError` via the existing
  `map_botocore_error`.

`pyproject.toml`: add `ssm` to the `boto3-stubs[...]` extras so `SSMClient` types resolve.

### Screen (`src/awst/screens/parameters.py`)

Mirrors `queues.py` / `functions.py`:

- `ParameterLister` — `Protocol` declaring
  `list_parameters(next_token: str | None = None) -> Page[ParameterSummary]`.
- `ParameterListScreen(ResourceListScreen[ParameterSummary])` with:
  - `TITLE = "SSM parameters"`
  - `COLUMNS = ("Name", "Type", "Tier", "Modified")`
  - `NOUN = "parameter"`
  - `_list` / `_list_more` / `_has_more` tracking `_next_token`, guarded by
    `get_current_worker().is_cancelled` exactly as the sibling screens do
  - `_sort_key` returning the parameter name
  - `_row` rendering `(name, param_type, tier, relative_age(modified, now))`
  - `_item_name` returning the parameter name (row key and filter target)

No Enter action: the base class does nothing on row selection, and there is no detail
screen. Loading state, error display, refresh (`r`), filter (`/`), paging (`m`), and the
SSO re-login path (`l`) are all inherited.

### Wiring

- `AwstApp` gains an `ssm_gateway` constructor parameter and a lazy `ssm_gateway` property
  mirroring the existing gateway properties, and clears `_ssm_gateway` in
  `reset_gateways()` so a region switch rebuilds it.
- `SERVICES` in `screens/home.py` gains an entry: `option_id="ssm"`, `name="SSM"`,
  `resource="Parameters"`, `enabled=True`,
  `screen_factory=lambda app: ParameterListScreen(app.ssm_gateway)`.

## Error handling

All API failures surface as `AwsError` and render through the existing
`ResourceListScreen` error path — a full-screen panel before the first successful load, a
toast notification afterwards. A `CredentialsError` on an SSO profile offers `l` to log in.
There are no write operations and no per-parameter calls, so no partial-failure cases
exist.

## Testing

- **Gateway** (`tests/test_ssm_gateway.py`): moto `mock_aws` for the happy paths — name,
  type, tier, and modified timestamp are mapped correctly; API order is preserved
  unsorted; an empty region returns no items. `Stubber` for the paging contract
  (`NextToken` forwarded on the second call, surfaced as `Page.next_token`, `None` on the
  last page) and for error mapping to `AwsError`.
- **UI** (`tests/test_parameter_list_screen.py`, pytest-asyncio + Textual pilot), mirroring
  `test_queue_list_screen.py`: one row per parameter with all four columns; empty region
  renders zero rows with the "parameter" noun; filter narrows rows live; initial load
  failure shows the error panel; rows sorted by name regardless of gateway order; `m`
  appends and re-sorts the next page; filtering fetches remaining pages to find matches
  beyond the first page.
- **Fakes** (`tests/fakes.py`): add `make_parameter` and `FakeSsmGateway`, shaped like
  `make_queue` / `FakeSqsGateway` (`parameters`, `error`, `pages`, recording `calls` and
  `next_tokens`).
- **App** (`tests/test_app.py`): a test that selecting SSM from the home menu opens the
  parameter list. The existing menu-wrap test asserts `options.highlighted == 3` for the
  last entry; that moves to `4`.
- **Docs**: extend the implemented-services sentence in `CLAUDE.md` to mention SSM
  (parameter list).
- Completion gate: `make test` (lint + unit) passes.
