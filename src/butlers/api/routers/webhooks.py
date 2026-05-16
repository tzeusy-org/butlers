"""Webhook management endpoints.

Provides full CRUD over ``public.webhooks`` plus a test-fire endpoint:

* ``GET    /api/webhooks``           — list all webhook registrations.
* ``POST   /api/webhooks``           — create a new registration.
* ``GET    /api/webhooks/{id}``      — get one registration.
* ``PUT    /api/webhooks/{id}``      — update a registration.
* ``DELETE /api/webhooks/{id}``      — delete a registration.
* ``POST   /api/webhooks/{id}/test`` — synthesize a test event, dispatch, return result.

Payloads are signed with HMAC-SHA256 using the per-row ``secret_hash`` field.
Retries follow the ``retry_policy`` JSONB column (``{"max_attempts": N,
"backoff_seconds": M}``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.routers import audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

_DEFAULT_RETRY_POLICY = {"max_attempts": 3, "backoff_seconds": 2}
_TEST_TIMEOUT_SECONDS = 10


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class RetryPolicy(BaseModel):
    """Webhook retry configuration."""

    max_attempts: int = 3
    backoff_seconds: int = 2


class WebhookCreate(BaseModel):
    """Request body for creating a webhook."""

    endpoint: str
    events: list[str] = []
    enabled: bool = True
    secret: str | None = None
    retry_policy: RetryPolicy = RetryPolicy()


class WebhookUpdate(BaseModel):
    """Request body for updating a webhook (all fields optional)."""

    endpoint: str | None = None
    events: list[str] | None = None
    enabled: bool | None = None
    secret: str | None = None
    retry_policy: RetryPolicy | None = None


class WebhookRow(BaseModel):
    """A webhook registration returned by the API."""

    id: uuid.UUID
    endpoint: str
    events: list[str]
    enabled: bool
    last_test_at: datetime | None = None
    last_test_ok: bool | None = None
    retry_policy: RetryPolicy
    created_at: datetime
    updated_at: datetime


class WebhookTestResult(BaseModel):
    """Result of a test-fire."""

    webhook_id: uuid.UUID
    status_code: int | None = None
    latency_ms: float | None = None
    ok: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_secret(secret: str) -> str:
    """One-way hash of the webhook secret for storage."""
    return hashlib.sha256(secret.encode()).hexdigest()


def _sign_payload(payload: bytes, secret: str) -> str:
    """HMAC-SHA256 signature of *payload* using *secret*."""
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _row_to_model(row: dict | object) -> WebhookRow:
    """Convert an asyncpg record (or dict) to ``WebhookRow``."""
    rp = row["retry_policy"]
    if isinstance(rp, str):
        rp = json.loads(rp)
    policy = RetryPolicy(
        max_attempts=rp.get("max_attempts", 3),
        backoff_seconds=rp.get("backoff_seconds", 2),
    )
    evts = row["events"]
    if isinstance(evts, str):
        evts = json.loads(evts)

    return WebhookRow(
        id=row["id"],
        endpoint=row["endpoint"],
        events=evts if isinstance(evts, list) else [],
        enabled=row["enabled"],
        last_test_at=row["last_test_at"],
        last_test_ok=row["last_test_ok"],
        retry_policy=policy,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _dispatch_webhook(
    endpoint: str,
    payload: dict,
    secret_hash: str | None,
    retry_policy: RetryPolicy,
) -> WebhookTestResult:
    """Fire the webhook and return the result including latency.

    ``secret_hash`` is already the stored hash; we use it as the signing key
    directly (callers store the hash so plaintext secret is not retrievable).
    """
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if secret_hash:
        sig = hmac.new(secret_hash.encode(), raw, hashlib.sha256).hexdigest()
        headers["X-Butler-Signature"] = f"sha256={sig}"

    max_attempts = retry_policy.max_attempts
    backoff = retry_policy.backoff_seconds
    wh_id = payload.get("webhook_id", "unknown")

    last_error: str | None = None
    last_status: int | None = None
    last_latency: float | None = None

    for attempt in range(max_attempts):
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_TEST_TIMEOUT_SECONDS) as client:
                resp = await client.post(endpoint, content=raw, headers=headers)
            last_latency = (time.monotonic() - t0) * 1000
            last_status = resp.status_code
            if resp.is_success:
                return WebhookTestResult(
                    webhook_id=wh_id,
                    status_code=last_status,
                    latency_ms=last_latency,
                    ok=True,
                )
            last_error = f"HTTP {resp.status_code}"
        except Exception as exc:
            last_latency = (time.monotonic() - t0) * 1000
            last_error = str(exc)
            logger.warning(
                "Webhook dispatch attempt %d/%d failed: %s", attempt + 1, max_attempts, exc
            )

        if attempt < max_attempts - 1:
            await asyncio.sleep(backoff)

    return WebhookTestResult(
        webhook_id=wh_id,
        status_code=last_status,
        latency_ms=last_latency,
        ok=False,
        error=last_error,
    )


# ---------------------------------------------------------------------------
# GET /api/webhooks
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[list[WebhookRow]])
async def list_webhooks(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[WebhookRow]]:
    """Return all webhook registrations ordered by created_at DESC."""
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    rows = await pool.fetch(
        "SELECT id, endpoint, events, enabled, last_test_at, last_test_ok, "
        "       retry_policy, created_at, updated_at "
        "FROM public.webhooks ORDER BY created_at DESC"
    )
    return ApiResponse(data=[_row_to_model(r) for r in rows])


# ---------------------------------------------------------------------------
# POST /api/webhooks
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse[WebhookRow], status_code=201)
async def create_webhook(
    body: WebhookCreate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[WebhookRow]:
    """Create a new webhook registration."""
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    secret_hash = _hash_secret(body.secret) if body.secret else None
    now = datetime.now(UTC)
    row_id = str(uuid.uuid4())

    rp_json = body.retry_policy.model_dump()

    row = await pool.fetchrow(
        "INSERT INTO public.webhooks "
        "(id, endpoint, events, enabled, secret_hash, retry_policy, created_at, updated_at) "
        "VALUES ($1::uuid, $2, $3::jsonb, $4, $5, $6::jsonb, $7, $8) "
        "RETURNING id, endpoint, events, enabled, last_test_at, last_test_ok, "
        "          retry_policy, created_at, updated_at",
        row_id,
        body.endpoint,
        json.dumps(body.events),
        body.enabled,
        secret_hash,
        json.dumps(rp_json),
        now,
        now,
    )

    await audit.append(pool, "owner", "webhook.create", target=str(row_id))

    return ApiResponse(data=_row_to_model(row))


# ---------------------------------------------------------------------------
# GET /api/webhooks/{id}
# ---------------------------------------------------------------------------


@router.get("/{webhook_id}", response_model=ApiResponse[WebhookRow])
async def get_webhook(
    webhook_id: uuid.UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[WebhookRow]:
    """Return one webhook registration by ID."""
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    row = await pool.fetchrow(
        "SELECT id, endpoint, events, enabled, last_test_at, last_test_ok, "
        "       retry_policy, created_at, updated_at "
        "FROM public.webhooks WHERE id = $1::uuid",
        str(webhook_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

    return ApiResponse(data=_row_to_model(row))


# ---------------------------------------------------------------------------
# PUT /api/webhooks/{id}
# ---------------------------------------------------------------------------


@router.put("/{webhook_id}", response_model=ApiResponse[WebhookRow])
async def update_webhook(
    webhook_id: uuid.UUID,
    body: WebhookUpdate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[WebhookRow]:
    """Update one webhook registration (partial update — only supplied fields change)."""
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    existing = await pool.fetchrow(
        "SELECT id, endpoint, events, enabled, secret_hash, last_test_at, last_test_ok, "
        "       retry_policy, created_at, updated_at "
        "FROM public.webhooks WHERE id = $1::uuid",
        str(webhook_id),
    )
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

    now = datetime.now(UTC)

    new_endpoint = body.endpoint if body.endpoint is not None else existing["endpoint"]
    new_enabled = body.enabled if body.enabled is not None else existing["enabled"]

    existing_events = existing["events"]
    if isinstance(existing_events, str):
        existing_events = json.loads(existing_events)
    new_events = body.events if body.events is not None else existing_events

    existing_rp = existing["retry_policy"]
    if isinstance(existing_rp, str):
        existing_rp = json.loads(existing_rp)
    new_rp = body.retry_policy.model_dump() if body.retry_policy is not None else existing_rp

    new_secret_hash = (
        _hash_secret(body.secret) if body.secret is not None else existing["secret_hash"]
    )

    row = await pool.fetchrow(
        "UPDATE public.webhooks "
        "SET endpoint = $1, events = $2::jsonb, enabled = $3, secret_hash = $4, "
        "    retry_policy = $5::jsonb, updated_at = $6 "
        "WHERE id = $7::uuid "
        "RETURNING id, endpoint, events, enabled, last_test_at, last_test_ok, "
        "          retry_policy, created_at, updated_at",
        new_endpoint,
        json.dumps(new_events),
        new_enabled,
        new_secret_hash,
        json.dumps(new_rp),
        now,
        str(webhook_id),
    )

    await audit.append(pool, "owner", "webhook.update", target=str(webhook_id))

    return ApiResponse(data=_row_to_model(row))


# ---------------------------------------------------------------------------
# DELETE /api/webhooks/{id}
# ---------------------------------------------------------------------------


class WebhookDeleteResponse(BaseModel):
    """Confirmation of webhook deletion."""

    deleted: bool
    id: uuid.UUID


@router.delete("/{webhook_id}", response_model=ApiResponse[WebhookDeleteResponse])
async def delete_webhook(
    webhook_id: uuid.UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[WebhookDeleteResponse]:
    """Delete a webhook registration."""
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    result = await pool.execute(
        "DELETE FROM public.webhooks WHERE id = $1::uuid",
        str(webhook_id),
    )
    deleted = result != "DELETE 0"

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

    await audit.append(pool, "owner", "webhook.delete", target=str(webhook_id))

    return ApiResponse(data=WebhookDeleteResponse(deleted=True, id=webhook_id))


# ---------------------------------------------------------------------------
# POST /api/webhooks/{id}/test
# ---------------------------------------------------------------------------


@router.post("/{webhook_id}/test", response_model=ApiResponse[WebhookTestResult])
async def test_webhook(
    webhook_id: uuid.UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[WebhookTestResult]:
    """Synthesize a ``webhook.test`` event and fire it at the registered endpoint.

    Signs the payload with the stored secret hash (HMAC-SHA256).
    Returns the receiver HTTP status code and latency.
    Retries per the ``retry_policy`` column.
    """
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    row = await pool.fetchrow(
        "SELECT id, endpoint, events, enabled, secret_hash, last_test_at, last_test_ok, "
        "       retry_policy, created_at, updated_at "
        "FROM public.webhooks WHERE id = $1::uuid",
        str(webhook_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

    rp_raw = row["retry_policy"]
    if isinstance(rp_raw, str):
        rp_raw = json.loads(rp_raw)
    retry_policy = RetryPolicy(
        max_attempts=rp_raw.get("max_attempts", 3),
        backoff_seconds=rp_raw.get("backoff_seconds", 2),
    )

    test_payload = {
        "event": "webhook.test",
        "webhook_id": str(webhook_id),
        "timestamp": datetime.now(UTC).isoformat(),
    }

    result = await _dispatch_webhook(
        endpoint=row["endpoint"],
        payload=test_payload,
        secret_hash=row["secret_hash"],
        retry_policy=retry_policy,
    )
    result.webhook_id = webhook_id  # type: ignore[assignment]

    # Update last_test_at / last_test_ok in the DB.
    await pool.execute(
        "UPDATE public.webhooks SET last_test_at = now(), last_test_ok = $1 WHERE id = $2::uuid",
        result.ok,
        str(webhook_id),
    )

    await audit.append(
        pool, "owner", "webhook.test", target=str(webhook_id), note=f"ok={result.ok}"
    )

    return ApiResponse(data=result)
