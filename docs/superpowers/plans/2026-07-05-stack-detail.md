# Stack Details Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CloudFormation stack details screen (overview, parameters, outputs, resources, events) opened with Enter from the stack list, with a confirmed, manually-refreshed stack delete.

**Architecture:** A new `StackDetailScreen` (Textual `TabbedContent`) loads a single `StackDetail` bundle from the gateway in one thread worker, mirroring the existing `StackListScreen` pattern. Delete goes through a reusable `ConfirmScreen` modal and a second thread worker. `StackNotFoundError` distinguishes "stack is gone" from other AWS failures so a post-delete refresh pops back to the list.

**Tech Stack:** Python >= 3.14, Textual, boto3/botocore, uv, pytest + pytest-asyncio, moto (`mock_aws`), ruff + ty.

**Spec:** `docs/superpowers/specs/2026-07-05-stack-detail-design.md`

## Global Constraints

- Run everything through uv: tests are `uv run --frozen pytest <path> -v`; the full check is `make test` (lint + unit).
- `make lint` (ruff check, `ruff format --check`, `ty check`) must pass before every commit; run `make format` first to auto-format.
- Line length is 120 characters.
- Source modules start with `from __future__ import annotations`; methods annotate `self: Self`; typing-only imports go under `if TYPE_CHECKING:`.
- Screens never import boto3/botocore; the AWS layer (`src/awst/aws/`) never imports Textual.
- Data models are `@dataclass(frozen=True, slots=True)`.
- Screens load data with `@work(thread=True, exit_on_error=False)` workers and handle results in `on_worker_state_changed`, dispatching on `event.worker.name`.
- Commit messages: short imperative summary line (repo style, e.g. "Add CloudFormation stack list"), then a blank line and these trailers:

  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01PbX9hyss41xvWj4GNdh7nd
  ```

## File Structure

- Modify `src/awst/aws/models.py` — add `StackNotFoundError`, `StackParameter`, `StackOutput`, `StackResource`, `StackEvent`, `StackDetail` (Task 1).
- Modify `src/awst/aws/cloudformation.py` — add `get_stack_detail`, `delete_stack`, private converters (Tasks 2-3).
- Create `src/awst/screens/confirm.py` — reusable `ConfirmScreen` Y/N modal (Task 4).
- Create `src/awst/screens/stack_detail.py` — `StackInspector` protocol + `StackDetailScreen` (Tasks 5-6).
- Modify `src/awst/screens/stacks.py` — `StackGateway` protocol, Enter opens details, refresh on resume (Task 7).
- Modify `src/awst/app.py` — gateway property typed as `StackGateway` (Task 7).
- Modify `tests/fakes.py`, `tests/test_models.py`, `tests/test_cloudformation_gateway.py`, `tests/test_stack_list_screen.py`, `tests/test_app.py`; create `tests/test_confirm_screen.py`, `tests/test_stack_detail_screen.py`.

---

### Task 1: Stack detail models and StackNotFoundError

**Files:**
- Modify: `src/awst/aws/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: existing `AwsError` in `src/awst/aws/models.py`.
- Produces (used by every later task):
  - `StackNotFoundError(AwsError)`
  - `StackParameter(key: str, value: str)`
  - `StackOutput(key: str, value: str, description: str | None)`
  - `StackResource(logical_id: str, physical_id: str | None, resource_type: str, status: str)`
  - `StackEvent(timestamp: datetime, logical_id: str, resource_type: str, status: str, reason: str | None)`
  - `StackDetail(name: str, stack_id: str, status: str, status_reason: str | None, description: str | None, created: datetime, updated: datetime, parameters: tuple[StackParameter, ...], outputs: tuple[StackOutput, ...], resources: tuple[StackResource, ...], events: tuple[StackEvent, ...])`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py` (and extend its import from `awst.aws.models` to include the new names):

```python
from awst.aws.models import (
    AwsError,
    StackDetail,
    StackNotFoundError,
    StackSummary,
)
```

```python
def test_stack_not_found_error_is_an_aws_error() -> None:
    error = StackNotFoundError("Stack alpha does not exist.")

    assert isinstance(error, AwsError)
    assert error.message == "Stack alpha does not exist."
    assert error.hint is None


def test_stack_detail_is_immutable() -> None:
    detail = StackDetail(
        name="alpha",
        stack_id="arn:aws:cloudformation:eu-west-1:123456789012:stack/alpha/abc",
        status="CREATE_COMPLETE",
        status_reason=None,
        description=None,
        created=datetime(2026, 1, 1, tzinfo=UTC),
        updated=datetime(2026, 1, 1, tzinfo=UTC),
        parameters=(),
        outputs=(),
        resources=(),
        events=(),
    )

    with pytest.raises(AttributeError):
        detail.status = "DELETE_COMPLETE"  # type: ignore[misc]  # ty: ignore[invalid-assignment]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'StackDetail' from 'awst.aws.models'`

- [ ] **Step 3: Write the implementation**

In `src/awst/aws/models.py`, add `StackNotFoundError` directly after `AwsError`, and the dataclasses after `StackSummary`:

```python
class StackNotFoundError(AwsError):
    """The named stack does not exist (for example, it finished deleting)."""
```

```python
@dataclass(frozen=True, slots=True)
class StackParameter:
    """One parameter the stack was created or updated with."""

    key: str
    value: str


@dataclass(frozen=True, slots=True)
class StackOutput:
    """One output exported by the stack."""

    key: str
    value: str
    description: str | None


@dataclass(frozen=True, slots=True)
class StackResource:
    """One resource managed by the stack."""

    logical_id: str
    physical_id: str | None
    resource_type: str
    status: str


@dataclass(frozen=True, slots=True)
class StackEvent:
    """One entry from the stack's event history."""

    timestamp: datetime
    logical_id: str
    resource_type: str
    status: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class StackDetail:
    """Everything the detail screen shows about one stack."""

    name: str
    stack_id: str
    status: str
    status_reason: str | None
    description: str | None
    created: datetime
    updated: datetime
    parameters: tuple[StackParameter, ...]
    outputs: tuple[StackOutput, ...]
    resources: tuple[StackResource, ...]
    events: tuple[StackEvent, ...]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_models.py -v`
Expected: all PASS

- [ ] **Step 5: Lint and commit**

```bash
make format && make lint
git add src/awst/aws/models.py tests/test_models.py
git commit -m "Add stack detail models and StackNotFoundError"
```

(Include the trailers from Global Constraints in this and every commit.)

---

### Task 2: Gateway get_stack_detail

**Files:**
- Modify: `src/awst/aws/cloudformation.py`
- Test: `tests/test_cloudformation_gateway.py`

**Interfaces:**
- Consumes: Task 1 models; existing `map_botocore_error` from `awst.aws.errors`.
- Produces: `CloudFormationGateway.get_stack_detail(name: str) -> StackDetail` — raises `StackNotFoundError` if the stack doesn't exist, `AwsError` otherwise. Events are newest-first, first page only.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cloudformation_gateway.py`, extend the imports:

```python
import pytest

from awst.aws.models import StackNotFoundError, StackParameter
```

Add a module-level template beside `TEMPLATE`:

```python
DETAIL_TEMPLATE = json.dumps(
    {
        "Description": "a detailed stack",
        "Parameters": {"Env": {"Type": "String"}},
        "Resources": {"Topic": {"Type": "AWS::SNS::Topic"}},
        "Outputs": {"TopicName": {"Value": {"Ref": "Topic"}, "Description": "the topic"}},
    }
)


def _create_detailed_stack() -> None:
    client = boto3.client("cloudformation", region_name="eu-west-1")
    client.create_stack(
        StackName="alpha",
        TemplateBody=DETAIL_TEMPLATE,
        Parameters=[{"ParameterKey": "Env", "ParameterValue": "prod"}],
    )
```

Append the tests:

```python
@mock_aws
def test_get_stack_detail_maps_overview_fields() -> None:
    _create_detailed_stack()

    detail = _gateway().get_stack_detail("alpha")

    assert detail.name == "alpha"
    assert detail.status == "CREATE_COMPLETE"
    assert detail.description == "a detailed stack"
    assert detail.stack_id.startswith("arn:")
    assert detail.created.tzinfo is not None
    assert detail.updated == detail.created


@mock_aws
def test_get_stack_detail_maps_parameters_and_outputs() -> None:
    _create_detailed_stack()

    detail = _gateway().get_stack_detail("alpha")

    assert detail.parameters == (StackParameter(key="Env", value="prod"),)
    assert len(detail.outputs) == 1
    assert detail.outputs[0].key == "TopicName"
    assert detail.outputs[0].description == "the topic"


@mock_aws
def test_get_stack_detail_lists_resources() -> None:
    _create_detailed_stack()

    detail = _gateway().get_stack_detail("alpha")

    assert len(detail.resources) == 1
    resource = detail.resources[0]
    assert resource.logical_id == "Topic"
    assert resource.resource_type == "AWS::SNS::Topic"
    assert resource.status == "CREATE_COMPLETE"


@mock_aws
def test_get_stack_detail_returns_events_newest_first() -> None:
    _create_detailed_stack()

    detail = _gateway().get_stack_detail("alpha")

    assert detail.events
    timestamps = [event.timestamp for event in detail.events]
    assert timestamps == sorted(timestamps, reverse=True)
    assert detail.events[0].logical_id
    assert detail.events[0].status


@mock_aws
def test_get_stack_detail_raises_stack_not_found_for_missing_stack() -> None:
    with pytest.raises(StackNotFoundError):
        _gateway().get_stack_detail("missing")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_cloudformation_gateway.py -v`
Expected: the five new tests FAIL with `AttributeError: 'CloudFormationGateway' object has no attribute 'get_stack_detail'`; the three existing tests still PASS.

- [ ] **Step 3: Write the implementation**

In `src/awst/aws/cloudformation.py`, extend the imports:

```python
from awst.aws.models import (
    AwsError,
    StackDetail,
    StackEvent,
    StackNotFoundError,
    StackOutput,
    StackParameter,
    StackResource,
    StackSummary,
)

if TYPE_CHECKING:
    from mypy_boto3_cloudformation import CloudFormationClient
    from mypy_boto3_cloudformation.type_defs import (
        OutputTypeDef,
        ParameterTypeDef,
        StackEventTypeDef,
        StackResourceSummaryTypeDef,
        StackTypeDef,
    )
```

Add the method to `CloudFormationGateway` after `list_stacks` (the class docstring's "Read-only" claim gets updated in Task 3):

```python
    def get_stack_detail(self: Self, name: str) -> StackDetail:
        """Return one stack's overview, parameters, outputs, resources, and recent events.

        Events are newest-first and limited to the first API page (~100 entries).
        Raises StackNotFoundError if the stack does not exist, AwsError for any other failure.
        """
        try:
            stack = self._client.describe_stacks(StackName=name)["Stacks"][0]
            resources = tuple(
                _to_resource(resource)
                for page in self._client.get_paginator("list_stack_resources").paginate(StackName=name)
                for resource in page["StackResourceSummaries"]
            )
            event_page = self._client.describe_stack_events(StackName=name)["StackEvents"]
            events = tuple(sorted((_to_event(event) for event in event_page), key=_event_time, reverse=True))
        except (BotoCoreError, ClientError) as error:
            raise _map_stack_error(error, name) from error
        return _to_detail(stack, resources, events)
```

Add the module-level helpers after `_to_summary`:

```python
def _map_stack_error(error: BotoCoreError | ClientError, name: str) -> AwsError:
    if isinstance(error, ClientError) and "does not exist" in error.response["Error"]["Message"]:
        return StackNotFoundError(f"Stack {name} does not exist.")
    return map_botocore_error(error)


def _event_time(event: StackEvent) -> datetime:
    return event.timestamp


def _to_detail(stack: StackTypeDef, resources: tuple[StackResource, ...], events: tuple[StackEvent, ...]) -> StackDetail:
    created = stack["CreationTime"]
    return StackDetail(
        name=stack["StackName"],
        stack_id=stack.get("StackId", ""),
        status=stack["StackStatus"],
        status_reason=stack.get("StackStatusReason"),
        description=stack.get("Description"),
        created=created,
        updated=stack.get("LastUpdatedTime", created),
        parameters=tuple(sorted((_to_parameter(p) for p in stack.get("Parameters", [])), key=lambda p: p.key)),
        outputs=tuple(sorted((_to_output(o) for o in stack.get("Outputs", [])), key=lambda o: o.key)),
        resources=resources,
        events=events,
    )


def _to_parameter(parameter: ParameterTypeDef) -> StackParameter:
    return StackParameter(key=parameter.get("ParameterKey", ""), value=parameter.get("ParameterValue", ""))


def _to_output(output: OutputTypeDef) -> StackOutput:
    return StackOutput(
        key=output.get("OutputKey", ""),
        value=output.get("OutputValue", ""),
        description=output.get("Description"),
    )


def _to_resource(resource: StackResourceSummaryTypeDef) -> StackResource:
    return StackResource(
        logical_id=resource["LogicalResourceId"],
        physical_id=resource.get("PhysicalResourceId"),
        resource_type=resource["ResourceType"],
        status=resource["ResourceStatus"],
    )


def _to_event(event: StackEventTypeDef) -> StackEvent:
    return StackEvent(
        timestamp=event["Timestamp"],
        logical_id=event.get("LogicalResourceId", ""),
        resource_type=event.get("ResourceType", ""),
        status=event.get("ResourceStatus", ""),
        reason=event.get("ResourceStatusReason"),
    )
```

`_event_time` needs `datetime` for its annotation — add `from datetime import datetime` under `if TYPE_CHECKING:` (module has `from __future__ import annotations`? It does **not** currently — it uses plain `from typing import TYPE_CHECKING, Self`. Add `from __future__ import annotations` as the first import so the TYPE_CHECKING-only `datetime` annotation works).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_cloudformation_gateway.py -v`
Expected: all PASS

- [ ] **Step 5: Lint and commit**

```bash
make format && make lint
git add src/awst/aws/cloudformation.py tests/test_cloudformation_gateway.py
git commit -m "Add CloudFormation get_stack_detail to the gateway"
```

---

### Task 3: Gateway delete_stack

**Files:**
- Modify: `src/awst/aws/cloudformation.py`
- Test: `tests/test_cloudformation_gateway.py`

**Interfaces:**
- Consumes: Task 2's gateway.
- Produces: `CloudFormationGateway.delete_stack(name: str) -> None` — requests the asynchronous delete and returns immediately; raises `AwsError` on failure.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cloudformation_gateway.py`:

```python
@mock_aws
def test_delete_stack_deletes_the_stack() -> None:
    client = boto3.client("cloudformation", region_name="eu-west-1")
    client.create_stack(StackName="alpha", TemplateBody=TEMPLATE)

    _gateway().delete_stack("alpha")

    assert _gateway().list_stacks() == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --frozen pytest tests/test_cloudformation_gateway.py::test_delete_stack_deletes_the_stack -v`
Expected: FAIL with `AttributeError: 'CloudFormationGateway' object has no attribute 'delete_stack'`

- [ ] **Step 3: Write the implementation**

Add to `CloudFormationGateway` after `get_stack_detail`, and update the class docstring since the gateway is no longer read-only:

```python
class CloudFormationGateway:
    """Access to CloudFormation, returning plain data models."""
```

```python
    def delete_stack(self: Self, name: str) -> None:
        """Request deletion of the stack; CloudFormation deletes asynchronously.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            self._client.delete_stack(StackName=name)
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_cloudformation_gateway.py -v`
Expected: all PASS

- [ ] **Step 5: Lint and commit**

```bash
make format && make lint
git add src/awst/aws/cloudformation.py tests/test_cloudformation_gateway.py
git commit -m "Add CloudFormation delete_stack to the gateway"
```

---

### Task 4: ConfirmScreen modal

**Files:**
- Create: `src/awst/screens/confirm.py`
- Create: `tests/test_confirm_screen.py`

**Interfaces:**
- Consumes: nothing project-specific.
- Produces: `ConfirmScreen(question: str)`, a `ModalScreen[bool]` that dismisses `True` on confirm (`y` key or Yes button) and `False` on cancel (`n`, `escape`, or No button). Later tasks open it with `self.app.push_screen(ConfirmScreen(question), callback)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_confirm_screen.py`:

```python
"""Tests for the confirmation modal."""

from typing import Self

import pytest
from textual.app import App
from textual.widgets import Static

from awst.screens.confirm import ConfirmScreen


class ConfirmApp(App[None]):
    """Harness that opens the confirmation modal and records the answer."""

    def __init__(self: Self) -> None:
        super().__init__()
        self.answers: list[bool | None] = []

    def on_mount(self: Self) -> None:
        self.push_screen(ConfirmScreen("Delete stack alpha? This cannot be undone."), self.answers.append)


@pytest.mark.asyncio
async def test_shows_the_question() -> None:
    app = ConfirmApp()

    async with app.run_test() as pilot:
        await pilot.pause()

        assert "Delete stack alpha?" in str(app.screen.query_one("#question", Static).content)


@pytest.mark.asyncio
async def test_y_key_confirms() -> None:
    app = ConfirmApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        assert app.answers == [True]


@pytest.mark.asyncio
async def test_n_key_cancels() -> None:
    app = ConfirmApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()

        assert app.answers == [False]


@pytest.mark.asyncio
async def test_escape_cancels() -> None:
    app = ConfirmApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.answers == [False]


@pytest.mark.asyncio
async def test_yes_button_confirms() -> None:
    app = ConfirmApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#yes")
        await pilot.pause()

        assert app.answers == [True]


@pytest.mark.asyncio
async def test_no_button_cancels() -> None:
    app = ConfirmApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#no")
        await pilot.pause()

        assert app.answers == [False]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_confirm_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awst.screens.confirm'`

- [ ] **Step 3: Write the implementation**

Create `src/awst/screens/confirm.py`:

```python
"""A reusable yes/no confirmation modal."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Self

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType


class ConfirmScreen(ModalScreen[bool]):
    """Ask a yes/no question; dismisses with True on confirm, False otherwise."""

    BINDINGS: ClassVar[list[BindingType]] = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    #dialog {
        width: auto;
        max-width: 60;
        height: auto;
        padding: 1 2;
        background: $panel;
        border: thick $primary;
    }
    #question { margin-bottom: 1; }
    #buttons { width: 100%; height: auto; align-horizontal: center; }
    #buttons Button { margin: 0 1; }
    """

    def __init__(self: Self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self: Self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._question, id="question")
            with Horizontal(id="buttons"):
                yield Button("Yes", variant="error", id="yes")
                yield Button("No", variant="primary", id="no")

    def on_mount(self: Self) -> None:
        self.query_one("#no", Button).focus()

    def on_button_pressed(self: Self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_confirm(self: Self) -> None:
        self.dismiss(True)

    def action_cancel(self: Self) -> None:
        self.dismiss(False)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_confirm_screen.py -v`
Expected: all PASS

- [ ] **Step 5: Lint and commit**

```bash
make format && make lint
git add src/awst/screens/confirm.py tests/test_confirm_screen.py
git commit -m "Add reusable yes/no confirmation modal"
```

---

### Task 5: Stack detail screen — load and render

**Files:**
- Modify: `tests/fakes.py`
- Create: `src/awst/screens/stack_detail.py`
- Create: `tests/test_stack_detail_screen.py`

**Interfaces:**
- Consumes: Task 1 models; `relative_age` / `status_style` from `awst.screens.formatting`.
- Produces:
  - `StackInspector` protocol with `get_stack_detail(name: str) -> StackDetail` and `delete_stack(name: str) -> None` (delete is wired to the UI in Task 6, but the protocol is complete now).
  - `StackDetailScreen(gateway: StackInspector, stack_name: str)`.
  - `FakeCloudFormationGateway` gains `detail`, `detail_error`, `delete_error` constructor kwargs and `detail_calls: list[str]`, `deleted: list[str]` recorders.
  - Test helper `_detail(...)` in `tests/test_stack_detail_screen.py`, reused by Task 7's tests.

- [ ] **Step 1: Extend the fake gateway**

Replace `tests/fakes.py` with:

```python
"""Test fakes for AWS gateways."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from awst.aws.models import StackNotFoundError

if TYPE_CHECKING:
    from awst.aws.models import AwsError, StackDetail, StackSummary


class FakeCloudFormationGateway:
    """In-memory stand-in for the real CloudFormation gateway."""

    def __init__(
        self: Self,
        stacks: list[StackSummary] | None = None,
        error: AwsError | None = None,
        detail: StackDetail | None = None,
        detail_error: AwsError | None = None,
        delete_error: AwsError | None = None,
    ) -> None:
        self.stacks = stacks or []
        self.error = error
        self.detail = detail
        self.detail_error = detail_error
        self.delete_error = delete_error
        self.calls = 0
        self.detail_calls: list[str] = []
        self.deleted: list[str] = []

    def list_stacks(self: Self) -> list[StackSummary]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.stacks)

    def get_stack_detail(self: Self, name: str) -> StackDetail:
        self.detail_calls.append(name)
        if self.detail_error is not None:
            raise self.detail_error
        if self.detail is None:
            raise StackNotFoundError(f"Stack {name} does not exist.")
        return self.detail

    def delete_stack(self: Self, name: str) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(name)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_stack_detail_screen.py`:

```python
"""Tests for the CloudFormation stack detail screen."""

from datetime import UTC, datetime
from typing import Self

import pytest
from rich.text import Text
from textual.app import App
from textual.widgets import DataTable, Static

from awst.aws.models import (
    AwsError,
    StackDetail,
    StackEvent,
    StackOutput,
    StackParameter,
    StackResource,
)
from awst.screens.stack_detail import StackDetailScreen
from tests.fakes import FakeCloudFormationGateway

CREATED = datetime(2026, 1, 1, tzinfo=UTC)
PARAMETERS = (StackParameter(key="Env", value="prod"),)
OUTPUTS = (StackOutput(key="Url", value="https://example.com", description="endpoint"),)


def _detail(
    parameters: tuple[StackParameter, ...] = PARAMETERS,
    outputs: tuple[StackOutput, ...] = OUTPUTS,
) -> StackDetail:
    return StackDetail(
        name="alpha",
        stack_id="arn:aws:cloudformation:eu-west-1:123456789012:stack/alpha/abc",
        status="CREATE_COMPLETE",
        status_reason=None,
        description="a test stack",
        created=CREATED,
        updated=CREATED,
        parameters=parameters,
        outputs=outputs,
        resources=(
            StackResource(
                logical_id="Topic",
                physical_id="arn:aws:sns:eu-west-1:123456789012:topic",
                resource_type="AWS::SNS::Topic",
                status="CREATE_COMPLETE",
            ),
        ),
        events=(
            StackEvent(
                timestamp=CREATED,
                logical_id="alpha",
                resource_type="AWS::CloudFormation::Stack",
                status="CREATE_COMPLETE",
                reason=None,
            ),
            StackEvent(
                timestamp=CREATED,
                logical_id="Topic",
                resource_type="AWS::SNS::Topic",
                status="CREATE_IN_PROGRESS",
                reason="Resource creation Initiated",
            ),
        ),
    )


class DetailScreenApp(App[None]):
    """Minimal harness that opens the stack detail screen directly."""

    def __init__(self: Self, gateway: FakeCloudFormationGateway) -> None:
        super().__init__()
        self.gateway = gateway

    def on_mount(self: Self) -> None:
        self.push_screen(StackDetailScreen(self.gateway, "alpha"))


async def _settle(app: App[None]) -> None:
    """Wait for workers and let their messages be processed."""
    await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_overview_shows_status_description_and_stack_id() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        info = str(app.screen.query_one("#overview-info", Static).content)

        assert "CREATE_COMPLETE" in info
        assert "a test stack" in info
        assert "stack/alpha/abc" in info


@pytest.mark.asyncio
async def test_overview_lists_parameters_and_outputs() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        parameters = app.screen.query_one("#parameters", DataTable)
        outputs = app.screen.query_one("#outputs", DataTable)

        assert parameters.get_row_at(0) == ["Env", "prod"]
        assert outputs.get_row_at(0) == ["Url", "https://example.com", "endpoint"]


@pytest.mark.asyncio
async def test_overview_shows_none_for_missing_parameters_and_outputs() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail(parameters=(), outputs=())))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one("#parameters", DataTable).display is False
        assert app.screen.query_one("#parameters-none", Static).display is True
        assert app.screen.query_one("#outputs", DataTable).display is False
        assert app.screen.query_one("#outputs-none", Static).display is True


@pytest.mark.asyncio
async def test_resources_tab_lists_resources_with_styled_status() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        row = app.screen.query_one("#resources", DataTable).get_row_at(0)

        assert row[0] == "Topic"
        assert row[2] == "AWS::SNS::Topic"
        assert isinstance(row[3], Text)
        assert str(row[3]) == "CREATE_COMPLETE"
        assert str(row[3].style) == "green"


@pytest.mark.asyncio
async def test_events_tab_lists_events_in_gateway_order() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        events = app.screen.query_one("#events", DataTable)

        assert events.row_count == 2
        assert events.get_row_at(0)[1] == "alpha"
        assert events.get_row_at(1)[1] == "Topic"
        assert events.get_row_at(1)[4] == "Resource creation Initiated"


@pytest.mark.asyncio
async def test_refresh_refetches_detail() -> None:
    gateway = FakeCloudFormationGateway(detail=_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert gateway.detail_calls == ["alpha", "alpha"]


@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    gateway = FakeCloudFormationGateway(detail_error=AwsError("no credentials", hint="run `aws sso login`"))
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.screen.query_one("#error", Static)

        assert panel.display is True
        assert "no credentials" in str(panel.content)
        assert "aws sso login" in str(panel.content)


@pytest.mark.asyncio
async def test_retry_after_initial_failure_recovers() -> None:
    gateway = FakeCloudFormationGateway(detail_error=AwsError("boom"))
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.detail_error = None
        gateway.detail = _detail()
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert app.screen.query_one("#error", Static).display is False
        assert "CREATE_COMPLETE" in str(app.screen.query_one("#overview-info", Static).content)


@pytest.mark.asyncio
async def test_escape_pops_back() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert isinstance(app.screen, StackDetailScreen)

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, StackDetailScreen)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_stack_detail_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awst.screens.stack_detail'`

Also run: `uv run --frozen pytest tests/test_stack_list_screen.py -v` — the fake changes must not break existing tests (all PASS).

- [ ] **Step 4: Write the implementation**

Create `src/awst/screens/stack_detail.py`:

```python
"""CloudFormation stack detail screen."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

from rich.text import Text
from textual import work
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static, TabbedContent, TabPane
from textual.worker import WorkerState

from awst.aws.models import AwsError, StackNotFoundError
from awst.screens.confirm import ConfirmScreen
from awst.screens.formatting import relative_age, status_style

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.worker import Worker

    from awst.aws.models import StackDetail

RESOURCE_COLUMNS = ("Logical ID", "Physical ID", "Type", "Status")
EVENT_COLUMNS = ("Time", "Logical ID", "Type", "Status", "Reason")


class StackInspector(Protocol):
    """The slice of the CloudFormation gateway this screen needs."""

    def get_stack_detail(self: Self, name: str) -> StackDetail: ...

    def delete_stack(self: Self, name: str) -> None: ...


class StackDetailScreen(Screen[None]):
    """Detail view of one CloudFormation stack, able to delete it."""

    TITLE = "Stack details"

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "back", "Back"),
        ("r", "refresh", "Refresh"),
        ("d", "delete", "Delete stack"),
    ]

    DEFAULT_CSS = """
    #overview-info { height: auto; padding: 1 2 0 2; }
    .heading { height: 1; padding: 0 2; margin-top: 1; text-style: bold; }
    .none-label { display: none; height: 1; padding: 0 2; color: $text-muted; }
    #parameters, #outputs { height: auto; }
    #error { display: none; padding: 1 2; color: $text-error; }
    """

    def __init__(self: Self, gateway: StackInspector, stack_name: str) -> None:
        super().__init__()
        self._gateway = gateway
        self._stack_name = stack_name
        self._loaded = False

    def compose(self: Self) -> ComposeResult:
        with TabbedContent(id="tabs"):
            with TabPane("Overview", id="overview-tab"), VerticalScroll():
                yield Static(id="overview-info")
                yield Static("Parameters", classes="heading")
                yield Static("none", id="parameters-none", classes="none-label")
                yield DataTable(id="parameters")
                yield Static("Outputs", classes="heading")
                yield Static("none", id="outputs-none", classes="none-label")
                yield DataTable(id="outputs")
            with TabPane("Resources", id="resources-tab"):
                yield DataTable(id="resources")
            with TabPane("Events", id="events-tab"):
                yield DataTable(id="events")
        yield Static(id="error")
        yield Footer()

    def on_mount(self: Self) -> None:
        self.sub_title = self._stack_name
        self.query_one("#parameters", DataTable).add_columns("Key", "Value")
        self.query_one("#outputs", DataTable).add_columns("Key", "Value", "Description")
        self.query_one("#resources", DataTable).add_columns(*RESOURCE_COLUMNS)
        self.query_one("#events", DataTable).add_columns(*EVENT_COLUMNS)
        for table in self.query(DataTable):
            table.cursor_type = "row"
        self.query_one("#tabs", TabbedContent).loading = True
        self._fetch_detail()

    @work(thread=True, exclusive=True, group="detail", exit_on_error=False)
    def _fetch_detail(self: Self) -> StackDetail:
        return self._gateway.get_stack_detail(self._stack_name)

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.worker.name == "_fetch_detail":
            self._handle_fetch(event)

    def _handle_fetch(self: Self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.SUCCESS:
            self._loaded = True
            self.query_one("#tabs", TabbedContent).loading = False
            detail = event.worker.result
            if detail is not None:
                self._render_detail(detail)
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, StackNotFoundError) and self._loaded:
                self.notify(f"Stack {self._stack_name} no longer exists.", title="Stack deleted")
                self.app.pop_screen()
            elif isinstance(error, AwsError):
                self._show_error(error)
            elif error is not None:
                raise error

    def _show_error(self: Self, error: AwsError) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.loading = False
        if self._loaded:
            message = error.message if error.hint is None else f"{error.message} ({error.hint})"
            self.notify(message, title="Refresh failed", severity="error")
            return
        tabs.display = False
        self.set_focus(None)
        panel = self.query_one("#error", Static)
        panel.update(error.message if error.hint is None else f"{error.message}\n{error.hint}")
        panel.display = True

    def _render_detail(self: Self, detail: StackDetail) -> None:
        now = datetime.now(tz=UTC)
        self.query_one("#overview-info", Static).update(_overview_text(detail, now))
        parameters = self.query_one("#parameters", DataTable)
        parameters.clear()
        for parameter in detail.parameters:
            parameters.add_row(parameter.key, parameter.value)
        self._toggle_none(parameters, "#parameters-none", empty=not detail.parameters)
        outputs = self.query_one("#outputs", DataTable)
        outputs.clear()
        for output in detail.outputs:
            outputs.add_row(output.key, output.value, output.description or "")
        self._toggle_none(outputs, "#outputs-none", empty=not detail.outputs)
        self._render_resources(detail)
        self._render_events(detail, now)

    def _render_resources(self: Self, detail: StackDetail) -> None:
        table = self.query_one("#resources", DataTable)
        table.clear()
        for resource in detail.resources:
            table.add_row(
                resource.logical_id,
                resource.physical_id or "",
                resource.resource_type,
                Text(resource.status, style=status_style(resource.status)),
            )

    def _render_events(self: Self, detail: StackDetail, now: datetime) -> None:
        table = self.query_one("#events", DataTable)
        table.clear()
        for stack_event in detail.events:
            table.add_row(
                relative_age(stack_event.timestamp, now),
                stack_event.logical_id,
                stack_event.resource_type,
                Text(stack_event.status, style=status_style(stack_event.status)),
                stack_event.reason or "",
            )

    def _toggle_none(self: Self, table: DataTable, none_selector: str, *, empty: bool) -> None:
        table.display = not empty
        self.query_one(none_selector, Static).display = empty

    def action_back(self: Self) -> None:
        self.app.pop_screen()

    def action_refresh(self: Self) -> None:
        self.query_one("#error", Static).display = False
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.display = True
        if not self._loaded:
            tabs.loading = True
        self._fetch_detail()

    def action_delete(self: Self) -> None:
        question = f"Delete stack {self._stack_name}? This cannot be undone."
        self.app.push_screen(ConfirmScreen(question), self._on_delete_confirmed)

    def _on_delete_confirmed(self: Self, confirmed: bool | None) -> None:
        if confirmed:
            self._request_delete()

    @work(thread=True, exclusive=True, group="delete", exit_on_error=False)
    def _request_delete(self: Self) -> None:
        self._gateway.delete_stack(self._stack_name)


def _overview_text(detail: StackDetail, now: datetime) -> Text:
    text = Text()
    text.append("Status       ")
    text.append(detail.status, style=status_style(detail.status))
    if detail.status_reason:
        text.append(f"\nReason       {detail.status_reason}")
    if detail.description:
        text.append(f"\nDescription  {detail.description}")
    text.append(f"\nCreated      {relative_age(detail.created, now)}")
    text.append(f"\nUpdated      {relative_age(detail.updated, now)}")
    text.append(f"\nStack ID     {detail.stack_id}")
    return text
```

Note: `_request_delete` and the delete plumbing get their worker-result handling (toasts) in Task 6; this task only needs the fetch path, but the delete worker is included here so the file is complete and the `d` binding doesn't reference a missing action.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_stack_detail_screen.py tests/test_stack_list_screen.py -v`
Expected: all PASS

- [ ] **Step 6: Lint and commit**

```bash
make format && make lint
git add src/awst/screens/stack_detail.py tests/test_stack_detail_screen.py tests/fakes.py
git commit -m "Add CloudFormation stack detail screen"
```

---

### Task 6: Stack detail screen — delete flow and not-found handling

**Files:**
- Modify: `src/awst/screens/stack_detail.py`
- Test: `tests/test_stack_detail_screen.py`

**Interfaces:**
- Consumes: Task 4 `ConfirmScreen`, Task 5 screen, fake's `deleted` / `delete_error` / `detail_error`.
- Produces: complete delete UX — `d` confirms then calls `delete_stack`; success toast says "Delete requested — press r to check progress."; failure shows an error toast; a refresh that raises `StackNotFoundError` notifies and pops back.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stack_detail_screen.py` (add imports: `from textual.app import App` is present; add `from awst.aws.models import StackNotFoundError` to the models import block and `from awst.screens.confirm import ConfirmScreen`):

```python
@pytest.mark.asyncio
async def test_d_opens_confirmation_modal_naming_the_stack() -> None:
    app = DetailScreenApp(FakeCloudFormationGateway(detail=_detail()))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()

        assert isinstance(app.screen, ConfirmScreen)
        assert "alpha" in str(app.screen.query_one("#question", Static).content)


@pytest.mark.asyncio
async def test_confirming_delete_calls_gateway_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeCloudFormationGateway(detail=_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await _settle(app)
        await pilot.pause()

        assert gateway.deleted == ["alpha"]
        assert any("Delete requested" in toast for toast in toasts)
        assert isinstance(app.screen, StackDetailScreen)  # stays put; refresh is manual


@pytest.mark.asyncio
async def test_cancelling_delete_does_not_call_gateway() -> None:
    gateway = FakeCloudFormationGateway(detail=_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("escape")
        await _settle(app)
        await pilot.pause()

        assert gateway.deleted == []
        assert isinstance(app.screen, StackDetailScreen)


@pytest.mark.asyncio
async def test_delete_failure_shows_error_toast(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeCloudFormationGateway(detail=_detail(), delete_error=AwsError("denied"))
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await _settle(app)
        await pilot.pause()

        assert gateway.deleted == []
        assert "denied" in toasts


@pytest.mark.asyncio
async def test_refresh_after_stack_deleted_notifies_and_pops_back(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeCloudFormationGateway(detail=_detail())
    app = DetailScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        gateway.detail = None
        gateway.detail_error = StackNotFoundError("Stack alpha does not exist.")
        await pilot.press("r")
        await _settle(app)
        await pilot.pause()

        assert not isinstance(app.screen, StackDetailScreen)
        assert any("no longer exists" in toast for toast in toasts)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_stack_detail_screen.py -v`
Expected: `test_confirming_delete_calls_gateway_and_notifies` and `test_delete_failure_shows_error_toast` FAIL (no toast is emitted — the delete worker's results aren't handled yet). The modal, cancel, and pop-back tests may already pass from Task 5's plumbing; that's fine.

- [ ] **Step 3: Write the implementation**

In `src/awst/screens/stack_detail.py`, extend `on_worker_state_changed` and add `_handle_delete`:

```python
    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.worker.name == "_fetch_detail":
            self._handle_fetch(event)
        elif event.worker.name == "_request_delete":
            self._handle_delete(event)

    def _handle_delete(self: Self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.SUCCESS:
            self.notify("Delete requested — press r to check progress.", title=self._stack_name)
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, AwsError):
                message = error.message if error.hint is None else f"{error.message} ({error.hint})"
                self.notify(message, title="Delete failed", severity="error")
            elif error is not None:
                raise error
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_stack_detail_screen.py tests/test_confirm_screen.py -v`
Expected: all PASS

- [ ] **Step 5: Lint and commit**

```bash
make format && make lint
git add src/awst/screens/stack_detail.py tests/test_stack_detail_screen.py
git commit -m "Add stack delete flow to the detail screen"
```

---

### Task 7: Wire the list screen to details, refresh on resume, widen app typing

**Files:**
- Modify: `src/awst/screens/stacks.py`
- Modify: `src/awst/app.py`
- Test: `tests/test_stack_list_screen.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `StackDetailScreen` and `StackInspector` from `awst.screens.stack_detail`; `_detail` helper from `tests.test_stack_detail_screen`.
- Produces: `StackGateway(StackLister, StackInspector, Protocol)` in `awst.screens.stacks`; Enter on a stack row opens the details screen; the list refetches when it becomes the active screen again; `AwstApp.cloudformation_gateway` is typed `StackGateway`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stack_list_screen.py` (add imports `from awst.screens.stack_detail import StackDetailScreen` and `from tests.test_stack_detail_screen import _detail`):

```python
@pytest.mark.asyncio
async def test_enter_on_row_opens_detail_screen_for_that_stack() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("prod-api"), _stack("prod-network")], detail=_detail())
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("enter")
        await _settle(app)
        await pilot.pause()

        assert isinstance(app.screen, StackDetailScreen)
        assert gateway.detail_calls == ["prod-api"]


@pytest.mark.asyncio
async def test_returning_from_detail_refreshes_the_list() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("prod-api")], detail=_detail())
    app = StackScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert gateway.calls == 1

        await pilot.press("enter")
        await _settle(app)
        await pilot.pause()
        await pilot.press("escape")
        await _settle(app)
        await pilot.pause()

        assert isinstance(app.screen, StackListScreen)
        assert gateway.calls == 2
```

Append to `tests/test_app.py` (add imports `from awst.screens.stack_detail import StackDetailScreen` and `from tests.test_stack_detail_screen import _detail`):

```python
@pytest.mark.asyncio
async def test_enter_twice_drills_from_home_into_stack_details() -> None:
    gateway = FakeCloudFormationGateway(stacks=[_stack("prod-api")], detail=_detail())
    app = AwstApp(cloudformation_gateway=gateway)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, StackDetailScreen)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_stack_list_screen.py tests/test_app.py -v`
Expected: the three new tests FAIL (Enter does nothing, so `app.screen` is still `StackListScreen` / the list never refetches); all existing tests PASS.

- [ ] **Step 3: Write the implementation**

In `src/awst/screens/stacks.py`:

Add the runtime import after the other `awst` imports:

```python
from awst.screens.stack_detail import StackDetailScreen, StackInspector
```

Add the combined protocol directly after `StackLister`:

```python
class StackGateway(StackLister, StackInspector, Protocol):
    """Everything the stack screens collectively need from CloudFormation."""
```

Change the constructor annotation:

```python
    def __init__(self: Self, gateway: StackGateway) -> None:
```

Add the two handlers after `on_input_changed`:

```python
    def on_data_table_row_selected(self: Self, event: DataTable.RowSelected) -> None:
        name = event.row_key.value
        if name is not None:
            self.app.push_screen(StackDetailScreen(self._gateway, name))

    def on_screen_resume(self: Self) -> None:
        if self._loaded:  # skip the initial push; on_mount already fetches
            self.action_refresh()
```

In `src/awst/app.py`, retype the gateway (the `TYPE_CHECKING` import changes from `StackLister` to `StackGateway`):

```python
if TYPE_CHECKING:
    from awst.screens.stacks import StackGateway
```

```python
    def __init__(self: Self, cloudformation_gateway: StackGateway | None = None) -> None:
```

```python
    @property
    def cloudformation_gateway(self: Self) -> StackGateway:
```

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `uv run --frozen pytest -v`
Expected: all PASS (including all pre-existing list-screen tests — the resume refresh must not break `test_refresh_does_not_steal_focus_from_filter` and friends).

- [ ] **Step 5: Lint and commit**

```bash
make format && make lint
git add src/awst/screens/stacks.py src/awst/app.py tests/test_stack_list_screen.py tests/test_app.py
git commit -m "Open stack details from the list and refresh on return"
```

---

### Task 8: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full local check**

Run: `make test`
Expected: ruff check, ruff format --check, ty check, and the whole pytest suite all pass.

- [ ] **Step 2: Check coverage stays above the floor**

Run: `make coverage`
Expected: PASS (coverage >= 75%).

- [ ] **Step 3: Fix anything that surfaced, then commit if changes were needed**

If lint/type/coverage surfaced fixes, apply them, re-run `make test`, and commit with message "Fix lint and coverage findings for stack details".
