"""AWS region discovery and selection."""

import os

import boto3


def active_region() -> str | None:
    """The region the default credential chain resolves right now, or None when unset."""
    return boto3.Session().region_name


def available_regions() -> list[str]:
    """Every standard-partition region, from botocore's bundled endpoint data (no network)."""
    return sorted(boto3.Session().get_available_regions("ec2"))


def select_region(name: str) -> None:
    """Make name the process-wide region; gateways rebuilt after reset_gateways pick it up."""
    os.environ["AWS_DEFAULT_REGION"] = name
