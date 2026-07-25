"""Tests for the SSM gateway."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import boto3
from botocore.stub import Stubber
from moto import mock_aws
import pytest

from awst.aws.models import AwsError
from awst.aws.ssm import SsmGateway, _to_detail, _to_summary

if TYPE_CHECKING:
    from mypy_boto3_ssm.literals import ParameterTypeType


def _gateway() -> SsmGateway:
    return SsmGateway(boto3.client("ssm", region_name="eu-west-1"))


def _put(name: str, param_type: ParameterTypeType = "String") -> None:
    client = boto3.client("ssm", region_name="eu-west-1")
    client.put_parameter(Name=name, Value="value", Type=param_type)


@mock_aws
def test_list_parameters_maps_name_type_and_tier() -> None:
    _put("/app/prod/db-url")
    _put("/app/prod/api-key", param_type="SecureString")

    items = {parameter.name: parameter for parameter in _gateway().list_parameters().items}

    assert items["/app/prod/db-url"].param_type == "String"
    assert items["/app/prod/api-key"].param_type == "SecureString"
    assert items["/app/prod/db-url"].tier == "Standard"


@mock_aws
def test_list_parameters_returns_timezone_aware_modified_timestamps() -> None:
    _put("/app/prod/db-url")

    modified = _gateway().list_parameters().items[0].modified

    assert modified.tzinfo is not None


@mock_aws
def test_list_parameters_returns_parameters_in_api_order_unsorted() -> None:
    for name in ("/gamma", "/alpha", "/beta"):
        _put(name)

    page = _gateway().list_parameters()

    assert [parameter.name for parameter in page.items] == ["/gamma", "/alpha", "/beta"]
    assert page.next_token is None


@mock_aws
def test_list_parameters_returns_empty_page_for_empty_region() -> None:
    page = _gateway().list_parameters()

    assert page.items == ()
    assert page.next_token is None


def test_list_parameters_forwards_next_token() -> None:
    modified = datetime(2026, 1, 1, tzinfo=UTC)
    first_page = {
        "Parameters": [{"Name": "/alpha", "Type": "String", "Tier": "Standard", "LastModifiedDate": modified}],
        "NextToken": "t1",
    }
    second_page = {
        "Parameters": [{"Name": "/beta", "Type": "String", "Tier": "Advanced", "LastModifiedDate": modified}],
    }
    client = boto3.client("ssm", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_response("describe_parameters", first_page, {"MaxResults": 50})
        stubber.add_response("describe_parameters", second_page, {"NextToken": "t1", "MaxResults": 50})

        first = SsmGateway(client).list_parameters()
        second = SsmGateway(client).list_parameters(first.next_token)

    assert first.next_token == "t1"
    assert [parameter.name for parameter in second.items] == ["/beta"]
    assert second.items[0].tier == "Advanced"
    assert second.next_token is None


def test_to_summary_defaults_missing_type_and_tier_to_empty_strings() -> None:
    summary = _to_summary({"Name": "/alpha", "LastModifiedDate": datetime(2026, 1, 1, tzinfo=UTC)})

    assert summary.name == "/alpha"
    assert summary.param_type == ""
    assert summary.tier == ""


def test_list_parameters_maps_client_error_to_aws_error() -> None:
    client = boto3.client("ssm", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "describe_parameters",
            service_error_code="AccessDeniedException",
            service_message="Access Denied",
        )

        with pytest.raises(AwsError) as excinfo:
            SsmGateway(client).list_parameters()

    assert excinfo.value.message == "Access Denied"


@mock_aws
def test_get_parameter_maps_every_field() -> None:
    _put("/app/prod/db-url")

    detail = _gateway().get_parameter("/app/prod/db-url")

    assert detail.name == "/app/prod/db-url"
    assert detail.param_type == "String"
    assert detail.value == "value"
    assert detail.version == 1
    assert detail.arn.endswith(":parameter/app/prod/db-url")
    assert detail.data_type == "text"
    assert detail.modified.tzinfo is not None


@mock_aws
def test_get_parameter_decrypts_secure_strings() -> None:
    _put("/app/prod/api-key", param_type="SecureString")

    detail = _gateway().get_parameter("/app/prod/api-key")

    # Without WithDecryption the API returns the value prefixed by its KMS key.
    assert detail.value == "value"
    assert detail.param_type == "SecureString"


def test_get_parameter_requests_decryption() -> None:
    client = boto3.client("ssm", region_name="eu-west-1")
    response = {
        "Parameter": {
            "Name": "/app/prod/api-key",
            "Type": "SecureString",
            "Value": "s3cret",
            "Version": 3,
            "ARN": "arn:aws:ssm:eu-west-1:123456789012:parameter/app/prod/api-key",
            "DataType": "text",
            "LastModifiedDate": datetime(2026, 1, 1, tzinfo=UTC),
        },
    }
    with Stubber(client) as stubber:
        stubber.add_response("get_parameter", response, {"Name": "/app/prod/api-key", "WithDecryption": True})

        detail = SsmGateway(client).get_parameter("/app/prod/api-key")

    assert detail.value == "s3cret"
    assert detail.version == 3


def test_to_detail_defaults_missing_optional_fields() -> None:
    detail = _to_detail({"Name": "/alpha", "LastModifiedDate": datetime(2026, 1, 1, tzinfo=UTC)})

    assert detail.param_type == ""
    assert detail.value == ""
    assert detail.version == 0
    assert detail.arn == ""
    assert detail.data_type == ""


def test_get_parameter_maps_access_denied_to_aws_error() -> None:
    client = boto3.client("ssm", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "get_parameter",
            service_error_code="AccessDeniedException",
            service_message="not authorized to perform kms:Decrypt",
        )

        with pytest.raises(AwsError) as excinfo:
            SsmGateway(client).get_parameter("/app/prod/api-key")

    assert "kms:Decrypt" in excinfo.value.message


def test_get_parameter_raises_aws_error_for_malformed_response() -> None:
    # A response missing expected keys (Name here) is not expected from AWS, but if it ever
    # happened, a bare KeyError would propagate uncaught, and _to_detail's raw dict — which
    # holds the decrypted Value — would end up in a crash traceback. Assert it maps to an
    # AwsError instead, and that the secret value never appears in the exception's message.
    client = boto3.client("ssm", region_name="eu-west-1")
    response = {
        "Parameter": {
            "Type": "SecureString",
            "Value": "s3cret",
            "Version": 3,
        },
    }
    with Stubber(client) as stubber:
        stubber.add_response("get_parameter", response, {"Name": "/app/prod/api-key", "WithDecryption": True})

        with pytest.raises(AwsError) as excinfo:
            SsmGateway(client).get_parameter("/app/prod/api-key")

    assert "s3cret" not in excinfo.value.message


def test_get_parameter_maps_parameter_not_found_to_aws_error() -> None:
    client = boto3.client("ssm", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "get_parameter",
            service_error_code="ParameterNotFound",
            service_message="Parameter /gone not found.",
        )

        with pytest.raises(AwsError):
            SsmGateway(client).get_parameter("/gone")
