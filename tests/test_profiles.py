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
