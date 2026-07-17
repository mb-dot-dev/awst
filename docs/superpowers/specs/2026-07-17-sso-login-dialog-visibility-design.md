# Fix: SSO login dialog renders nothing (device code invisible)

**Date:** 2026-07-17
**Status:** Approved

## Problem

When a list screen fails with a credentials error and the user presses `l`, the
SSO login modal (`SsoLoginScreen`) opens but paints nothing inside its border:
no title, no status, and — critically — no device code or verification URL. The
login flow itself works (the browser opens and approval succeeds), but a user
who needs the code (headless browser, SSH, or verifying the code matches the
AWS page) cannot see it.

## Root cause

`#dialog` is a `Vertical` styled `width: auto`. Its children are all `Static`
widgets, and `Static` has no width rule of its own, so each child gets the
widget default of `1fr`. In Textual 8.x, `1fr` inside an auto-width parent
resolves to 0, so every child renders at 0×0 and the dialog collapses to a
6×4 box of border and padding.

Reproduced headlessly: `#dialog` region is `width=6, height=4`; `#title`,
`#status`, `#code`, and `#url` are all `0×0`; an `export_screenshot()` contains
none of the dialog text.

`ConfirmScreen` uses the same dialog pattern but is unaffected: its `Button`
children have auto width, which props the dialog open.

The existing test (`test_shows_code_and_url_and_opens_browser`) passes because
it asserts `widget.content` — the widget's data — not rendered geometry.

## Fix

In `SsoLoginScreen.DEFAULT_CSS`, add one rule:

```css
#dialog Static { width: auto; }
```

Children then size to their content, the auto-width dialog grows around them
(capped by the existing `max-width: 80`, so the long verification URL wraps),
and the dialog keeps its compact hug-the-content look. Verified in a minimal
headless repro: after the fix, the device code appears in the exported
screenshot and all widget regions are nonzero.

No Python logic, gateway, or worker changes.

## Test changes

Extend `test_shows_code_and_url_and_opens_browser` in
`tests/test_sso_login_screen.py` with rendering assertions alongside the
existing content assertions:

- the `#code` widget's `region` has nonzero width and height;
- the user code (`ABCD-EFGH`) appears in `app.export_screenshot()` — proof it
  was actually painted.

## Out of scope

- `ConfirmScreen` shares the auto-width-dialog pattern but renders correctly
  today; explicitly left untouched (user decision).
