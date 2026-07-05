# Stack details page with delete — design

Date: 2026-07-05
Status: approved

## Goal

Add a CloudFormation stack details page to `awst`, opened by pressing Enter on a
row in the stack list. It shows the stack's overview, parameters, outputs,
resources, and recent events, and can delete the stack after a Y/N
confirmation. After a delete is requested the user refreshes manually (`r`) to
watch progress; there is no polling.

## Models (`src/awst/aws/models.py`)

New frozen, slotted dataclasses in the style of `StackSummary`, using tuples
for collections so instances stay immutable:

- `StackParameter(key: str, value: str)`
- `StackOutput(key: str, value: str, description: str | None)`
- `StackResource(logical_id: str, physical_id: str | None, resource_type: str, status: str)`
- `StackEvent(timestamp: datetime, logical_id: str, resource_type: str, status: str, reason: str | None)`
- `StackDetail(name, stack_id, status, status_reason: str | None, description: str | None, created, updated, parameters: tuple[StackParameter, ...], outputs: tuple[StackOutput, ...], resources: tuple[StackResource, ...], events: tuple[StackEvent, ...])`

New error type: `StackNotFoundError(AwsError)`. It lets the UI distinguish
"the stack is gone" (expected after a completed delete) from other failures.

## Gateway (`src/awst/aws/cloudformation.py`)

Two new methods on `CloudFormationGateway`:

- `get_stack_detail(name: str) -> StackDetail` — one bundle, three API calls:
  - `describe_stacks(StackName=name)` → overview, parameters, outputs.
  - `list_stack_resources` (paginated) → resources.
  - `describe_stack_events`, **first page only** (~100 most recent events,
    newest first; no pagination).
  - A `ValidationError` whose message says the stack does not exist raises
    `StackNotFoundError`; all other botocore errors go through the existing
    `map_botocore_error`.
- `delete_stack(name: str) -> None` — calls the (asynchronous) CloudFormation
  delete API and returns immediately; botocore errors map via
  `map_botocore_error`.

## Detail screen (`src/awst/screens/stack_detail.py`)

`StackDetailScreen(Screen[None])`, constructed with a gateway and the stack
name. The gateway is typed as a new `StackInspector` protocol:

```python
class StackInspector(Protocol):
    def get_stack_detail(self, name: str) -> StackDetail: ...
    def delete_stack(self, name: str) -> None: ...
```

Layout is a `TabbedContent` with three tabs:

- **Overview** — status (styled with `status_style`), status reason,
  description, created/updated (via `relative_age`), stack ID, then
  parameters and outputs rendered as small tables. A stack with no
  parameters/outputs shows "none" under that heading.
- **Resources** — `DataTable`: Logical ID, Physical ID, Type, Status
  (status styled).
- **Events** — `DataTable`: Time (relative), Logical ID, Type, Status,
  Reason; newest first.

Bindings: `escape` → back (pop screen), `r` → refresh, `d` → delete.

Data loading follows the `StackListScreen` pattern exactly: one thread worker
(`@work(thread=True, exclusive=True, exit_on_error=False)`) fetches the whole
`StackDetail`; `on_worker_state_changed` dispatches on worker name. First-load
failure shows a full-screen error panel (message + hint); refresh failure
raises a `notify(..., severity="error")` toast and keeps the stale data
visible.

If a refresh raises `StackNotFoundError`, the screen notifies that the stack
no longer exists and pops back to the stack list.

## Delete flow

- `d` pushes `ConfirmScreen(ModalScreen[bool])` (a reusable yes/no modal in
  `src/awst/screens/confirm.py`), showing the stack name
  with Delete/Cancel buttons; `y` confirms, `n` or `escape` cancels.
- On confirm, a second thread worker calls `gateway.delete_stack(name)`.
  - Success → `notify("Delete requested — press r to check progress")` and the
    screen stays put (manual refresh, no polling).
  - `AwsError` → error toast with message (+ hint).
- Subsequent refreshes show `DELETE_IN_PROGRESS` status and delete events;
  once the stack is gone, refresh hits the `StackNotFoundError` path above and
  returns to the list.

## Wiring changes to existing code

- `StackListScreen` (`src/awst/screens/stacks.py`):
  - `on_data_table_row_selected` pushes
    `StackDetailScreen(self._gateway, stack_name)` (row key is the stack name).
  - Its constructor protocol widens to a combined protocol
    (`StackLister` + `StackInspector`) so it can hand the gateway on.
  - Gains `on_screen_resume` → refresh, so returning from the details page
    (especially after a delete) never shows stale rows. This is the only
    behavior change to existing screens.
- `AwstApp` (`src/awst/app.py`): the `cloudformation_gateway` property's type
  annotation updates to the combined protocol. No construction changes —
  `CloudFormationGateway` already satisfies it.

## Testing

- `tests/fakes.py`: `FakeCloudFormationGateway` grows `get_stack_detail` and
  `delete_stack`; it can be primed with a `StackDetail` or an error per
  method, and records delete calls for assertions.
- UI tests (pytest-asyncio + `run_test()` pilot), new
  `tests/test_stack_detail_screen.py` plus a navigation test in
  `tests/test_stack_list_screen.py`:
  - Enter on a stack row opens the details screen for that stack.
  - Overview/Resources/Events tabs render the primed data.
  - `d` then confirm calls `delete_stack` on the fake and shows the toast.
  - `d` then cancel (or `escape`) does not call `delete_stack`.
  - Refresh that raises `StackNotFoundError` notifies and pops back to the
    list; the list refreshes on resume.
  - Initial-load `AwsError` shows the full-screen error panel.
- Gateway tests (moto `mock_aws`), extending
  `tests/test_cloudformation_gateway.py`:
  - `get_stack_detail` returns populated overview/parameters/outputs/
    resources/events for a created stack.
  - `delete_stack` deletes the stack.
  - A missing stack raises `StackNotFoundError`.

## Out of scope

- Polling / auto-refresh of any kind.
- Event pagination beyond the first page.
- Deleting from the list screen, retain-resources deletes, termination
  protection handling, and any other stack mutations (update, drift, etc.).
