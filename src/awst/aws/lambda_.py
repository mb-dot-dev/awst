"""Gateway to the Lambda API."""

from datetime import datetime
from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import FunctionSummary, Page

if TYPE_CHECKING:
    from mypy_boto3_lambda import LambdaClient
    from mypy_boto3_lambda.type_defs import FunctionConfigurationTypeDef


class LambdaGateway:
    """Access to Lambda, returning plain data models."""

    def __init__(self: Self, client: LambdaClient) -> None:
        self._client = client

    def list_functions(self: Self, next_token: str | None = None) -> Page[FunctionSummary]:
        """Return one page of functions in the region.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            if next_token is None:
                response = self._client.list_functions()
            else:
                response = self._client.list_functions(Marker=next_token)
            functions = tuple(_to_summary(function) for function in response.get("Functions", []))
        except (BotoCoreError, ClientError, ValueError) as error:
            # ValueError: _to_summary rejects an unparseable LastModified string
            raise map_botocore_error(error) from error
        return Page(items=functions, next_token=response.get("NextMarker"))


def _to_summary(function: FunctionConfigurationTypeDef) -> FunctionSummary:
    # LastModified is an ISO-8601 string (e.g. "2026-01-01T12:00:00.000+0000"), unlike S3/CFN datetimes
    return FunctionSummary(
        name=function["FunctionName"],
        runtime=function.get("Runtime", ""),
        memory_mb=function.get("MemorySize", 0),
        timeout_s=function.get("Timeout", 0),
        modified=datetime.fromisoformat(function["LastModified"]),
    )
