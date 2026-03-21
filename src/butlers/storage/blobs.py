"""Blob storage abstraction with S3-compatible backend.

All blob I/O goes through an S3-compatible API (Garage, MinIO, AWS S3, etc.).
"""

import logging
import mimetypes
import uuid
from datetime import UTC, datetime
from typing import NamedTuple, Protocol

import aioboto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class BlobRef(NamedTuple):
    """Reference to a stored blob.

    Format: ``s3://{bucket}/{key}``
    """

    scheme: str  # 's3'
    key: str  # 'bucket/general/2026/02/16/abc123.jpg'

    @classmethod
    def parse(cls, storage_ref: str) -> "BlobRef":
        """Parse a storage_ref string into a BlobRef.

        Args:
            storage_ref: String like 's3://bucket/general/2026/02/16/abc123.jpg'

        Returns:
            BlobRef instance

        Raises:
            ValueError: If storage_ref format is invalid
        """
        if "://" not in storage_ref:
            msg = f"Invalid storage_ref format (missing '://'): {storage_ref}"
            raise ValueError(msg)

        scheme, key = storage_ref.split("://", 1)
        return cls(scheme=scheme, key=key)

    def to_ref(self) -> str:
        """Convert BlobRef to storage_ref string."""
        return f"{self.scheme}://{self.key}"


class BlobStore(Protocol):
    """Protocol for blob storage backends."""

    async def put(self, data: bytes, *, content_type: str, filename: str | None = None) -> str:
        """Store blob, return storage_ref string.

        Args:
            data: Binary data to store
            content_type: MIME type (e.g., 'image/jpeg')
            filename: Optional original filename for extension hint

        Returns:
            Storage reference string (e.g., 's3://bucket/general/2026/02/16/abc123.jpg')
        """
        ...

    async def get(self, storage_ref: str) -> bytes:
        """Retrieve blob by storage_ref.

        Args:
            storage_ref: Storage reference string

        Returns:
            Binary blob data

        Raises:
            BlobNotFoundError: If blob does not exist
        """
        ...

    async def delete(self, storage_ref: str) -> None:
        """Delete blob.

        Args:
            storage_ref: Storage reference string

        Raises:
            BlobNotFoundError: If blob does not exist
        """
        ...

    async def exists(self, storage_ref: str) -> bool:
        """Check if blob exists.

        Args:
            storage_ref: Storage reference string

        Returns:
            True if blob exists, False otherwise
        """
        ...


class BlobNotFoundError(Exception):
    """Raised when a blob cannot be found in storage."""

    def __init__(self, storage_ref: str):
        self.storage_ref = storage_ref
        super().__init__(f"Blob not found: {storage_ref}")


class S3BlobStore:
    """S3-compatible blob store.

    Keys are butler-prefixed and date-partitioned:
    - Format: {butler_name}/{YYYY}/{MM}/{DD}/{uuid}{ext}
    - Refs: 's3://{bucket}/{butler_name}/2026/02/16/abc123.jpg'

    Args:
        bucket: S3 bucket name
        butler_name: Butler name for key-prefix isolation
        endpoint_url: S3-compatible endpoint URL
        access_key_id: AWS access key ID (or None for default credential chain)
        secret_access_key: AWS secret access key (or None for default credential chain)
        region: AWS region name (default: 'us-east-1')
    """

    def __init__(
        self,
        *,
        bucket: str,
        butler_name: str,
        endpoint_url: str,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region: str = "us-east-1",
    ):
        self.bucket = bucket
        self.butler_name = butler_name
        self.endpoint_url = endpoint_url
        self.region = region
        self.scheme = "s3"

        self._session = aioboto3.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )
        # Path-style addressing required for Garage, MinIO, and most
        # S3-compatible stores.
        self._boto_config = BotoConfig(s3={"addressing_style": "path"})
        self._client = None

    def _s3_client(self):
        """Return an async context manager for an S3 client."""
        kwargs: dict = {"config": self._boto_config}
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        return self._session.client("s3", **kwargs)

    def _generate_key(self, content_type: str, filename: str | None = None) -> str:
        """Generate a butler-prefixed, date-partitioned key.

        Args:
            content_type: MIME type for extension hint
            filename: Optional filename for extension hint

        Returns:
            Key string like 'general/2026/02/16/abc123.jpg'
        """
        now = datetime.now(UTC)
        date_prefix = now.strftime("%Y/%m/%d")
        unique_id = uuid.uuid4()

        ext = ""
        if filename:
            from pathlib import PurePosixPath

            file_ext = PurePosixPath(filename).suffix
            if file_ext:
                ext = file_ext
        if not ext:
            guessed_ext = mimetypes.guess_extension(content_type)
            if guessed_ext:
                ext = guessed_ext

        return f"{self.butler_name}/{date_prefix}/{unique_id}{ext}"

    def _parse_ref(self, storage_ref: str) -> str:
        """Parse storage_ref and return the S3 object key (without bucket prefix).

        Args:
            storage_ref: 's3://bucket/key'

        Returns:
            The object key portion

        Raises:
            ValueError: If scheme is not 's3' or format is invalid
        """
        blob_ref = BlobRef.parse(storage_ref)
        if blob_ref.scheme != self.scheme:
            msg = f"Storage scheme mismatch: expected '{self.scheme}', got '{blob_ref.scheme}'"
            raise ValueError(msg)
        # key = "bucket/rest/of/path" → strip the bucket prefix
        full_key = blob_ref.key
        if full_key.startswith(self.bucket + "/"):
            return full_key[len(self.bucket) + 1 :]
        return full_key

    async def put(self, data: bytes, *, content_type: str, filename: str | None = None) -> str:
        """Store blob in S3, return storage_ref string."""
        key = self._generate_key(content_type, filename)
        async with self._s3_client() as s3:
            await s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        return BlobRef(scheme=self.scheme, key=f"{self.bucket}/{key}").to_ref()

    async def get(self, storage_ref: str) -> bytes:
        """Retrieve blob from S3 by storage_ref."""
        key = self._parse_ref(storage_ref)
        async with self._s3_client() as s3:
            try:
                response = await s3.get_object(Bucket=self.bucket, Key=key)
                return await response["Body"].read()
            except ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    raise BlobNotFoundError(storage_ref) from e
                raise

    async def delete(self, storage_ref: str) -> None:
        """Delete blob from S3."""
        key = self._parse_ref(storage_ref)
        # S3 delete is idempotent — check existence first to match protocol
        async with self._s3_client() as s3:
            try:
                await s3.head_object(Bucket=self.bucket, Key=key)
            except ClientError as e:
                if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                    raise BlobNotFoundError(storage_ref) from e
                raise
            await s3.delete_object(Bucket=self.bucket, Key=key)

    async def exists(self, storage_ref: str) -> bool:
        """Check if blob exists in S3."""
        try:
            key = self._parse_ref(storage_ref)
        except ValueError:
            return False
        async with self._s3_client() as s3:
            try:
                await s3.head_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError:
                return False

    async def startup_check(self) -> None:
        """Validate S3 connectivity by checking the bucket exists.

        Raises:
            RuntimeError: If the endpoint is unreachable or bucket does not exist
        """
        async with self._s3_client() as s3:
            try:
                await s3.head_bucket(Bucket=self.bucket)
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code in ("404", "NoSuchBucket"):
                    msg = (
                        f"S3 bucket '{self.bucket}' does not exist "
                        f"at endpoint {self.endpoint_url}"
                    )
                    raise RuntimeError(msg) from e
                msg = (
                    f"S3 connectivity check failed for bucket '{self.bucket}' "
                    f"at endpoint {self.endpoint_url}: {e}"
                )
                raise RuntimeError(msg) from e
            except Exception as e:
                msg = (
                    f"Cannot reach S3 endpoint {self.endpoint_url}: {e}"
                )
                raise RuntimeError(msg) from e
        logger.info(
            "S3 blob storage ready: endpoint=%s bucket=%s prefix=%s",
            self.endpoint_url,
            self.bucket,
            self.butler_name,
        )

    async def close(self) -> None:
        """Clean up the aioboto3 session resources."""
        # aioboto3 sessions don't hold persistent connections —
        # the context-manager-per-call pattern handles cleanup.
        # This method exists for lifecycle symmetry with startup_check().
        pass
