# Region Switcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the AWS region from anywhere in the app via `ctrl+g`; the app returns to the home screen with the new region shown in the header as `profile @ region`.

**Architecture:** Mirrors the existing profile mechanism: a new `awst.aws.regions` module sets `AWS_DEFAULT_REGION` process-wide, `AwstApp.reset_gateways()` drops the cached gateways so the lazy properties rebuild them with the new region, and a new `RegionSelectScreen` (a near-clone of `ProfileSelectScreen`) is pushed by a global app binding. Spec: `docs/superpowers/specs/2026-07-19-region-switcher-design.md`.

**Tech Stack:** Python 3.14, Textual, boto3/botocore (bundled endpoint data only — no network), pytest + pytest-asyncio with Textual's `run_test()` pilot.

## Global Constraints

- Python >= 3.14; run everything through `uv` (`uv run --frozen pytest …`, `make lint`, `make test`).
- Screens never import boto3/botocore; all AWS access lives in `src/awst/aws/`.
- No per-item AWS API calls; `available_regions()` must come from botocore's bundled endpoint data, not a network call.
- 120-char line length; every function annotated (flake8-annotations is enforced); methods take `self: Self`; runtime-only imports go under `if TYPE_CHECKING:`.
- The region choice is process-wide and session-only — nothing is persisted to disk.
- Commit messages follow repo style: imperative, no conventional-commit prefix (e.g. "Add region switcher"), and end with:

  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0142EKLHqkrwzy2WYhW1uK5u
  ```

---

### Task 1: Region helpers in the AWS layer

**Files:**
- Create: `src/awst/aws/regions.py`
- Modify: `tests/conftest.py` (scrub `AWS_REGION` so region resolution is deterministic)
- Test: `tests/test_regions.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces (Tasks 2 and 3 rely on these exact signatures):
  - `awst.aws.regions.active_region() -> str | None`
  - `awst.aws.regions.available_regions() -> list[str]` (sorted)
  - `awst.aws.regions.select_region(name: str) -> None`

- [ ] **Step 1: Scrub `AWS_REGION` in the shared fixture**

The scrub guards against a shell-exported `AWS_REGION` mattering to other tools and any future botocore behavior; if a developer's shell exports it, region tests should not depend on it being ignored. In `tests/conftest.py`, add one line directly after the existing `monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")` line:

```python
    monkeypatch.delenv("AWS_REGION", raising=False)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_regions.py`:

```python
"""Tests for AWS region discovery and selection."""

import os

import pytest

from awst.aws import regions


def test_active_region_reads_the_environment() -> None:
    # conftest sets AWS_DEFAULT_REGION=eu-west-1 for every test
    assert regions.active_region() == "eu-west-1"


def test_active_region_is_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_DEFAULT_REGION")

    assert regions.active_region() is None


def test_available_regions_are_sorted_and_include_the_majors() -> None:
    names = regions.available_regions()

    assert names == sorted(names)
    assert "eu-west-1" in names
    assert "us-east-1" in names


def test_select_region_sets_the_environment() -> None:
    regions.select_region("ap-southeast-2")

    assert os.environ["AWS_DEFAULT_REGION"] == "ap-southeast-2"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_regions.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'awst.aws.regions'`

- [ ] **Step 4: Implement the module**

Create `src/awst/aws/regions.py`:

```python
"""AWS region discovery and selection."""

import os

import boto3


def active_region() -> str | None:
    """The region the default credential chain resolves right now, or None when unset."""
    return boto3.Session().region_name


def available_regions() -> list[str]:
    """Every standard-partition region, from botocore's bundled endpoint data (no network)."""
    return sorted(boto3.Session().get_available_regions("ec2"))


def select_region(name: str) -> None:
    """Make name the process-wide region; gateways rebuilt after reset_gateways pick it up."""
    os.environ["AWS_DEFAULT_REGION"] = name
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_regions.py -v`
Expected: 4 passed

- [ ] **Step 6: Lint**

Run: `make lint`
Expected: ruff check, ruff format --check, and ty check all pass

- [ ] **Step 7: Commit**

```bash
git add src/awst/aws/regions.py tests/test_regions.py tests/conftest.py
git commit -m "Add AWS region helpers

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0142EKLHqkrwzy2WYhW1uK5u"
```

---

### Task 2: RegionSelectScreen

**Files:**
- Create: `src/awst/screens/regions.py`
- Test: `tests/test_region_select_screen.py`

**Interfaces:**
- Consumes: nothing from Task 1 (the screen takes plain data; the app wires them together in Task 3).
- Produces (Task 3 relies on this exact signature):
  - `RegionSelectScreen(region_names: list[str], current: str | None)` — a `Screen[str | None]` that dismisses with the chosen region name on Enter, or `None` on Escape. The `current` region is preselected when present in `region_names`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_region_select_screen.py` (harness-app pattern, as in `tests/test_confirm_screen.py`):

```python
"""Tests for the region selection screen."""

from typing import Self

import pytest
from textual.app import App
from textual.widgets import OptionList

from awst.screens.regions import RegionSelectScreen

_REGIONS = ["eu-central-1", "eu-west-1", "us-east-1"]


class RegionApp(App[None]):
    """Harness that opens the region picker and records the answer."""

    def __init__(self: Self, current: str | None = "eu-west-1") -> None:
        super().__init__()
        self._current = current
        self.answers: list[str | None] = []

    def on_mount(self: Self) -> None:
        self.push_screen(RegionSelectScreen(list(_REGIONS), self._current), self.answers.append)


@pytest.mark.asyncio
async def test_lists_all_regions() -> None:
    app = RegionApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        options = app.screen.query_one(OptionList)

        assert options.option_count == 3
        assert options.get_option("eu-central-1") is not None
        assert options.get_option("us-east-1") is not None


@pytest.mark.asyncio
async def test_current_region_is_preselected() -> None:
    app = RegionApp(current="eu-west-1")

    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.screen.query_one(OptionList).highlighted == 1


@pytest.mark.asyncio
async def test_unknown_current_region_defaults_to_the_top() -> None:
    app = RegionApp(current=None)

    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.screen.query_one(OptionList).highlighted == 0


@pytest.mark.asyncio
async def test_enter_dismisses_with_the_highlighted_region() -> None:
    app = RegionApp(current="eu-west-1")

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.answers == ["eu-west-1"]


@pytest.mark.asyncio
async def test_escape_dismisses_with_none() -> None:
    app = RegionApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.answers == [None]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_region_select_screen.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'awst.screens.regions'`

- [ ] **Step 3: Implement the screen**

Create `src/awst/screens/regions.py` (mirrors `src/awst/screens/profiles.py`):

```python
"""Region selection screen, opened from anywhere with ctrl+g."""

from typing import TYPE_CHECKING, ClassVar, Self

from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType


class RegionSelectScreen(Screen[str | None]):
    """Pick the AWS region the whole app will use; dismisses with its name, or None to cancel."""

    TITLE = "awst"

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Back")]

    DEFAULT_CSS = """
    #prompt { padding: 1 2 0 2; color: $text-muted; }
    #regions { margin: 1 2; }
    """

    def __init__(self: Self, region_names: list[str], current: str | None) -> None:
        super().__init__()
        self._region_names = region_names
        self._current = current

    def compose(self: Self) -> ComposeResult:
        yield Static("Select an AWS region", id="prompt")
        yield OptionList(*[Option(name, id=name) for name in self._region_names], id="regions")
        yield Footer()

    def on_mount(self: Self) -> None:
        if self._current in self._region_names:
            self.query_one("#regions", OptionList).highlighted = self._region_names.index(self._current)

    def on_option_list_option_selected(self: Self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self: Self) -> None:
        self.dismiss(None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --frozen pytest tests/test_region_select_screen.py -v`
Expected: 5 passed

- [ ] **Step 5: Lint**

Run: `make lint`
Expected: all checks pass

- [ ] **Step 6: Commit**

```bash
git add src/awst/screens/regions.py tests/test_region_select_screen.py
git commit -m "Add region selection screen

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0142EKLHqkrwzy2WYhW1uK5u"
```

---

### Task 3: App wiring — global binding, gateway reset, header

**Files:**
- Modify: `src/awst/app.py`
- Modify: `tests/test_profile_select_screen.py:43-78` (sub-title assertions change)
- Test: `tests/test_app.py` (new tests appended)

**Interfaces:**
- Consumes: `awst.aws.regions.active_region() -> str | None`, `available_regions() -> list[str]`, `select_region(name: str) -> None` (Task 1); `RegionSelectScreen(region_names: list[str], current: str | None)`, a `Screen[str | None]` (Task 2).
- Produces: `AwstApp.reset_gateways() -> None`; app binding `ctrl+g` → `action_switch_region`; header sub-title format `profile @ region` (region alone when no profile is active).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py` (add `import os`, `from awst.aws import regions`, and `from awst.screens.regions import RegionSelectScreen` to the imports at the top; `os` goes in a separate import block per isort ordering — ruff will tell you if the order is wrong):

```python
@pytest.mark.asyncio
async def test_ctrl_g_opens_the_region_picker_from_home() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+g")
        await pilot.pause()

        assert isinstance(app.screen, RegionSelectScreen)


@pytest.mark.asyncio
async def test_switching_region_from_a_list_screen_returns_home() -> None:
    gateway = FakeCloudFormationGateway(stacks=[make_stack("prod-api")])
    app = AwstApp(cloudformation_gateway=gateway)
    names = regions.available_regions()
    target = names[names.index("eu-west-1") + 1]  # the region one below the preselected one

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, StackListScreen)

        await pilot.press("ctrl+g")
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, HomeScreen)
        assert os.environ["AWS_DEFAULT_REGION"] == target
        assert app.sub_title == target


@pytest.mark.asyncio
async def test_escape_closes_the_region_picker_without_change() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+g")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, HomeScreen)
        assert os.environ["AWS_DEFAULT_REGION"] == "eu-west-1"


@pytest.mark.asyncio
async def test_region_picker_is_unavailable_on_the_startup_profile_picker() -> None:
    Path(os.environ["AWS_CONFIG_FILE"]).write_text("[profile dev]\nregion = eu-west-1\n")
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+g")
        await pilot.pause()

        assert isinstance(app.screen, ProfileSelectScreen)


def test_reset_gateways_rebuilds_on_next_access() -> None:
    app = AwstApp()

    first = app.s3_gateway
    app.reset_gateways()

    assert app.s3_gateway is not first
```

This also needs `from pathlib import Path` and `from awst.screens.profiles import ProfileSelectScreen` in the imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_app.py -v`
Expected: the 5 new tests FAIL (`ctrl+g` does nothing → wrong screen type; `AwstApp` has no attribute `reset_gateways`); the 8 existing tests still pass.

- [ ] **Step 3: Implement the app wiring**

In `src/awst/app.py`:

1. Change the profiles import and add the regions import and screen import:

```python
from awst.aws import profiles, regions
```

```python
from awst.screens.regions import RegionSelectScreen
```

2. Add `ClassVar` to the `typing` import, and add `BindingType` to the `TYPE_CHECKING` block:

```python
from typing import TYPE_CHECKING, ClassVar, Self
```

```python
    from textual.binding import BindingType
```

3. Add the class-level binding on `AwstApp` (directly under the docstring):

```python
    BINDINGS: ClassVar[list[BindingType]] = [("ctrl+g", "switch_region", "Region")]
```

4. Add `reset_gateways` after the gateway properties:

```python
    def reset_gateways(self: Self) -> None:
        """Drop the cached gateways so the next use rebuilds them from the current environment."""
        self._cloudformation_gateway = None
        self._s3_gateway = None
        self._lambda_gateway = None
        self._sqs_gateway = None
```

5. Replace `on_mount` and `_on_profile_selected` with versions that use a shared sub-title helper:

```python
    def on_mount(self: Self) -> None:
        self._refresh_sub_title()
        if profiles.active_profile() is not None:
            self.push_screen(HomeScreen())
            return
        names = profiles.available_profiles()
        if names:
            self.push_screen(ProfileSelectScreen(names), self._on_profile_selected)
        else:
            self.push_screen(HomeScreen())

    def _on_profile_selected(self: Self, name: str | None) -> None:
        if name is not None:
            profiles.select_profile(name)
            self._refresh_sub_title()
        self.push_screen(HomeScreen())

    def _refresh_sub_title(self: Self) -> None:
        parts = [part for part in (profiles.active_profile(), regions.active_region()) if part]
        self.sub_title = " @ ".join(parts)
```

6. Add the region-switch action, its guard, and the selection callback:

```python
    def check_action(self: Self, action: str, parameters: tuple[object, ...]) -> bool | None:  # noqa: ARG002
        if action == "switch_region":
            return any(isinstance(screen, HomeScreen) for screen in self.screen_stack)
        return True

    def action_switch_region(self: Self) -> None:
        if isinstance(self.screen, RegionSelectScreen):
            return
        picker = RegionSelectScreen(regions.available_regions(), regions.active_region())
        self.push_screen(picker, self._on_region_selected)

    def _on_region_selected(self: Self, name: str | None) -> None:
        if name is None:
            return
        regions.select_region(name)
        self.reset_gateways()
        while not isinstance(self.screen, HomeScreen):
            self.pop_screen()
        self._refresh_sub_title()
```

The `check_action` guard keeps `ctrl+g` inert (and out of the footer) on the startup profile picker, where no `HomeScreen` exists yet — `_on_region_selected` pops back to `HomeScreen`, so one must be in the stack.

- [ ] **Step 4: Update the sub-title assertions in the existing tests**

The header now always includes the region, so three tests in `tests/test_profile_select_screen.py` change:

In `test_selecting_a_profile_sets_it_and_opens_home` (line ~54):

```python
        assert app.sub_title == "dev @ eu-west-1"
```

In `test_picker_skipped_when_profile_env_is_set` (line ~67):

```python
        assert app.sub_title == "prod @ eu-west-1"
```

In `test_picker_skipped_when_no_profiles_exist`, add an assertion after the screen check (line ~77):

```python
        assert app.sub_title == "eu-west-1"
```

(`eu-west-1` is the `AWS_DEFAULT_REGION` that `tests/conftest.py` sets for every test.)

- [ ] **Step 5: Run the affected test files to verify they pass**

Run: `uv run --frozen pytest tests/test_app.py tests/test_profile_select_screen.py -v`
Expected: all pass (13 in test_app.py, 5 in test_profile_select_screen.py)

- [ ] **Step 6: Run the full check**

Run: `make test`
Expected: lint clean, all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/awst/app.py tests/test_app.py tests/test_profile_select_screen.py
git commit -m "Add global region switcher on ctrl+g

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0142EKLHqkrwzy2WYhW1uK5u"
```

---

### Task 4: Documentation

**Files:**
- Modify: `CLAUDE.md` (project-overview paragraph)

**Interfaces:**
- Consumes: the finished feature (Tasks 1–3).
- Produces: nothing for other tasks.

- [ ] **Step 1: Update the project overview**

In `CLAUDE.md`, extend the project-overview paragraph: after the sentence describing the profile selector/header, add:

```
`ctrl+g` opens a region picker from any screen; switching region resets the cached gateways and returns to the home screen, with the header showing `profile @ region`.
```

In the architecture bullet for `src/awst/aws/`, change `` `profiles.py` (profile discovery/selection + SSO config resolution) `` to:

```
`profiles.py` (profile discovery/selection + SSO config resolution), `regions.py` (region discovery/selection)
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "Document the region switcher

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0142EKLHqkrwzy2WYhW1uK5u"
```
