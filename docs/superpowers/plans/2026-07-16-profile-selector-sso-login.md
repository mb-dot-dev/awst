# AWS Profile Selector and SSO Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a profile picker at startup when no AWS profile is active, and let the user complete an in-process AWS SSO device login (boto3 `sso-oidc`) when credentials are missing or expired.

**Architecture:** A new `aws/profiles.py` module resolves profiles and SSO settings from botocore config; a new `aws/sso.py` gateway drives the OIDC device flow and writes botocore's token cache. A `ProfileSelectScreen` runs before `HomeScreen`; an `SsoLoginScreen` modal is launched from a new `l` binding on `ResourceListScreen` when a `CredentialsError` is shown and the active profile has SSO config. Screens never import boto3/botocore — they reach the AWS layer through `AwstApp` (duck-typed `sso_login_possible` / `make_sso_login_screen`) and models from `aws/models.py`.

**Tech Stack:** Python ≥3.14, Textual, boto3/botocore, pytest + pytest-asyncio (Textual pilot), botocore `Stubber` for the sso-oidc gateway (moto does not model `sso-oidc`), `uv` + `make`.

**Spec:** `docs/superpowers/specs/2026-07-16-profile-selector-sso-login-design.md`

## Global Constraints

- Run everything through `uv`/`make`: tests via `uv run --frozen pytest …`, full check via `make test` (= `make lint` + `make unit`).
- `make lint` (ruff check, ruff format --check, ty check) must pass before any commit; run `make format` to fix formatting.
- Ruff is strict (bandit, bugbear, annotations, pathlib, FBT, EM, TRY…), line length 120. Known traps and their required spellings are baked into the code blocks below (`# noqa: FBT001`, `# noqa: S324`, `# noqa: ARG002`, `dismiss(result=True)`, `message = "…"` before `raise`).
- Screens never import boto3/botocore. Screen-visible models and exceptions live in `src/awst/aws/models.py`.
- Every screen method takes `self: Self` and full type annotations; files start with a module docstring and `from __future__ import annotations`.
- Coverage must stay ≥75% (`make coverage`).
- All work happens on the existing branch `feature/profile-selector-and-sso-login`.

## File Map

| File | Change |
|---|---|
| `src/awst/aws/models.py` | Add `CredentialsError`, `SlowDownError`, `SsoConfig`, `DeviceAuthorization`, `SsoToken` |
| `src/awst/aws/errors.py` | Map credential exceptions to `CredentialsError` |
| `src/awst/aws/profiles.py` | New: profile discovery/selection, SSO config resolution |
| `src/awst/aws/sso.py` | New: `SsoLoginGateway` (device flow + token cache) |
| `src/awst/screens/profiles.py` | New: `ProfileSelectScreen` |
| `src/awst/screens/sso_login.py` | New: `SsoLoginScreen` modal + `SsoAuthorizer` protocol |
| `src/awst/screens/resource_list.py` | `l` — Login binding, error-panel hint, modal launch |
| `src/awst/app.py` | Startup picker, subtitle, SSO gateway factory seam |
| `pyproject.toml` + `uv.lock` | Add `sso-oidc` extra to boto3-stubs |
| `tests/conftest.py` | Hermetic AWS config/profile environment |
| `tests/fakes.py` | `make_sso_config`, `make_device_authorization`, `make_sso_token`, `FakeSsoLoginGateway` |
| `tests/test_errors.py` | `CredentialsError` mapping tests |
| `tests/test_profiles.py` | New |
| `tests/test_sso_gateway.py` | New |
| `tests/test_profile_select_screen.py` | New |
| `tests/test_sso_login_screen.py` | New |
| `tests/test_resource_list_login.py` | New |

---

### Task 1: `CredentialsError` model and error mapping

**Files:**
- Modify: `src/awst/aws/models.py` (after `AwsError`, line ~19)
- Modify: `src/awst/aws/errors.py:22-30`
- Test: `tests/test_errors.py`

**Interfaces:**
- Consumes: existing `AwsError`, `map_botocore_error`.
- Produces: `CredentialsError(AwsError)` in `awst.aws.models`; `map_botocore_error` returns a `CredentialsError` for `NoCredentialsError`, `SSOTokenLoadError`, `TokenRetrievalError`, `UnauthorizedSSOTokenError`. Later tasks import `CredentialsError` from `awst.aws.models`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_errors.py` (extend the existing botocore imports with `SSOTokenLoadError` and `UnauthorizedSSOTokenError`, and import `CredentialsError` from `awst.aws.models`):

```python
@pytest.mark.parametrize(
    "botocore_error",
    [
        NoCredentialsError(),
        SSOTokenLoadError(profile_name="dev", error_msg="missing"),
        TokenRetrievalError(provider="sso", error_msg="expired"),
        UnauthorizedSSOTokenError(),
    ],
)
def test_credential_failures_map_to_credentials_error(botocore_error: Exception) -> None:
    error = map_botocore_error(botocore_error)

    assert isinstance(error, CredentialsError)


def test_non_credential_failures_are_not_credentials_errors() -> None:
    error = map_botocore_error(BotoCoreError())

    assert not isinstance(error, CredentialsError)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_errors.py -v`
Expected: FAIL — `ImportError: cannot import name 'CredentialsError'`

- [ ] **Step 3: Implement**

In `src/awst/aws/models.py`, directly after the `AwsError` class:

```python
class CredentialsError(AwsError):
    """AWS credentials are missing or expired; logging in may fix it."""
```

In `src/awst/aws/errors.py`, import `CredentialsError` alongside `AwsError` and change the credential branch of `map_botocore_error`:

```python
from awst.aws.models import AwsError, CredentialsError
```

```python
def map_botocore_error(error: Exception) -> AwsError:
    """Return the AwsError equivalent of a botocore exception."""
    if isinstance(error, _CREDENTIAL_ERRORS):
        return CredentialsError("No valid AWS credentials found.", hint=_CREDENTIALS_HINT)
    if isinstance(error, _NETWORK_ERRORS):
        return AwsError(str(error), hint=_NETWORK_HINT)
    if isinstance(error, ClientError):
        return AwsError(error.response["Error"]["Message"])
    return AwsError(str(error))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_errors.py -v`
Expected: PASS (all, including the pre-existing tests — message and hint are unchanged)

- [ ] **Step 5: Lint and commit**

```bash
make format && make lint
git add src/awst/aws/models.py src/awst/aws/errors.py tests/test_errors.py
git commit -m "Add CredentialsError for credential/SSO failures"
```

---

### Task 2: Hermetic test environment and `aws/profiles.py`

**Files:**
- Modify: `tests/conftest.py`
- Modify: `src/awst/aws/models.py` (add `SsoConfig`)
- Create: `src/awst/aws/profiles.py`
- Test: `tests/test_profiles.py`

**Interfaces:**
- Produces:
  - `SsoConfig` frozen dataclass in `awst.aws.models`: `start_url: str`, `sso_region: str`, `session_name: str | None`.
  - `awst.aws.profiles` functions: `active_profile() -> str | None`, `available_profiles() -> list[str]`, `select_profile(name: str) -> None`, `sso_config(name: str | None) -> SsoConfig | None`.
  - conftest guarantees: `AWS_CONFIG_FILE`/`AWS_SHARED_CREDENTIALS_FILE` point into `tmp_path` (initially nonexistent), `AWS_PROFILE`/`AWS_DEFAULT_PROFILE` are absent and are restored at teardown even if a test (or the app under test) sets them. Tests create profiles by writing INI text to `Path(os.environ["AWS_CONFIG_FILE"])`.

- [ ] **Step 1: Make the test environment hermetic**

Replace `tests/conftest.py` with:

```python
"""Shared test fixtures."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _aws_test_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Fake AWS credentials and isolated config files so no test can ever touch a real account."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    # setenv-then-delenv registers the original (absent) value with monkeypatch, so any
    # AWS_PROFILE the code under test sets is removed again at teardown.
    monkeypatch.setenv("AWS_PROFILE", "scrubbed")
    monkeypatch.delenv("AWS_PROFILE")
    monkeypatch.setenv("AWS_DEFAULT_PROFILE", "scrubbed")
    monkeypatch.delenv("AWS_DEFAULT_PROFILE")
```

Run: `uv run --frozen pytest -x -q`
Expected: PASS — the whole existing suite still passes with the isolated environment.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_profiles.py`:

```python
"""Tests for AWS profile discovery, selection, and SSO config resolution."""

import os
from pathlib import Path

import pytest

from awst.aws import profiles

_CONFIG = """\
[default]
sso_start_url = https://default.awsapps.com/start
sso_region = eu-central-1

[profile dev]
sso_start_url = https://legacy.awsapps.com/start
sso_region = eu-west-1
sso_account_id = 123456789012
sso_role_name = Dev

[profile plain]
region = eu-west-1

[profile modern]
sso_session = corp
sso_account_id = 123456789012
sso_role_name = Admin

[profile dangling]
sso_session = missing

[sso-session corp]
sso_start_url = https://corp.awsapps.com/start
sso_region = us-east-1
"""


@pytest.fixture
def config_file() -> Path:
    path = Path(os.environ["AWS_CONFIG_FILE"])
    path.write_text(_CONFIG)
    return path


def test_active_profile_is_none_when_unset() -> None:
    assert profiles.active_profile() is None


def test_active_profile_prefers_aws_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_DEFAULT_PROFILE", "fallback")
    monkeypatch.setenv("AWS_PROFILE", "primary")

    assert profiles.active_profile() == "primary"


def test_active_profile_falls_back_to_aws_default_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_DEFAULT_PROFILE", "fallback")

    assert profiles.active_profile() == "fallback"


def test_available_profiles_reads_the_config_file(config_file: Path) -> None:
    assert profiles.available_profiles() == ["default", "dev", "plain", "modern", "dangling"]


def test_available_profiles_is_empty_without_config_files() -> None:
    assert profiles.available_profiles() == []


def test_select_profile_sets_the_environment() -> None:
    profiles.select_profile("dev")

    assert os.environ["AWS_PROFILE"] == "dev"


def test_sso_config_resolves_a_legacy_inline_profile(config_file: Path) -> None:
    config = profiles.sso_config("dev")

    assert config is not None
    assert config.start_url == "https://legacy.awsapps.com/start"
    assert config.sso_region == "eu-west-1"
    assert config.session_name is None


def test_sso_config_resolves_an_sso_session_profile(config_file: Path) -> None:
    config = profiles.sso_config("modern")

    assert config is not None
    assert config.start_url == "https://corp.awsapps.com/start"
    assert config.sso_region == "us-east-1"
    assert config.session_name == "corp"


def test_sso_config_uses_the_default_profile_for_none(config_file: Path) -> None:
    config = profiles.sso_config(None)

    assert config is not None
    assert config.start_url == "https://default.awsapps.com/start"


def test_sso_config_is_none_for_a_non_sso_profile(config_file: Path) -> None:
    assert profiles.sso_config("plain") is None


def test_sso_config_is_none_for_a_dangling_sso_session(config_file: Path) -> None:
    assert profiles.sso_config("dangling") is None


def test_sso_config_is_none_for_an_unknown_profile(config_file: Path) -> None:
    assert profiles.sso_config("nope") is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awst.aws.profiles'`

- [ ] **Step 4: Implement**

Add to `src/awst/aws/models.py` (after `CredentialsError` and `StackNotFoundError`, with the other dataclasses):

```python
@dataclass(frozen=True, slots=True)
class SsoConfig:
    """The SSO settings a profile uses to log in.

    session_name is set for [sso-session] profiles and None for legacy inline
    sso_* profiles; the token-cache format differs between the two.
    """

    start_url: str
    sso_region: str
    session_name: str | None
```

Create `src/awst/aws/profiles.py`:

```python
"""AWS profile discovery, selection, and SSO config resolution."""

from __future__ import annotations

import os

import boto3
import botocore.session

from awst.aws.models import SsoConfig


def active_profile() -> str | None:
    """The profile named by the environment, or None when unset."""
    return os.environ.get("AWS_PROFILE") or os.environ.get("AWS_DEFAULT_PROFILE") or None


def available_profiles() -> list[str]:
    """Every profile defined in the AWS config and credentials files."""
    return boto3.Session().available_profiles


def select_profile(name: str) -> None:
    """Make name the process-wide profile; lazily built gateways pick it up."""
    os.environ["AWS_PROFILE"] = name


def sso_config(name: str | None) -> SsoConfig | None:
    """The named profile's SSO settings (the default profile when None).

    Returns None when the profile does not exist or has no SSO configuration;
    "is this an SSO profile?" is simply `sso_config(name) is not None`.
    """
    full_config = botocore.session.Session().full_config
    profile = full_config.get("profiles", {}).get(name or "default", {})
    session_name = profile.get("sso_session")
    if session_name is not None:
        section = full_config.get("sso_sessions", {}).get(session_name, {})
        return _to_config(section, session_name)
    return _to_config(profile, None)


def _to_config(section: dict[str, str], session_name: str | None) -> SsoConfig | None:
    start_url = section.get("sso_start_url")
    region = section.get("sso_region")
    if start_url is None or region is None:
        return None
    return SsoConfig(start_url=start_url, sso_region=region, session_name=session_name)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_profiles.py -v`
Expected: PASS (12 tests)

- [ ] **Step 6: Lint, full suite, commit**

```bash
make format && make lint && make unit
git add tests/conftest.py src/awst/aws/models.py src/awst/aws/profiles.py tests/test_profiles.py
git commit -m "Add AWS profile discovery and SSO config resolution"
```

---

### Task 3: SSO models and `SsoLoginGateway` device flow

**Files:**
- Modify: `pyproject.toml:17` (boto3-stubs extras) and `uv.lock`
- Modify: `src/awst/aws/models.py` (add `SlowDownError`, `DeviceAuthorization`, `SsoToken`)
- Create: `src/awst/aws/sso.py`
- Modify: `tests/fakes.py` (add `make_sso_config`, `make_device_authorization`, `make_sso_token`)
- Test: `tests/test_sso_gateway.py`

**Interfaces:**
- Consumes: `SsoConfig` (Task 2), `map_botocore_error` (Task 1).
- Produces:
  - `awst.aws.models.SlowDownError(Exception)` — poll less often.
  - `awst.aws.models.DeviceAuthorization` frozen dataclass: `client_id: str`, `client_secret: str`, `registration_expires_at: datetime`, `device_code: str`, `user_code: str`, `verification_uri: str`, `verification_uri_complete: str`, `interval: int`, `expires_at: datetime`.
  - `awst.aws.models.SsoToken` frozen dataclass: `access_token: str`, `expires_at: datetime`, `refresh_token: str | None`.
  - `awst.aws.sso.SsoLoginGateway(client, cache_dir: Path | None = None)` with `start_device_authorization(config: SsoConfig) -> DeviceAuthorization` and `poll_token(authorization: DeviceAuthorization) -> SsoToken | None`.
  - Test factories in `tests/fakes.py`: `make_sso_config(session_name: str | None = None) -> SsoConfig`, `make_device_authorization(interval: int = 0, expires_in_s: int = 600) -> DeviceAuthorization`, `make_sso_token(refresh_token: str | None = None) -> SsoToken`.

- [ ] **Step 1: Add sso-oidc type stubs**

In `pyproject.toml`, change the boto3-stubs dev dependency line to:

```toml
    "boto3-stubs[cloudformation,lambda,s3,sqs,sso-oidc]>=1.43.40",
```

Run: `uv lock && make install-dev`
Expected: lockfile updated, sync succeeds.

- [ ] **Step 2: Add the test factories**

Add to `tests/fakes.py` (extend the `datetime` import to include `timedelta`, and the models import with `DeviceAuthorization`, `SsoConfig`, `SsoToken`):

```python
def make_sso_config(session_name: str | None = None) -> SsoConfig:
    """SSO settings matching the canned device-flow responses in tests."""
    return SsoConfig(start_url="https://legacy.awsapps.com/start", sso_region="eu-west-1", session_name=session_name)


def make_device_authorization(interval: int = 0, expires_in_s: int = 600) -> DeviceAuthorization:
    """A device authorization with sensible defaults; interval 0 keeps tests fast."""
    now = datetime.now(tz=UTC)
    return DeviceAuthorization(
        client_id="client-id",
        client_secret="client-secret",
        registration_expires_at=now + timedelta(days=90),
        device_code="device-code",
        user_code="ABCD-EFGH",
        verification_uri="https://device.sso.eu-west-1.amazonaws.com/",
        verification_uri_complete="https://device.sso.eu-west-1.amazonaws.com/?user_code=ABCD-EFGH",
        interval=interval,
        expires_at=now + timedelta(seconds=expires_in_s),
    )


def make_sso_token(refresh_token: str | None = None) -> SsoToken:
    """An SSO access token for login-flow tests."""
    return SsoToken(
        access_token="access-token",
        expires_at=datetime.now(tz=UTC) + timedelta(hours=8),
        refresh_token=refresh_token,
    )
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_sso_gateway.py`:

```python
"""Tests for the SSO OIDC login gateway."""

from datetime import UTC, datetime

import boto3
from botocore.stub import Stubber
import pytest

from awst.aws.models import AwsError, SlowDownError
from awst.aws.sso import SsoLoginGateway
from tests.fakes import make_device_authorization, make_sso_config

_REGISTER_EXPECTED = {"clientName": "awst", "clientType": "public"}
_REGISTER_RESPONSE = {
    "clientId": "client-id",
    "clientSecret": "client-secret",
    "clientSecretExpiresAt": 1893456000,
}
_DEVICE_EXPECTED = {
    "clientId": "client-id",
    "clientSecret": "client-secret",
    "startUrl": "https://legacy.awsapps.com/start",
}
_DEVICE_RESPONSE = {
    "deviceCode": "device-code",
    "userCode": "ABCD-EFGH",
    "verificationUri": "https://device.sso.eu-west-1.amazonaws.com/",
    "verificationUriComplete": "https://device.sso.eu-west-1.amazonaws.com/?user_code=ABCD-EFGH",
    "expiresIn": 600,
    "interval": 5,
}
_TOKEN_EXPECTED = {
    "clientId": "client-id",
    "clientSecret": "client-secret",
    "grantType": "urn:ietf:params:oauth:grant-type:device_code",
    "deviceCode": "device-code",
}


def _client():  # noqa: ANN202 — the stubs' client type is verbose and irrelevant here
    return boto3.client("sso-oidc", region_name="eu-west-1")


def test_start_device_authorization_registers_and_starts() -> None:
    client = _client()
    with Stubber(client) as stubber:
        stubber.add_response("register_client", _REGISTER_RESPONSE, _REGISTER_EXPECTED)
        stubber.add_response("start_device_authorization", _DEVICE_RESPONSE, _DEVICE_EXPECTED)

        authorization = SsoLoginGateway(client).start_device_authorization(make_sso_config())

    assert authorization.client_id == "client-id"
    assert authorization.client_secret == "client-secret"  # noqa: S105
    assert authorization.registration_expires_at == datetime.fromtimestamp(1893456000, tz=UTC)
    assert authorization.device_code == "device-code"
    assert authorization.user_code == "ABCD-EFGH"
    assert authorization.verification_uri_complete.endswith("user_code=ABCD-EFGH")
    assert authorization.interval == 5
    assert authorization.expires_at > datetime.now(tz=UTC)


def test_start_device_authorization_maps_failures_to_aws_error() -> None:
    client = _client()
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "register_client", service_error_code="AccessDeniedException", service_message="denied"
        )

        with pytest.raises(AwsError) as excinfo:
            SsoLoginGateway(client).start_device_authorization(make_sso_config())

    assert excinfo.value.message == "denied"


def test_poll_token_returns_none_while_pending() -> None:
    client = _client()
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "create_token", service_error_code="AuthorizationPendingException", service_message="pending"
        )

        assert SsoLoginGateway(client).poll_token(make_device_authorization()) is None


def test_poll_token_raises_slow_down() -> None:
    client = _client()
    with Stubber(client) as stubber:
        stubber.add_client_error("create_token", service_error_code="SlowDownException", service_message="slow down")

        with pytest.raises(SlowDownError):
            SsoLoginGateway(client).poll_token(make_device_authorization())


def test_poll_token_maps_expiry_to_aws_error() -> None:
    client = _client()
    with Stubber(client) as stubber:
        stubber.add_client_error("create_token", service_error_code="ExpiredTokenException", service_message="expired")

        with pytest.raises(AwsError) as excinfo:
            SsoLoginGateway(client).poll_token(make_device_authorization())

    assert excinfo.value.message == "expired"


def test_poll_token_returns_the_token_on_approval() -> None:
    client = _client()
    response = {"accessToken": "access-token", "tokenType": "Bearer", "expiresIn": 28800, "refreshToken": "refresh"}
    with Stubber(client) as stubber:
        stubber.add_response("create_token", response, _TOKEN_EXPECTED)

        token = SsoLoginGateway(client).poll_token(make_device_authorization())

    assert token is not None
    assert token.access_token == "access-token"  # noqa: S105
    assert token.refresh_token == "refresh"  # noqa: S105
    assert token.expires_at > datetime.now(tz=UTC)


def test_poll_token_refresh_token_is_none_when_absent() -> None:
    client = _client()
    response = {"accessToken": "access-token", "tokenType": "Bearer", "expiresIn": 28800}
    with Stubber(client) as stubber:
        stubber.add_response("create_token", response, _TOKEN_EXPECTED)

        token = SsoLoginGateway(client).poll_token(make_device_authorization())

    assert token is not None
    assert token.refresh_token is None
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_sso_gateway.py -v`
Expected: FAIL — first `ImportError` on the models (`SlowDownError`), then `ModuleNotFoundError: No module named 'awst.aws.sso'`

- [ ] **Step 5: Implement the models**

Add to `src/awst/aws/models.py` (exception next to the other exceptions, dataclasses with the other dataclasses):

```python
class SlowDownError(Exception):
    """The SSO OIDC service asked us to poll less often; wait longer and retry."""
```

```python
@dataclass(frozen=True, slots=True)
class DeviceAuthorization:
    """One in-flight SSO OIDC device authorization, plus its client registration."""

    client_id: str
    client_secret: str
    registration_expires_at: datetime
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    interval: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SsoToken:
    """An SSO access token minted by the device flow."""

    access_token: str
    expires_at: datetime
    refresh_token: str | None
```

- [ ] **Step 6: Implement the gateway**

Create `src/awst/aws/sso.py`:

```python
"""Gateway for the AWS SSO OIDC device-authorization login flow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import DeviceAuthorization, SlowDownError, SsoToken

if TYPE_CHECKING:
    from mypy_boto3_sso_oidc import SSOOIDCClient

    from awst.aws.models import SsoConfig

_CLIENT_NAME = "awst"
_CLIENT_TYPE = "public"
_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_DEFAULT_CACHE_DIR = Path("~/.aws/sso/cache")
_DEFAULT_POLL_INTERVAL_S = 5


class SsoLoginGateway:
    """Drives the SSO OIDC device flow and caches the resulting token."""

    def __init__(self: Self, client: SSOOIDCClient, cache_dir: Path | None = None) -> None:
        self._client = client
        self._cache_dir = cache_dir or _DEFAULT_CACHE_DIR

    def start_device_authorization(self: Self, config: SsoConfig) -> DeviceAuthorization:
        """Register this app with the OIDC service and start a device authorization.

        Raises AwsError for any failure.
        """
        try:
            registration = self._client.register_client(clientName=_CLIENT_NAME, clientType=_CLIENT_TYPE)
            authorization = self._client.start_device_authorization(
                clientId=registration["clientId"],
                clientSecret=registration["clientSecret"],
                startUrl=config.start_url,
            )
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        return DeviceAuthorization(
            client_id=registration["clientId"],
            client_secret=registration["clientSecret"],
            registration_expires_at=datetime.fromtimestamp(registration["clientSecretExpiresAt"], tz=UTC),
            device_code=authorization["deviceCode"],
            user_code=authorization["userCode"],
            verification_uri=authorization["verificationUri"],
            verification_uri_complete=authorization["verificationUriComplete"],
            interval=authorization.get("interval", _DEFAULT_POLL_INTERVAL_S),
            expires_at=datetime.now(tz=UTC) + timedelta(seconds=authorization["expiresIn"]),
        )

    def poll_token(self: Self, authorization: DeviceAuthorization) -> SsoToken | None:
        """One create-token attempt; None while the user has not approved yet.

        Raises SlowDownError when the service asks for a longer poll interval,
        and AwsError when the authorization expired or the call failed.
        """
        try:
            response = self._client.create_token(
                clientId=authorization.client_id,
                clientSecret=authorization.client_secret,
                grantType=_GRANT_TYPE,
                deviceCode=authorization.device_code,
            )
        except self._client.exceptions.AuthorizationPendingException:
            return None
        except self._client.exceptions.SlowDownException as error:
            raise SlowDownError from error
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        return SsoToken(
            access_token=response["accessToken"],
            expires_at=datetime.now(tz=UTC) + timedelta(seconds=response["expiresIn"]),
            refresh_token=response.get("refreshToken"),
        )
```

Note: the order of the `except` clauses matters — `AuthorizationPendingException` and `SlowDownException` are `ClientError` subclasses and must come first.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_sso_gateway.py -v`
Expected: PASS (7 tests)

- [ ] **Step 8: Lint and commit**

```bash
make format && make lint
git add pyproject.toml uv.lock src/awst/aws/models.py src/awst/aws/sso.py tests/fakes.py tests/test_sso_gateway.py
git commit -m "Add SSO OIDC device-flow gateway"
```

---

### Task 4: Token cache writing

**Files:**
- Modify: `src/awst/aws/sso.py`
- Test: `tests/test_sso_gateway.py`

**Interfaces:**
- Consumes: `SsoConfig`, `DeviceAuthorization`, `SsoToken`, `SsoLoginGateway` (Tasks 2–3).
- Produces: `SsoLoginGateway.write_token_cache(config: SsoConfig, authorization: DeviceAuthorization, token: SsoToken) -> None`. May raise `OSError`. Writes into the `cache_dir` given at construction (default `~/.aws/sso/cache`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_sso_gateway.py` (extend imports with `hashlib`, `json`, `re`, `from pathlib import Path`, and `make_sso_token` from fakes):

```python
_ISO_UTC = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"


def test_write_token_cache_legacy_profile_keys_on_start_url(tmp_path: Path) -> None:
    gateway = SsoLoginGateway(_client(), cache_dir=tmp_path)
    config = make_sso_config()

    gateway.write_token_cache(config, make_device_authorization(), make_sso_token())

    expected_name = hashlib.sha1(config.start_url.encode()).hexdigest() + ".json"  # noqa: S324
    entry = json.loads((tmp_path / expected_name).read_text())
    assert entry["startUrl"] == config.start_url
    assert entry["region"] == "eu-west-1"
    assert entry["accessToken"] == "access-token"
    assert re.fullmatch(_ISO_UTC, entry["expiresAt"])
    assert "clientId" not in entry
    assert "refreshToken" not in entry


def test_write_token_cache_sso_session_profile_keys_on_session_name(tmp_path: Path) -> None:
    gateway = SsoLoginGateway(_client(), cache_dir=tmp_path)
    config = make_sso_config(session_name="corp")

    gateway.write_token_cache(config, make_device_authorization(), make_sso_token(refresh_token="refresh"))

    expected_name = hashlib.sha1(b"corp").hexdigest() + ".json"  # noqa: S324
    entry = json.loads((tmp_path / expected_name).read_text())
    assert entry["startUrl"] == config.start_url
    assert entry["clientId"] == "client-id"
    assert entry["clientSecret"] == "client-secret"  # noqa: S105
    assert re.fullmatch(_ISO_UTC, entry["registrationExpiresAt"])
    assert entry["refreshToken"] == "refresh"  # noqa: S105


def test_write_token_cache_omits_refresh_token_when_absent(tmp_path: Path) -> None:
    gateway = SsoLoginGateway(_client(), cache_dir=tmp_path)
    config = make_sso_config(session_name="corp")

    gateway.write_token_cache(config, make_device_authorization(), make_sso_token())

    expected_name = hashlib.sha1(b"corp").hexdigest() + ".json"  # noqa: S324
    entry = json.loads((tmp_path / expected_name).read_text())
    assert "refreshToken" not in entry


def test_write_token_cache_creates_the_cache_directory(tmp_path: Path) -> None:
    cache_dir = tmp_path / "sso" / "cache"
    gateway = SsoLoginGateway(_client(), cache_dir=cache_dir)

    gateway.write_token_cache(make_sso_config(), make_device_authorization(), make_sso_token())

    assert len(list(cache_dir.iterdir())) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_sso_gateway.py -v -k write_token_cache`
Expected: FAIL — `AttributeError: 'SsoLoginGateway' object has no attribute 'write_token_cache'`

- [ ] **Step 3: Implement**

Add to `src/awst/aws/sso.py` (new imports: `hashlib`, `json`; method on `SsoLoginGateway`, helper at module level):

```python
    def write_token_cache(self: Self, config: SsoConfig, authorization: DeviceAuthorization, token: SsoToken) -> None:
        """Persist the token where botocore's SSO credential provider reads it.

        The filename and JSON shape mirror what the AWS CLI writes — botocore has no
        public API for this. The whole format lives in this one method, and the tests
        pin it so a botocore change is caught here.
        """
        entry: dict[str, str] = {
            "startUrl": config.start_url,
            "region": config.sso_region,
            "accessToken": token.access_token,
            "expiresAt": _utc_iso(token.expires_at),
        }
        if config.session_name is not None:
            entry["clientId"] = authorization.client_id
            entry["clientSecret"] = authorization.client_secret
            entry["registrationExpiresAt"] = _utc_iso(authorization.registration_expires_at)
            if token.refresh_token is not None:
                entry["refreshToken"] = token.refresh_token
        cache_key = config.session_name or config.start_url
        directory = self._cache_dir.expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        # SHA-1 is the CLI's cache-filename convention, not a security control.
        path = directory / f"{hashlib.sha1(cache_key.encode()).hexdigest()}.json"  # noqa: S324
        path.write_text(json.dumps(entry))
        path.chmod(0o600)
```

```python
def _utc_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_sso_gateway.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Lint and commit**

```bash
make format && make lint
git add src/awst/aws/sso.py tests/test_sso_gateway.py
git commit -m "Write the botocore SSO token cache after login"
```

---

### Task 5: Profile selector at startup

**Files:**
- Create: `src/awst/screens/profiles.py`
- Modify: `src/awst/app.py:71-72` (`on_mount`)
- Test: `tests/test_profile_select_screen.py`

**Interfaces:**
- Consumes: `awst.aws.profiles` functions (Task 2).
- Produces: `ProfileSelectScreen(profile_names: list[str])`, a `Screen[str]` that dismisses with the chosen profile name. `AwstApp.on_mount` shows it when no profile is active and profiles exist; `AwstApp.sub_title` always carries the active profile name once known.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_profile_select_screen.py`:

```python
"""Tests for the startup profile selector."""

import os
from pathlib import Path

import pytest
from textual.widgets import OptionList

from awst.app import AwstApp
from awst.screens.home import HomeScreen
from awst.screens.profiles import ProfileSelectScreen
from tests.fakes import FakeCloudFormationGateway

_CONFIG = """\
[profile dev]
sso_start_url = https://legacy.awsapps.com/start
sso_region = eu-west-1

[profile prod]
region = eu-west-1
"""


def _write_config() -> None:
    Path(os.environ["AWS_CONFIG_FILE"]).write_text(_CONFIG)


@pytest.mark.asyncio
async def test_picker_shows_when_no_profile_is_active() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ProfileSelectScreen)
        options = app.screen.query_one(OptionList)
        assert options.option_count == 2
        assert options.get_option("dev") is not None
        assert options.get_option("prod") is not None


@pytest.mark.asyncio
async def test_selecting_a_profile_sets_it_and_opens_home() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # first option: dev
        await pilot.pause()

        assert os.environ["AWS_PROFILE"] == "dev"
        assert isinstance(app.screen, HomeScreen)
        assert app.sub_title == "dev"


@pytest.mark.asyncio
async def test_picker_skipped_when_profile_env_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config()
    monkeypatch.setenv("AWS_PROFILE", "prod")
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()

        assert isinstance(app.screen, HomeScreen)
        assert app.sub_title == "prod"


@pytest.mark.asyncio
async def test_picker_skipped_when_no_profiles_exist() -> None:
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()

        assert isinstance(app.screen, HomeScreen)


@pytest.mark.asyncio
async def test_q_quits_from_picker() -> None:
    _write_config()
    app = AwstApp(cloudformation_gateway=FakeCloudFormationGateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()

    assert app.return_code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_profile_select_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awst.screens.profiles'`

- [ ] **Step 3: Implement the screen**

Create `src/awst/screens/profiles.py`:

```python
"""Profile selection screen, shown at startup when no AWS profile is active."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Self

from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType


class ProfileSelectScreen(Screen[str]):
    """Pick the AWS profile the whole app will use; dismisses with its name."""

    TITLE = "awst"

    BINDINGS: ClassVar[list[BindingType]] = [("q", "app.quit", "Quit")]

    DEFAULT_CSS = """
    #prompt { padding: 1 2 0 2; color: $text-muted; }
    #profiles { margin: 1 2; }
    """

    def __init__(self: Self, profile_names: list[str]) -> None:
        super().__init__()
        self._profile_names = profile_names

    def compose(self: Self) -> ComposeResult:
        yield Static("Select an AWS profile", id="prompt")
        yield OptionList(*[Option(name, id=name) for name in self._profile_names], id="profiles")
        yield Footer()

    def on_option_list_option_selected(self: Self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self.dismiss(event.option.id)
```

- [ ] **Step 4: Wire up the app**

In `src/awst/app.py`, add imports:

```python
from awst.aws import profiles
from awst.screens.profiles import ProfileSelectScreen
```

Replace `on_mount` and add the callback:

```python
def on_mount(self: Self) -> None:
    profile = profiles.active_profile()
    if profile is not None:
        self.sub_title = profile
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
        self.sub_title = name
    self.push_screen(HomeScreen())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_profile_select_screen.py tests/test_app.py -v`
Expected: PASS — the new tests and all existing app tests (the hermetic conftest means no profiles exist by default, so existing tests still land on `HomeScreen`).

- [ ] **Step 6: Lint, full suite, commit**

```bash
make format && make lint && make unit
git add src/awst/screens/profiles.py src/awst/app.py tests/test_profile_select_screen.py
git commit -m "Show a profile selector at startup when no profile is active"
```

---

### Task 6: SSO login modal

**Files:**
- Create: `src/awst/screens/sso_login.py`
- Modify: `tests/fakes.py` (add `FakeSsoLoginGateway`)
- Test: `tests/test_sso_login_screen.py`

**Interfaces:**
- Consumes: `AwsError`, `SlowDownError`, `SsoConfig`, `DeviceAuthorization`, `SsoToken` from `awst.aws.models`; test factories from Task 3.
- Produces:
  - `awst.screens.sso_login.SsoAuthorizer` protocol: `start_device_authorization(config) -> DeviceAuthorization`, `poll_token(authorization) -> SsoToken | None`, `write_token_cache(config, authorization, token) -> None` (matches `SsoLoginGateway`).
  - `SsoLoginScreen(gateway: SsoAuthorizer, config: SsoConfig)`, a `ModalScreen[bool]` that dismisses `True` after caching a token, `False` on failure or escape.
  - `tests.fakes.FakeSsoLoginGateway(authorization=None, token=None, pending_polls=0, start_error=None, poll_error=None)` with recorded `poll_calls: int` and `cached: list[tuple[SsoConfig, DeviceAuthorization, SsoToken]]`.

- [ ] **Step 1: Add the fake gateway**

Add to `tests/fakes.py`:

```python
class FakeSsoLoginGateway:
    """In-memory stand-in for the SSO OIDC login gateway."""

    def __init__(
        self: Self,
        authorization: DeviceAuthorization | None = None,
        token: SsoToken | None = None,
        pending_polls: int = 0,
        start_error: AwsError | None = None,
        poll_error: AwsError | None = None,
    ) -> None:
        self.authorization = authorization or make_device_authorization()
        self.token = token or make_sso_token()
        self.pending_polls = pending_polls
        self.start_error = start_error
        self.poll_error = poll_error
        self.poll_calls = 0
        self.cached: list[tuple[SsoConfig, DeviceAuthorization, SsoToken]] = []

    def start_device_authorization(self: Self, config: SsoConfig) -> DeviceAuthorization:  # noqa: ARG002
        if self.start_error is not None:
            raise self.start_error
        return self.authorization

    def poll_token(self: Self, authorization: DeviceAuthorization) -> SsoToken | None:  # noqa: ARG002
        self.poll_calls += 1
        if self.poll_error is not None:
            raise self.poll_error
        if self.poll_calls <= self.pending_polls:
            return None
        return self.token

    def write_token_cache(
        self: Self,
        config: SsoConfig,
        authorization: DeviceAuthorization,
        token: SsoToken,
    ) -> None:
        self.cached.append((config, authorization, token))
```

Note: `AwsError` stays in the `TYPE_CHECKING` block of `tests/fakes.py` — like the existing fakes, it only appears in annotations (`from __future__ import annotations` keeps them lazy).

- [ ] **Step 2: Write the failing tests**

Create `tests/test_sso_login_screen.py`:

```python
"""Tests for the SSO login modal."""

from typing import Self
import webbrowser

import pytest
from textual.app import App
from textual.pilot import Pilot
from textual.widgets import Static

from awst.aws.models import AwsError
from awst.screens.sso_login import SsoLoginScreen
from tests.fakes import FakeSsoLoginGateway, make_device_authorization, make_sso_config


@pytest.fixture(autouse=True)
def opened_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record browser launches instead of opening a real browser."""
    urls: list[str] = []
    monkeypatch.setattr(webbrowser, "open", urls.append)
    return urls


class SsoModalApp(App[None]):
    """Harness that opens the login modal directly and records its result."""

    def __init__(self: Self, gateway: FakeSsoLoginGateway) -> None:
        super().__init__()
        self.gateway = gateway
        self.results: list[bool | None] = []

    def on_mount(self: Self) -> None:
        self.push_screen(SsoLoginScreen(self.gateway, make_sso_config()), self.results.append)


async def _until_dismissed(app: SsoModalApp, pilot: Pilot[None]) -> None:
    """Let the two chained workers (start, poll) run to completion."""
    for _ in range(100):
        await app.workers.wait_for_complete()
        await pilot.pause()
        if app.results:
            return
    pytest.fail("modal never dismissed")


async def _until_code_shown(app: SsoModalApp, pilot: Pilot[None]) -> None:
    for _ in range(100):
        await pilot.pause()
        if str(app.screen.query_one("#code", Static).content):
            return
    pytest.fail("device code never rendered")


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


@pytest.mark.asyncio
async def test_successful_login_caches_token_and_dismisses_true() -> None:
    gateway = FakeSsoLoginGateway(pending_polls=2)
    app = SsoModalApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert app.results == [True]
    assert gateway.poll_calls == 3
    assert len(gateway.cached) == 1


@pytest.mark.asyncio
async def test_expired_authorization_dismisses_false_without_polling() -> None:
    gateway = FakeSsoLoginGateway(authorization=make_device_authorization(expires_in_s=-1))
    app = SsoModalApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert app.results == [False]
    assert gateway.poll_calls == 0
    assert gateway.cached == []


@pytest.mark.asyncio
async def test_start_failure_notifies_and_dismisses_false(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        toasts.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    gateway = FakeSsoLoginGateway(start_error=AwsError("denied"))
    app = SsoModalApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert app.results == [False]
    assert toasts == ["denied"]


@pytest.mark.asyncio
async def test_poll_failure_dismisses_false() -> None:
    gateway = FakeSsoLoginGateway(poll_error=AwsError("expired"))
    app = SsoModalApp(gateway)

    async with app.run_test() as pilot:
        await _until_dismissed(app, pilot)

    assert app.results == [False]
    assert gateway.cached == []


@pytest.mark.asyncio
async def test_escape_cancels_and_dismisses_false() -> None:
    gateway = FakeSsoLoginGateway(authorization=make_device_authorization(interval=60), pending_polls=10**6)
    app = SsoModalApp(gateway)

    async with app.run_test() as pilot:
        await _until_code_shown(app, pilot)
        await pilot.press("escape")
        await pilot.pause()

    assert app.results == [False]
    assert gateway.cached == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_sso_login_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'awst.screens.sso_login'`

- [ ] **Step 4: Implement the modal**

Create `src/awst/screens/sso_login.py`:

```python
"""Modal that walks the user through an AWS SSO device login."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
import time
from typing import TYPE_CHECKING, ClassVar, Protocol, Self, cast
import webbrowser

from textual import work
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Static
from textual.worker import WorkerState, get_current_worker

from awst.aws.models import AwsError, DeviceAuthorization, SlowDownError, SsoToken

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.binding import BindingType
    from textual.worker import Worker

    from awst.aws.models import SsoConfig

_SLOW_DOWN_INCREMENT_S = 5
_CANCEL_POLL_S = 0.1


class SsoAuthorizer(Protocol):
    """The slice of the SSO login gateway this screen needs."""

    def start_device_authorization(self: Self, config: SsoConfig) -> DeviceAuthorization: ...

    def poll_token(self: Self, authorization: DeviceAuthorization) -> SsoToken | None: ...

    def write_token_cache(
        self: Self,
        config: SsoConfig,
        authorization: DeviceAuthorization,
        token: SsoToken,
    ) -> None: ...


class SsoLoginScreen(ModalScreen[bool]):
    """Run the SSO OIDC device flow; dismisses True once a token is cached."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "cancel", "Cancel")]

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
    #title { text-style: bold; }
    #status { color: $text-muted; margin-top: 1; }
    #code { text-style: bold; margin-top: 1; }
    """

    def __init__(self: Self, gateway: SsoAuthorizer, config: SsoConfig) -> None:
        super().__init__()
        self._gateway = gateway
        self._config = config
        self._authorization: DeviceAuthorization | None = None

    def compose(self: Self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("AWS SSO login", id="title")
            yield Static("Contacting AWS SSO…", id="status")
            yield Static(id="code")
            yield Static(id="url")
        yield Footer()

    def on_mount(self: Self) -> None:
        self._start_authorization()

    @work(thread=True, exclusive=True, group="sso-start", exit_on_error=False)
    def _start_authorization(self: Self) -> DeviceAuthorization:
        return self._gateway.start_device_authorization(self._config)

    @work(thread=True, exclusive=True, group="sso-poll", exit_on_error=False)
    def _await_token(self: Self, authorization: DeviceAuthorization) -> SsoToken:
        worker = get_current_worker()
        interval = authorization.interval
        while not worker.is_cancelled:
            if datetime.now(tz=UTC) >= authorization.expires_at:
                message = "The login request expired before it was approved."
                raise AwsError(message, hint="Press l to start again.")
            self._sleep(worker, interval)
            try:
                token = self._gateway.poll_token(authorization)
            except SlowDownError:
                interval += _SLOW_DOWN_INCREMENT_S
                continue
            if token is not None:
                return token
        message = "The login was cancelled."  # unreachable for the UI: cancellation discards the worker's result
        raise AwsError(message)

    def _sleep(self: Self, worker: Worker[SsoToken], seconds: int) -> None:
        deadline = time.monotonic() + seconds
        while not worker.is_cancelled and time.monotonic() < deadline:
            time.sleep(_CANCEL_POLL_S)

    def on_worker_state_changed(self: Self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.SUCCESS and event.worker.name == "_start_authorization":
            self._show_authorization(cast("DeviceAuthorization", event.worker.result))
        elif event.state == WorkerState.SUCCESS and event.worker.name == "_await_token":
            self._finish_login(cast("SsoToken", event.worker.result))
        elif event.state == WorkerState.ERROR:
            error = event.worker.error
            if isinstance(error, AwsError):
                self._fail(error)
            elif error is not None:
                raise error

    def _show_authorization(self: Self, authorization: DeviceAuthorization) -> None:
        self._authorization = authorization
        self.query_one("#status", Static).update("Approve the request in your browser, then return here.")
        self.query_one("#code", Static).update(f"Code: {authorization.user_code}")
        self.query_one("#url", Static).update(authorization.verification_uri_complete)
        with contextlib.suppress(webbrowser.Error):  # the URL on screen is enough (e.g. headless, SSH)
            webbrowser.open(authorization.verification_uri_complete)
        self._await_token(authorization)

    def _finish_login(self: Self, token: SsoToken) -> None:
        if self._authorization is None:  # pragma: no cover — polling only starts after authorization
            return
        try:
            self._gateway.write_token_cache(self._config, self._authorization, token)
        except OSError as error:
            message = f"Could not write the SSO token cache: {error}"
            self._fail(AwsError(message))
            return
        self.dismiss(result=True)

    def _fail(self: Self, error: AwsError) -> None:
        message = error.message if error.hint is None else f"{error.message} ({error.hint})"
        self.notify(message, title="Login failed", severity="error")
        self.dismiss(result=False)

    def action_cancel(self: Self) -> None:
        self.dismiss(result=False)
```

Note the start-failure test asserts `toasts == ["denied"]` — `_fail` passes the composed message, and for a hint-less error that is just `error.message`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_sso_login_screen.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Lint and commit**

```bash
make format && make lint
git add src/awst/screens/sso_login.py tests/fakes.py tests/test_sso_login_screen.py
git commit -m "Add SSO device-login modal screen"
```

---

### Task 7: Login binding on list screens and app wiring

**Files:**
- Modify: `src/awst/screens/resource_list.py`
- Modify: `src/awst/app.py`
- Test: `tests/test_resource_list_login.py`

**Interfaces:**
- Consumes: `CredentialsError` (Task 1), `profiles.sso_config`/`active_profile` (Task 2), `SsoLoginGateway` (Tasks 3–4), `SsoLoginScreen`/`SsoAuthorizer` (Task 6), `FakeSsoLoginGateway` (Task 6).
- Produces:
  - `AwstApp(..., sso_gateway_factory: Callable[[SsoConfig], SsoAuthorizer] | None = None)`.
  - `AwstApp.sso_login_possible` (property, `bool`) and `AwstApp.make_sso_login_screen() -> SsoLoginScreen` — the duck-typed seam `ResourceListScreen` reads via `getattr`, so plain harness apps keep working.
  - `ResourceListScreen`: `l` — Login binding (visible only after a `CredentialsError` when login is possible), error-panel line `Press l to log in via AWS SSO.`, refresh after a successful login.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resource_list_login.py`:

```python
"""Tests for the SSO login binding on resource list screens."""

import os
from pathlib import Path
from typing import Self
import webbrowser

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from awst.app import AwstApp
from awst.aws.models import AwsError, CredentialsError
from awst.screens.buckets import BucketListScreen
from awst.screens.sso_login import SsoLoginScreen
from tests.fakes import FakeS3Gateway, FakeSsoLoginGateway, make_bucket, make_device_authorization

_SSO_CONFIG = """\
[profile dev]
sso_start_url = https://legacy.awsapps.com/start
sso_region = eu-west-1
"""

_PLAIN_CONFIG = """\
[profile dev]
region = eu-west-1
"""


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webbrowser, "open", lambda _url: True)


def _activate_profile(monkeypatch: pytest.MonkeyPatch, config: str) -> None:
    Path(os.environ["AWS_CONFIG_FILE"]).write_text(config)
    monkeypatch.setenv("AWS_PROFILE", "dev")


# Amended during execution: subclassing AwstApp to override on_mount does not work —
# Textual dispatches on_mount from every class in the MRO, so AwstApp.on_mount still
# runs and pushes HomeScreen. Tests instead use plain AwstApp and push
# BucketListScreen(app.s3_gateway) from the test body after landing on HomeScreen.


class PlainBucketApp(App[None]):
    """Harness without the SSO seam, like any third-party App."""

    def __init__(self: Self, gateway: FakeS3Gateway) -> None:
        super().__init__()
        self.gateway = gateway

    def on_mount(self: Self) -> None:
        self.push_screen(BucketListScreen(self.gateway))


def _panel_text(app: App[None]) -> str:
    return str(app.screen.query_one("#error", Static).content)


@pytest.mark.asyncio
async def test_login_recovers_from_credential_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _activate_profile(monkeypatch, _SSO_CONFIG)
    sso_gateway = FakeSsoLoginGateway()
    gateway = FakeS3Gateway(error=CredentialsError("token expired", hint="log in"))
    app = BucketLoginApp(s3_gateway=gateway, sso_gateway_factory=lambda _config: sso_gateway)

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Press l to log in via AWS SSO." in _panel_text(app)

        gateway.error = None
        gateway.buckets = [make_bucket("assets")]
        await pilot.press("l")
        for _ in range(100):
            await app.workers.wait_for_complete()
            await pilot.pause()
            if gateway.calls > 1:
                break

        assert len(sso_gateway.cached) == 1
        assert isinstance(app.screen, BucketListScreen)
        assert app.screen.query_one(DataTable).row_count == 1

        # after a successful load the binding is gone again
        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, BucketListScreen)


@pytest.mark.asyncio
async def test_login_binding_appears_after_refresh_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _activate_profile(monkeypatch, _SSO_CONFIG)
    sso_gateway = FakeSsoLoginGateway(authorization=make_device_authorization(interval=60), pending_polls=10**6)
    gateway = FakeS3Gateway(buckets=[make_bucket("assets")])
    app = BucketLoginApp(s3_gateway=gateway, sso_gateway_factory=lambda _config: sso_gateway)

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()

        gateway.error = CredentialsError("token expired")
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()

        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, SsoLoginScreen)


@pytest.mark.asyncio
async def test_no_login_for_non_sso_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    _activate_profile(monkeypatch, _PLAIN_CONFIG)
    gateway = FakeS3Gateway(error=CredentialsError("no creds"))
    app = BucketLoginApp(s3_gateway=gateway)

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Press l" not in _panel_text(app)

        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, BucketListScreen)


@pytest.mark.asyncio
async def test_no_login_for_non_credential_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _activate_profile(monkeypatch, _SSO_CONFIG)
    gateway = FakeS3Gateway(error=AwsError("throttled"))
    app = BucketLoginApp(s3_gateway=gateway)

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Press l" not in _panel_text(app)

        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, BucketListScreen)


@pytest.mark.asyncio
async def test_no_login_without_app_support() -> None:
    gateway = FakeS3Gateway(error=CredentialsError("no creds"))
    app = PlainBucketApp(gateway)

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Press l" not in _panel_text(app)

        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, BucketListScreen)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/test_resource_list_login.py -v`
Expected: FAIL — `TypeError: AwstApp.__init__() got an unexpected keyword argument 'sso_gateway_factory'`

- [ ] **Step 3: Extend `AwstApp`**

In `src/awst/app.py`:

Imports (runtime):

```python
from awst.aws.sso import SsoLoginGateway
from awst.screens.sso_login import SsoLoginScreen
```

Type-checking imports:

```python
if TYPE_CHECKING:
    from collections.abc import Callable

    from awst.aws.models import SsoConfig
    from awst.screens.buckets import BucketLister
    from awst.screens.functions import FunctionLister
    from awst.screens.queues import QueueLister
    from awst.screens.sso_login import SsoAuthorizer
    from awst.screens.stacks import StackGateway
```

Constructor — add the parameter and assignment:

```python
    def __init__(
        self: Self,
        cloudformation_gateway: StackGateway | None = None,
        s3_gateway: BucketLister | None = None,
        lambda_gateway: FunctionLister | None = None,
        sqs_gateway: QueueLister | None = None,
        sso_gateway_factory: Callable[[SsoConfig], SsoAuthorizer] | None = None,
    ) -> None:
        super().__init__()
        self._cloudformation_gateway = cloudformation_gateway
        self._s3_gateway = s3_gateway
        self._lambda_gateway = lambda_gateway
        self._sqs_gateway = sqs_gateway
        self._sso_gateway_factory = sso_gateway_factory
```

New members (below the gateway properties):

```python
@property
def sso_login_possible(self: Self) -> bool:
    """Whether the active profile has SSO settings to log in with."""
    return profiles.sso_config(profiles.active_profile()) is not None


def make_sso_login_screen(self: Self) -> SsoLoginScreen:
    """A login modal for the active profile; only valid when sso_login_possible."""
    config = profiles.sso_config(profiles.active_profile())
    if config is None:
        message = "the active profile has no SSO configuration"
        raise RuntimeError(message)
    if self._sso_gateway_factory is not None:
        return SsoLoginScreen(self._sso_gateway_factory(config), config)
    session = boto3.Session()
    client = session.client("sso-oidc", region_name=config.sso_region)
    return SsoLoginScreen(SsoLoginGateway(client), config)
```

- [ ] **Step 4: Extend `ResourceListScreen`**

In `src/awst/screens/resource_list.py`:

Import `CredentialsError`:

```python
from awst.aws.models import AwsError, CredentialsError
```

Add the binding:

```python
    BINDINGS: ClassVar[list[BindingType]] = [
        ("escape", "back_or_clear", "Back"),
        ("r", "refresh", "Refresh"),
        ("slash", "focus_filter", "Filter"),
        ("l", "login", "Login"),
    ]
```

Initialize the flag in `__init__`:

```python
    def __init__(self: Self) -> None:
        super().__init__()
        self._all_items: list[ItemT] = []
        self._loaded = False
        self._show_login = False
```

Hide the binding unless a credential failure is showing (new method):

```python
    def check_action(self: Self, action: str, parameters: tuple[object, ...]) -> bool | None:  # noqa: ARG002
        if action == "login":
            return self._show_login
        return True
```

Reset the flag on a successful load — the `WorkerState.SUCCESS` branch of `on_worker_state_changed` becomes:

```python
        if event.state == WorkerState.SUCCESS:
            self._show_login = False
            self.refresh_bindings()
            was_loaded = self._loaded
            self._loaded = True
            self._all_items = event.worker.result or []
            table = self.query_one("#items", DataTable)
            table.loading = False
            self._render_rows()
            if not was_loaded:
                table.focus()
```

Set the flag and render the hint in `_show_error` (full replacement) plus a helper:

```python
def _show_error(self: Self, error: AwsError) -> None:
    self._show_login = isinstance(error, CredentialsError) and bool(getattr(self.app, "sso_login_possible", False))
    self.refresh_bindings()
    if self._loaded:
        message = error.message if error.hint is None else f"{error.message} ({error.hint})"
        self.notify(message, title="Refresh failed", severity="error")
        self._render_rows()  # restores the count text over "refreshing…"
        return
    table = self.query_one("#items", DataTable)
    table.loading = False
    table.display = False
    self.query_one("#filter", Input).display = False
    self.query_one("#count", Static).display = False
    self.set_focus(None)
    panel = self.query_one("#error", Static)
    panel.update(self._error_text(error))
    panel.display = True


def _error_text(self: Self, error: AwsError) -> str:
    text = error.message if error.hint is None else f"{error.message}\n{error.hint}"
    if self._show_login:
        text += "\nPress l to log in via AWS SSO."
    return text
```

Launch the modal (new methods at the end of the class):

```python
def action_login(self: Self) -> None:
    factory = getattr(self.app, "make_sso_login_screen", None)
    if factory is None:
        return
    self.app.push_screen(factory(), self._on_login_finished)


def _on_login_finished(self: Self, logged_in: bool | None) -> None:  # noqa: FBT001
    if logged_in:
        self.action_refresh()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/test_resource_list_login.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Lint, full suite, commit**

```bash
make format && make lint && make unit
git add src/awst/app.py src/awst/screens/resource_list.py tests/test_resource_list_login.py
git commit -m "Offer SSO login from credential failures on list screens"
```

---

### Task 8: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Full local check (mirrors CI)**

Run: `make test`
Expected: ruff check, ruff format --check, ty check all clean; entire pytest suite passes.

- [ ] **Step 2: Coverage**

Run: `make coverage`
Expected: PASS with total coverage ≥75%.

- [ ] **Step 3: Manual smoke test (optional but recommended)**

Run `uv run awst` in a terminal with no `AWS_PROFILE` set: the profile picker should list the profiles from `~/.aws/config`, selection should land on the service menu with the profile in the header. With an expired SSO token, opening a service list should show the error panel with the login hint; `l` should show the device code and open the browser.

- [ ] **Step 4: Commit any stragglers and stop**

The branch is ready for review/merge (use superpowers:finishing-a-development-branch).
