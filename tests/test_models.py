"""Tests for the AWS data models."""

from datetime import UTC, datetime

import pytest

from awst.aws.models import AwsError, StackSummary


def test_aws_error_carries_message_and_hint() -> None:
    error = AwsError("access denied", hint="check your IAM role")

    assert str(error) == "access denied"
    assert error.message == "access denied"
    assert error.hint == "check your IAM role"


def test_aws_error_hint_defaults_to_none() -> None:
    assert AwsError("boom").hint is None


def test_stack_summary_is_immutable() -> None:
    stack = StackSummary(
        name="stack-a",
        status="CREATE_COMPLETE",
        created=datetime(2026, 1, 1, tzinfo=UTC),
        updated=datetime(2026, 1, 2, tzinfo=UTC),
        description=None,
    )

    with pytest.raises(AttributeError):
        stack.name = "other"  # type: ignore[misc]  # ty: ignore[invalid-assignment]
