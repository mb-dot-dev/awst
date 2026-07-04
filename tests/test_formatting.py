"""Tests for presentation formatting helpers."""

from datetime import UTC, datetime, timedelta

import pytest

from awst.screens.formatting import relative_age, status_style

NOW = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(seconds=30), "just now"),
        (timedelta(minutes=5), "5m ago"),
        (timedelta(hours=2), "2h ago"),
        (timedelta(days=3), "3d ago"),
        (timedelta(days=400), "400d ago"),
    ],
)
def test_relative_age(age: timedelta, expected: str) -> None:
    assert relative_age(NOW - age, NOW) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("CREATE_COMPLETE", "green"),
        ("UPDATE_COMPLETE", "green"),
        ("UPDATE_IN_PROGRESS", "yellow"),
        ("CREATE_FAILED", "red"),
        ("ROLLBACK_IN_PROGRESS", "red"),
        ("UPDATE_ROLLBACK_COMPLETE", "red"),
        ("REVIEW_IN_PROGRESS", "yellow"),
    ],
)
def test_status_style(status: str, expected: str) -> None:
    assert status_style(status) == expected
