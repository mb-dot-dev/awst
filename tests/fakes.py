"""Test fakes and model factories for AWS gateways."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

from awst.aws.models import (
    StackDetail,
    StackEvent,
    StackNotFoundError,
    StackOutput,
    StackParameter,
    StackResource,
    StackSummary,
)

if TYPE_CHECKING:
    from awst.aws.models import AwsError

_CREATED = datetime(2026, 1, 1, tzinfo=UTC)
_PARAMETERS = (StackParameter(key="Env", value="prod"),)
_OUTPUTS = (StackOutput(key="Url", value="https://example.com", description="endpoint"),)


def make_stack(name: str, status: str = "CREATE_COMPLETE") -> StackSummary:
    """A stack summary with sensible defaults for list-screen tests."""
    return StackSummary(name=name, status=status, created=_CREATED, updated=_CREATED, description=None)


def make_detail(
    parameters: tuple[StackParameter, ...] = _PARAMETERS,
    outputs: tuple[StackOutput, ...] = _OUTPUTS,
) -> StackDetail:
    """A fully populated stack detail named "alpha" for detail-screen tests."""
    return StackDetail(
        name="alpha",
        stack_id="arn:aws:cloudformation:eu-west-1:123456789012:stack/alpha/abc",
        status="CREATE_COMPLETE",
        status_reason=None,
        description="a test stack",
        created=_CREATED,
        updated=_CREATED,
        parameters=parameters,
        outputs=outputs,
        resources=(
            StackResource(
                logical_id="Topic",
                physical_id="arn:aws:sns:eu-west-1:123456789012:topic",
                resource_type="AWS::SNS::Topic",
                status="CREATE_COMPLETE",
            ),
        ),
        events=(
            StackEvent(
                timestamp=_CREATED,
                logical_id="alpha",
                resource_type="AWS::CloudFormation::Stack",
                status="CREATE_COMPLETE",
                reason=None,
            ),
            StackEvent(
                timestamp=_CREATED,
                logical_id="Topic",
                resource_type="AWS::SNS::Topic",
                status="CREATE_IN_PROGRESS",
                reason="Resource creation Initiated",
            ),
        ),
    )


class FakeCloudFormationGateway:
    """In-memory stand-in for the real CloudFormation gateway."""

    def __init__(
        self: Self,
        stacks: list[StackSummary] | None = None,
        error: AwsError | None = None,
        detail: StackDetail | None = None,
        detail_error: AwsError | None = None,
        delete_error: AwsError | None = None,
    ) -> None:
        self.stacks = stacks or []
        self.error = error
        self.detail = detail
        self.detail_error = detail_error
        self.delete_error = delete_error
        self.calls = 0
        self.detail_calls: list[str] = []
        self.deleted: list[str] = []

    def list_stacks(self: Self) -> list[StackSummary]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.stacks)

    def get_stack_detail(self: Self, name: str) -> StackDetail:
        self.detail_calls.append(name)
        if self.detail_error is not None:
            raise self.detail_error
        if self.detail is None:
            message = f"Stack {name} does not exist."
            raise StackNotFoundError(message)
        return self.detail

    def delete_stack(self: Self, name: str) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(name)
