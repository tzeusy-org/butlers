"""Passport-book secrets inventory endpoint — /api/secrets/inventory.

Provides the aggregated, evidence-rich inventory endpoint that backs the
redesigned /secrets page.  This router owns the new /api/secrets/* namespace
defined in the redesign-secrets-passport OpenSpec change.

The existing /api/butlers/{name}/secrets/* CRUD surface in secrets.py is
preserved unchanged per the compatibility requirement.

Endpoints
---------
GET /api/secrets/inventory?identity=<uuid>
    Aggregated read across all butler schemas + public.entity_info.
    Returns ApiResponse<InventoryData> with cli/system/user arrays and
    a meta.needs_hand_count computed server-side.

Design decisions
----------------
- Butler schema discovery uses db.butler_names (registered pools) rather
  than a pg_class query.  The DatabaseManager already holds a pool per
  butler (one-DB/multi-schema topology) so iterating pool names is cheaper
  and correct.
- Fingerprints are SHA-256(value)[:8] hex, computed on-read, never persisted.
  The value is fetched only to compute the fingerprint and then discarded.
- The ?identity= filter is applied at SQL level inside the entity_info query.
- Probe-log LRU: the most recent row in public.secret_probe_log for each
  (credential_scope, credential_key) is joined on the read path (one query
  per credential family), providing the TestResult for the spec.
- meta.needs_hand_count = len([row for row in all_rows if row.state != 'ok']),
  computed server-side from the full row set before the response is serialised.
  (Q7 resolution: server-computed, client-derived per-row flag stays stable.)
- Graceful degradation: if a butler's pool is unreachable the butler's rows are
  silently omitted; the response still returns HTTP 200 (degraded-mode pattern).

Spec anchor
-----------
openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
§Inventory endpoint shape
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiMeta, ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/secrets", tags=["secrets"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class TestResult(BaseModel):
    """Most recent probe outcome for a credential."""

    ok: bool
    code: int | None = None
    message: str | None = None
    at: str | None = None  # human-friendly relative timestamp


class SystemSecret(BaseModel):
    """A system-level credential stored in a butler's butler_secrets table."""

    key: str
    category: str = "general"
    description: str | None = None
    state: str  # 'ok' | 'warn' | 'failing' | 'expired' | 'never_set'
    fingerprint: str | None = None  # sha256[:8] hex, computed on-read
    last_verified: datetime | None = None
    last_test_ok: bool | None = None
    last_test_code: int | None = None
    last_test_message: str | None = None
    butler: str  # which butler schema owns this row
    test: TestResult | None = None


class UserSecret(BaseModel):
    """A per-user credential stored in public.entity_info."""

    id: str  # entity_info row id (UUID)
    entity_id: str  # entity UUID
    type: str  # e.g. 'google_oauth_refresh'
    label: str | None = None
    state: str  # 'ok' | 'warn' | 'failing' | 'expired' | 'never_set'
    fingerprint: str | None = None  # sha256[:8] hex, computed on-read
    last_verified: datetime | None = None
    last_test_ok: bool | None = None
    last_test_code: int | None = None
    last_test_message: str | None = None
    test: TestResult | None = None


class CliRuntime(BaseModel):
    """A CLI runtime token stored in the credential_shared_pool."""

    key: str
    category: str = "cli"
    description: str | None = None
    state: str  # 'ok' | 'warn' | 'failing' | 'expired' | 'never_set'
    fingerprint: str | None = None  # sha256[:8] hex, computed on-read
    last_verified: datetime | None = None
    last_test_ok: bool | None = None
    last_test_code: int | None = None
    last_test_message: str | None = None
    test: TestResult | None = None


class InventoryData(BaseModel):
    """Payload returned by GET /api/secrets/inventory."""

    cli: list[CliRuntime] = Field(default_factory=list)
    system: list[SystemSecret] = Field(default_factory=list)
    user: list[UserSecret] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Fingerprint helper
# ---------------------------------------------------------------------------


def _fingerprint(value: str | None) -> str | None:
    """Compute SHA-256 fingerprint and return first 8 hex chars.

    Returns None when value is None or empty.
    The algorithm is SHA-256[:8-hex] (32 bits of leakage — per design.md
    §Risks: not enough for offline brute-force against any non-trivial secret).
    """
    if not value:
        return None
    digest = hashlib.sha256(value.encode()).hexdigest()
    return digest[:8]


# ---------------------------------------------------------------------------
# State derivation
# ---------------------------------------------------------------------------


def _derive_state(
    *,
    is_set: bool,
    last_test_ok: bool | None,
    expires_at: datetime | None = None,
) -> str:
    """Derive a display state string from credential metadata.

    State machine
    -------------
    never_set   → is_set is False and no probe result
    expired     → expires_at is in the past
    failing     → most recent probe was not ok
    ok          → most recent probe was ok
    warn        → set, no probe result (unknown state)
    """
    if not is_set:
        return "never_set"
    if expires_at is not None:
        now = datetime.now(tz=UTC)
        # Ensure expires_at is tz-aware for comparison
        effective_expires = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        if effective_expires <= now:
            return "expired"
    if last_test_ok is None:
        return "warn"
    return "ok" if last_test_ok else "failing"


# ---------------------------------------------------------------------------
# Probe-log LRU helper
# ---------------------------------------------------------------------------

_PROBE_TIMESTAMP_FORMAT = "%H:%M %Z"
_PROBE_DATE_FORMAT = "%Y-%m-%d"


def _format_probe_time(recorded_at: datetime | None) -> str | None:
    """Format a probe timestamp to a human-friendly relative string.

    Per spec §Probe-log LRU integration: format to "14:21 today" or
    "yesterday 09:08" before serialisation.

    Uses calendar-day difference (not 24-hour elapsed time) so that a probe
    recorded at 23:55 the previous calendar day is always "yesterday" even
    when fewer than 24 hours have passed.
    """
    if recorded_at is None:
        return None
    now = datetime.now(tz=UTC)
    # Ensure tz-aware
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)
    time_str = recorded_at.strftime("%H:%M")
    days_diff = (now.date() - recorded_at.date()).days
    if days_diff == 0:
        return f"{time_str} today"
    if days_diff == 1:
        return f"yesterday {time_str}"
    return recorded_at.strftime("%Y-%m-%d ") + time_str


async def _fetch_probe_log(
    pool: Any,
    credential_scope: str,
    credential_key: str,
) -> TestResult | None:
    """Fetch the most recent probe row from public.secret_probe_log.

    Returns a TestResult or None if no probe has been recorded.
    Silently returns None when the table does not exist (migration not yet run).
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT ok, code, message, recorded_at
            FROM public.secret_probe_log
            WHERE credential_scope = $1 AND credential_key = $2
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            credential_scope,
            credential_key,
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "does not exist" in msg or "undefined" in msg.lower():
            return None
        logger.debug(
            "probe_log lookup failed for scope=%s key=%s: %s",
            credential_scope,
            credential_key,
            exc,
        )
        return None

    if row is None:
        return None

    return TestResult(
        ok=row["ok"],
        code=row["code"],
        message=row["message"],
        at=_format_probe_time(row["recorded_at"]),
    )


# ---------------------------------------------------------------------------
# Per-family query helpers
# ---------------------------------------------------------------------------


async def _fetch_system_secrets(
    pool: Any,
    butler_name: str,
) -> list[SystemSecret]:
    """Fetch all butler_secrets rows from a single butler's schema pool.

    Returns an empty list when the table doesn't exist or the pool errors.
    """
    try:
        rows = await pool.fetch(
            """
            SELECT
                secret_key,
                secret_value,
                category,
                description,
                is_sensitive,
                created_at,
                updated_at,
                expires_at,
                last_verified,
                last_test_ok,
                last_test_code,
                last_test_message
            FROM butler_secrets
            ORDER BY category, secret_key
            """
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "does not exist" in msg or "undefined" in msg.lower():
            logger.debug("butler_secrets not found for butler %s", butler_name)
            return []
        logger.warning("Failed to fetch system secrets for butler %s: %s", butler_name, exc)
        return []

    results: list[SystemSecret] = []
    for row in rows:
        secret_value: str | None = row["secret_value"]
        expires_at: datetime | None = row["expires_at"]
        last_test_ok: bool | None = row["last_test_ok"]

        state = _derive_state(
            is_set=bool(secret_value),
            last_test_ok=last_test_ok,
            expires_at=expires_at,
        )
        fp = _fingerprint(secret_value)

        # Fetch probe log for this credential
        test = await _fetch_probe_log(pool, "system", row["secret_key"])

        results.append(
            SystemSecret(
                key=row["secret_key"],
                category=row["category"] or "general",
                description=row["description"],
                state=state,
                fingerprint=fp,
                last_verified=row["last_verified"],
                last_test_ok=last_test_ok,
                last_test_code=row["last_test_code"],
                last_test_message=row["last_test_message"],
                butler=butler_name,
                test=test,
            )
        )

    return results


async def _fetch_user_secrets(
    pool: Any,
    *,
    identity: UUID | None,
) -> list[UserSecret]:
    """Fetch entity_info rows from the shared pool.

    When identity is provided, filters to that entity.  When omitted, uses
    the owner entity (projection-lens semantics per spec §Inventory endpoint
    shape and design.md Q4).
    """
    try:
        if identity is not None:
            rows = await pool.fetch(
                """
                SELECT
                    ei.id,
                    ei.entity_id,
                    ei.type,
                    ei.value,
                    ei.label,
                    ei.created_at,
                    ei.last_verified,
                    ei.last_test_ok,
                    ei.last_test_code,
                    ei.last_test_message
                FROM public.entity_info ei
                WHERE ei.entity_id = $1
                  AND ei.secured = true
                ORDER BY ei.type
                """,
                identity,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT
                    ei.id,
                    ei.entity_id,
                    ei.type,
                    ei.value,
                    ei.label,
                    ei.created_at,
                    ei.last_verified,
                    ei.last_test_ok,
                    ei.last_test_code,
                    ei.last_test_message
                FROM public.entity_info ei
                JOIN public.entities e ON e.id = ei.entity_id
                WHERE 'owner' = ANY(e.roles)
                  AND ei.secured = true
                ORDER BY ei.type
                """
            )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "does not exist" in msg or "undefined" in msg.lower():
            logger.debug("entity_info or entities table not available: %s", exc)
            return []
        logger.warning("Failed to fetch user secrets: %s", exc)
        return []

    results: list[UserSecret] = []
    for row in rows:
        value: str | None = row["value"]
        last_test_ok: bool | None = row["last_test_ok"]

        state = _derive_state(
            is_set=bool(value),
            last_test_ok=last_test_ok,
        )
        fp = _fingerprint(value)

        # Fetch probe log for this credential
        test = await _fetch_probe_log(pool, "user", row["type"])

        results.append(
            UserSecret(
                id=str(row["id"]),
                entity_id=str(row["entity_id"]),
                type=row["type"],
                label=row["label"],
                state=state,
                fingerprint=fp,
                last_verified=row["last_verified"],
                last_test_ok=last_test_ok,
                last_test_code=row["last_test_code"],
                last_test_message=row["last_test_message"],
                test=test,
            )
        )

    return results


async def _fetch_cli_secrets(
    pool: Any,
) -> list[CliRuntime]:
    """Fetch CLI runtime tokens from the shared credential pool.

    CLI tokens are stored in the shared butler_secrets table under
    category='cli'.  Returns empty list when the table doesn't exist.
    """
    try:
        rows = await pool.fetch(
            """
            SELECT
                secret_key,
                secret_value,
                category,
                description,
                created_at,
                updated_at,
                expires_at,
                last_verified,
                last_test_ok,
                last_test_code,
                last_test_message
            FROM butler_secrets
            WHERE category = 'cli'
            ORDER BY secret_key
            """
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "does not exist" in msg or "undefined" in msg.lower():
            logger.debug("butler_secrets (cli) not found in shared pool")
            return []
        logger.warning("Failed to fetch CLI secrets: %s", exc)
        return []

    results: list[CliRuntime] = []
    for row in rows:
        value: str | None = row["secret_value"]
        expires_at: datetime | None = row["expires_at"]
        last_test_ok: bool | None = row["last_test_ok"]

        state = _derive_state(
            is_set=bool(value),
            last_test_ok=last_test_ok,
            expires_at=expires_at,
        )
        fp = _fingerprint(value)

        test = await _fetch_probe_log(pool, "cli", row["secret_key"])

        results.append(
            CliRuntime(
                key=row["secret_key"],
                category=row["category"] or "cli",
                description=row["description"],
                state=state,
                fingerprint=fp,
                last_verified=row["last_verified"],
                last_test_ok=last_test_ok,
                last_test_code=row["last_test_code"],
                last_test_message=row["last_test_message"],
                test=test,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Severity counting
# ---------------------------------------------------------------------------


def _count_severity(items: list[Any]) -> dict[str, int]:
    """Return a severity breakdown dict for a list of credential rows."""
    counts: dict[str, int] = {"ok": 0, "warn": 0, "failing": 0, "expired": 0, "never_set": 0}
    for item in items:
        state = getattr(item, "state", "warn")
        counts[state] = counts.get(state, 0) + 1
    return counts


def _needs_hand_count(items: list[Any]) -> int:
    """Count credentials that need attention (state != 'ok')."""
    return sum(1 for item in items if getattr(item, "state", "warn") != "ok")


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get(
    "/inventory",
    response_model=ApiResponse[InventoryData],
)
async def get_inventory(
    identity: UUID | None = Query(
        default=None,
        description=(
            "Filter the user credentials array to this entity UUID. "
            "When omitted, defaults to the owner entity (projection-lens semantics)."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[InventoryData]:
    """Return the aggregated secrets inventory for the passport-book /secrets page.

    Merges across all butler schemas (system credentials) and
    public.entity_info (user credentials).  CLI runtime tokens are read
    from the shared credential pool.

    The ?identity= parameter filters the user array to credentials associated
    with the specified entity UUID.  When omitted, the owner entity is used
    as the default projection lens.

    Every credential row includes:
    - state (derived from test-state columns + expiry)
    - fingerprint (sha256 first-8 hex, computed on-read, never persisted)
    - per-family identity (key / type / id)

    Raw credential values are NEVER returned.

    meta.needs_hand_count is computed server-side from the full row set as
    count(row.state != 'ok').
    """
    # --- Fetch system secrets across all butler schemas ---
    system_secrets: list[SystemSecret] = []
    for butler_name in db.butler_names:
        try:
            pool = db.pool(butler_name)
        except KeyError:
            continue
        butler_rows = await _fetch_system_secrets(pool, butler_name)
        system_secrets.extend(butler_rows)

    # --- Fetch user secrets from shared pool ---
    user_secrets: list[UserSecret] = []
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        shared_pool = None

    if shared_pool is not None:
        user_secrets = await _fetch_user_secrets(shared_pool, identity=identity)

    # --- Fetch CLI tokens from shared pool ---
    cli_secrets: list[CliRuntime] = []
    if shared_pool is not None:
        cli_secrets = await _fetch_cli_secrets(shared_pool)

    # --- Build response ---
    all_items: list[Any] = [*cli_secrets, *system_secrets, *user_secrets]

    # Single pass: derive both severity breakdown and needs_hand_count from the
    # same _count_severity call to avoid iterating all_items twice.
    counts = _count_severity(all_items)
    needs_hand = sum(v for k, v in counts.items() if k != "ok")
    severity = {k: v for k, v in counts.items() if v > 0}

    data = InventoryData(
        cli=cli_secrets,
        system=system_secrets,
        user=user_secrets,
    )

    # ApiMeta has extra="allow" so extra kwargs are serialised.
    # meta.needs_hand_count is computed server-side from the full row set
    # (Q7 resolution: server-computed aggregate; per-row flag stays client-derived).
    meta = ApiMeta(needs_hand_count=needs_hand, severity=severity)

    return ApiResponse[InventoryData](data=data, meta=meta)
