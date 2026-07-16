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
