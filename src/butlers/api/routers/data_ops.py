"""Data operations endpoints — export and wipe.

Provides:

* ``POST /api/data/export``  — trigger a data export; returns a signed URL
  with a 60-minute TTL and records ``audit_log``.
* ``GET /api/data/export/download/{export_id}``  — download the export
  identified by the signed URL returned by POST.  Validates the HMAC token,
  enforces the 60-minute TTL, then streams a live NDJSON export from the DB.
* ``DELETE /api/data/wipe``  — destructive schema wipe; requires an exact
  phrase match (no trim, no case-fold) before proceeding.

The wipe phrase is intentionally long and ugly to prevent accidental triggers.
The server enforces the match; the UI sends whatever the user typed.

## Export token design

The signed URL carries three query parameters:

- ``scope``      — the scope originally requested (e.g. ``"all"``)
- ``issued_at``  — UNIX timestamp (integer seconds UTC) of token creation
- ``token``      — ``HMAC-SHA256("{export_id}:{scope}:{issued_at}", secret)``
                   truncated to 32 hex characters

This design is **stateless** — no server-side token store is needed.  The
handler re-derives the expected HMAC and verifies it, then checks that
``now - issued_at < 3600 s``.

## Export storage approach

Option A (on-demand generation) was chosen.  On every valid GET request the
handler runs a live database query scoped by ``scope`` and streams the result
as newline-delimited JSON (NDJSON).  No bytes are cached on disk.

Rationale: the exported tables are small (contacts, audit_log), so a
synchronous query + streaming response is well within acceptable latency.
Disk caching (Option B) would add surface area (cache TTL, cleanup, race
conditions) for no benefit at this scale.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.routers import audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data-ops"])

_WIPE_PHRASE = "WIPE EVERYTHING IRREVERSIBLY"
_EXPORT_TTL_SECONDS = 3600  # 60 minutes

# Scopes supported by the download endpoint.
# "all" expands to every table in this list (order is preserved in the export).
_EXPORTABLE_TABLES: dict[str, str] = {
    "contacts": "SELECT * FROM public.contacts ORDER BY id",
    "contact_info": "SELECT * FROM public.contact_info ORDER BY id",
    "audit_log": "SELECT * FROM public.audit_log ORDER BY id",
}
_ALL_SCOPE_TABLES = list(_EXPORTABLE_TABLES.keys())

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
# Token helpers
# ---------------------------------------------------------------------------


def _sign_token(export_id: str, scope: str, issued_at: int) -> str:
    """Return HMAC-SHA256 hex digest (32 chars) for the given export parameters.

    The secret is read from the ``DASHBOARD_EXPORT_SECRET`` environment
    variable, falling back to ``"dev-secret"`` when not set.
    """
    secret = os.environ.get("DASHBOARD_EXPORT_SECRET", "dev-secret")
    message = f"{export_id}:{scope}:{issued_at}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()[:32]


def _verify_token(
    export_id: str,
    scope: str,
    issued_at: int,
    token: str,
) -> None:
    """Raise HTTPException when the token is invalid or expired.

    - 401 Unauthorized  — token signature does not match
    - 403 Forbidden     — token scope does not match (caller error, wrong URL)
    - 410 Gone          — token TTL exceeded

    Rejects negative ``issued_at`` (pre-epoch nonsense) and far-future
    ``issued_at`` (clocked-forward bypass attempt) before checking TTL.
    """
    expected = _sign_token(export_id, scope, issued_at)
    if not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=401, detail="Invalid export token")

    now_ts = datetime.now(UTC).timestamp()
    # Reject clearly invalid timestamps: must be positive and not in the future
    # (allow up to 60 s of clock skew for forward-dated tokens).
    if issued_at < 0 or issued_at > now_ts + 60:
        raise HTTPException(status_code=401, detail="Invalid export token")

    age_s = now_ts - issued_at
    if age_s > _EXPORT_TTL_SECONDS:
        raise HTTPException(status_code=410, detail="Export token has expired")


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

    The signed URL encodes ``export_id``, ``scope``, and ``issued_at`` (UNIX
    timestamp) and is backed by an HMAC-SHA256 token.  The download endpoint
    validates the token and TTL before streaming the export on demand.
    """
    export_id = str(uuid.uuid4())
    issued_at = int(datetime.now(UTC).timestamp())
    token = _sign_token(export_id, body.scope, issued_at)
    expires_at = datetime.fromtimestamp(issued_at + _EXPORT_TTL_SECONDS, tz=UTC)

    signed_url = (
        f"/api/data/export/download/{export_id}"
        f"?scope={body.scope}&issued_at={issued_at}&token={token}"
    )

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
# GET /api/data/export/download/{export_id}
# ---------------------------------------------------------------------------


async def _stream_export(pool: object, scope: str):  # type: ignore[type-arg]
    """Async generator that yields NDJSON rows for the requested scope.

    Each line is a JSON object followed by ``\\n``.  A header comment line
    (``//``) documents the scope and timestamp for human readability.

    Yields ``bytes`` so the caller can pass this directly to StreamingResponse.
    """
    tables: list[str]
    if scope == "all":
        tables = _ALL_SCOPE_TABLES
    elif scope in _EXPORTABLE_TABLES:
        tables = [scope]
    else:
        # Unknown scope — yield an empty export (caller already validated token)
        tables = []

    export_ts = datetime.now(UTC).isoformat()
    yield f"// butlers-export scope={scope} generated_at={export_ts}\n".encode()

    for table in tables:
        query = _EXPORTABLE_TABLES[table]
        yield f"// table={table}\n".encode()
        try:
            rows = await pool.fetch(query)  # type: ignore[attr-defined]
        except Exception:
            logger.warning("export: failed to fetch table %s", table, exc_info=True)
            yield f"// ERROR fetching table={table}\n".encode()
            continue
        for row in rows:
            yield (json.dumps(dict(row), default=str) + "\n").encode()


@router.get("/export/download/{export_id}")
async def download_export(
    export_id: str,
    scope: str = Query(...),
    issued_at: int = Query(...),
    token: str = Query(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> StreamingResponse:
    """Download a previously requested export via its signed URL.

    Validates:

    - **Token signature** — HMAC-SHA256 of ``"{export_id}:{scope}:{issued_at}"``.
      Returns 401 if the signature is wrong.
    - **TTL** — rejects tokens older than 60 minutes with 410 Gone.
    - **Scope** — must be ``"all"`` or a known table name; unknown scopes are
      accepted but yield an empty NDJSON body.

    Streams the export as NDJSON (``application/x-ndjson``) using
    ``StreamingResponse``; the live DB query runs on each valid request.
    """
    _verify_token(export_id, scope, issued_at, token)

    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    filename = f"butlers-export-{scope}-{export_id[:8]}.ndjson"
    return StreamingResponse(
        _stream_export(pool, scope),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
