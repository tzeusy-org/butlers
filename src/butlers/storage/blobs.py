"""Blob storage abstraction with local filesystem backend.

Provides a swappable interface for storing and retrieving media blobs.
Start with local filesystem for dev, designed to support S3/MinIO later.
"""

import mimetypes
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, Protocol


class BlobRef(NamedTuple):
    """Reference to a stored blob."""

    scheme: str  # 'local', 'file', future: 's3'
    key: str  # '2026/02/16/abc123.jpg'

    @classmethod
    def parse(cls, storage_ref: str) -> "BlobRef":
        """Parse a storage_ref string into a BlobRef.

        Args:
            storage_ref: String like 'local://2026/02/16/abc123.jpg'

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
            Storage reference string (e.g., 'local://2026/02/16/abc123.jpg')
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


class LocalBlobStore:
    """Filesystem-backed blob store for development.

    Keys are date-partitioned with random suffix to avoid collisions:
    - Format: YYYY/MM/DD/{uuid}{ext}
    - Example: 2026/02/16/a1b2c3d4-e5f6-7890-abcd-ef1234567890.jpg
    - Refs: 'local://2026/02/16/a1b2c3d4-e5f6-7890-abcd-ef1234567890.jpg'

    Args:
        base_dir: Root directory for blob storage
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir).resolve()
        self.scheme = "local"

    def _generate_key(self, content_type: str, filename: str | None = None) -> str:
        """Generate a date-partitioned key with random suffix.

        Args:
            content_type: MIME type for extension hint
            filename: Optional filename for extension hint

        Returns:
            Key string like '2026/02/16/abc123.jpg'
        """
        now = datetime.now(UTC)
        date_prefix = now.strftime("%Y/%m/%d")
        unique_id = uuid.uuid4()

        # Determine file extension
        ext = ""
        if filename:
            # Try to extract extension from filename
            file_ext = Path(filename).suffix
            if file_ext:
                ext = file_ext
        if not ext:
            # Fall back to MIME type mapping
            guessed_ext = mimetypes.guess_extension(content_type)
            if guessed_ext:
                ext = guessed_ext

        return f"{date_prefix}/{unique_id}{ext}"

    def _ref_to_path(self, storage_ref: str) -> Path:
        """Convert storage_ref to filesystem path.

        Args:
            storage_ref: Storage reference string

        Returns:
            Absolute path to blob file

        Raises:
            ValueError: If storage_ref has wrong scheme or attempts path traversal
        """
        blob_ref = BlobRef.parse(storage_ref)
        if blob_ref.scheme != self.scheme:
            msg = f"Storage scheme mismatch: expected '{self.scheme}', got '{blob_ref.scheme}'"
            raise ValueError(msg)

        # Resolve path and check for traversal outside base_dir
        resolved_path = (self.base_dir / blob_ref.key).resolve()

        # Ensure resolved path is within base_dir
        try:
            resolved_path.relative_to(self.base_dir)
        except ValueError as e:
            msg = f"Path traversal attempt detected: {storage_ref}"
            raise ValueError(msg) from e

        return resolved_path

    async def put(self, data: bytes, *, content_type: str, filename: str | None = None) -> str:
        """Store blob, return storage_ref string.

        Args:
            data: Binary data to store
            content_type: MIME type (e.g., 'image/jpeg')
            filename: Optional original filename for extension hint

        Returns:
            Storage reference string (e.g., 'local://2026/02/16/abc123.jpg')
        """
        key = self._generate_key(content_type, filename)
        file_path = self.base_dir / key

        # Create parent directories if needed
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write blob to disk
        file_path.write_bytes(data)

        return BlobRef(scheme=self.scheme, key=key).to_ref()

    async def get(self, storage_ref: str) -> bytes:
        """Retrieve blob by storage_ref.

        Args:
            storage_ref: Storage reference string

        Returns:
            Binary blob data

        Raises:
            BlobNotFoundError: If blob does not exist
        """
        file_path = self._ref_to_path(storage_ref)

        if not file_path.exists():
            raise BlobNotFoundError(storage_ref)

        return file_path.read_bytes()

    async def delete(self, storage_ref: str) -> None:
        """Delete blob.

        Args:
            storage_ref: Storage reference string

        Raises:
            BlobNotFoundError: If blob does not exist
        """
        file_path = self._ref_to_path(storage_ref)

        if not file_path.exists():
            raise BlobNotFoundError(storage_ref)

        file_path.unlink()

    async def exists(self, storage_ref: str) -> bool:
        """Check if blob exists.

        Args:
            storage_ref: Storage reference string

        Returns:
            True if blob exists, False otherwise
        """
        try:
            file_path = self._ref_to_path(storage_ref)
            return file_path.exists()
        except ValueError:
            # Wrong scheme or path traversal, blob doesn't exist in this store
            return False
