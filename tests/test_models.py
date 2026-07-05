"""Tests for the AWS data models."""

from datetime import UTC, datetime

import pytest

from awst.aws.models import (
    AwsError,
    StackDetail,
    StackNotFoundError,
    StackSummary,
)


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


def test_stack_not_found_error_is_an_aws_error() -> None:
    error = StackNotFoundError("Stack alpha does not exist.")

    assert isinstance(error, AwsError)
    assert error.message == "Stack alpha does not exist."
    assert error.hint is None


def test_stack_detail_is_immutable() -> None:
    detail = StackDetail(
        name="alpha",
        stack_id="arn:aws:cloudformation:eu-west-1:123456789012:stack/alpha/abc",
        status="CREATE_COMPLETE",
        status_reason=None,
        description=None,
        created=datetime(2026, 1, 1, tzinfo=UTC),
        updated=datetime(2026, 1, 1, tzinfo=UTC),
        parameters=(),
        outputs=(),
        resources=(),
        events=(),
    )

    with pytest.raises(AttributeError):
        detail.status = "DELETE_COMPLETE"  # type: ignore[misc]  # ty: ignore[invalid-assignment]
