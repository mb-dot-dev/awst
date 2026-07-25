# SSM parameter details — design

Date: 2026-07-25
Status: approved

## Goal

Add a detail screen for a single SSM parameter, opened with Enter from the parameter list.
It shows the parameter's value — masked when the parameter is a `SecureString`, revealed on
an explicit keypress — alongside the metadata `get_parameter` returns. This is the "future
detail screen behind an explicit keypress" that the parameter-list design deferred values
to.

## Decisions

- **Fetch the value on open, render it masked.** The screen calls `get_parameter` with
  `WithDecryption=True` as soon as it opens, but a `SecureString` renders as a fixed
  `••••••••` until the user presses `s`. Fetching lazily behind a second keypress would
  keep secrets off the wire until asked, but it makes the common case two keypresses and
  leaves the screen half-empty on arrival; masking already prevents a secret from landing
  on screen or in scrollback by accident.
- **Fixed-width mask.** The mask is a constant eight bullets, not one per character, so it
  does not leak the secret's length.
- **One API call.** `get_parameter` returns everything the screen shows except `Tier`, and
  the list screen already holds `Tier` in its `ParameterSummary`. A second
  `describe_parameters` call would add `Description`, `KeyId`, `AllowedPattern`, and
  `Policies`, but doubles the request count for fields the console shows and this screen
  does not need yet.
- **Overview only — no version history, no tags.** `get_parameter_history` and
  `list_tags_for_resource` are each another call and another tab; neither is needed to
  answer "what is this parameter set to right now".
- **A decrypt failure is a plain error.** When the caller lacks `kms:Decrypt` for a
  `SecureString`'s key, `get_parameter` raises `AccessDeniedException`; it maps to `AwsError`
  and renders through the standard full-screen error panel. Degrading to "metadata shown,
  value unavailable" would need a dedicated error subclass and a branch in `errors.py` to
  salvage metadata that is already visible on the list screen behind it.
- **No `ParameterNotFoundError` subclass.** `stack_detail.py` special-cases
  `StackNotFoundError` because deleting a stack from that very screen makes disappearance an
  expected outcome. Nothing in this screen deletes a parameter, so a parameter vanishing
  between list and open is just another `AwsError`.

## Components

### Model (`src/awst/aws/models.py`)

`ParameterDetail` — frozen, slotted dataclass:

- `name: str` — the full parameter name
- `param_type: str` — `"String"`, `"StringList"`, or `"SecureString"`
- `value: str` — the decrypted value
- `version: int`
- `arn: str`
- `data_type: str` — `"text"`, `"aws:ec2:image"`, …
- `modified: datetime` — `LastModifiedDate`

Its docstring notes that, unlike `ParameterSummary`, this model does hold a secret.

### Gateway (`src/awst/aws/ssm.py`)

`SsmGateway.get_parameter(name: str) -> ParameterDetail`:

- calls `self._client.get_parameter(Name=name, WithDecryption=True)`
- maps the response's `Parameter` through a module-level `_to_detail`
- wraps `BotoCoreError`/`ClientError` with `map_botocore_error`, as `list_parameters` does

Every field of `ParameterTypeDef` is optional in the API model even though all are present
in practice; `_to_detail` mirrors `_to_summary`'s handling — `.get(..., "")` / `.get(..., 0)`
for the fields that are cosmetic, direct subscript for `Name` and `LastModifiedDate`.

### Formatting (`src/awst/screens/formatting.py`)

`mask_value(value: str, param_type: str, *, revealed: bool) -> str` — returns `"••••••••"`
when `param_type == "SecureString"` and not `revealed`, otherwise `value` unchanged. Pure
function, no widget knowledge.

### Screen (`src/awst/screens/parameter_detail.py`)

`ParameterInspector` — `Protocol` declaring `get_parameter(name: str) -> ParameterDetail`.

`ParameterDetailScreen(Screen[None])`, constructed with `(gateway: ParameterInspector,
summary: ParameterSummary)`:

- `TITLE = "Parameter details"`; `sub_title` is the parameter name, set in `on_mount`
- a single `VerticalScroll` holding a `Static` overview and a `Static` value block — no
  `TabbedContent`, there is only one section — plus the hidden `#error` `Static` and a
  `Footer`, matching `stack_detail.py`'s composition
- overview lines, label-aligned like `_overview_text` in `stack_detail.py`: Type, Tier,
  Version, Data type, Modified (via `relative_age`), ARN. `Tier` comes from the passed
  summary; the rest from the fetched detail.
- the value block renders `mask_value(detail.value, detail.param_type, revealed=self._revealed)`,
  wrapping so multi-line and `StringList` values stay readable
- loading via `loading = True` on the scroll container, data via
  `@work(thread=True, exclusive=True, exit_on_error=False)` handled in
  `on_worker_state_changed`
- before the first successful load, errors replace the screen with the `#error` panel; after
  it, a failed refresh toasts instead — the `_loaded` split from `stack_detail.py`

Bindings:

| Key | Action |
|-----|--------|
| `escape` | back (`app.pop_screen`) |
| `r` | refresh; resets `_revealed` to `False` |
| `s` | toggle reveal; a visual no-op for non-`SecureString` types, which are never masked |
| `c` | copy the full value to the clipboard via `self.app.copy_to_clipboard`, then toast "Value copied to clipboard." |

`c` copies the real value regardless of reveal state. Before the fetch completes there is no
value, so both `s` and `c` return silently.

### List screen (`src/awst/screens/parameters.py`)

- `ParameterGateway(ParameterLister, ParameterInspector, Protocol)` — the union of what the
  two parameter screens need, mirroring `StackGateway`; `ParameterListScreen.__init__` takes
  it instead of `ParameterLister`.
- `on_data_table_row_selected` resolves the selected row key against the base class's
  `self._all_items` (row keys are `_item_name`, i.e. the parameter name) and pushes
  `ParameterDetailScreen(self._gateway, summary)`. No extra bookkeeping: the base already
  holds every loaded summary. A row key that matches nothing is ignored, as
  `stacks.py` ignores a `None` key.

### Wiring

Nothing changes in `AwstApp` or `SERVICES`: `ssm_gateway` already exists, and the concrete
`SsmGateway` satisfies `ParameterGateway` once `get_parameter` lands.

## Error handling

All failures surface as `AwsError` through `map_botocore_error` and render with the panel /
toast split described above. `AccessDeniedException` (no `kms:Decrypt`) and
`ParameterNotFound` (deleted between list and open) both take that path. The screen performs
no writes, so there are no partial-failure cases.

## Testing

- **Gateway** (`tests/test_ssm_gateway.py`, extend): moto `mock_aws` — put a `String` and a
  `SecureString`, then assert `get_parameter` maps name, type, value, version, ARN, data
  type, and modified, and that the `SecureString` comes back decrypted. `Stubber` for
  `WithDecryption=True` being sent, and for `AccessDeniedException` and `ParameterNotFound`
  mapping to `AwsError`.
- **Formatting** (`tests/test_formatting.py`, extend): `mask_value` masks a `SecureString`
  when hidden, returns it verbatim when revealed, never masks `String`/`StringList`, and
  produces the same fixed-width mask for values of different lengths.
- **Detail screen** (`tests/test_parameter_detail_screen.py`, pytest-asyncio + Textual
  pilot): overview fields all render, including `Tier` from the summary; a `SecureString`
  starts masked and `s` reveals then re-hides it; `r` re-fetches and re-masks; `c` copies the
  full value while it is masked; an initial-load failure shows the error panel; a failure
  after a successful load toasts instead.
- **List → detail** (`tests/test_parameter_list_screen.py`, extend): Enter on a row pushes
  `ParameterDetailScreen` for that parameter.
- **Fakes** (`tests/fakes.py`): `FakeSsmGateway` gains a `detail` attribute, a `get_parameter`
  method recording the names it was called with, and an error hook; plus a
  `make_parameter_detail` helper alongside `make_parameter`.
- **Docs**: extend the SSM sentence in `CLAUDE.md` to cover the detail screen and its
  bindings.
- Completion gate: `make test` (lint + unit) passes.
