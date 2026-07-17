"""Tests for the SSO OIDC login gateway."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import re
from typing import TYPE_CHECKING

import boto3
from botocore.stub import Stubber
import pytest

from awst.aws.models import AwsError, SlowDownError
from awst.aws.sso import SsoLoginGateway
from tests.fakes import make_device_authorization, make_sso_config, make_sso_token

if TYPE_CHECKING:
    from pathlib import Path

_ISO_UTC = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"

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
    assert authorization.client_secret == "client-secret"
    assert authorization.registration_expires_at == datetime.fromtimestamp(1893456000, tz=UTC)
    assert authorization.device_code == "device-code"
    assert authorization.user_code == "ABCD-EFGH"
    assert authorization.verification_uri_complete.endswith("user_code=ABCD-EFGH")
    assert authorization.interval == 5
    assert authorization.expires_at > datetime.now(tz=UTC)


def test_start_device_authorization_clamps_a_zero_interval() -> None:
    client = _client()
    with Stubber(client) as stubber:
        stubber.add_response("register_client", _REGISTER_RESPONSE, _REGISTER_EXPECTED)
        stubber.add_response("start_device_authorization", _DEVICE_RESPONSE | {"interval": 0}, _DEVICE_EXPECTED)

        authorization = SsoLoginGateway(client).start_device_authorization(make_sso_config())

    assert authorization.interval == 1


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
    assert token.access_token == "access-token"
    assert token.refresh_token == "refresh"
    assert token.expires_at > datetime.now(tz=UTC)


def test_poll_token_refresh_token_is_none_when_absent() -> None:
    client = _client()
    response = {"accessToken": "access-token", "tokenType": "Bearer", "expiresIn": 28800}
    with Stubber(client) as stubber:
        stubber.add_response("create_token", response, _TOKEN_EXPECTED)

        token = SsoLoginGateway(client).poll_token(make_device_authorization())

    assert token is not None
    assert token.refresh_token is None


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
    assert entry["clientSecret"] == "client-secret"
    assert re.fullmatch(_ISO_UTC, entry["registrationExpiresAt"])
    assert entry["refreshToken"] == "refresh"


def test_write_token_cache_omits_refresh_token_when_absent(tmp_path: Path) -> None:
    gateway = SsoLoginGateway(_client(), cache_dir=tmp_path)
    config = make_sso_config(session_name="corp")

    gateway.write_token_cache(config, make_device_authorization(), make_sso_token())

    expected_name = hashlib.sha1(b"corp").hexdigest() + ".json"  # noqa: S324
    entry = json.loads((tmp_path / expected_name).read_text())
    assert "refreshToken" not in entry


def test_write_token_cache_file_is_owner_only(tmp_path: Path) -> None:
    gateway = SsoLoginGateway(_client(), cache_dir=tmp_path)

    gateway.write_token_cache(make_sso_config(), make_device_authorization(), make_sso_token())

    path = next(tmp_path.iterdir())
    assert path.stat().st_mode & 0o777 == 0o600


def test_write_token_cache_tightens_an_existing_looser_cache_file(tmp_path: Path) -> None:
    config = make_sso_config()
    existing = tmp_path / (hashlib.sha1(config.start_url.encode()).hexdigest() + ".json")  # noqa: S324
    existing.write_text("{}")
    existing.chmod(0o644)
    gateway = SsoLoginGateway(_client(), cache_dir=tmp_path)

    gateway.write_token_cache(config, make_device_authorization(), make_sso_token())

    assert existing.stat().st_mode & 0o777 == 0o600
    assert json.loads(existing.read_text())["accessToken"] == "access-token"


def test_write_token_cache_creates_the_cache_directory(tmp_path: Path) -> None:
    cache_dir = tmp_path / "sso" / "cache"
    gateway = SsoLoginGateway(_client(), cache_dir=cache_dir)

    gateway.write_token_cache(make_sso_config(), make_device_authorization(), make_sso_token())

    assert len(list(cache_dir.iterdir())) == 1
