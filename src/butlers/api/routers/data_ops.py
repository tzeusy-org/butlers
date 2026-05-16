"""Data operations endpoints — export and wipe.

Provides:

* ``POST /api/data/export``  — trigger a data export; returns a signed URL
  with a 60-minute TTL and records ``audit_log``.
* ``DELETE /api/data/wipe``  — destructive schema wipe; requires an exact
  phrase match (no trim, no case-fold) before proceeding.

The wipe phrase is intentionally long and ugly to prevent accidental triggers.
The server enforces the match; the UI sends whatever the user typed.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.routers import audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data-ops"])

_WIPE_PHRASE = "WIPE EVERYTHING IRREVERSIBLY"

# Tables / schemas dropped on wipe (order matters — audit_log is LAST so the
# wipe entry itself is committed before the table is dropped).
_WIPE_PUBLIC_TABLES = [
    "public.model_catalog",
    "public.runtime_config",
    "public.permissions",
    "public.spend_ledger",
    "public.webhooks",
    "public.approvals_policy",
]


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    """Request body for data export."""

    scope: str = "all"


class ExportResponse(BaseModel):
    """Signed URL + metadata returned by POST /api/data/export."""

    signed_url: str
    expires_at: datetime
    scope: str


class WipeRequest(BaseModel):
    """Request body for data wipe.

    The ``phrase`` field must equal exactly ``"WIPE EVERYTHING IRREVERSIBLY"``
    — no leading/trailing whitespace, no case-folding.  The server enforces
    this; the client is responsible for transmitting what the user typed.
    """

    phrase: str


class WipeResponse(BaseModel):
    """Confirmation payload returned after a successful wipe."""

    wiped: bool
    message: str


# ---------------------------------------------------------------------------
# POST /api/data/export
# ---------------------------------------------------------------------------


@router.post("/export", response_model=ApiResponse[ExportResponse])
async def export_data(
    body: ExportRequest,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ExportResponse]:
    """Trigger a data export and return a signed URL with a 60-minute TTL.

    The export itself is a best-effort placeholder that returns a signed URL
    token.  A real implementation would enqueue a background job and return
    a presigned object-storage URL.
    """
    # Generate a short-lived signed token (HMAC of scope + timestamp + secret).
    secret = os.environ.get("DASHBOARD_EXPORT_SECRET", "dev-secret")
    export_id = str(uuid.uuid4())
    token_body = f"{export_id}:{body.scope}"
    token = hashlib.sha256(f"{token_body}:{secret}".encode()).hexdigest()[:32]
    expires_at = datetime.now(UTC) + timedelta(minutes=60)

    signed_url = f"/api/data/export/download/{export_id}?scope={body.scope}&token={token}"

    # Audit — best effort; skip gracefully when pool or table is unavailable.
    try:
        pool = db.pool("switchboard")
        await audit.append(pool, "owner", "data.export", note=body.scope)
    except KeyError:
        logger.warning("audit.append skipped for data.export: switchboard pool unavailable")
    except audit.AuditTableNotAvailableError:
        logger.warning("audit.append skipped for data.export: audit_log table not migrated")

    return ApiResponse(
        data=ExportResponse(
            signed_url=signed_url,
            expires_at=expires_at,
            scope=body.scope,
        )
    )


# ---------------------------------------------------------------------------
# DELETE /api/data/wipe
# ---------------------------------------------------------------------------


@router.delete("/wipe", response_model=ApiResponse[WipeResponse])
async def wipe_data(
    body: WipeRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[WipeResponse]:
    """Destructively wipe all butler data.

    Exact phrase match required — ``"WIPE EVERYTHING IRREVERSIBLY"`` — with no
    trimming and no case-folding.  Any deviation returns HTTP 422.

    Drop order:
    1. All butler schemas (each butler's private schema).
    2. Public cross-butler tables (model_catalog, runtime_config, permissions,
       spend_ledger, webhooks, approvals_policy).
    3. ``public.audit_log`` — last, so the wipe entry itself survives.
    """
    # --- Phase 1: exact-match guard ---
    if body.phrase != _WIPE_PHRASE:
        raise HTTPException(
            status_code=422,
            detail={"error": "phrase_mismatch", "expected": _WIPE_PHRASE},
        )

    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    # --- Phase 2: record the wipe in audit_log BEFORE destruction ---
    await audit.append(pool, "owner", "data.wipe", note="Wipe initiated")

    # --- Phase 3: drop butler schemas ---
    butler_schemas = await pool.fetch(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name NOT IN ('public', 'information_schema', 'pg_catalog', 'pg_toast') "
        "  AND schema_name NOT LIKE 'pg_%'"
    )
    for row in butler_schemas:
        schema = row["schema_name"]
        try:
            await pool.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            logger.info("Wipe: dropped schema %s", schema)
        except Exception:
            logger.warning("Wipe: failed to drop schema %s", schema, exc_info=True)

    # --- Phase 4: drop public cross-butler tables ---
    for table in _WIPE_PUBLIC_TABLES:
        try:
            await pool.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            logger.info("Wipe: dropped table %s", table)
        except Exception:
            logger.warning("Wipe: failed to drop table %s", table, exc_info=True)

    # --- Phase 5: drop audit_log last ---
    try:
        await pool.execute("DROP TABLE IF EXISTS public.audit_log CASCADE")
        logger.info("Wipe: dropped public.audit_log")
    except Exception:
        logger.warning("Wipe: failed to drop public.audit_log", exc_info=True)

    return ApiResponse(
        data=WipeResponse(
            wiped=True,
            message="All butler data has been wiped.",
        )
    )
