"""Plain data models and errors for the AWS layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from datetime import datetime


class AwsError(Exception):
    """A user-presentable AWS failure with an optional remediation hint."""

    def __init__(self: Self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class StackNotFoundError(AwsError):
    """The named stack does not exist (for example, it finished deleting)."""


@dataclass(frozen=True, slots=True)
class StackSummary:
    """A CloudFormation stack, reduced to what the UI needs."""

    name: str
    status: str
    created: datetime
    updated: datetime
    description: str | None


@dataclass(frozen=True, slots=True)
class StackParameter:
    """One parameter the stack was created or updated with."""

    key: str
    value: str


@dataclass(frozen=True, slots=True)
class StackOutput:
    """One output exported by the stack."""

    key: str
    value: str
    description: str | None


@dataclass(frozen=True, slots=True)
class StackResource:
    """One resource managed by the stack."""

    logical_id: str
    physical_id: str | None
    resource_type: str
    status: str


@dataclass(frozen=True, slots=True)
class StackEvent:
    """One entry from the stack's event history."""

    timestamp: datetime
    logical_id: str
    resource_type: str
    status: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class StackDetail:
    """Everything the detail screen shows about one stack."""

    name: str
    stack_id: str
    status: str
    status_reason: str | None
    description: str | None
    created: datetime
    updated: datetime
    parameters: tuple[StackParameter, ...]
    outputs: tuple[StackOutput, ...]
    resources: tuple[StackResource, ...]
    events: tuple[StackEvent, ...]
