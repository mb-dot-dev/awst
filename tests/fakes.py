"""Test fakes and model factories for AWS gateways."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Self

from awst.aws.models import (
    BucketSummary,
    DeviceAuthorization,
    FunctionSummary,
    ObjectPage,
    ObjectSummary,
    Page,
    QueueSummary,
    SsoConfig,
    SsoToken,
    StackDetail,
    StackEvent,
    StackNotFoundError,
    StackOutput,
    StackParameter,
    StackResource,
    StackSummary,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    import threading

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

    def __init__(  # noqa: PLR0913
        self: Self,
        stacks: list[StackSummary] | None = None,
        error: AwsError | None = None,
        detail: StackDetail | None = None,
        detail_error: AwsError | None = None,
        delete_error: AwsError | None = None,
        pages: dict[str | None, Page[StackSummary]] | None = None,
    ) -> None:
        self.stacks = stacks or []
        self.error = error
        self.detail = detail
        self.detail_error = detail_error
        self.delete_error = delete_error
        self.pages = pages
        self.calls = 0
        self.next_tokens: list[str | None] = []
        self.detail_calls: list[str] = []
        self.deleted: list[str] = []

    def list_stacks(self: Self, next_token: str | None = None) -> Page[StackSummary]:
        self.calls += 1
        self.next_tokens.append(next_token)
        if self.error is not None:
            raise self.error
        if self.pages is not None:
            return self.pages.get(next_token, Page(items=(), next_token=None))
        return Page(items=tuple(self.stacks), next_token=None)

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


def make_bucket(name: str, region: str = "eu-west-1") -> BucketSummary:
    """A bucket summary with sensible defaults for list-screen tests."""
    return BucketSummary(name=name, region=region, created=_CREATED)


def make_object(key: str, size: int = 2048) -> ObjectSummary:
    """An object summary with sensible defaults for list-screen tests."""
    return ObjectSummary(key=key, size=size, modified=_CREATED)


class FakeS3Gateway:
    """In-memory stand-in for the real S3 gateway."""

    def __init__(  # noqa: PLR0913
        self: Self,
        buckets: list[BucketSummary] | None = None,
        error: AwsError | None = None,
        bucket_pages: dict[str | None, Page[BucketSummary]] | None = None,
        empty_batches: list[int] | None = None,
        empty_error: AwsError | None = None,
        empty_gate: threading.Event | None = None,
        object_pages: dict[tuple[str, str | None], ObjectPage] | None = None,
        objects_error: AwsError | None = None,
        objects_gate: threading.Event | None = None,
    ) -> None:
        self.buckets = buckets or []
        self.error = error
        self.bucket_pages = bucket_pages
        self.empty_batches = empty_batches or []
        self.empty_error = empty_error
        self.empty_gate = empty_gate
        self.object_pages = object_pages or {}
        self.objects_error = objects_error
        self.objects_gate = objects_gate
        self.object_calls: list[tuple[str, str, str, str | None]] = []
        self.calls = 0
        self.next_tokens: list[str | None] = []
        self.emptied: list[tuple[str, str]] = []

    def list_buckets(self: Self, next_token: str | None = None) -> Page[BucketSummary]:
        self.calls += 1
        self.next_tokens.append(next_token)
        if self.error is not None:
            raise self.error
        if self.bucket_pages is not None:
            return self.bucket_pages.get(next_token, Page(items=(), next_token=None))
        return Page(items=tuple(self.buckets), next_token=None)

    def list_objects(
        self: Self,
        bucket: str,
        region: str,
        prefix: str = "",
        continuation_token: str | None = None,
    ) -> ObjectPage:
        self.object_calls.append((bucket, region, prefix, continuation_token))
        if self.objects_error is not None:
            raise self.objects_error
        if continuation_token is not None and self.objects_gate is not None:
            self.objects_gate.wait(timeout=5)  # lets tests freeze a load-more mid-flight
        empty = ObjectPage(folders=(), objects=(), continuation_token=None)
        return self.object_pages.get((prefix, continuation_token), empty)

    def empty_bucket(self: Self, name: str, region: str) -> Iterator[int]:
        self.emptied.append((name, region))
        for index, count in enumerate(self.empty_batches):
            if index > 0 and self.empty_gate is not None:
                self.empty_gate.wait(timeout=5)  # lets tests freeze the worker mid-delete
            yield count
        if self.empty_error is not None:
            raise self.empty_error


def make_function(name: str, runtime: str = "python3.14") -> FunctionSummary:
    """A function summary with sensible defaults for list-screen tests."""
    return FunctionSummary(name=name, runtime=runtime, memory_mb=128, timeout_s=30, modified=_CREATED)


class FakeLambdaGateway:
    """In-memory stand-in for the real Lambda gateway."""

    def __init__(
        self: Self,
        functions: list[FunctionSummary] | None = None,
        error: AwsError | None = None,
        pages: dict[str | None, Page[FunctionSummary]] | None = None,
    ) -> None:
        self.functions = functions or []
        self.error = error
        self.pages = pages
        self.calls = 0
        self.next_tokens: list[str | None] = []

    def list_functions(self: Self, next_token: str | None = None) -> Page[FunctionSummary]:
        self.calls += 1
        self.next_tokens.append(next_token)
        if self.error is not None:
            raise self.error
        if self.pages is not None:
            return self.pages.get(next_token, Page(items=(), next_token=None))
        return Page(items=tuple(self.functions), next_token=None)


def make_queue(name: str) -> QueueSummary:
    """A queue summary whose FIFO flag follows the .fifo naming rule."""
    return QueueSummary(name=name, is_fifo=name.endswith(".fifo"))


class FakeSqsGateway:
    """In-memory stand-in for the real SQS gateway."""

    def __init__(
        self: Self,
        queues: list[QueueSummary] | None = None,
        error: AwsError | None = None,
        pages: dict[str | None, Page[QueueSummary]] | None = None,
    ) -> None:
        self.queues = queues or []
        self.error = error
        self.pages = pages
        self.calls = 0
        self.next_tokens: list[str | None] = []

    def list_queues(self: Self, next_token: str | None = None) -> Page[QueueSummary]:
        self.calls += 1
        self.next_tokens.append(next_token)
        if self.error is not None:
            raise self.error
        if self.pages is not None:
            return self.pages.get(next_token, Page(items=(), next_token=None))
        return Page(items=tuple(self.queues), next_token=None)


class FakeSsoLoginGateway:
    """In-memory stand-in for the SSO OIDC login gateway."""

    def __init__(
        self: Self,
        authorization: DeviceAuthorization | None = None,
        token: SsoToken | None = None,
        pending_polls: int = 0,
        start_error: AwsError | None = None,
        poll_error: AwsError | None = None,
    ) -> None:
        self.authorization = authorization or make_device_authorization()
        self.token = token or make_sso_token()
        self.pending_polls = pending_polls
        self.start_error = start_error
        self.poll_error = poll_error
        self.poll_calls = 0
        self.cached: list[tuple[SsoConfig, DeviceAuthorization, SsoToken]] = []

    def start_device_authorization(self: Self, config: SsoConfig) -> DeviceAuthorization:  # noqa: ARG002
        if self.start_error is not None:
            raise self.start_error
        return self.authorization

    def poll_token(self: Self, authorization: DeviceAuthorization) -> SsoToken | None:  # noqa: ARG002
        self.poll_calls += 1
        if self.poll_error is not None:
            raise self.poll_error
        if self.poll_calls <= self.pending_polls:
            return None
        return self.token

    def write_token_cache(
        self: Self,
        config: SsoConfig,
        authorization: DeviceAuthorization,
        token: SsoToken,
    ) -> None:
        self.cached.append((config, authorization, token))


def make_sso_config(session_name: str | None = None) -> SsoConfig:
    """SSO settings matching the canned device-flow responses in tests."""
    return SsoConfig(start_url="https://legacy.awsapps.com/start", sso_region="eu-west-1", session_name=session_name)


def make_device_authorization(interval: int = 0, expires_in_s: int = 600) -> DeviceAuthorization:
    """A device authorization with sensible defaults; interval 0 keeps tests fast."""
    now = datetime.now(tz=UTC)
    return DeviceAuthorization(
        client_id="client-id",
        client_secret="client-secret",
        registration_expires_at=now + timedelta(days=90),
        device_code="device-code",
        user_code="ABCD-EFGH",
        verification_uri="https://device.sso.eu-west-1.amazonaws.com/",
        verification_uri_complete="https://device.sso.eu-west-1.amazonaws.com/?user_code=ABCD-EFGH",
        interval=interval,
        expires_at=now + timedelta(seconds=expires_in_s),
    )


def make_sso_token(refresh_token: str | None = None) -> SsoToken:
    """An SSO access token for login-flow tests."""
    return SsoToken(
        access_token="access-token",
        expires_at=datetime.now(tz=UTC) + timedelta(hours=8),
        refresh_token=refresh_token,
    )
