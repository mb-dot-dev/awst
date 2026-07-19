"""Tests for the S3 gateway."""

from datetime import UTC, datetime

import boto3
from botocore.stub import Stubber
from moto import mock_aws
import pytest

from awst.aws.models import AwsError
from awst.aws.s3 import S3Gateway, _to_summary


def _gateway() -> S3Gateway:
    return S3Gateway(boto3.client("s3", region_name="eu-west-1"))


def _create_bucket(name: str) -> None:
    client = boto3.client("s3", region_name="eu-west-1")
    client.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})


@mock_aws
def test_list_buckets_returns_all_buckets_sorted_by_name() -> None:
    for name in ("gamma", "alpha", "beta"):
        _create_bucket(name)

    buckets = _gateway().list_buckets()

    assert [bucket.name for bucket in buckets] == ["alpha", "beta", "gamma"]


@mock_aws
def test_list_buckets_maps_fields() -> None:
    _create_bucket("alpha")

    bucket = _gateway().list_buckets()[0]

    assert bucket.name == "alpha"
    assert bucket.created.tzinfo is not None


@mock_aws
def test_list_buckets_returns_empty_list_for_empty_account() -> None:
    assert _gateway().list_buckets() == []


def test_to_summary_maps_bucket_region_when_present() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)

    summary = _to_summary({"Name": "alpha", "CreationDate": created, "BucketRegion": "eu-west-1"})

    assert summary.name == "alpha"
    assert summary.region == "eu-west-1"
    assert summary.created == created


def test_to_summary_defaults_region_to_empty_when_missing() -> None:
    # moto (and older endpoints) omit BucketRegion; the UI renders a blank cell
    summary = _to_summary({"Name": "alpha", "CreationDate": datetime(2026, 1, 1, tzinfo=UTC)})

    assert summary.region == ""


def test_list_buckets_maps_client_error_to_aws_error() -> None:
    client = boto3.client("s3", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_client_error("list_buckets", service_error_code="AccessDenied", service_message="Access Denied")

        with pytest.raises(AwsError) as excinfo:
            S3Gateway(client).list_buckets()

    assert excinfo.value.message == "Access Denied"


@mock_aws
def test_empty_bucket_deletes_all_objects_and_yields_cumulative_count() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    for index in range(3):
        client.put_object(Bucket="alpha", Key=f"key-{index}", Body=b"data")

    counts = list(_gateway().empty_bucket("alpha"))

    assert counts == [3]
    assert client.list_objects_v2(Bucket="alpha")["KeyCount"] == 0


@mock_aws
def test_empty_bucket_deletes_versions_and_delete_markers() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    client.put_bucket_versioning(Bucket="alpha", VersioningConfiguration={"Status": "Enabled"})
    client.put_object(Bucket="alpha", Key="doc", Body=b"v1")
    client.put_object(Bucket="alpha", Key="doc", Body=b"v2")
    client.delete_object(Bucket="alpha", Key="doc")  # adds a delete marker

    counts = list(_gateway().empty_bucket("alpha"))

    assert counts == [3]  # two versions + one delete marker
    versions = client.list_object_versions(Bucket="alpha")
    assert "Versions" not in versions
    assert "DeleteMarkers" not in versions


@mock_aws
def test_empty_bucket_on_already_empty_bucket_yields_nothing() -> None:
    _create_bucket("alpha")

    assert list(_gateway().empty_bucket("alpha")) == []


@mock_aws
def test_empty_bucket_deletes_in_batches_of_1000() -> None:
    _create_bucket("alpha")
    client = boto3.client("s3", region_name="eu-west-1")
    for index in range(1050):
        client.put_object(Bucket="alpha", Key=f"key-{index:04}", Body=b"")

    counts = list(_gateway().empty_bucket("alpha"))

    assert counts == [1000, 1050]
    assert client.list_objects_v2(Bucket="alpha")["KeyCount"] == 0


@mock_aws
def test_empty_bucket_maps_missing_bucket_to_aws_error() -> None:
    deletions = _gateway().empty_bucket("missing")  # lazy: nothing raises until iterated

    with pytest.raises(AwsError):
        list(deletions)


def test_empty_bucket_raises_on_partial_failure() -> None:
    client = boto3.client("s3", region_name="eu-west-1")
    with Stubber(client) as stubber:
        stubber.add_response(
            "list_object_versions",
            {"Versions": [{"Key": "locked", "VersionId": "v1"}], "IsTruncated": False},
        )
        stubber.add_response(
            "delete_objects",
            {"Errors": [{"Key": "locked", "VersionId": "v1", "Code": "AccessDenied", "Message": "Access Denied"}]},
        )

        deletions = S3Gateway(client).empty_bucket("alpha")  # lazy: nothing raises until iterated

        with pytest.raises(AwsError) as excinfo:
            list(deletions)

    assert "locked" in excinfo.value.message
    assert "Access Denied" in excinfo.value.message
