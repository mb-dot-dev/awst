"""Gateway to the SQS API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import QueueSummary

if TYPE_CHECKING:
    from mypy_boto3_sqs import SQSClient


class SqsGateway:
    """Access to SQS, returning plain data models."""

    def __init__(self: Self, client: SQSClient) -> None:
        self._client = client

    def list_queues(self: Self) -> list[QueueSummary]:
        """Return every queue in the region, sorted by name.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            paginator = self._client.get_paginator("list_queues")
            queues = [_to_summary(url) for page in paginator.paginate() for url in page.get("QueueUrls", [])]
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        return sorted(queues, key=lambda queue: queue.name)


def _to_summary(queue_url: str) -> QueueSummary:
    # A page with no queues omits the QueueUrls key entirely; list_queues returns only URLs,
    # so the name is the last path segment and FIFO-ness comes from the mandatory .fifo suffix.
    name = queue_url.rsplit("/", 1)[-1]
    return QueueSummary(name=name, is_fifo=name.endswith(".fifo"))
