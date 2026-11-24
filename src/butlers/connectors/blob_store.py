"""The connector-owned S3 blob store, built from the credential store.

Connector blobs belong to no single butler's schema: they are routing-layer
input stored before any butler is chosen, so the key prefix names the
connector tier (``{bucket}/connectors/``), which every butler's attachment
tools accept (RFC 0016).
"""

from __future__ import annotations

import logging

from butlers.credential_store import CredentialStore
from butlers.storage import BlobStorageStartupError, BlobStore, S3BlobStore

logger = logging.getLogger(__name__)


async def connector_blob_store(store: CredentialStore, *, label: str) -> BlobStore | None:
    """Resolve ``BLOB_S3_*`` and return a checked store, or ``None``; never raises.

    ``label`` names the connector in log lines. A ``None`` result means the
    connector records attachments as unavailable instead of storing them.
    """
    try:
        s3_endpoint = await store.resolve("BLOB_S3_ENDPOINT_URL", env_fallback=False)
        s3_bucket = await store.resolve("BLOB_S3_BUCKET", env_fallback=False)
        s3_region = await store.resolve("BLOB_S3_REGION", env_fallback=False)
        s3_access_key = await store.resolve("BLOB_S3_ACCESS_KEY_ID", env_fallback=False)
        s3_secret_key = await store.resolve("BLOB_S3_SECRET_ACCESS_KEY", env_fallback=False)
    except Exception as exc:
        logger.warning("%s: BLOB_S3_* credential resolution failed (non-fatal): %s", label, exc)
        return None

    if not s3_endpoint or not s3_bucket:
        logger.warning(
            "%s: S3 blob storage not configured (missing BLOB_S3_ENDPOINT_URL / "
            "BLOB_S3_BUCKET); attachments will be recorded as unavailable. Configure via "
            "the dashboard secrets UI (/secrets).",
            label,
        )
        return None

    blob_store = S3BlobStore(
        bucket=s3_bucket,
        butler_name="connectors",
        endpoint_url=s3_endpoint,
        access_key_id=s3_access_key,
        secret_access_key=s3_secret_key,
        region=s3_region or "us-east-1",
    )
    try:
        await blob_store.startup_check()
    except BlobStorageStartupError as exc:
        logger.warning(
            "%s: S3 blob storage unavailable; attachments will be recorded as unavailable: %s",
            label,
            exc,
        )
        return None
    return blob_store
