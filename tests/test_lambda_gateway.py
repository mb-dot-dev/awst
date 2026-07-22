"""Tests for the Lambda gateway."""

from datetime import UTC, datetime
import io
import zipfile

import boto3
from botocore.stub import Stubber
from moto import mock_aws
import pytest

from awst.aws.lambda_ import LambdaGateway, _to_summary
from awst.aws.models import AwsError


def _gateway() -> LambdaGateway:
    return LambdaGateway(boto3.client("lambda", region_name="eu-west-1"))


def _role_arn() -> str:
    """Create an IAM role; moto's Lambda backend requires one that exists."""
    iam = boto3.client("iam", region_name="eu-west-1")
    document = '{"Version": "2012-10-17", "Statement": []}'
    return iam.create_role(RoleName="lambda-role", AssumeRolePolicyDocument=document)["Role"]["Arn"]


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("handler.py", "def handler(event, context):\n    return None\n")
    return buffer.getvalue()


def _create_function(name: str, role_arn: str) -> None:
    client = boto3.client("lambda", region_name="eu-west-1")
    client.create_function(
        FunctionName=name,
        Runtime="python3.12",
        Role=role_arn,
        Handler="handler.handler",
        Code={"ZipFile": _zip_bytes()},
        Timeout=30,
        MemorySize=256,
    )


@mock_aws
def test_list_functions_returns_functions_in_api_order_unsorted() -> None:
    role_arn = _role_arn()
    for name in ("gamma", "alpha", "beta"):
        _create_function(name, role_arn)

    page = _gateway().list_functions()

    assert [function.name for function in page.items] == ["gamma", "alpha", "beta"]
    assert page.next_token is None


@mock_aws
def test_list_functions_maps_fields() -> None:
    _create_function("alpha", _role_arn())

    function = _gateway().list_functions().items[0]

    assert function.name == "alpha"
    assert function.runtime == "python3.12"
    assert function.memory_mb == 256
    assert function.timeout_s == 30
    assert function.modified.tzinfo is not None


@mock_aws
def test_list_functions_returns_empty_list_for_empty_account() -> None:
    assert _gateway().list_functions().items == ()


def test_list_functions_forwards_marker() -> None:
    client = boto3.client("lambda", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_response(
            "list_functions",
            {
                "Functions": [{"FunctionName": "alpha", "LastModified": "2026-01-01T12:00:00.000+0000"}],
                "NextMarker": "t1",
            },
            {},
        )
        stubber.add_response(
            "list_functions",
            {"Functions": [{"FunctionName": "beta", "LastModified": "2026-01-01T12:00:00.000+0000"}]},
            {"Marker": "t1"},
        )

        first = LambdaGateway(client).list_functions()
        second = LambdaGateway(client).list_functions(first.next_token)

    assert first.next_token == "t1"
    assert [function.name for function in second.items] == ["beta"]
    assert second.next_token is None


def test_to_summary_parses_last_modified_string() -> None:
    # Lambda returns LastModified as an ISO-8601 string, not a datetime
    summary = _to_summary(
        {
            "FunctionName": "alpha",
            "Runtime": "python3.12",
            "MemorySize": 128,
            "Timeout": 3,
            "LastModified": "2026-01-01T12:00:00.000+0000",
        }
    )

    assert summary.modified == datetime(2026, 1, 1, 12, tzinfo=UTC)


def test_to_summary_defaults_runtime_to_empty_for_image_functions() -> None:
    # container-image functions have no Runtime field; the UI renders a blank cell
    summary = _to_summary(
        {
            "FunctionName": "img",
            "MemorySize": 512,
            "Timeout": 60,
            "LastModified": "2026-01-01T12:00:00.000+0000",
        }
    )

    assert summary.runtime == ""


def test_list_functions_maps_malformed_last_modified_to_aws_error() -> None:
    client = boto3.client("lambda", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_response(
            "list_functions",
            {"Functions": [{"FunctionName": "alpha", "LastModified": "not-a-timestamp"}]},
        )

        with pytest.raises(AwsError) as excinfo:
            LambdaGateway(client).list_functions()

    assert "not-a-timestamp" in excinfo.value.message


def test_list_functions_maps_client_error_to_aws_error() -> None:
    client = boto3.client("lambda", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_client_error("list_functions", service_error_code="AccessDenied", service_message="Access Denied")

        with pytest.raises(AwsError) as excinfo:
            LambdaGateway(client).list_functions()

    assert excinfo.value.message == "Access Denied"
