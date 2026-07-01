"""Webhook management endpoints.

Provides full CRUD over ``public.webhooks`` plus a test-fire endpoint and
production event dispatch:

* ``GET    /api/webhooks``           — list all webhook registrations.
* ``POST   /api/webhooks``           — create a new registration.
* ``GET    /api/webhooks/{id}``      — get one registration.
* ``PUT    /api/webhooks/{id}``      — update a registration.
* ``DELETE /api/webhooks/{id}``      — delete a registration.
* ``POST   /api/webhooks/{id}/test`` — synthesize a test event, dispatch, return result.

Production dispatch
-------------------
:func:`dispatch_event` is the production entry-point.  Domain-event routers
(permissions, data-ops, webhook CRUD) call it after each mutation.  It fetches
all enabled webhooks whose ``events`` list contains the event name (or ``"*"``
for catch-all subscriptions), fires each one via :func:`_dispatch_webhook` with
full retry, and records the outcome on ``last_delivery_at`` /
``last_delivery_ok`` so the Settings Console aggregator can surface a
``kind="webhook_failure"`` attention item without mixing test results with real
delivery failures.

The call is fire-and-forget (an ``asyncio.Task`` is created) so domain
endpoints are never blocked by webhook delivery.

Payload signing
---------------
Secrets are stored encrypted with AES-256-GCM (see
:mod:`butlers.core.crypto.aes_gcm`).  On dispatch the plaintext is decrypted
and used directly as the HMAC-SHA256 key — the standard webhook verification
pattern that receivers can replicate without knowledge of server internals.

The server-side key is loaded from the ``WEBHOOK_SECRET_KEY`` environment
variable (64-hex-char, 32 bytes).  The daemon will fail loudly at the first
encrypt/decrypt call if this variable is missing.

Retries follow the ``retry_policy`` JSONB column (``{"max_attempts": N,
"backoff_seconds": M}``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.routers import audit
from butlers.core.crypto import aes_gcm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

#: Strong reference set that keeps background dispatch tasks alive until they
#: complete.  Without this, CPython's GC may collect a task before the event
#: loop finishes executing it.
_background_tasks: set[asyncio.Task] = set()

_DEFAULT_RETRY_POLICY = {"max_attempts": 3, "backoff_seconds": 2}
_TEST_TIMEOUT_SECONDS = 10

#: Number of leading secret characters surfaced in ``secret_prefix`` for human
#: identification.  The full secret is never echoed after creation.
_SECRET_PREFIX_LEN = 6

#: Column projection for endpoints returning the public ``WebhookRow`` shape
#: (no ``secret_encrypted``).  Centralised so the SELECT/RETURNING column lists
#: stay in lockstep with :func:`_row_to_model`.
_WEBHOOK_PROJECTION = (
    "id, endpoint, events, enabled, secret_prefix, last_test_at, last_test_ok, "
    "retry_policy, created_at, updated_at"
)


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
    """Request body for creating a webhook.

    The signing secret is generated server-side; clients cannot supply one.
    """

    endpoint: str
    events: list[str] = []
    enabled: bool = True
    retry_policy: RetryPolicy = RetryPolicy()


class WebhookUpdate(BaseModel):
    """Request body for updating a webhook (all fields optional).

    Set ``regenerate_secret=True`` to rotate the signing secret; the new secret
    is returned ONCE in the response.  Without it the secret is left untouched
    and never echoed.
    """

    endpoint: str | None = None
    events: list[str] | None = None
    enabled: bool | None = None
    retry_policy: RetryPolicy | None = None
    regenerate_secret: bool = False


class WebhookRow(BaseModel):
    """A webhook registration returned by the API.

    The plaintext signing secret is NEVER included — only ``secret_prefix``
    (the first few characters plus an ellipsis) for human identification.
    """

    id: uuid.UUID
    endpoint: str
    events: list[str]
    enabled: bool
    secret_prefix: str | None = None
    last_test_at: datetime | None = None
    last_test_ok: bool | None = None
    retry_policy: RetryPolicy
    created_at: datetime
    updated_at: datetime


class WebhookWithSecret(WebhookRow):
    """A webhook plus its plaintext secret, returned ONCE at create/regenerate.

    ``secret`` is populated only by ``POST /api/webhooks`` and by
    ``PUT /api/webhooks/{id}`` with ``regenerate_secret=True``.  Every other
    endpoint returns :class:`WebhookRow` (no secret).
    """

    secret: str | None = None


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


def _generate_secret() -> str:
    """Generate a fresh, URL-safe webhook signing secret."""
    return secrets.token_urlsafe(32)


def _secret_prefix(secret: str) -> str:
    """Return the first few chars of *secret* plus an ellipsis for display."""
    return f"{secret[:_SECRET_PREFIX_LEN]}…"


def _encrypt_secret(secret: str) -> bytes:
    """Encrypt a webhook secret with AES-256-GCM for storage."""
    return aes_gcm.encrypt(secret)


def _decrypt_secret(ciphertext: bytes) -> str:
    """Decrypt a stored AES-256-GCM webhook secret to plaintext."""
    return aes_gcm.decrypt(ciphertext)


def _sign_payload(payload: bytes, secret: str) -> str:
    """HMAC-SHA256 signature of *payload* using the plaintext *secret*.

    This is the standard webhook signing pattern: the receiver can verify by
    computing ``HMAC-SHA256(secret, body)`` with their shared secret directly,
    without any additional hashing step.
    """
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
        secret_prefix=row["secret_prefix"],
        last_test_at=row["last_test_at"],
        last_test_ok=row["last_test_ok"],
        retry_policy=policy,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _dispatch_webhook(
    endpoint: str,
    payload: dict,
    secret_encrypted: bytes | None,
    retry_policy: RetryPolicy,
) -> WebhookTestResult:
    """Fire the webhook and return the result including latency.

    If *secret_encrypted* is provided it is decrypted to the plaintext secret
    which is then used as the HMAC-SHA256 signing key — the standard pattern
    that allows receivers to verify with their shared plaintext secret.
    """
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if secret_encrypted:
        plaintext_secret = _decrypt_secret(secret_encrypted)
        sig = _sign_payload(raw, plaintext_secret)
        headers["X-Butler-Signature"] = f"sha256={sig}"

    max_attempts = retry_policy.max_attempts
    backoff = retry_policy.backoff_seconds
    wh_id = payload.get("webhook_id", "unknown")

    last_error: str | None = None
    last_status: int | None = None
    last_latency: float | None = None

    async with httpx.AsyncClient(timeout=_TEST_TIMEOUT_SECONDS) as client:
        for attempt in range(max_attempts):
            t0 = time.monotonic()
            try:
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
            except httpx.RequestError as exc:
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
# Production event dispatch
# ---------------------------------------------------------------------------


async def _dispatch_event_impl(
    pool: object,
    event_name: str,
    payload: dict,
) -> None:
    """Deliver *event_name* to all matching enabled webhooks and record outcomes.

    A webhook row matches when its ``events`` list contains *event_name* or the
    literal string ``"*"`` (catch-all).  An empty ``events`` list does NOT match
    -- the webhook must opt in to at least one event.

    For each matching webhook the delivery is attempted via
    :func:`_dispatch_webhook` (with full retry per ``retry_policy``).  The
    outcome is written back to ``last_delivery_at`` / ``last_delivery_ok`` so
    the Settings Console aggregator can surface a ``kind="webhook_failure"``
    attention item that derives from real delivery failures rather than test
    results.

    Failures are logged at WARNING level but never propagate -- this is
    fire-and-forget from the caller's perspective.
    """
    try:
        rows = await pool.fetch(
            "SELECT id, endpoint, secret_encrypted, retry_policy, events "
            "FROM public.webhooks WHERE enabled = true"
        )
    except Exception as exc:
        logger.warning("dispatch_event: failed to fetch webhook rows: %s", exc)
        return

    for row in rows:
        try:
            evts = row["events"]
            if isinstance(evts, str):
                evts = json.loads(evts)
            if not isinstance(evts, list):
                evts = []
            if not evts:
                # Empty subscription list: opt-out by default.
                continue
            if event_name not in evts and "*" not in evts:
                continue

            rp_raw = row["retry_policy"]
            if isinstance(rp_raw, str):
                rp_raw = json.loads(rp_raw)
            if not isinstance(rp_raw, dict):
                rp_raw = {}
            retry_policy = RetryPolicy(
                max_attempts=rp_raw.get("max_attempts", 3),
                backoff_seconds=rp_raw.get("backoff_seconds", 2),
            )

            wh_id = row["id"]
            delivery_payload = {"event": event_name, "webhook_id": str(wh_id), **payload}

            try:
                result = await _dispatch_webhook(
                    endpoint=row["endpoint"],
                    payload=delivery_payload,
                    secret_encrypted=row["secret_encrypted"],
                    retry_policy=retry_policy,
                )
            except Exception as exc:
                logger.warning(
                    "dispatch_event: unhandled error delivering %s to %s: %s",
                    event_name,
                    wh_id,
                    exc,
                )
                ok = False
            else:
                ok = result.ok
                if not ok:
                    logger.warning(
                        "dispatch_event: webhook %s exhausted retries for event %s: %s",
                        wh_id,
                        event_name,
                        result.error,
                    )

            try:
                await pool.execute(
                    "UPDATE public.webhooks "
                    "SET last_delivery_at = now(), last_delivery_ok = $1 "
                    "WHERE id = $2::uuid",
                    ok,
                    str(wh_id),
                )
            except Exception as exc:
                logger.warning(
                    "dispatch_event: failed to record delivery outcome for %s: %s", wh_id, exc
                )
        except Exception as exc:
            logger.warning("dispatch_event: failed to process webhook row: %s", exc)


def dispatch_event(
    pool: object,
    event_name: str,
    payload: dict | None = None,
) -> asyncio.Task:
    """Schedule production webhook dispatch for *event_name* as a background task.

    Creates an ``asyncio.Task`` that delivers the event to all enabled,
    subscribed webhooks.  Returns the task so callers can optionally await it
    in tests; in production it is fire-and-forget.

    Parameters
    ----------
    pool:
        An asyncpg connection pool (e.g. ``db.pool("switchboard")``).
    event_name:
        Domain event name, e.g. ``"permission.set"`` or ``"data.export"``.
    payload:
        Optional extra fields merged into the webhook JSON body alongside
        ``event`` and ``webhook_id``.  Use for event-specific context such as
        ``{"target": "butler.perm", "granted": True}``.

    Returns
    -------
    asyncio.Task
        The background task.  Callers in production code should not await it;
        tests may await it to drive the delivery synchronously.
    """
    task = asyncio.create_task(_dispatch_event_impl(pool, event_name, payload or {}))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


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
        f"SELECT {_WEBHOOK_PROJECTION} FROM public.webhooks ORDER BY created_at DESC"
    )
    return ApiResponse(data=[_row_to_model(r) for r in rows])


# ---------------------------------------------------------------------------
# POST /api/webhooks
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse[WebhookWithSecret], status_code=201)
async def create_webhook(
    body: WebhookCreate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[WebhookWithSecret]:
    """Create a new webhook registration with a server-generated signing secret.

    A fresh secret is generated, stored encrypted with AES-256-GCM, and returned
    exactly ONCE in this response body.  No subsequent endpoint ever echoes it
    again — only ``secret_prefix`` is exposed thereafter.
    """
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    secret = _generate_secret()
    secret_encrypted = _encrypt_secret(secret)
    secret_prefix = _secret_prefix(secret)
    now = datetime.now(UTC)
    row_id = str(uuid.uuid4())

    rp_json = body.retry_policy.model_dump()

    row = await pool.fetchrow(
        "INSERT INTO public.webhooks "
        "(id, endpoint, events, enabled, secret_encrypted, secret_prefix, "
        " retry_policy, created_at, updated_at) "
        "VALUES ($1::uuid, $2, $3::jsonb, $4, $5, $6, $7::jsonb, $8, $9) "
        f"RETURNING {_WEBHOOK_PROJECTION}",
        row_id,
        body.endpoint,
        json.dumps(body.events),
        body.enabled,
        secret_encrypted,
        secret_prefix,
        json.dumps(rp_json),
        now,
        now,
    )

    await audit.append(pool, "owner", "webhook.create", target=str(row_id))
    dispatch_event(pool, "webhook.create", {"target": row_id})

    base = _row_to_model(row)
    return ApiResponse(data=WebhookWithSecret(**base.model_dump(), secret=secret))


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
        f"SELECT {_WEBHOOK_PROJECTION} FROM public.webhooks WHERE id = $1::uuid",
        str(webhook_id),
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

    return ApiResponse(data=_row_to_model(row))


# ---------------------------------------------------------------------------
# PUT /api/webhooks/{id}
# ---------------------------------------------------------------------------


@router.put("/{webhook_id}", response_model=ApiResponse[WebhookWithSecret])
async def update_webhook(
    webhook_id: uuid.UUID,
    body: WebhookUpdate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[WebhookWithSecret]:
    """Update one webhook registration (partial update — only supplied fields change).

    With ``regenerate_secret=True`` a fresh signing secret is generated and
    returned ONCE in the ``secret`` field.  Without it, the stored secret is left
    untouched and ``secret`` is ``null`` (never echoed).
    """
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    existing = await pool.fetchrow(
        "SELECT id, endpoint, events, enabled, secret_encrypted, secret_prefix, "
        "       last_test_at, last_test_ok, retry_policy, created_at, updated_at "
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

    # Secret rotation: only when explicitly requested.  Otherwise the stored
    # ciphertext and prefix are preserved verbatim and never echoed.
    generated_secret: str | None = None
    if body.regenerate_secret:
        generated_secret = _generate_secret()
        new_secret_encrypted = _encrypt_secret(generated_secret)
        new_secret_prefix = _secret_prefix(generated_secret)
    else:
        new_secret_encrypted = existing["secret_encrypted"]
        new_secret_prefix = existing["secret_prefix"]

    row = await pool.fetchrow(
        "UPDATE public.webhooks "
        "SET endpoint = $1, events = $2::jsonb, enabled = $3, secret_encrypted = $4, "
        "    secret_prefix = $5, retry_policy = $6::jsonb, updated_at = $7 "
        "WHERE id = $8::uuid "
        f"RETURNING {_WEBHOOK_PROJECTION}",
        new_endpoint,
        json.dumps(new_events),
        new_enabled,
        new_secret_encrypted,
        new_secret_prefix,
        json.dumps(new_rp),
        now,
        str(webhook_id),
    )

    await audit.append(pool, "owner", "webhook.update", target=str(webhook_id))
    dispatch_event(pool, "webhook.update", {"target": str(webhook_id)})

    base = _row_to_model(row)
    return ApiResponse(data=WebhookWithSecret(**base.model_dump(), secret=generated_secret))


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
    dispatch_event(pool, "webhook.delete", {"target": str(webhook_id)})

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

    Decrypts the stored AES-256-GCM secret and signs the payload with
    HMAC-SHA256(plaintext_secret, body) — the standard webhook signing pattern.
    Returns the receiver HTTP status code and latency.
    Retries per the ``retry_policy`` column.
    """
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    row = await pool.fetchrow(
        "SELECT id, endpoint, events, enabled, secret_encrypted, last_test_at, last_test_ok, "
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
        secret_encrypted=row["secret_encrypted"],
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
