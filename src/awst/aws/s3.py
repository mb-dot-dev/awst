"""Gateway to the S3 API."""

from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import AwsError, BucketSummary, ObjectPage, ObjectSummary

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import BucketTypeDef, ObjectIdentifierTypeDef


class S3Gateway:
    """Access to S3, returning plain data models."""

    def __init__(
        self: Self,
        client: S3Client,
        regional_client_factory: Callable[[str], S3Client] | None = None,
    ) -> None:
        self._client = client
        self._regional_client_factory = regional_client_factory
        self._regional_clients: dict[str, S3Client] = {}

    def list_buckets(self: Self) -> list[BucketSummary]:
        """Return every bucket in the account, sorted by name.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            paginator = self._client.get_paginator("list_buckets")
            buckets = [_to_summary(bucket) for page in paginator.paginate() for bucket in page["Buckets"]]
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        return sorted(buckets, key=lambda bucket: bucket.name)

    def list_objects(
        self: Self,
        bucket: str,
        region: str,
        prefix: str = "",
        continuation_token: str | None = None,
    ) -> ObjectPage:
        """Return one page (up to 1000 keys) of one prefix level of the bucket.

        Folders are the level's common prefixes; the zero-byte "folder marker"
        object equal to the prefix itself is filtered out. Raises AwsError for
        any credential, network, or API failure.
        """
        client = self._client_for(region)
        try:
            if continuation_token is None:
                page = client.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter="/", MaxKeys=1000)
            else:
                page = client.list_objects_v2(
                    Bucket=bucket,
                    Prefix=prefix,
                    Delimiter="/",
                    MaxKeys=1000,
                    ContinuationToken=continuation_token,
                )
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        folders = tuple(entry["Prefix"] for entry in page.get("CommonPrefixes", []) if "Prefix" in entry)
        objects = tuple(
            ObjectSummary(key=obj["Key"], size=obj["Size"], modified=obj["LastModified"])
            for obj in page.get("Contents", [])
            if obj["Key"] != prefix
        )
        return ObjectPage(folders=folders, objects=objects, continuation_token=page.get("NextContinuationToken"))

    def _client_for(self: Self, region: str) -> S3Client:
        """The base client for the home (or unknown) region, a cached regional client otherwise."""
        if not region or region == self._client.meta.region_name or self._regional_client_factory is None:
            return self._client
        if region not in self._regional_clients:
            self._regional_clients[region] = self._regional_client_factory(region)
        return self._regional_clients[region]

    def empty_bucket(self: Self, name: str) -> Iterator[int]:
        """Delete every object version and delete marker in the bucket.

        Yields the cumulative deleted-object count after each batch of up to
        1000 keys; an already-empty bucket yields nothing. Raises AwsError for
        any credential, network, or API failure, including per-key failures
        reported by DeleteObjects.
        """
        deleted = 0
        try:
            # Re-list from the start after each batch instead of paginating with
            # markers: resuming from a just-deleted key breaks under moto, and
            # restarting is the standard pattern for delete-while-listing anyway.
            # Each round deletes everything it listed (or raises), so the loop
            # always makes progress.
            while True:
                page = self._client.list_object_versions(Bucket=name, MaxKeys=1000)
                items = [*page.get("Versions", []), *page.get("DeleteMarkers", [])]
                if not items:
                    break
                keys: list[ObjectIdentifierTypeDef] = [
                    {"Key": item["Key"], "VersionId": item["VersionId"]} for item in items
                ]
                self._delete_batch(name, keys)
                deleted += len(keys)
                yield deleted
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error

    def _delete_batch(self: Self, name: str, keys: list[ObjectIdentifierTypeDef]) -> None:
        response = self._client.delete_objects(Bucket=name, Delete={"Objects": keys, "Quiet": True})
        errors = response.get("Errors", [])
        if errors:
            first = errors[0]
            reason = first.get("Message", first.get("Code", "unknown error"))
            message = f"Could not delete {first.get('Key', 'an object')}: {reason}"
            raise AwsError(message)


def _to_summary(bucket: BucketTypeDef) -> BucketSummary:
    return BucketSummary(
        name=bucket["Name"],
        region=bucket.get("BucketRegion", ""),
        created=bucket["CreationDate"],
    )
