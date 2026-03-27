"""Blob storage configuration status and connectivity test endpoints.

Provides ``router`` at ``/api/settings/blob-storage``:

- ``GET  /api/settings/blob-storage``      — current config status (which keys are set)
- ``POST /api/settings/blob-storage/test`` — test S3 connectivity using stored secrets
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings/blob-storage", tags=["blob-storage"])

_BLOB_KEYS = [
    "BLOB_S3_ENDPOINT_URL",
    "BLOB_S3_BUCKET",
    "BLOB_S3_REGION",
    "BLOB_S3_ACCESS_KEY_ID",
    "BLOB_S3_SECRET_ACCESS_KEY",
]


def _get_db_manager() -> DatabaseManager:
    """Dependency stub -- overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class BlobStorageStatus(BaseModel):
    """Current blob storage configuration status."""

    endpoint_url: str | None = None
    bucket: str | None = None
    region: str | None = None
    has_access_key: bool = False
    has_secret_key: bool = False
    configured: bool = False


class BlobStorageTestResult(BaseModel):
    """Result of an S3 connectivity test."""

    success: bool
    error: str | None = None
    latency_ms: int = 0
    endpoint_url: str | None = None
    bucket: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shared_store(db: DatabaseManager) -> CredentialStore:
    """Return a CredentialStore backed by the shared credential pool."""
    try:
        pool = db.credential_shared_pool()
    except KeyError:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="Shared credential database is not available",
        )
    return CredentialStore(pool)


async def _load_blob_config(store: CredentialStore) -> dict[str, str | None]:
    """Load all BLOB_S3_* secrets from the shared credential store."""
    config: dict[str, str | None] = {}
    for key in _BLOB_KEYS:
        config[key] = await store.load(key)
    return config


# ---------------------------------------------------------------------------
# GET /api/settings/blob-storage — config status
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[BlobStorageStatus])
async def get_blob_storage_status(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[BlobStorageStatus]:
    """Return current blob storage configuration status.

    Shows which fields are configured without revealing secret values.
    """
    store = _shared_store(db)
    config = await _load_blob_config(store)

    endpoint_url = config.get("BLOB_S3_ENDPOINT_URL")
    bucket = config.get("BLOB_S3_BUCKET")
    region = config.get("BLOB_S3_REGION")
    has_access_key = bool(config.get("BLOB_S3_ACCESS_KEY_ID"))
    has_secret_key = bool(config.get("BLOB_S3_SECRET_ACCESS_KEY"))

    configured = bool(endpoint_url and bucket and has_access_key and has_secret_key)

    return ApiResponse[BlobStorageStatus](
        data=BlobStorageStatus(
            endpoint_url=endpoint_url,
            bucket=bucket,
            region=region,
            has_access_key=has_access_key,
            has_secret_key=has_secret_key,
            configured=configured,
        )
    )


# ---------------------------------------------------------------------------
# POST /api/settings/blob-storage/test — connectivity test
# ---------------------------------------------------------------------------


@router.post("/test", response_model=ApiResponse[BlobStorageTestResult])
async def test_blob_storage(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[BlobStorageTestResult]:
    """Test S3 connectivity by checking that the configured bucket exists.

    Uses the stored BLOB_S3_* secrets to instantiate an S3BlobStore and
    run ``startup_check()``.  Returns a structured result even on failure.
    """
    store = _shared_store(db)
    config = await _load_blob_config(store)

    endpoint_url = config.get("BLOB_S3_ENDPOINT_URL")
    bucket = config.get("BLOB_S3_BUCKET")
    region = config.get("BLOB_S3_REGION") or "us-east-1"
    access_key = config.get("BLOB_S3_ACCESS_KEY_ID")
    secret_key = config.get("BLOB_S3_SECRET_ACCESS_KEY")

    if not endpoint_url or not bucket:
        return ApiResponse[BlobStorageTestResult](
            data=BlobStorageTestResult(
                success=False,
                error="Endpoint URL and bucket must be configured before testing",
                endpoint_url=endpoint_url,
                bucket=bucket,
            )
        )

    from butlers.storage.blobs import S3BlobStore

    blob_store = S3BlobStore(
        bucket=bucket,
        butler_name="__test__",
        endpoint_url=endpoint_url,
        access_key_id=access_key,
        secret_access_key=secret_key,
        region=region,
    )

    t0 = time.monotonic()
    try:
        await blob_store.startup_check()
        latency_ms = int((time.monotonic() - t0) * 1000)
        return ApiResponse[BlobStorageTestResult](
            data=BlobStorageTestResult(
                success=True,
                latency_ms=latency_ms,
                endpoint_url=endpoint_url,
                bucket=bucket,
            )
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return ApiResponse[BlobStorageTestResult](
            data=BlobStorageTestResult(
                success=False,
                error=str(exc),
                latency_ms=latency_ms,
                endpoint_url=endpoint_url,
                bucket=bucket,
            )
        )
