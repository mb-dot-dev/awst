"""Gateway to the CloudFormation API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import (
    AwsError,
    StackDetail,
    StackEvent,
    StackNotFoundError,
    StackOutput,
    StackParameter,
    StackResource,
    StackSummary,
)

if TYPE_CHECKING:
    from datetime import datetime

    from mypy_boto3_cloudformation import CloudFormationClient
    from mypy_boto3_cloudformation.type_defs import (
        OutputTypeDef,
        ParameterTypeDef,
        StackEventTypeDef,
        StackResourceSummaryTypeDef,
        StackTypeDef,
    )


class CloudFormationGateway:
    """Access to CloudFormation, returning plain data models."""

    def __init__(self: Self, client: CloudFormationClient) -> None:
        self._client = client

    def list_stacks(self: Self) -> list[StackSummary]:
        """Return every stack in the account/region, sorted by name.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            paginator = self._client.get_paginator("describe_stacks")
            stacks = [_to_summary(stack) for page in paginator.paginate() for stack in page["Stacks"]]
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        return sorted(stacks, key=lambda stack: stack.name)

    def get_stack_detail(self: Self, name: str) -> StackDetail:
        """Return one stack's overview, parameters, outputs, resources, and recent events.

        Events are newest-first and limited to the first API page (~100 entries).
        Raises StackNotFoundError if the stack does not exist, AwsError for any other failure.
        """
        try:
            stack = self._client.describe_stacks(StackName=name)["Stacks"][0]
            resources = tuple(
                _to_resource(resource)
                for page in self._client.get_paginator("list_stack_resources").paginate(StackName=name)
                for resource in page["StackResourceSummaries"]
            )
            event_page = self._client.describe_stack_events(StackName=name)["StackEvents"]
            events = tuple(sorted((_to_event(event) for event in event_page), key=_event_time, reverse=True))
        except (BotoCoreError, ClientError) as error:
            raise _map_stack_error(error, name) from error
        return _to_detail(stack, resources, events)

    def delete_stack(self: Self, name: str) -> None:
        """Request deletion of the stack; CloudFormation deletes asynchronously.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            self._client.delete_stack(StackName=name)
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error


def _to_summary(stack: StackTypeDef) -> StackSummary:
    created = stack["CreationTime"]
    return StackSummary(
        name=stack["StackName"],
        status=stack["StackStatus"],
        created=created,
        updated=stack.get("LastUpdatedTime", created),
        description=stack.get("Description"),
    )


def _map_stack_error(error: BotoCoreError | ClientError, name: str) -> AwsError:
    if isinstance(error, ClientError) and "does not exist" in error.response["Error"]["Message"]:
        return StackNotFoundError(f"Stack {name} does not exist.")
    return map_botocore_error(error)


def _event_time(event: StackEvent) -> datetime:
    return event.timestamp


def _to_detail(
    stack: StackTypeDef, resources: tuple[StackResource, ...], events: tuple[StackEvent, ...]
) -> StackDetail:
    created = stack["CreationTime"]
    return StackDetail(
        name=stack["StackName"],
        stack_id=stack.get("StackId", ""),
        status=stack["StackStatus"],
        status_reason=stack.get("StackStatusReason"),
        description=stack.get("Description"),
        created=created,
        updated=stack.get("LastUpdatedTime", created),
        parameters=tuple(sorted((_to_parameter(p) for p in stack.get("Parameters", [])), key=lambda p: p.key)),
        outputs=tuple(sorted((_to_output(o) for o in stack.get("Outputs", [])), key=lambda o: o.key)),
        resources=resources,
        events=events,
    )


def _to_parameter(parameter: ParameterTypeDef) -> StackParameter:
    return StackParameter(key=parameter.get("ParameterKey", ""), value=parameter.get("ParameterValue", ""))


def _to_output(output: OutputTypeDef) -> StackOutput:
    return StackOutput(
        key=output.get("OutputKey", ""),
        value=output.get("OutputValue", ""),
        description=output.get("Description"),
    )


def _to_resource(resource: StackResourceSummaryTypeDef) -> StackResource:
    return StackResource(
        logical_id=resource["LogicalResourceId"],
        physical_id=resource.get("PhysicalResourceId"),
        resource_type=resource["ResourceType"],
        status=resource["ResourceStatus"],
    )


def _to_event(event: StackEventTypeDef) -> StackEvent:
    return StackEvent(
        timestamp=event["Timestamp"],
        logical_id=event.get("LogicalResourceId", ""),
        resource_type=event.get("ResourceType", ""),
        status=event.get("ResourceStatus", ""),
        reason=event.get("ResourceStatusReason"),
    )
