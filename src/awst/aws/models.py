"""Plain data models and errors for the AWS layer."""

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


class CredentialsError(AwsError):
    """AWS credentials are missing or expired; logging in may fix it."""


class StackNotFoundError(AwsError):
    """The named stack does not exist (for example, it finished deleting)."""


class SlowDownError(Exception):
    """The SSO OIDC service asked us to poll less often; wait longer and retry."""


@dataclass(frozen=True, slots=True)
class SsoConfig:
    """The SSO settings a profile uses to log in.

    session_name is set for [sso-session] profiles and None for legacy inline
    sso_* profiles; the token-cache format differs between the two.
    """

    start_url: str
    sso_region: str
    session_name: str | None


@dataclass(frozen=True, slots=True)
class DeviceAuthorization:
    """One in-flight SSO OIDC device authorization, plus its client registration."""

    client_id: str
    client_secret: str
    registration_expires_at: datetime
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    interval: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SsoToken:
    """An SSO access token minted by the device flow."""

    access_token: str
    expires_at: datetime
    refresh_token: str | None


@dataclass(frozen=True, slots=True)
class BucketSummary:
    """An S3 bucket, reduced to what the UI needs."""

    name: str
    region: str
    created: datetime


@dataclass(frozen=True, slots=True)
class ObjectSummary:
    """An S3 object, reduced to what the UI needs."""

    key: str  # the full key, including any prefix
    size: int  # bytes
    modified: datetime


@dataclass(frozen=True, slots=True)
class ObjectPage:
    """One page of one prefix level of a bucket listing."""

    folders: tuple[str, ...]  # common prefixes, each ending "/"
    objects: tuple[ObjectSummary, ...]
    continuation_token: str | None  # None when this is the last page


@dataclass(frozen=True, slots=True)
class FunctionSummary:
    """A Lambda function, reduced to what the UI needs."""

    name: str
    runtime: str  # "" for container-image functions
    memory_mb: int
    timeout_s: int
    modified: datetime


@dataclass(frozen=True, slots=True)
class QueueSummary:
    """An SQS queue, reduced to what the UI needs."""

    name: str
    is_fifo: bool


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
