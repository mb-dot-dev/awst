# Profile Selector Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users narrow the AWS profile picker and region picker by typing, selecting a match with Enter.

**Architecture:** A new generic base screen, `FilterableSelectScreen[ResultT]`, owns a prompt line, an always-focused filter `Input`, and an `OptionList` rebuilt on every keystroke by case-insensitive substring match. `ProfileSelectScreen` and `RegionSelectScreen` subclass it, keeping only their prompt text, key bindings, and how they dismiss.

**Tech Stack:** Python 3.14, Textual 8.2.8, pytest + pytest-asyncio with Textual's `run_test()` pilot, `uv` for dependency management, `ruff` + `ty` for lint/typecheck.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-profile-selector-filter-design.md`.
- Line length 120; ruff runs a broad rule set (flake8-annotations, bandit, bugbear, complexity, pathlib). Every method annotates `self` as `Self` and its return type, matching existing screens.
- `BINDINGS` is always annotated `ClassVar[list[BindingType]]`, with `BindingType` imported under `if TYPE_CHECKING`.
- Screens never import boto3/botocore.
- Matching is case-insensitive substring: `query in name.lower()`, identical to `src/awst/screens/resource_list.py:195`.
- Tests assert `Static` text with `str(widget.content)`, the existing convention (see `tests/test_object_list_screen.py:147`).
- `make lint` and `make unit` must pass before each commit; the final task runs `make test`.
- Work happens on the existing branch `feature/filterable-select`.

---

### Task 1: Filterable base screen + profile picker

**Files:**
- Create: `src/awst/screens/filterable_select.py`
- Modify: `src/awst/screens/profiles.py` (rewrite; currently 38 lines)
- Test: `tests/test_profile_select_screen.py`

**Interfaces:**
- Consumes: `AwstApp(cloudformation_gateway=...)` from `src/awst/app.py`, which pushes `ProfileSelectScreen(names)` and dismisses into `_on_profile_selected(name: str | None)`.
- Produces: `FilterableSelectScreen[ResultT]` in `src/awst/screens/filterable_select.py` with class vars `PROMPT: ClassVar[str]` and `NOUN: ClassVar[str]`, `__init__(self, names: list[str])`, instance attribute `self._preferred: str | None` (name to highlight when nothing is highlighted yet), overridable `_selected(self, name: str) -> None` and `_cancel(self) -> None`, and action `action_clear_or_cancel`. Widget ids: `#prompt`, `#filter`, `#options`. Task 2 subclasses this same base.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_profile_select_screen.py` (the file already has `_write_config()`, `_CONFIG` with profiles `dev` and `prod`, and imports `os`, `Path`, `pytest`, `OptionList`, `AwstApp`, `HomeScreen`, `ProfileSelectScreen`, `FakeCloudFormationGateway`). Add `Input, Static` to the existing `from textual.widgets import OptionList` line so it reads `from textual.widgets import Input, OptionList, Static`:

```python
@pytest.mark.asyncio
async def test_typing_narrows_the_profiles() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "r", "o")
        await pilot.pause()

        options = app.screen.query_one(OptionList)
        assert options.option_count == 1
        assert options.get_option_at_index(0).id == "prod"
        assert str(app.screen.query_one("#prompt", Static).content) == "1 of 2 profiles"


@pytest.mark.asyncio
async def test_enter_selects_the_filtered_profile() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "r", "o")
        await pilot.press("enter")
        await pilot.pause()

        assert os.environ["AWS_PROFILE"] == "prod"
        assert isinstance(app.screen, HomeScreen)


@pytest.mark.asyncio
async def test_escape_clears_the_filter() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p", "r", "o")
        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, ProfileSelectScreen)
        assert app.screen.query_one("#filter", Input).value == ""
        assert app.screen.query_one(OptionList).option_count == 2
        assert str(app.screen.query_one("#prompt", Static).content) == "Select an AWS profile"


@pytest.mark.asyncio
async def test_no_matches_reports_it_and_enter_does_nothing() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("z", "z", "z")
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ProfileSelectScreen)
        assert app.screen.query_one(OptionList).option_count == 0
        assert str(app.screen.query_one("#prompt", Static).content) == "no profiles match"
```

Then replace the existing `test_q_quits_from_picker` (the last test in the file) entirely with:

```python
@pytest.mark.asyncio
async def test_ctrl_q_quits_from_picker() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()

    assert app.return_code == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_profile_select_screen.py -v`
Expected: the four new tests FAIL (no `#prompt` update, no `#filter` widget — `NoMatches` on `query_one`, and typing `p` does not filter). `test_ctrl_q_quits_from_picker` may pass already via Textual's default quit binding; the other existing tests must still pass.

- [ ] **Step 3: Create the base screen**

Create `src/awst/screens/filterable_select.py`:

```python
"""Shared base for pickers that narrow a list of names with a filter input."""

from typing import TYPE_CHECKING, ClassVar, Self

from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType


class FilterableSelectScreen[ResultT](Screen[ResultT]):
    """An option list of names narrowed by an always-focused filter input.

    Subclasses set PROMPT and NOUN and implement _selected. Escape and quit
    bindings belong to the subclass, so each picker labels them for itself;
    both point at action_clear_or_cancel.
    """

    TITLE = "awst"

    PROMPT: ClassVar[str]
    NOUN: ClassVar[str]

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    DEFAULT_CSS = """
    #prompt { padding: 1 2 0 2; color: $text-muted; }
    #filter { margin: 1 2 0 2; }
    #options { margin: 0 2 1 2; }
    """

    def __init__(self: Self, names: list[str]) -> None:
        super().__init__()
        self._names = names
        self._preferred: str | None = None

    def _selected(self: Self, name: str) -> None:
        """Dismiss with the chosen name; subclasses type the result."""
        raise NotImplementedError

    def _cancel(self: Self) -> None:
        """Escape with an empty filter; a no-op unless the subclass can cancel."""

    def compose(self: Self) -> ComposeResult:
        yield Static(self.PROMPT, id="prompt")
        yield Input(placeholder=f"filter {self.NOUN}s by name", id="filter")
        yield OptionList(id="options")
        yield Footer()

    def on_mount(self: Self) -> None:
        self._render_options()
        self.query_one("#filter", Input).focus()

    def _render_options(self: Self) -> None:
        query = self.query_one("#filter", Input).value.strip().lower()
        visible = [name for name in self._names if query in name.lower()]
        options = self.query_one("#options", OptionList)
        wanted = self._highlighted_name(options) or self._preferred
        options.clear_options()
        options.add_options([Option(name, id=name) for name in visible])
        if visible:
            options.highlighted = visible.index(wanted) if wanted in visible else 0
        self._update_prompt(len(visible), filtering=bool(query))

    def _highlighted_name(self: Self, options: OptionList) -> str | None:
        highlighted = options.highlighted_option
        return None if highlighted is None else highlighted.id

    def _update_prompt(self: Self, shown: int, *, filtering: bool) -> None:
        total = len(self._names)
        if not filtering:
            text = self.PROMPT
        elif shown == 0:
            text = f"no {self.NOUN}s match"
        else:
            noun = self.NOUN if total == 1 else f"{self.NOUN}s"
            text = f"{shown} of {total} {noun}"
        self.query_one("#prompt", Static).update(text)

    def on_input_changed(self: Self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._render_options()

    def on_input_submitted(self: Self, event: Input.Submitted) -> None:
        if event.input.id == "filter":
            # No-op when nothing is highlighted, which is exactly the zero-match case.
            self.query_one("#options", OptionList).action_select()

    def on_option_list_option_selected(self: Self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self._selected(event.option.id)

    def action_cursor_up(self: Self) -> None:
        self.query_one("#options", OptionList).action_cursor_up()

    def action_cursor_down(self: Self) -> None:
        self.query_one("#options", OptionList).action_cursor_down()

    def action_clear_or_cancel(self: Self) -> None:
        filter_input = self.query_one("#filter", Input)
        if filter_input.value:
            filter_input.value = ""  # fires Input.Changed, which re-renders the options
        else:
            self._cancel()
```

- [ ] **Step 4: Rewrite the profile picker on top of the base**

Replace the whole contents of `src/awst/screens/profiles.py` with:

```python
"""Profile selection screen, shown at startup when no AWS profile is active."""

from typing import TYPE_CHECKING, ClassVar, Self

from awst.screens.filterable_select import FilterableSelectScreen

if TYPE_CHECKING:
    from textual.binding import BindingType


class ProfileSelectScreen(FilterableSelectScreen[str]):
    """Pick the AWS profile the whole app will use; dismisses with its name."""

    PROMPT = "Select an AWS profile"
    NOUN = "profile"

    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "clear_or_cancel", "Clear"),
        ("ctrl+q", "app.quit", "Quit"),
    ]

    def _selected(self: Self, name: str) -> None:
        self.dismiss(name)
```

Note: the base's `__init__(names)` matches the existing call site `ProfileSelectScreen(names)` in `src/awst/app.py:118`, so the app needs no change.

- [ ] **Step 5: Run the profile tests to verify they pass**

Run: `uv run --frozen pytest tests/test_profile_select_screen.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 6: Run the full suite and lint**

Run: `make unit && make lint`
Expected: everything passes. If `ty` complains that `_selected`'s `dismiss(name)` is untyped in the base, confirm the base's `_selected` still just raises `NotImplementedError` — only subclasses call `dismiss`.

- [ ] **Step 7: Commit**

```bash
git add src/awst/screens/filterable_select.py src/awst/screens/profiles.py tests/test_profile_select_screen.py
git commit -m "Filter the profile picker by typing"
```

---

### Task 2: Region picker on the shared base

**Files:**
- Modify: `src/awst/screens/regions.py` (rewrite; currently 45 lines)
- Test: `tests/test_region_select_screen.py`

**Interfaces:**
- Consumes: `FilterableSelectScreen[ResultT]` from Task 1 — `__init__(names: list[str])`, `self._preferred: str | None`, `_selected(name: str) -> None`, `_cancel() -> None`, `action_clear_or_cancel`, ids `#prompt`/`#filter`/`#options`.
- Produces: nothing new. `RegionSelectScreen(region_names: list[str], current: str | None)` keeps its existing constructor signature, used by `src/awst/app.py:140`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_region_select_screen.py` (it already defines `RegionApp` and `_REGIONS = ["eu-central-1", "eu-west-1", "us-east-1"]`). Change its widget import to `from textual.widgets import Input, OptionList, Static`:

```python
@pytest.mark.asyncio
async def test_typing_narrows_the_regions() -> None:
    app = RegionApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u", "s")
        await pilot.pause()

        options = app.screen.query_one(OptionList)
        assert options.option_count == 1
        assert options.get_option_at_index(0).id == "us-east-1"
        assert str(app.screen.query_one("#prompt", Static).content) == "1 of 3 regions"


@pytest.mark.asyncio
async def test_enter_dismisses_with_the_filtered_region() -> None:
    app = RegionApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u", "s")
        await pilot.press("enter")
        await pilot.pause()

        assert app.answers == ["us-east-1"]


@pytest.mark.asyncio
async def test_escape_clears_the_filter_before_cancelling() -> None:
    app = RegionApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u", "s")
        await pilot.press("escape")
        await pilot.pause()

        assert app.answers == []
        assert app.screen.query_one("#filter", Input).value == ""
        assert app.screen.query_one(OptionList).option_count == 3

        await pilot.press("escape")
        await pilot.pause()

        assert app.answers == [None]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --frozen pytest tests/test_region_select_screen.py -v`
Expected: the three new tests FAIL (no `#filter` widget, so `query_one` raises `NoMatches`; typing does not narrow). The five existing tests still pass.

- [ ] **Step 3: Rewrite the region picker on top of the base**

Replace the whole contents of `src/awst/screens/regions.py` with:

```python
"""Region selection screen, opened from anywhere with ctrl+g."""

from typing import TYPE_CHECKING, ClassVar, Self

from awst.screens.filterable_select import FilterableSelectScreen

if TYPE_CHECKING:
    from textual.binding import BindingType


class RegionSelectScreen(FilterableSelectScreen[str | None]):
    """Pick the AWS region the whole app will use; dismisses with its name, or None to cancel."""

    PROMPT = "Select an AWS region"
    NOUN = "region"

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "clear_or_cancel", "Back")]

    def __init__(self: Self, region_names: list[str], current: str | None) -> None:
        super().__init__(region_names)
        self._preferred = current

    def _selected(self: Self, name: str) -> None:
        self.dismiss(name)

    def _cancel(self: Self) -> None:
        self.dismiss(None)
```

- [ ] **Step 4: Run the region tests to verify they pass**

Run: `uv run --frozen pytest tests/test_region_select_screen.py -v`
Expected: all 8 tests PASS, including the pre-existing `test_current_region_is_preselected` (highlight index 1 via `_preferred`) and `test_unknown_current_region_defaults_to_the_top` (index 0).

- [ ] **Step 5: Run the full suite and lint**

Run: `make unit && make lint`
Expected: everything passes. `tests/test_app.py` exercises `ctrl+g` region switching — if a test there presses keys expecting the option list to hold focus, update it to the typing model rather than changing the screen.

- [ ] **Step 6: Commit**

```bash
git add src/awst/screens/regions.py tests/test_region_select_screen.py
git commit -m "Filter the region picker by typing"
```

---

### Task 3: Documentation and full verification

**Files:**
- Modify: `CLAUDE.md` (project overview paragraph, and the `src/awst/screens/` bullet under "Architecture")

**Interfaces:**
- Consumes: the finished behaviour from Tasks 1 and 2.
- Produces: nothing code-facing.

- [ ] **Step 1: Update the project overview in `CLAUDE.md`**

In the "Project overview" section, the sentence beginning "At startup the app shows a profile-selector screen…" — extend it so it reads:

> At startup the app shows a profile-selector screen when no AWS profile is active and profiles exist in `~/.aws/config`; otherwise (or once a profile is chosen) it opens on the service-menu home screen, with the active profile shown in the header. Both the profile and region pickers filter as you type: the filter input is focused on entry, up/down moves the highlight, Enter selects, escape clears the filter (and, on the region picker, cancels when the filter is already empty), and `ctrl+q` quits the profile picker.

- [ ] **Step 2: Update the screens bullet in `CLAUDE.md`**

In the "Architecture" section, the bullet starting "`src/awst/screens/` holds one Textual `Screen` per page" lists the screen modules. Add `filterable_select.py` to that list by changing the parenthetical entry for the pickers to:

> `profiles.py` for the profile picker and `regions.py` for the region picker (both subclassing `FilterableSelectScreen` in `filterable_select.py`, which supplies the type-to-filter option list)

- [ ] **Step 3: Run the full local check**

Run: `make test`
Expected: `ruff check`, `ruff format --check`, `ty check`, and the whole pytest suite all pass. Paste the tail of the output into the task report — no success claim without it.

- [ ] **Step 4: Check coverage did not regress**

Run: `make coverage`
Expected: PASS (the gate fails under 75%).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "Document type-to-filter pickers"
```

---

## Notes for the implementer

- Textual details already verified against the installed 8.2.8, so do not redesign around them: `Input` binds `enter` → `submit` (hence `on_input_submitted`) and binds neither `up` nor `down` (hence the screen-level bindings that forward to the `OptionList`). `OptionList.action_select()` returns without posting anything when `highlighted is None`.
- `Input.value = ""` fires `Input.Changed`, so `action_clear_or_cancel` does not need to call `_render_options()` itself.
- Do not add a filter to `HomeScreen`: four fixed entries, and it would pull `disabled` options into the base for no gain.
- There is deliberately no `tests/test_filterable_select.py`; the base is covered through both concrete pickers.
