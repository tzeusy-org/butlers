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

- ``memory`` — facts, rules, and episodes tables of every butler running the
               memory module, each read through that butler's own pool and
               effective memory schema
- ``audit``  — ``public.audit_log``
- ``config`` — each butler’s own ``runtime_config``, ``public.model_catalog``,
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
import re
import secrets
import uuid
import zipfile
from datetime import UTC, datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from butlers.api.deps import Db
from butlers.api.models import ApiResponse
from butlers.api.models.base import ResponseSchemaModel
from butlers.api.routers import audit
from butlers.api.routers.webhooks import dispatch_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data-ops"])

_WIPE_PHRASE = "WIPE EVERYTHING IRREVERSIBLY"
_WIPE_ENABLED = False  # disabled pending atomic + authenticated reimplementation
_EXPORT_TTL_SECONDS = 3600  # 60 minutes

# ---------------------------------------------------------------------------
# Export scope map
# ---------------------------------------------------------------------------

_MEMORY_RELATIONS = ("facts", "rules", "episodes")
_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Each entry: scope_name -> [(display_table_name, SQL_query), ...]
# Columns `embedding`, `search_vector`, and `description_embedding` are
# excluded from serialisation — they are large binary/generated columns that
# would bloat the archive and are not useful for human-readable exports.
_SCOPE_MAP: dict[str, list[tuple[str, str]]] = {
    # Relation templates only: memory tables live in each butler's own schema, never in a
    # shared ``memory`` schema, and are never queried from here.  The export expands the
    # scope per butler through ``_memory_export_entries`` (schema-qualified, run on that
    # butler's own pool).
    "memory": [
        (f"{{butler}}.memory.{rel}", f"SELECT * FROM {rel} ORDER BY created_at")
        for rel in _MEMORY_RELATIONS
    ],
    "audit": [
        ("public.audit_log", "SELECT * FROM public.audit_log ORDER BY id"),
    ],
    "config": [
        ("{butler}.runtime_config", "SELECT * FROM runtime_config ORDER BY butler_name"),
        ("public.model_catalog", "SELECT * FROM public.model_catalog ORDER BY id"),
        ("public.permissions", "SELECT * FROM public.permissions ORDER BY butler, permission"),
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


class ExportResponse(ResponseSchemaModel):
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


class WipeResponse(ResponseSchemaModel):
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
    *,
    db: Db,
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

    signed_query = f"scope={body.scope}&issued_at={issued_at}&token={token}"
    signed_url = f"/data/export/download/{export_id}?{signed_query}"

    # data.export has no companion state mutation to roll back (the signed
    # URL is stateless — see the module docstring), so unlike the
    # transactional write sites there is no conn.transaction() to wrap here.
    # But the dashboard-audit-log spec's "audit.append raises on missing
    # table" scenario is unconditional on every audit.append() call, not just
    # ones with a rollback: "the calling endpoint propagates the exception;
    # the HTTP response is 503 ... {error: audit_unavailable}". The spec's
    # Purpose section also names "data ops" explicitly as one of the
    # endpoint families the primitive is a prerequisite for. So
    # AuditTableNotAvailableError is intentionally NOT caught here — it
    # propagates to the app-level handler (butlers.api.middleware), matching
    # memory.py/butler_management.py/approvals.py/oauth.py (bu-6exf0).
    #
    # A missing *switchboard pool* (KeyError) is a distinct, unrelated
    # infra-registration condition (not a missing-migration condition) and
    # stays best-effort — out of scope for the audit-unavailable contract.
    try:
        pool = db.pool("switchboard")
    except KeyError:
        logger.warning("audit.append skipped for data.export: switchboard pool unavailable")
    else:
        await audit.append(pool, "owner", "data.export", note=body.scope)
        dispatch_event(pool, "data.export", {"scope": body.scope})

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


def _memory_export_entries(db: object) -> list[tuple[str, str, str]]:
    """Return ``(display_name, query, butler_name)`` for every butler's memory tables.

    Butlers come from the manager's module census (those running the memory module);
    without module metadata every registered butler is tried.  The schema is the
    butler's effective memory schema.  Each query must run on that butler's own pool,
    because a butler role sees only its own schema plus ``public``.

    Raises ``HTTPException`` 503 when a configured memory butler has no pool and 500
    when no memory butler is registered at all.
    """
    # A configured memory butler whose dashboard pool failed at startup is absent from
    # the pooled roster; exporting without it would be a silent partial archive, so this
    # is an availability error (same census pattern as the calendar workspace).
    configured = db.configured_butlers_with_module("memory")  # type: ignore[attr-defined]
    if configured is not None:
        pooled = set(db.butler_names)  # type: ignore[attr-defined]
        unavailable = sorted(name for name in configured if name not in pooled)
        if unavailable:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Export unavailable: memory butler database not available for "
                    + ", ".join(unavailable)
                ),
            )
    butlers = db.butlers_with_module("memory")  # type: ignore[attr-defined]
    if butlers is None:
        butlers = db.butler_names  # type: ignore[attr-defined]
    if not butlers:
        # A known scope must never silently map to no tables.
        raise HTTPException(
            status_code=500,
            detail="Export failed: no butler with a memory schema is registered",
        )
    entries: list[tuple[str, str, str]] = []
    for butler in butlers:
        schema = db.memory_schema_for_butler(butler)  # type: ignore[attr-defined]
        if schema is not None and not _SQL_IDENTIFIER_PATTERN.fullmatch(schema):
            raise ValueError(f"Unsafe schema identifier: {schema!r}")
        for rel in _MEMORY_RELATIONS:
            source = f'"{schema}"."{rel}"' if schema is not None else rel
            entries.append(
                (f"{butler}.memory.{rel}", f"SELECT * FROM {source} ORDER BY created_at", butler)
            )
    return entries


def _runtime_config_export_entries(db: object) -> list[tuple[str, str, str | None]]:
    """Resolve each configured source without hiding an unavailable own pool.

    The legacy archive member is a logical name, not a public-schema source.
    A census-free caller retains unqualified lookup on its supplied pool only.
    """
    configured = set(getattr(db, "configured_butler_names", ()))
    pooled = set(getattr(db, "butler_names", ()))
    unavailable = sorted(configured - pooled)
    if unavailable:
        raise HTTPException(
            status_code=503,
            detail="Export unavailable: config butler database not available for "
            + ", ".join(unavailable),
        )
    butlers = sorted(configured | pooled)
    if not butlers:
        return [
            ("public.runtime_config", "SELECT * FROM runtime_config ORDER BY butler_name", None)
        ]
    entries: list[tuple[str, str, str | None]] = []
    for butler in butlers:
        schema = db.schema_for_butler(butler)  # type: ignore[attr-defined]
        if schema is not None and not _SQL_IDENTIFIER_PATTERN.fullmatch(schema):
            raise HTTPException(status_code=500, detail="Export failed: unsafe config schema")
        source = f'"{schema}"."runtime_config"' if schema is not None else "runtime_config"
        entries.append(
            ("public.runtime_config", f"SELECT * FROM {source} ORDER BY butler_name", butler)
        )
    return entries


async def _build_export_zip(pool: object, scope: str, db: object) -> bytes:
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
    if resolved != "all" and resolved not in _SCOPE_MAP:
        # Belt-and-suspenders: POST already rejected unknown scopes.
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unknown_scope",
                "scope": scope,
                "valid_scopes": sorted(_KNOWN_SCOPES),
            },
        )
    scope_names = list(_SCOPE_MAP) if resolved == "all" else [resolved]

    # (display_name, query, butler whose pool runs it; None = the caller's pool)
    table_entries: list[tuple[str, str, str | None]] = []
    for scope_name in scope_names:
        if scope_name == "memory":
            table_entries.extend(_memory_export_entries(db))
        elif scope_name == "config":
            table_entries.extend(_runtime_config_export_entries(db))
            table_entries.extend((n, q, None) for n, q in _SCOPE_MAP["config"][1:])
        else:
            table_entries.extend((n, q, None) for n, q in _SCOPE_MAP[scope_name])

    # Multiple own-pool config sources share one stable logical archive member.
    members: dict[str, list[tuple[str, str | None]]] = {}
    for name, query, butler in table_entries:
        members.setdefault(name, []).append((query, butler))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table_name, sources in members.items():
            safe_name = table_name.replace(".", "_").replace("/", "_")
            try:
                with zf.open(f"{safe_name}.ndjson", "w") as member_file:
                    for query, butler in sources:
                        source = pool if butler is None else db.pool(butler)  # type: ignore[attr-defined]
                        rows = await source.fetch(query)  # type: ignore[attr-defined]
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
    *,
    db: Db,
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
    except KeyError as exc:
        raise HTTPException(
            status_code=503, detail="Switchboard database is not available"
        ) from exc

    zip_bytes = await _build_export_zip(pool, scope, db=db)
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
    *,
    db: Db,
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
    except KeyError as exc:
        raise HTTPException(
            status_code=503, detail="Switchboard database is not available"
        ) from exc

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
