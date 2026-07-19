# Region switcher — design

**Date**: 2026-07-19
**Status**: approved

## Goal

Switch the AWS region from anywhere in the app. After switching, the app returns
to the home screen and shows the new region in the header. The choice lasts for
the current process only, matching how profile selection works.

## Background

Gateways are built lazily on `AwstApp` from `boto3.Session()` and cached forever,
so the region is silently whatever the active profile or environment resolves.
There is no way to change it mid-session. Profile selection already works by
setting `AWS_PROFILE` process-wide before any gateway exists
(`awst.aws.profiles.select_profile`); the region switcher mirrors that mechanism
and adds the one missing piece: invalidating the cached gateways.

## Design

### AWS layer — new `src/awst/aws/regions.py`

Mirrors `profiles.py`:

- `active_region() -> str | None` — the region the default chain resolves right
  now (`boto3.Session().region_name`); respects env vars and the active
  profile's config.
- `available_regions() -> list[str]` — sorted region names from botocore's
  bundled endpoint data (`boto3.Session().get_available_regions("ec2")`). No
  network call, no credentials needed, works before login.
- `select_region(name: str) -> None` — sets `AWS_DEFAULT_REGION` process-wide.
  Session-only; nothing is persisted to disk.

### App wiring — `AwstApp`

- New `reset_gateways()` method sets the four cached gateway attributes back to
  `None`; the existing lazy properties rebuild them (picking up the new region)
  on next use.
- Global app-level binding **`ctrl+g` — "Region"**. A ctrl-key is required so
  the binding still fires while a filter `Input` has focus; a plain letter
  would be swallowed by typing.
- On selection: `select_region(name)` → `reset_gateways()` → pop all screens
  back to `HomeScreen` → update the header sub-title to `profile @ region`
  (e.g. `dev @ eu-central-1`; region alone when no profile is active).
- Open list screens hold references to the old-region gateways, which is why
  the app returns home rather than refreshing in place.

### Screen — new `src/awst/screens/regions.py`

`RegionSelectScreen(Screen[str | None])`, a near-clone of `ProfileSelectScreen`:

- Prompt text ("Select an AWS region") over an `OptionList` of all regions.
- The currently active region is preselected (cursor on it).
- Enter dismisses with the chosen region name; Escape dismisses with `None`
  (no change).

### Error handling

Nothing new. A disabled or invalid region surfaces as the existing `AwsError`
panel on list screens, recoverable with `r` (refresh) or another `ctrl+g`.

### Testing

- Unit tests for `regions.py`; env-var isolation already provided by
  `tests/conftest.py`.
- Pilot tests: `ctrl+g` opens the picker from home and from a list screen;
  selecting a region updates the sub-title and lands on home; Escape changes
  nothing.
- App test: after `reset_gateways()`, the next gateway property access builds a
  fresh gateway.

## Decisions made during brainstorming

- Direction: cross-cutting UX (filtering and refresh already exist in list
  screens).
- Feature: region switcher, over mid-session profile switching and a command
  palette.
- Availability: anywhere via a global binding; the app returns home after a
  switch rather than refreshing the current screen in place.
- Mechanism: env var + gateway reset, mirroring `select_profile`, over explicit
  region state on the app or an app-owned `boto3.Session` refactor (YAGNI).
