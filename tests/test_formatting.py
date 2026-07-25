"""Tests for presentation formatting helpers."""

from datetime import UTC, datetime, timedelta

import pytest

from awst.screens.formatting import human_size, mask_value, relative_age, status_style

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


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1048575, "1.0 MB"),
        (1048576, "1.0 MB"),
        (1073741823, "1.0 GB"),
        (5 * 1024**3, "5.0 GB"),
        (2 * 1024**4, "2.0 TB"),
        (1024**5, "1.0 PB"),
    ],
)
def test_human_size(size: int, expected: str) -> None:
    assert human_size(size) == expected


def test_mask_value_hides_a_secure_string_when_not_revealed() -> None:
    assert mask_value("s3cret", "SecureString", revealed=False) == "••••••••"


def test_mask_value_shows_a_secure_string_when_revealed() -> None:
    assert mask_value("s3cret", "SecureString", revealed=True) == "s3cret"


def test_mask_value_never_masks_plain_types() -> None:
    assert mask_value("postgres://db", "String", revealed=False) == "postgres://db"
    assert mask_value("a,b,c", "StringList", revealed=False) == "a,b,c"


def test_mask_value_mask_width_does_not_depend_on_value_length() -> None:
    short = mask_value("x", "SecureString", revealed=False)
    long = mask_value("x" * 200, "SecureString", revealed=False)

    assert short == long
