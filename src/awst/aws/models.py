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


@dataclass(frozen=True, slots=True)
class StackSummary:
    """A CloudFormation stack, reduced to what the UI needs."""

    name: str
    status: str
    created: datetime
    updated: datetime
    description: str | None
