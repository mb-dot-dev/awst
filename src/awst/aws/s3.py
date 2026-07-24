"""Gateway to the S3 API."""

from typing import TYPE_CHECKING, Self

from botocore.exceptions import BotoCoreError, ClientError

from awst.aws.errors import map_botocore_error
from awst.aws.models import AwsError, BucketSummary, ObjectPage, ObjectSummary, Page

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

    def list_buckets(self: Self, next_token: str | None = None) -> Page[BucketSummary]:
        """Return one page of buckets in the account.

        Raises AwsError for any credential, network, or API failure.
        """
        try:
            if next_token is None:
                response = self._client.list_buckets()
            else:
                response = self._client.list_buckets(ContinuationToken=next_token)
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error
        buckets = tuple(_to_summary(bucket) for bucket in response.get("Buckets", []))
        return Page(items=buckets, next_token=response.get("ContinuationToken"))

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

    def delete_object(self: Self, bucket: str, region: str, key: str) -> Iterator[int]:
        """Delete every version and delete marker of one object key.

        Yields the cumulative deleted count after each batch of up to 1000
        keys; a key that does not exist yields nothing. Raises AwsError for any
        credential, network, or API failure.
        """
        # Prefix=key also returns keys that merely extend it ("file.txt.bak"), so match exactly.
        # S3 returns keys in lexicographic order and key sorts before anything extending it,
        # so a page holding no exact match means this key's versions are exhausted.
        return self._delete_versions(bucket, region, key, lambda candidate: candidate == key)

    def delete_prefix(self: Self, bucket: str, region: str, prefix: str) -> Iterator[int]:
        """Delete every version and delete marker of every key beneath the prefix.

        Yields the cumulative deleted count after each batch of up to 1000
        keys; a prefix holding no keys yields nothing. Raises AwsError for any
        credential, network, or API failure.
        """
        return self._delete_versions(bucket, region, prefix, lambda _: True)

    def empty_bucket(self: Self, name: str, region: str) -> Iterator[int]:
        """Delete every object version and delete marker in the bucket.

        Yields the cumulative deleted-object count after each batch of up to
        1000 keys; an already-empty bucket yields nothing. Raises AwsError for
        any credential, network, or API failure, including per-key failures
        reported by DeleteObjects.
        """
        return self._delete_versions(name, region, "", lambda _: True)

    def _delete_versions(
        self: Self,
        bucket: str,
        region: str,
        prefix: str,
        match: Callable[[str], bool],
    ) -> Iterator[int]:
        """Delete every version and delete marker under prefix whose key satisfies match."""
        client = self._client_for(region)
        deleted = 0
        try:
            # Re-list from the start after each batch instead of paginating with
            # markers: resuming from a just-deleted key breaks under moto, and
            # restarting is the standard pattern for delete-while-listing anyway.
            # Each round deletes everything it matched (or raises), so the loop
            # always makes progress.
            while True:
                page = client.list_object_versions(Bucket=bucket, Prefix=prefix, MaxKeys=1000)
                items = [*page.get("Versions", []), *page.get("DeleteMarkers", [])]
                keys: list[ObjectIdentifierTypeDef] = [
                    {"Key": item["Key"], "VersionId": item["VersionId"]} for item in items if match(item["Key"])
                ]
                if not keys:
                    break
                self._delete_batch(client, bucket, keys)
                deleted += len(keys)
                yield deleted
        except (BotoCoreError, ClientError) as error:
            raise map_botocore_error(error) from error

    def _delete_batch(self: Self, client: S3Client, name: str, keys: list[ObjectIdentifierTypeDef]) -> None:
        response = client.delete_objects(Bucket=name, Delete={"Objects": keys, "Quiet": True})
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
