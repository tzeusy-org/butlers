"""Data operations endpoints — export and wipe.

Provides:

* ``POST /api/data/export``  — trigger a data export; returns a signed URL
  with a 60-minute TTL and records ``audit_log``.
* ``GET /api/data/export/download/{export_id}``  — download the export
  identified by the signed URL returned by POST.  Validates the HMAC token,
  enforces the 60-minute TTL, then builds and returns an AES-256-GCM encrypted
  ZIP containing one NDJSON file per exported table.
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

## Export format — encrypted ZIP

On every valid GET request the handler:

1. Queries each table in the requested scope.
2. Serialises each table as NDJSON (one JSON object per line; one file per
   table inside the ZIP, named ``{schema}_{table}.ndjson``).
3. Deflate-compresses the files into an in-memory ZIP archive.
4. Encrypts the ZIP bytes with AES-256-GCM (nonce prepended).
5. Returns the encrypted bytes as ``application/octet-stream``.

The file on disk is therefore **not** a ZIP — it is an encrypted blob.

## Decryption contract

Wire format::

    [ nonce (12 bytes) ][ AES-256-GCM ciphertext + 16-byte auth tag ]

Encryption key: ``DASHBOARD_EXPORT_ENCRYPTION_KEY`` environment variable —
a 64-character hex string encoding 32 bytes (AES-256).  In dev/test the module
falls back to a 32-byte all-zeros key so that tests can run without the variable.

Decrypt with Python::

    import io, os, zipfile
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    data = open("butlers-export-all-XXXXXXXX.enc", "rb").read()
    key  = bytes.fromhex(os.environ["DASHBOARD_EXPORT_ENCRYPTION_KEY"])
    nonce, ct = data[:12], data[12:]
    zip_bytes = AESGCM(key).decrypt(nonce, ct, None)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            print(f"--- {name} ---")
            print(zf.read(name).decode())

## Supported export scopes

- ``memory`` — memory butler's facts, rules, and episodes tables
- ``audit``  — ``public.audit_log``
- ``config`` — ``public.runtime_config``, ``public.model_catalog``,
               ``public.permissions``
- ``all``    — union of every scope above (``full`` is accepted as an alias)
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import secrets
import uuid
import zipfile
from datetime import UTC, datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.routers import audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data-ops"])

_WIPE_PHRASE = "WIPE EVERYTHING IRREVERSIBLY"
_WIPE_ENABLED = False  # disabled pending atomic + authenticated reimplementation
_EXPORT_TTL_SECONDS = 3600  # 60 minutes

# ---------------------------------------------------------------------------
# Export scope map
# ---------------------------------------------------------------------------

# Each entry: scope_name -> [(display_table_name, SQL_query), ...]
# Columns `embedding`, `search_vector`, and `description_embedding` are
# excluded from serialisation — they are large binary/generated columns that
# would bloat the archive and are not useful for human-readable exports.
_SCOPE_MAP: dict[str, list[tuple[str, str]]] = {
    "memory": [
        ("memory.facts", "SELECT * FROM memory.facts ORDER BY created_at"),
        ("memory.rules", "SELECT * FROM memory.rules ORDER BY created_at"),
        ("memory.episodes", "SELECT * FROM memory.episodes ORDER BY created_at"),
    ],
    "audit": [
        ("public.audit_log", "SELECT * FROM public.audit_log ORDER BY id"),
    ],
    "config": [
        ("public.runtime_config", "SELECT * FROM public.runtime_config ORDER BY id"),
        ("public.model_catalog", "SELECT * FROM public.model_catalog ORDER BY id"),
        ("public.permissions", "SELECT * FROM public.permissions ORDER BY id"),
    ],
}

# "full" is an accepted alias for "all"
_SCOPE_ALIASES: dict[str, str] = {"full": "all"}

# All names a caller may pass as scope (including aliases)
_KNOWN_SCOPES: frozenset[str] = frozenset(_SCOPE_MAP) | {"all"} | frozenset(_SCOPE_ALIASES)

# Columns excluded from serialisation — large binary or generated columns
_SKIP_COLUMNS: frozenset[str] = frozenset({"embedding", "search_vector", "description_embedding"})

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
# Token helpers (HMAC-SHA256 signing for the export signed URL)
# ---------------------------------------------------------------------------

# Dev-mode fallback used when DASHBOARD_EXPORT_SECRET is unset and the process
# is NOT in production.  Tokens signed with this key are forgeable in dev/test,
# which is acceptable.  The literal "dev-secret" string is never used.
_DEV_EXPORT_SECRET = "dev-mode-export-secret-NOT-FOR-PRODUCTION"


def _is_production() -> bool:
    """Return True when the process is configured as production (ENV starts with 'prod')."""
    return os.environ.get("ENV", "").strip().lower().startswith("prod")


def _sign_token(export_id: str, scope: str, issued_at: int) -> str:
    """Return HMAC-SHA256 hex digest (32 chars) for the given export parameters.

    The secret is read from the ``DASHBOARD_EXPORT_SECRET`` environment
    variable.

    When the variable is not set:

    * **Production** (``ENV`` starts with ``"prod"``): raises ``RuntimeError``.
      Signing is refused; using a known literal default would make all tokens
      forgeable.
    * **Dev/test** (any other ``ENV`` value, including unset): falls back to an
      explicit non-literal dev secret.  Tokens are forgeable in dev, which is
      acceptable, but the literal ``"dev-secret"`` string is never used.
    """
    secret = os.environ.get("DASHBOARD_EXPORT_SECRET")
    if not secret:
        if _is_production():
            raise RuntimeError(
                "DASHBOARD_EXPORT_SECRET is not set in production. "
                "Refusing to sign export tokens with an insecure default. "
                "Set DASHBOARD_EXPORT_SECRET to a strong random secret."
            )
        secret = _DEV_EXPORT_SECRET
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
# Encryption helpers (AES-256-GCM)
# ---------------------------------------------------------------------------

# Dev-mode fallback key: 32 zero bytes.  Never used in production.
_DEV_EXPORT_ENCRYPTION_KEY: bytes = bytes(32)


def _get_export_encryption_key() -> bytes:
    """Return the AES-256 key for export ZIP encryption.

    Reads from ``DASHBOARD_EXPORT_ENCRYPTION_KEY`` (64 hex chars = 32 bytes).

    When the variable is not set:

    * **Production** (``ENV`` starts with ``"prod"``): raises ``RuntimeError``.
    * **Dev/test**: falls back to a 32-byte all-zeros key so tests run without
      the variable set.

    Generate a production key with::

        python -c "import secrets; print(secrets.token_hex(32))"
    """
    raw = os.environ.get("DASHBOARD_EXPORT_ENCRYPTION_KEY", "").strip()
    if not raw:
        if _is_production():
            raise RuntimeError(
                "DASHBOARD_EXPORT_ENCRYPTION_KEY is not set in production. "
                'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return _DEV_EXPORT_ENCRYPTION_KEY
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise RuntimeError(f"DASHBOARD_EXPORT_ENCRYPTION_KEY is not valid hex: {exc}") from exc
    if len(key) != 32:
        raise RuntimeError(
            f"DASHBOARD_EXPORT_ENCRYPTION_KEY must be 64 hex chars (32 bytes); got {len(key)} bytes"
        )
    return key


def _encrypt_export(data: bytes, *, key: bytes | None = None) -> bytes:
    """Encrypt *data* with AES-256-GCM.

    Wire format: ``nonce (12 bytes) || ciphertext + GCM auth tag (16 bytes)``.

    A fresh cryptographically-random 12-byte nonce is generated per call so
    that two calls with the same plaintext produce different ciphertext blobs.

    Args:
        data: Raw bytes to encrypt (typically a deflate-compressed ZIP).
        key:  Override the key for testing.  If ``None``, reads from the
              ``DASHBOARD_EXPORT_ENCRYPTION_KEY`` environment variable.

    Returns:
        Bytes in the layout ``nonce (12) || ciphertext+tag``.
    """
    if key is None:
        key = _get_export_encryption_key()
    aesgcm = AESGCM(key)
    nonce = secrets.token_bytes(12)
    ct = aesgcm.encrypt(nonce, data, None)
    return nonce + ct


def _decrypt_export(data: bytes, *, key: bytes | None = None) -> bytes:
    """Decrypt an export blob produced by :func:`_encrypt_export`.

    Wire format: ``nonce (12 bytes) || ciphertext + GCM auth tag``.

    Exported primarily for round-trip tests; operators decrypt via the
    decryption recipe in the module docstring.

    Args:
        data: Bytes in the layout ``nonce (12) || ciphertext+tag``.
        key:  Override the key for testing.  If ``None``, reads from the
              ``DASHBOARD_EXPORT_ENCRYPTION_KEY`` environment variable.

    Returns:
        Decrypted plaintext bytes (typically a ZIP archive).

    Raises:
        ValueError: if *data* is too short (< 28 bytes).
        cryptography.exceptions.InvalidTag: if authentication fails.
    """
    if key is None:
        key = _get_export_encryption_key()
    if len(data) < 28:  # 12-byte nonce + 16-byte GCM tag minimum
        raise ValueError(f"Export blob too short: {len(data)} bytes (minimum 28)")
    nonce, ct = data[:12], data[12:]
    return AESGCM(key).decrypt(nonce, ct, None)


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
    validates the token and TTL, then builds and returns an AES-256-GCM
    encrypted ZIP of the requested scope.

    Supported scopes: ``all``, ``memory``, ``audit``, ``config``, ``full``
    (alias for ``all``).  Unknown scopes are rejected with ``400 Bad Request``.
    """
    if body.scope not in _KNOWN_SCOPES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unknown_scope",
                "scope": body.scope,
                "valid_scopes": sorted(_KNOWN_SCOPES),
            },
        )

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


async def _build_export_zip(pool: object, scope: str) -> bytes:  # type: ignore[type-arg]
    """Build a deflate-compressed ZIP of NDJSON exports for *scope*.

    Each table in the scope becomes one ``.ndjson`` file inside the ZIP.
    Binary / generated columns (``embedding``, ``search_vector``,
    ``description_embedding``) are stripped so they don't bloat the archive.

    Returns raw ZIP bytes (not encrypted — the caller encrypts).

    Raises:
        HTTPException(400): if *scope* is not a recognised scope name.
        HTTPException(500): if a database fetch fails for any table in the scope
            (fail-fast — a partial export is worse than a clear error).
    """
    resolved = _SCOPE_ALIASES.get(scope, scope)
    if resolved == "all":
        table_entries: list[tuple[str, str]] = [
            (name, q) for tables in _SCOPE_MAP.values() for name, q in tables
        ]
    elif resolved in _SCOPE_MAP:
        table_entries = list(_SCOPE_MAP[resolved])
    else:
        # Belt-and-suspenders: POST already rejected unknown scopes.
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unknown_scope",
                "scope": scope,
                "valid_scopes": sorted(_KNOWN_SCOPES),
            },
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table_name, query in table_entries:
            safe_name = table_name.replace(".", "_").replace("/", "_")
            try:
                rows = await pool.fetch(query)  # type: ignore[attr-defined]
                with zf.open(f"{safe_name}.ndjson", "w") as member_file:
                    for row in rows:
                        cleaned = {k: v for k, v in dict(row).items() if k not in _SKIP_COLUMNS}
                        member_file.write((json.dumps(cleaned, default=str) + "\n").encode())
            except HTTPException:
                raise
            except Exception as exc:
                logger.error("export: failed to export table %s", table_name, exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"Export failed: unable to fetch table {table_name}",
                ) from exc

    return buf.getvalue()


@router.get("/export/download/{export_id}")
async def download_export(
    export_id: str,
    scope: str = Query(...),
    issued_at: int = Query(...),
    token: str = Query(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> Response:
    """Download a previously requested export via its signed URL.

    Validates:

    - **Token signature** — HMAC-SHA256 of ``"{export_id}:{scope}:{issued_at}"``.
      Returns 401 if the signature is wrong.
    - **TTL** — rejects tokens older than 60 minutes with 410 Gone.
    - **Scope** — must be a known scope name; unknown scopes return 400.

    Returns the export as an AES-256-GCM encrypted ZIP
    (``application/octet-stream``).  See the module docstring for the wire
    format and decryption recipe.
    """
    _verify_token(export_id, scope, issued_at, token)

    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    zip_bytes = await _build_export_zip(pool, scope)
    encrypted = _encrypt_export(zip_bytes)

    filename = f"butlers-export-{scope}-{export_id[:8]}.enc"
    return Response(
        content=encrypted,
        media_type="application/octet-stream",
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

    .. note::
        Wipe is currently disabled (``_WIPE_ENABLED = False``) because the
        existing implementation is non-atomic and unauthenticated.  All calls
        return HTTP 503 ``{"error": "wipe_disabled"}`` without touching the DB.
        Re-enable by setting ``_WIPE_ENABLED = True`` once the replacement is
        shipped.
    """
    # --- Disabled guard — short-circuit before any DB access ---
    if not _WIPE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail={"error": "wipe_disabled"},
        )

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
