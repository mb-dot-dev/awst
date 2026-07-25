# Profile selector filter — design

**Date**: 2026-07-25
**Status**: approved

## Goal

Narrow the profile picker by typing. Users with many SSO profiles currently scroll
an unfiltered `OptionList`; typing a few characters should cut it to the matching
names, with Enter selecting one. The region picker (35+ entries) gets the same
behaviour from the same code.

## Background

`ProfileSelectScreen` (`src/awst/screens/profiles.py`) and `RegionSelectScreen`
(`src/awst/screens/regions.py`) are near-identical: a prompt `Static`, an
`OptionList` of names with `id=name`, a `Footer`, and a handler that dismisses
with the selected option's id. Neither can be filtered.

`ResourceListScreen` (`src/awst/screens/resource_list.py`) already establishes the
app's filter vocabulary: an `Input#filter` above the data widget, case-insensitive
substring matching (`query in name.lower()`), and a muted count line. This design
reuses that vocabulary, but drives it differently: list screens focus their table
first and reach the filter with `/`, whereas the pickers exist only to choose one
name, so the filter is focused from the start.

## Design

### New `src/awst/screens/filterable_select.py`

`FilterableSelectScreen[ResultT](Screen[ResultT])` — a generic base owning
everything about "a list of names narrowed by a filter".

- Class vars: `PROMPT` (e.g. `"Select an AWS profile"`) and `NOUN` (`"profile"` /
  `"region"`), used for the prompt text, the input placeholder, and the count line.
- `__init__(names: list[str])` stores the full name list.
- `compose` yields `Static#prompt`, `Input#filter`, `OptionList#options`, `Footer`
  — the same widget vocabulary as `ResourceListScreen`, so both filters look alike.
- `_render_options()` rebuilds the `OptionList` from
  `[n for n in names if query in n.lower()]`, keeping `id=name` so the existing
  `dismiss(event.option.id)` path is unchanged. Called on mount and from
  `on_input_changed`.
- `_cancel()` hook, a no-op by default, invoked when Escape is pressed with an
  empty filter.

Subclasses shrink to almost nothing: `ProfileSelectScreen` keeps its class vars
and its quit binding; `RegionSelectScreen` keeps its `current`-region initial
highlight and overrides `_cancel()` to `dismiss(None)`. Neither retains any
filtering logic.

`HomeScreen` is out of scope — four fixed entries need no filter, and including it
would drag `disabled` options into the base for no gain.

### Interaction

- **Focus**: the `Input` is focused on mount; typing filters live.
- **Navigation**: `Input` binds neither `up` nor `down`, so the screen binds both
  and forwards them to the `OptionList` cursor. Enter reaches the screen as
  `Input.Submitted` (the `Input` consumes the key), handled by selecting the
  highlighted option. Mouse clicks still go through the normal
  `OptionList.OptionSelected` path.
- **Highlight**: after each keystroke, keep the highlighted name if it survives the
  filter, otherwise highlight the first match — Enter always has a sensible target.
- **Escape**: clears a non-empty filter; with an empty filter it calls `_cancel()`
  (no-op for profiles, `dismiss(None)` for regions, preserving today's behaviour).
- **Quit**: `q` can no longer quit the profile picker — it types into the filter.
  The binding moves to `ctrl+q`, Textual's own quit key, shown in the footer.
- **Status**: the prompt line doubles as status — `Select an AWS profile` when
  unfiltered, `2 of 7 profiles` while filtering, `no profiles match` at zero
  matches. Enter does nothing when nothing is highlighted.
- **Region initial highlight**: `RegionSelectScreen` still highlights the active
  region on mount, re-applied through the shared highlight logic; it drops away
  once a filter narrows that region out.

### Matching

Case-insensitive substring, identical to `ResourceListScreen`. Fuzzy subsequence
matching was rejected: it would be inconsistent with the list screens and noisier
on short queries.

### Error handling

Nothing new. Both pickers operate on in-memory name lists — no AWS calls, no
failure modes beyond the empty-match state described above.

## Behaviour changes

- `q` no longer quits the profile picker; `ctrl+q` does.
- Escape on the region picker cancels only when the filter is empty; otherwise it
  clears the filter.

## Testing

No dedicated test file for the base — the shared behaviour is exercised through
both concrete screens, where the real wiring lives.

`tests/test_profile_select_screen.py`:

- typing narrows the option list to matching profiles
- Enter selects the highlighted match after filtering, sets `AWS_PROFILE`, opens home
- Escape clears the filter and restores every option
- zero matches: no options, prompt reads `no profiles match`, Enter is a no-op
- `test_q_quits_from_picker` rewritten to press `ctrl+q`

`tests/test_region_select_screen.py`:

- typing narrows; Enter selects the filtered match
- Escape with a non-empty filter clears instead of cancelling; a second Escape
  cancels with `None`
- existing mount-highlight and cancel tests stay green

Verification: `make test` (lint + full suite) before the branch is done.

## Decisions made during brainstorming

- Interaction: type-to-filter with the input focused on mount, over `/`-to-focus
  (list-screen parity) and a hidden input revealed by `/` — the pickers exist only
  to choose a name, so typing should work immediately.
- Scope: both pickers via a shared base, over a profile-only inline
  implementation — regions benefit most from filtering, and one implementation
  beats two near-identical ones.
- Matching: case-insensitive substring, over fuzzy subsequence.
