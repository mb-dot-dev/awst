"""Gateway to the SSM Parameter Store API."""

from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import Page, ParameterSummary

if TYPE_CHECKING:
    from mypy_boto3_ssm import SSMClient
    from mypy_boto3_ssm.type_defs import ParameterMetadataTypeDef


class SsmGateway:
    """Access to SSM Parameter Store, returning plain data models."""

    def __init__(self: Self, client: SSMClient) -> None:
        self._client = client

    def list_parameters(self: Self, next_token: str | None = None) -> Page[ParameterSummary]:
        """Return one page of parameter metadata in the region.

        Values are never fetched: describe_parameters returns metadata only.
        Raises AwsError for any credential, network, or API failure.
        """
        try:
            if next_token is None:
                response = self._client.describe_parameters()
            else:
                response = self._client.describe_parameters(NextToken=next_token)
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        parameters = tuple(_to_summary(parameter) for parameter in response.get("Parameters", []))
        return Page(items=parameters, next_token=response.get("NextToken"))


def _to_summary(parameter: ParameterMetadataTypeDef) -> ParameterSummary:
    # A page with no parameters omits the Parameters key entirely; Type and Tier are always
    # present in practice, but every describe_parameters field is optional in the API model.
    return ParameterSummary(
        name=parameter["Name"],
        param_type=parameter.get("Type", ""),
        tier=parameter.get("Tier", ""),
        modified=parameter["LastModifiedDate"],
    )
