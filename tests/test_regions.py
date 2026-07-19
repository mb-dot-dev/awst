"""Tests for AWS region discovery and selection."""

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from awst.aws import regions


def test_active_region_reads_the_environment() -> None:
    # conftest sets AWS_DEFAULT_REGION=eu-west-1 for every test
    assert regions.active_region() == "eu-west-1"


def test_active_region_is_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_DEFAULT_REGION")

    assert regions.active_region() is None


def test_available_regions_are_sorted_and_include_the_majors() -> None:
    names = regions.available_regions()

    assert names == sorted(names)
    assert "eu-west-1" in names
    assert "us-east-1" in names


def test_select_region_sets_the_environment() -> None:
    regions.select_region("ap-southeast-2")

    assert os.environ["AWS_DEFAULT_REGION"] == "ap-southeast-2"
