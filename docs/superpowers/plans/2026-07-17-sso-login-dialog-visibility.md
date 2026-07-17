# SSO Login Dialog Visibility Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the SSO login modal actually render its content (title, status, device code, verification URL), which today collapses to an empty 6×4 border box.

**Architecture:** One CSS rule in `SsoLoginScreen.DEFAULT_CSS` (`#dialog Static { width: auto; }`) so the all-`Static` children size to content instead of resolving `1fr` to 0 inside the auto-width dialog. The existing screen test gains rendering assertions (widget region size + exported screenshot content) so a layout collapse can never again pass on `.content` checks alone.

**Tech Stack:** Python 3.14, Textual 8.2.8, pytest + pytest-asyncio with Textual's `run_test()` pilot, `uv` via `make` targets.

## Global Constraints

- All commands run through `uv`; prefer `make` targets (`make lint`, `make unit`, `make test`).
- Single tests run as: `uv run --frozen pytest <nodeid>`.
- Ruff line length is 120; `tests/**/*.py` may use `assert`.
- No Python logic, gateway, or worker changes — CSS and test changes only (spec: "No Python logic, gateway, or worker changes").
- `ConfirmScreen` is explicitly out of scope; do not touch `src/awst/screens/confirm.py`.

---

### Task 1: Render the SSO dialog content and pin it with rendering assertions

**Files:**
- Modify: `src/awst/screens/sso_login.py:50-63` (the `DEFAULT_CSS` block)
- Test: `tests/test_sso_login_screen.py` (extend `test_shows_code_and_url_and_opens_browser`)

**Interfaces:**
- Consumes: `SsoLoginScreen` (existing), `FakeSsoLoginGateway` / `make_device_authorization` from `tests/fakes.py` (existing; the fake's user code is `ABCD-EFGH`), Textual's `Widget.region` and `App.export_screenshot()`.
- Produces: nothing new — no signatures change.

- [ ] **Step 1: Extend the existing test with rendering assertions (failing first)**

In `tests/test_sso_login_screen.py`, replace `test_shows_code_and_url_and_opens_browser` with:

```python
@pytest.mark.asyncio
async def test_shows_code_and_url_and_opens_browser(opened_urls: list[str]) -> None:
    authorization = make_device_authorization(interval=60)  # long interval: modal stays up
    gateway = FakeSsoLoginGateway(authorization=authorization, pending_polls=10**6)
    app = SsoModalApp(gateway)

    async with app.run_test() as pilot:
        await _until_code_shown(app, pilot)

        assert "ABCD-EFGH" in str(app.screen.query_one("#code", Static).content)
        assert str(app.screen.query_one("#url", Static).content) == authorization.verification_uri_complete
        assert opened_urls == [authorization.verification_uri_complete]

        # The content being set is not enough: a zero-size widget renders nothing.
        await pilot.pause()
        code_widget = app.screen.query_one("#code", Static)
        assert code_widget.region.width > 0
        assert code_widget.region.height > 0
        assert "ABCD-EFGH" in app.export_screenshot()
```

Only the last five lines (from the comment down) are new; everything above them is the current test unchanged. `app.export_screenshot()` returns the screen as SVG text and must be called while the app is still running, i.e. inside the `async with` block.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --frozen pytest tests/test_sso_login_screen.py::test_shows_code_and_url_and_opens_browser -v`

Expected: FAIL on `assert code_widget.region.width > 0` (the region is 0×0 because the dialog collapses).

- [ ] **Step 3: Add the CSS rule**

In `src/awst/screens/sso_login.py`, change the `DEFAULT_CSS` block to add one line after the `#dialog { ... }` rule:

```python
    DEFAULT_CSS = """
    SsoLoginScreen { align: center middle; }
    #dialog {
        width: auto;
        max-width: 80;
        height: auto;
        padding: 1 2;
        background: $panel;
        border: thick $primary;
    }
    #dialog Static { width: auto; }
    #title { text-style: bold; }
    #status { color: $text-muted; margin-top: 1; }
    #code { text-style: bold; margin-top: 1; }
    """
```

The `#dialog Static { width: auto; }` line is the only change. Without it, `Static` children default to `1fr` width, which Textual 8.x resolves to 0 inside a `width: auto` parent.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --frozen pytest tests/test_sso_login_screen.py::test_shows_code_and_url_and_opens_browser -v`

Expected: PASS.

- [ ] **Step 5: Run the full check**

Run: `make test`

Expected: `ruff check`, `ruff format --check`, `ty check`, and the whole pytest suite all pass.

- [ ] **Step 6: Commit**

```bash
git add src/awst/screens/sso_login.py tests/test_sso_login_screen.py
git commit -m "Fix SSO login dialog rendering as an empty box

Static children of the auto-width dialog defaulted to 1fr width, which
Textual resolves to 0 inside an auto-width parent, collapsing the whole
dialog. Size the children to their content instead, and assert rendered
geometry in the test so a layout collapse can't pass on content checks.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YSaXUVEben5CeEoxmRTZtR"
```

If the commit fails with a 1Password signing error ("failed to fill whole buffer"), retry the same `git commit` once — the signer can fail transiently.
