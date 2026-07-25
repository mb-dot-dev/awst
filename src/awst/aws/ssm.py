"""Gateway to the SSM Parameter Store API."""

from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import AwsError, Page, ParameterDetail, ParameterSummary

if TYPE_CHECKING:
    from mypy_boto3_ssm import SSMClient
    from mypy_boto3_ssm.type_defs import ParameterMetadataTypeDef, ParameterTypeDef


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
            # The API defaults MaxResults to 10 and caps it at 50; request the cap explicitly so
            # each page (and each page-fetching API call) covers as many parameters as possible.
            if next_token is None:
                response = self._client.describe_parameters(MaxResults=50)
            else:
                response = self._client.describe_parameters(NextToken=next_token, MaxResults=50)
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        parameters = tuple(_to_summary(parameter) for parameter in response.get("Parameters", []))
        return Page(items=parameters, next_token=response.get("NextToken"))

    def get_parameter(self: Self, name: str) -> ParameterDetail:
        """Return one parameter including its value, decrypting SecureString values.

        Raises AwsError for any credential, network, or API failure — including the
        AccessDeniedException raised when the caller cannot decrypt the parameter's KMS key.
        """
        try:
            response = self._client.get_parameter(Name=name, WithDecryption=True)
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        try:
            return _to_detail(response["Parameter"])
        except KeyError as error:
            # AWS always populates these fields in practice, but if a response ever didn't, a bare
            # KeyError would propagate as an uncaught exception, and Textual prints fatal tracebacks
            # with local variables — including _to_detail's raw dict, which holds the decrypted
            # value. Map it to an AwsError so only a message (never the dict) escapes.
            message = f"SSM returned a parameter response missing {error}."
            raise AwsError(message) from error


def _to_summary(parameter: ParameterMetadataTypeDef) -> ParameterSummary:
    # A page with no parameters omits the Parameters key entirely; Type and Tier are always
    # present in practice, but every describe_parameters field is optional in the API model.
    return ParameterSummary(
        name=parameter["Name"],
        param_type=parameter.get("Type", ""),
        tier=parameter.get("Tier", ""),
        modified=parameter["LastModifiedDate"],
    )


def _to_detail(parameter: ParameterTypeDef) -> ParameterDetail:
    # Every get_parameter field except Name is optional in the API model, though all are
    # present in practice; default the cosmetic ones so a sparse response still renders.
    return ParameterDetail(
        name=parameter["Name"],
        param_type=parameter.get("Type", ""),
        value=parameter.get("Value", ""),
        version=parameter.get("Version", 0),
        arn=parameter.get("ARN", ""),
        data_type=parameter.get("DataType", ""),
        modified=parameter["LastModifiedDate"],
    )
