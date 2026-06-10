"""Passport-book secrets namespace — /api/secrets/*.

Provides the aggregated inventory endpoint, per-credential read endpoints, and
the audit-history endpoint that back the redesigned /secrets page.  This router
owns the new /api/secrets/* namespace defined in the redesign-secrets-passport
OpenSpec change.

Live credential probes
----------------------
The probe endpoints make LIVE calls to the provider's verify endpoint for
supported providers (Google, GitHub, Home Assistant, Steam).  For OwnTracks
the system probe runs a local presence/format check — there is no remote to
call, per the bead specification.  Unsupported providers fall back to a
local-state check.

Provider         | Probe type | Verify call
---------------- | ---------- | -----------
google           | user       | Token exchange → GET /oauth2/v1/userinfo
github           | user       | GET https://api.github.com/user
home_assistant   | user       | GET {configured_url}/api/ (Bearer token)
steam            | user       | GET ISteamUser/GetPlayerSummaries/v2
owntracks_webhook_token | system | presence + 64-char hex format check
spotify          | user       | PKCE token refresh → GET https://api.spotify.com/v1/me

The existing /api/butlers/{name}/secrets/* CRUD surface in secrets.py is
preserved unchanged per the compatibility requirement.

Endpoints
---------
GET /api/secrets/inventory?identity=<uuid>
    Aggregated read across all butler schemas + public.entity_info.
    Returns ApiResponse<InventoryData> with cli/system/user arrays and
    a meta.needs_hand_count computed server-side.

GET /api/secrets/user/<provider>?identity=<uuid>
    Per-credential read for a user-scoped credential (public.entity_info).
    Returns ApiResponse<UserSecretDetail> with full evidence payload.
    404 when no matching entity_info row exists.

GET /api/secrets/system/<key>
    Per-credential read for a system-scoped credential (butler_secrets).
    Returns ApiResponse<SystemSecretDetail>.  The first matching row across
    all butler schemas is returned; row_state reflects shared/local/missing.
    404 when no matching row exists across any butler schema.

GET /api/secrets/cli/<id>
    Per-credential read for a CLI runtime token (butler_secrets category='cli').
    Returns ApiResponse<CliRuntimeDetail>.
    404 when no matching row exists.

GET /api/secrets/audit/<scope>/<key>?limit=50
    Recent audit events for a single credential.
    Returns ApiResponse<AuditEvent[]> with pre-formatted timestamps and
    a meta.deep_link pointing to /audit-log?key=<canonical-key>.

GET /api/secrets/breaks-catalogue?provider=<p>
    Provider feature catalogue for the WhatBreaks affordance.
    Returns ApiResponse<BreakEntry[]> filtered to provider, sorted
    severity DESC.  When ?provider= is omitted, returns the full catalogue
    in meta.by_provider keyed by provider slug.

POST /api/secrets/cli/<id>/rotate
    Generate a new random secret for a CLI runtime token.
    Returns ApiResponse<{fingerprint: str, value: str}> with the raw value
    returned EXACTLY ONCE in the response body (not fetchable again via GET).
    Writes audit action 'rotated'.
    404 when no matching CLI token exists.

POST /api/secrets/cli/<id>/revoke
    Revoke (delete) a CLI runtime token.
    Returns ApiResponse<{status: "revoked"}>.
    Writes audit action 'disconnected'.
    404 when no matching CLI token exists.

POST /api/secrets/cli/<id>/reauthorize
    Initiate (or resume) re-authentication for a device-code or api-key CLI
    runtime.  Does NOT reimplement device-code logic — delegates to the
    existing cli-auth subsystem (/api/cli-auth/<provider>/start).
    device_code providers → ApiResponse<CliReauthorizeResponse> with
      auth_mode="device_code", session_id, auth_url, device_code.  Poll
      GET /api/cli-auth/sessions/<session_id> for completion.
    api_key providers → ApiResponse<CliReauthorizeResponse> with
      auth_mode="api_key", env_var, prompt (the human-readable instruction).
    Writes audit action 'attempted'.
    404 when <id> is not a known CLI auth provider.

POST /api/secrets/system/<key>
    Set (first-time create), rotate (value replaced), or override (per-butler).
    Body: { value, target: "shared" | "shared-public" | "<butler>" }
    Returns ApiResponse<SystemSecretDetail> (updated).
    Audit: set (first-time), rotated (existing key), overrode (override).

    target="shared-public" writes to the public credential pool
    (public.butler_secrets via credential_shared_pool()), which is the pool
    that modules read via CredentialStore.  target="shared" continues to
    write to the switchboard schema (preserved for backwards compatibility).

POST /api/secrets/system/<key>/probe
    Probe a system credential; writes probe_log + test-state cache + audit.
    Returns ApiResponse<TestResult>.
    Rate-limited: 1 call per 5 s per key (in-process TTL guard).
    Audit: verified (ok), failed (not-ok), warned (scope mismatch/expiring).

DELETE /api/secrets/system/<key>?target=<butler|shared|shared-public>
    Remove a system credential row.
    target=shared → delete the shared (switchboard) row; audit disconnected.
    target=shared-public → delete from the public credential pool; audit disconnected.
    target=<butler> → delete the per-butler override row; audit revoked.
    Returns ApiResponse<{ status: "disconnected" | "revoked" }>.

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
- Per-credential endpoints return 404 on miss (no credential found).  This is
  chosen over 200-with-null because the resource semantically does not exist.
- Provider-to-type mapping: entity_info.type follows the convention
  '<provider>_oauth_refresh' (e.g. 'google_oauth_refresh').  The user
  endpoint matches WHERE type LIKE '<provider>_%' AND secured = true to
  accommodate any provider-specific suffix.
- Audit endpoint: scope ∈ {user, system, cli} is validated and rejected with
  422 for unknown values.  normalize_credential_key() maps scope+key to the
  canonical target used in public.audit_log.  Timestamps are pre-formatted
  server-side using the same relative formatter as probe-log LRU.

Design decisions (system mutations)
------------------------------------
- Shared vs override: target="shared" routes to db.pool("switchboard") (the
  switchboard butler schema holds the canonical shared secrets).  Per the spec:
  "when target='shared' the value is written to the switchboard's butler_secrets
  table; when target='<butler>' an override row is created in that butler's
  butler_secrets table."
- shared-public target: target="shared-public" routes to db.credential_shared_pool()
  (the public schema's butler_secrets table), which is where modules read shared
  application secrets via CredentialStore.  Inventory rows tagged butler="shared-public"
  are NOT read_only — the passport renders the generic editor for them wired to
  this target.  target="shared" (switchboard schema) is preserved unchanged.
  Migration note: no existing data migration is performed; the public pool and
  switchboard pool are separate schema partitions.  Any key that was previously
  written via target="shared" to the switchboard schema stays there; only keys
  that were always in the public pool (written via CredentialStore.store() at
  module boot) are served with butler="shared-public".
- Rate limit on probe: an in-process TTL dict keyed on key (not client IP)
  with a 5 s window.  Shared per server process; adequate for single-owner
  admin use-case.  TTL chosen per spec "1 call/page-load/key".
- Probe result is state-derived (no external provider calls), consistent with
  the user probe pattern (BE-8).
- system probe writes butler_secrets test-state columns in the same transaction
  as the probe_log INSERT, replicating the user probe atomicity invariant.
- DELETE distinguishes shared vs override via the target query param:
  target=shared → DELETE shared (switchboard) row; audit disconnected.
  target=shared-public → DELETE from the public credential pool; audit disconnected.
  target=<butler> → DELETE per-butler override row; audit revoked.

Spec anchor
-----------
openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
§Inventory endpoint shape
§Per-credential read endpoints
§Probe-log LRU integration
§Audit history endpoint
§System credential mutations
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets as _secrets_mod
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID

import httpx
from asyncpg.exceptions import PostgresError, UndefinedTableError
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from butlers._sql_utils import escape_like_pattern
from butlers.api.db import DatabaseManager
from butlers.api.models import ApiMeta, ApiResponse
from butlers.api.models.cli_auth import CLIAuthSessionState
from butlers.api.routers import audit as audit_router
from butlers.cli_auth.registry import PROVIDERS
from butlers.cli_auth.session import CLIAuthSession, store_session
from butlers.core.credential_keys import normalize_credential_key
from butlers.credential_store import CredentialStore
from butlers.google_credentials import KEY_CLIENT_ID, KEY_CLIENT_SECRET
from butlers.secrets_provider_catalog import PROVIDER_CATALOG, ProviderMetadata

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
    # True for rows that must not be edited through the generic mutate path.
    # Shared-pool rows (butler="shared-public") are now editable via
    # target="shared-public"; the passport renders the generic editor for them.
    # Reserved for future use (e.g. externally-managed secrets).
    read_only: bool = False


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


class IdentityInfo(BaseModel):
    """Identity metadata for one entity referenced by the inventory.

    Returned as a top-level ``identities`` array alongside the credential
    families so the frontend switcher can show real names and roles without
    N round-trips per entity_id.

    ``name`` comes from ``public.entities.canonical_name``.
    ``role`` is 'owner' when the entity has 'owner' in its roles array,
    otherwise 'member'.
    """

    entity_id: str
    name: str
    role: str  # 'owner' | 'member'


class InventoryData(BaseModel):
    """Payload returned by GET /api/secrets/inventory."""

    cli: list[CliRuntime] = Field(default_factory=list)
    system: list[SystemSecret] = Field(default_factory=list)
    user: list[UserSecret] = Field(default_factory=list)
    identities: list[IdentityInfo] = Field(default_factory=list)
    providers: dict[str, ProviderMetadata] = Field(default_factory=dict)
    """Provider display metadata catalog keyed by provider slug.

    Included so the frontend never needs a separate round-trip and the
    static FE copy stays in sync with this authoritative backend source.
    Shape mirrors ProviderInfo in frontend/src/components/secrets/passport/types.ts.
    """


# ---------------------------------------------------------------------------
# Per-credential detail models (richer payloads for single-credential reads)
# ---------------------------------------------------------------------------
# These extend the inventory models with the full evidence payload as defined
# in spec §Per-credential read endpoints.  Fields not yet stored in the DB
# (scopes, feeds, audit, breaks, etc.) are nullable and default to None/[].
# ---------------------------------------------------------------------------


class UserSecretDetail(BaseModel):
    """Full evidence payload for a single user credential.

    Returned by GET /api/secrets/user/<provider>.
    Fields without a DB column yet are nullable and default to None/[].
    """

    # Identity
    id: str  # entity_info row id (UUID)
    entity_id: str  # entity UUID
    type: str  # e.g. 'google_oauth_refresh'
    provider: str  # normalised provider name (e.g. 'google')
    label: str | None = None

    # State
    state: str  # 'ok' | 'warn' | 'failing' | 'expired' | 'never_set'
    fingerprint: str | None = None  # sha256[:8] hex, computed on-read

    # Timestamps
    issued: datetime | None = None  # created_at
    expires: datetime | None = None  # expires_at (not yet in entity_info)
    last_verified: datetime | None = None
    last_used: datetime | None = None  # not yet persisted

    # Scope inventory (not yet persisted)
    scopes_required: list[str] = Field(default_factory=list)
    scopes_granted: list[str] = Field(default_factory=list)

    # Connector feeds that consume this credential (not yet persisted)
    feeds: list[str] = Field(default_factory=list)

    # Evidence tail
    failure_tail: str | None = None  # verbatim last_test_message
    breaks: list[dict] = Field(default_factory=list)  # BreakEntry[] from catalogue
    test: TestResult | None = None  # most recent probe result
    audit: list[dict] = Field(default_factory=list)  # last 10 AuditEvent rows


class SystemSecretDetail(BaseModel):
    """Full evidence payload for a single system credential.

    Returned by GET /api/secrets/system/<key>.
    row_state reflects whether this key is shared (switchboard) or has a
    per-butler override, or is missing.
    """

    key: str
    category: str = "general"
    description: str | None = None

    # State
    state: str  # 'ok' | 'warn' | 'failing' | 'expired' | 'never_set'
    fingerprint: str | None = None  # sha256[:8] hex, computed on-read

    # Row provenance (not yet: advanced multi-butler override tracking)
    row_state: str = "shared"  # 'shared' | 'local' | 'missing'
    source: str | None = None  # butler that owns the canonical value
    target: str | None = None  # butler that the override targets (overrides only)

    # Timestamps
    last_verified: datetime | None = None

    # Dependents (not yet persisted)
    used_by: list[str] = Field(default_factory=list)  # butler names

    # Evidence
    breaks: list[dict] = Field(default_factory=list)  # BreakEntry[]
    test: TestResult | None = None  # most recent probe result
    audit: list[dict] = Field(default_factory=list)  # last 10 AuditEvent rows

    # Butler attribution
    butler: str  # schema that owns this row


class CliRuntimeDetail(BaseModel):
    """Full evidence payload for a single CLI runtime token.

    Returned by GET /api/secrets/cli/<id>.
    """

    # Identity
    id: str  # secret_key (CLI token identifier)
    label: str | None = None  # description field

    # State
    state: str  # 'ok' | 'warn' | 'failing' | 'expired' | 'never_set'
    fingerprint: str | None = None  # sha256[:8] hex, computed on-read

    # Timestamps
    issued: datetime | None = None  # created_at
    expires: datetime | None = None  # expires_at
    last_used: datetime | None = None  # not yet persisted

    # Scope inventory (not yet persisted)
    scopes_required: list[str] = Field(default_factory=list)
    scopes_granted: list[str] = Field(default_factory=list)

    # Evidence
    test: TestResult | None = None  # most recent probe result


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


def _row_to_test_result(row: Any) -> TestResult:
    """Convert an asyncpg probe-log row to a TestResult.

    Expects row to have columns: ok, code, message, recorded_at.
    """
    return TestResult(
        ok=row["ok"],
        code=row["code"],
        message=row["message"],
        at=_format_probe_time(row["recorded_at"]),
    )


async def _fetch_probe_log(
    pool: Any,
    credential_scope: str,
    credential_key: str,
) -> TestResult | None:
    """Fetch the most recent probe row from public.secret_probe_log.

    Returns a TestResult or None if no probe has been recorded.
    Silently returns None when the table does not exist (migration not yet run).

    Use _fetch_probe_logs_bulk for multi-credential inventory paths to avoid
    N+1 query patterns.
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
    except UndefinedTableError:
        return None
    except PostgresError as exc:
        logger.debug(
            "probe_log lookup failed for scope=%s key=%s: %s",
            credential_scope,
            credential_key,
            exc,
        )
        return None

    if row is None:
        return None

    return _row_to_test_result(row)


async def _fetch_probe_logs_bulk(
    pool: Any,
    scope: str,
    keys: list[str],
) -> dict[str, TestResult]:
    """Fetch the most recent probe row for each key in a single query.

    Issues ONE query using DISTINCT ON (credential_key) + ANY($2) instead of
    one fetchrow per credential key.  Eliminates the N+1 pattern in the three
    inventory helpers (_fetch_system_secrets, _fetch_user_secrets,
    _fetch_cli_secrets).

    Returns a dict mapping credential_key → TestResult for keys that have a
    probe row.  Keys with no probe row are absent from the dict (callers treat
    a missing key as None / no probe recorded).

    Silently returns an empty dict when the table does not exist (migration not
    yet run) or when keys is empty.
    """
    if not keys:
        return {}
    try:
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (credential_key)
                   credential_key, ok, code, message, recorded_at
            FROM public.secret_probe_log
            WHERE credential_scope = $1 AND credential_key = ANY($2)
            ORDER BY credential_key, recorded_at DESC
            """,
            scope,
            keys,
        )
    except UndefinedTableError:
        return {}
    except PostgresError as exc:
        logger.debug(
            "probe_log bulk lookup failed for scope=%s keys=%s: %s",
            scope,
            keys,
            exc,
        )
        return {}
    return {row["credential_key"]: _row_to_test_result(row) for row in rows}


# ---------------------------------------------------------------------------
# Per-family query helpers
# ---------------------------------------------------------------------------


async def _fetch_system_secrets(
    pool: Any,
    butler_name: str,
    *,
    read_only: bool = False,
) -> list[SystemSecret]:
    """Fetch all butler_secrets rows from a single butler's schema pool.

    Returns an empty list when the table doesn't exist or the pool errors.

    ``read_only`` marks the returned rows as managed in the shared credential
    pool (see :class:`SystemSecret.read_only`); set it when scanning the shared
    pool rather than a per-butler schema.
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
    except UndefinedTableError:
        logger.debug("butler_secrets not found for butler %s", butler_name)
        return []
    except Exception as exc:  # noqa: BLE001
        # A missing COLUMN (schema drift, e.g. test-state columns absent) must
        # not be silently treated as "table not found" — it hides every row in
        # this scan from the inventory (bu-urcwx).
        logger.warning("Failed to fetch system secrets for butler %s: %s", butler_name, exc)
        return []

    # Bulk-fetch probe logs for all keys in a single query (eliminates N+1).
    credential_keys = [row["secret_key"] for row in rows]
    probe_map = await _fetch_probe_logs_bulk(pool, "system", credential_keys)

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
                test=probe_map.get(row["secret_key"]),
                read_only=read_only,
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

    Owner-default path (identity=None): in addition to the owner entity's own
    secured credentials, also includes the PRIMARY Google account's companion
    entity secured credentials (e.g. google_oauth_refresh) so the Health
    scope-grant CTA is reachable from /secrets without a manual ?identity= param.

    SECURITY: the join on ``public.google_accounts`` is guarded by
    ``is_primary = true AND status != 'revoked'`` so that only the primary
    account surfaces in the owner-default view.  Non-primary accounts MUST NOT
    appear here — they are only accessible under an explicit ?identity= lens.
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
            # Owner-default projection: include both the owner entity's credentials
            # AND the primary Google account companion entity's credentials.
            #
            # The UNION merges two disjoint sets:
            #   1. Credentials anchored on the owner entity (telegram_token, etc.)
            #      — priority 0, so they always sort first.
            #   2. Credentials anchored on the primary Google account companion
            #      entity (google_oauth_refresh), gated by is_primary=true AND
            #      status != 'revoked' (includes 'active' and 'expired' so the
            #      reauth CTA is reachable even for expired accounts).
            #      — priority 1, so they always sort after owner credentials.
            #
            # ORDER BY priority, type guarantees owner entity_id appears first in
            # seen_eids (built in encounter order), which preserves the owner-first
            # contract in _fetch_identity_info and the identities[] switcher chip.
            #
            # SECURITY: non-primary Google accounts are excluded from this query.
            # They only appear when the caller provides an explicit ?identity= param.
            _owner_default_sql = """
                -- Owner entity credentials (priority 0: always first)
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
                    ei.last_test_message,
                    0 AS priority
                FROM public.entity_info ei
                JOIN public.entities e ON e.id = ei.entity_id
                WHERE 'owner' = ANY(e.roles)
                  AND ei.secured = true

                UNION ALL

                -- Primary Google account companion entity credentials (priority 1).
                -- Guarded: is_primary = true AND status != 'revoked' only.
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
                    ei.last_test_message,
                    1 AS priority
                FROM public.entity_info ei
                JOIN public.google_accounts ga ON ga.entity_id = ei.entity_id
                WHERE ga.is_primary = true
                  AND ga.status != 'revoked'
                  AND ei.secured = true

                ORDER BY priority, type
            """
            try:
                rows = await pool.fetch(_owner_default_sql)
            except UndefinedTableError as exc:
                # google_accounts table not yet migrated — fall back to owner-only
                # query so existing owner credentials are not hidden.
                logger.debug(
                    "google_accounts table not available, falling back to owner-only query: %s",
                    exc,
                )
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

    # Bulk-fetch probe logs for all credential types in a single query (eliminates N+1).
    # For user credentials the probe key is the entity_info.type value.
    credential_keys = [row["type"] for row in rows]
    probe_map = await _fetch_probe_logs_bulk(pool, "user", credential_keys)

    results: list[UserSecret] = []
    for row in rows:
        value: str | None = row["value"]
        last_test_ok: bool | None = row["last_test_ok"]

        state = _derive_state(
            is_set=bool(value),
            last_test_ok=last_test_ok,
        )
        fp = _fingerprint(value)

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
                test=probe_map.get(row["type"]),
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
    except UndefinedTableError:
        logger.debug("butler_secrets (cli) not found in shared pool")
        return []
    except Exception as exc:  # noqa: BLE001
        # Schema drift (e.g. a missing column) is a real failure, not an
        # absent table — surface it instead of silently returning nothing.
        logger.warning("Failed to fetch CLI secrets: %s", exc)
        return []

    # Bulk-fetch probe logs for all CLI keys in a single query (eliminates N+1).
    credential_keys = [row["secret_key"] for row in rows]
    probe_map = await _fetch_probe_logs_bulk(pool, "cli", credential_keys)

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
                test=probe_map.get(row["secret_key"]),
            )
        )

    return results


# ---------------------------------------------------------------------------
# Identity enrichment helper
# ---------------------------------------------------------------------------


async def _fetch_identity_info(
    pool: Any,
    entity_ids: list[str],
) -> list[IdentityInfo]:
    """Fetch identity metadata for a list of entity UUIDs.

    Joins ``public.entities`` to retrieve ``canonical_name`` and ``roles``
    for each entity referenced in the user credentials array.

    Returns an ``IdentityInfo`` per unique entity_id.  Entities with
    ``'owner' = ANY(roles)`` get role='owner'; all others get role='member'.
    Silently returns an empty list when ``public.entities`` does not exist
    (migration not yet run).

    Order: owner first, then members in the order they appear in entity_ids.
    """
    if not entity_ids:
        return []

    try:
        rows = await pool.fetch(
            """
            SELECT id, canonical_name, roles
            FROM public.entities
            WHERE id = ANY($1::uuid[])
            """,
            entity_ids,
        )
    except UndefinedTableError as exc:
        logger.debug("public.entities not available for identity enrichment: %s", exc)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Transient error fetching identity info: %s", exc)
        return []

    # Build a lookup keyed on entity_id string for ordered output.
    lookup: dict[str, IdentityInfo] = {}
    for row in rows:
        eid = str(row["id"])
        roles: list[str] = row["roles"] or []
        role = "owner" if "owner" in roles else "member"
        lookup[eid] = IdentityInfo(
            entity_id=eid,
            name=row["canonical_name"] or eid,
            role=role,
        )

    # Return in entity_ids order (preserves API ordering: owner first when
    # the endpoint uses the default projection-lens path).
    result: list[IdentityInfo] = []
    seen: set[str] = set()
    for eid in entity_ids:
        if eid in lookup and eid not in seen:
            result.append(lookup[eid])
            seen.add(eid)
    return result


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

    Merges system credentials across all butler schemas and the shared
    credential pool (public.butler_secrets — shared application config such as
    the Google OAuth app credentials), plus public.entity_info (user
    credentials).  CLI runtime tokens are read from the shared credential pool.

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
    # --- Resolve the shared credential pool (public schema) ---
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        shared_pool = None

    # --- Fetch system secrets across all butler schemas + the shared pool ---
    system_secrets: list[SystemSecret] = []
    for butler_name in db.butler_names:
        try:
            pool = db.pool(butler_name)
        except KeyError:
            continue
        butler_rows = await _fetch_system_secrets(pool, butler_name)
        system_secrets.extend(butler_rows)

    # Shared application config (Google OAuth app credentials, butler email /
    # telegram tokens, S3, Spotify, …) lives in public.butler_secrets via the
    # shared credential pool, which is NOT one of the per-butler schemas above.
    # Surface those rows so the System family reflects shared config; tag them
    # butler="shared-public" so the frontend adapter can wire mutations to
    # target="shared-public" (which routes writes to this pool).
    # (cli-auth/* rows are rerouted to the CLI family by the frontend adapter.)
    #
    # Exclude category='cli' rows: CLI runtime tokens have their own family and
    # are read separately from this same pool by _fetch_cli_secrets(). Including
    # them here would double-list them across the system and cli arrays and
    # double-count them in meta.severity / needs_hand_count.
    #
    # read_only=False: these rows are now editable via target="shared-public".
    if shared_pool is not None:
        shared_system = await _fetch_system_secrets(shared_pool, "shared-public", read_only=False)
        system_secrets.extend(s for s in shared_system if s.category != "cli")

    # --- Fetch user secrets from shared pool ---
    user_secrets: list[UserSecret] = []
    if shared_pool is not None:
        user_secrets = await _fetch_user_secrets(shared_pool, identity=identity)

    # --- Fetch CLI tokens from shared pool ---
    cli_secrets: list[CliRuntime] = []
    if shared_pool is not None:
        cli_secrets = await _fetch_cli_secrets(shared_pool)

    # --- Enrich identities: join public.entities for name + role ---
    # Collect unique entity_ids in encounter order (owner first in default path).
    identities: list[IdentityInfo] = []
    if shared_pool is not None and user_secrets:
        seen_eids: list[str] = []
        seen_set: set[str] = set()
        for us in user_secrets:
            if us.entity_id not in seen_set:
                seen_eids.append(us.entity_id)
                seen_set.add(us.entity_id)
        identities = await _fetch_identity_info(shared_pool, seen_eids)

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
        identities=identities,
        providers=PROVIDER_CATALOG,
    )

    # ApiMeta has extra="allow" so extra kwargs are serialised.
    # meta.needs_hand_count is computed server-side from the full row set
    # (Q7 resolution: server-computed aggregate; per-row flag stays client-derived).
    meta = ApiMeta(needs_hand_count=needs_hand, severity=severity)

    return ApiResponse[InventoryData](data=data, meta=meta)


# ---------------------------------------------------------------------------
# Per-credential single-item fetch helpers
# ---------------------------------------------------------------------------


async def _fetch_single_user_secret(
    pool: Any,
    *,
    provider: str,
    identity: UUID | None,
) -> UserSecretDetail | None:
    """Fetch a single entity_info row matching the given provider.

    The provider maps to entity_info.type via a LIKE '<provider>_%' prefix
    match (e.g. 'google' matches 'google_oauth_refresh').  When identity is
    provided, filters to that entity; otherwise uses the owner entity.

    Returns None when no matching row exists.
    """
    try:
        if identity is not None:
            row = await pool.fetchrow(
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
                  AND ei.type LIKE $2
                  AND ei.secured = true
                ORDER BY ei.created_at DESC
                LIMIT 1
                """,
                identity,
                f"{escape_like_pattern(provider)}_%",
            )
        else:
            row = await pool.fetchrow(
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
                  AND ei.type LIKE $1
                  AND ei.secured = true
                ORDER BY ei.created_at DESC
                LIMIT 1
                """,
                f"{escape_like_pattern(provider)}_%",
            )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "does not exist" in msg or "undefined" in msg.lower():
            logger.debug("entity_info or entities table not available: %s", exc)
            return None
        logger.warning("Failed to fetch user secret for provider %s: %s", provider, exc)
        return None

    if row is None:
        return None

    value: str | None = row["value"]
    last_test_ok: bool | None = row["last_test_ok"]

    state = _derive_state(is_set=bool(value), last_test_ok=last_test_ok)
    fp = _fingerprint(value)
    test = await _fetch_probe_log(pool, "user", row["type"])

    return UserSecretDetail(
        id=str(row["id"]),
        entity_id=str(row["entity_id"]),
        type=row["type"],
        provider=provider,
        label=row["label"],
        state=state,
        fingerprint=fp,
        issued=row["created_at"],
        last_verified=row["last_verified"],
        failure_tail=row["last_test_message"],
        test=test,
    )


async def _fetch_single_system_secret(
    pool: Any,
    butler_name: str,
    key: str,
) -> SystemSecretDetail | None:
    """Fetch a single butler_secrets row matching the given key.

    Returns None when no matching row exists or when the table doesn't exist.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT
                secret_key,
                secret_value,
                category,
                description,
                expires_at,
                last_verified,
                last_test_ok,
                last_test_code,
                last_test_message,
                created_at
            FROM butler_secrets
            WHERE secret_key = $1
            """,
            key,
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "does not exist" in msg or "undefined" in msg.lower():
            logger.debug("butler_secrets not found for butler %s", butler_name)
            return None
        logger.warning("Failed to fetch system secret key=%s butler=%s: %s", key, butler_name, exc)
        return None

    if row is None:
        return None

    secret_value: str | None = row["secret_value"]
    expires_at: datetime | None = row["expires_at"]
    last_test_ok: bool | None = row["last_test_ok"]

    state = _derive_state(
        is_set=bool(secret_value),
        last_test_ok=last_test_ok,
        expires_at=expires_at,
    )
    fp = _fingerprint(secret_value)
    test = await _fetch_probe_log(pool, "system", key)

    return SystemSecretDetail(
        key=row["secret_key"],
        category=row["category"] or "general",
        description=row["description"],
        state=state,
        fingerprint=fp,
        row_state="shared",
        source=butler_name,
        last_verified=row["last_verified"],
        test=test,
        butler=butler_name,
    )


async def _fetch_single_cli_secret(
    pool: Any,
    credential_id: str,
) -> CliRuntimeDetail | None:
    """Fetch a single CLI runtime token by key (id).

    CLI tokens are stored in butler_secrets with category='cli'.
    Returns None when no matching row exists.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT
                secret_key,
                secret_value,
                description,
                category,
                created_at,
                expires_at,
                last_verified,
                last_test_ok,
                last_test_code,
                last_test_message
            FROM butler_secrets
            WHERE secret_key = $1
              AND category = 'cli'
            """,
            credential_id,
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "does not exist" in msg or "undefined" in msg.lower():
            logger.debug("butler_secrets (cli) not found in shared pool")
            return None
        logger.warning("Failed to fetch CLI secret id=%s: %s", credential_id, exc)
        return None

    if row is None:
        return None

    value: str | None = row["secret_value"]
    expires_at: datetime | None = row["expires_at"]
    last_test_ok: bool | None = row["last_test_ok"]

    state = _derive_state(
        is_set=bool(value),
        last_test_ok=last_test_ok,
        expires_at=expires_at,
    )
    fp = _fingerprint(value)
    test = await _fetch_probe_log(pool, "cli", credential_id)

    return CliRuntimeDetail(
        id=row["secret_key"],
        label=row["description"],
        state=state,
        fingerprint=fp,
        issued=row["created_at"],
        expires=expires_at,
        test=test,
    )


# ---------------------------------------------------------------------------
# Per-credential read routes
# ---------------------------------------------------------------------------


@router.get(
    "/user/{provider}",
    response_model=ApiResponse[UserSecretDetail],
)
async def get_user_credential(
    provider: str,
    identity: UUID | None = Query(
        default=None,
        description=(
            "Entity UUID to fetch the credential for. "
            "When omitted, defaults to the owner entity (projection-lens semantics)."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[UserSecretDetail]:
    """Return full evidence payload for a single user-scoped credential.

    The <provider> path segment maps to entity_info.type via the convention
    '<provider>_oauth_refresh' (or any type starting with '<provider>_').
    When ?identity= is omitted, the owner entity is used.

    Returns 404 when no matching credential exists.
    Raw credential values are NEVER returned.
    """
    shared_pool: Any = None
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        pass

    if shared_pool is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    detail = await _fetch_single_user_secret(shared_pool, provider=provider, identity=identity)
    if detail is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    return ApiResponse[UserSecretDetail](data=detail, meta=ApiMeta())


@router.get(
    "/system/{key}",
    response_model=ApiResponse[SystemSecretDetail],
)
async def get_system_credential(
    key: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SystemSecretDetail]:
    """Return full evidence payload for a single system-scoped credential.

    Searches across all registered butler schemas and then the shared credential
    pool (public.butler_secrets) for a butler_secrets row with the given key.
    Returns the first match found.

    Returns 404 when no matching credential exists in any butler schema or the
    shared pool.
    Raw credential values are NEVER returned.
    """
    for butler_name in db.butler_names:
        try:
            pool = db.pool(butler_name)
        except KeyError:
            continue

        detail = await _fetch_single_system_secret(pool, butler_name, key)
        if detail is not None:
            return ApiResponse[SystemSecretDetail](data=detail, meta=ApiMeta())

    # Also search the shared credential pool (public.butler_secrets).
    try:
        shared_pool = db.credential_shared_pool()
        detail = await _fetch_single_system_secret(shared_pool, "shared-public", key)
        if detail is not None:
            return ApiResponse[SystemSecretDetail](data=detail, meta=ApiMeta())
    except KeyError:
        pass

    raise HTTPException(status_code=404, detail="Credential not found")


@router.get(
    "/cli/{credential_id}",
    response_model=ApiResponse[CliRuntimeDetail],
)
async def get_cli_credential(
    credential_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CliRuntimeDetail]:
    """Return full evidence payload for a single CLI runtime token.

    CLI tokens are stored in the shared credential pool under
    butler_secrets with category='cli'.

    Returns 404 when no matching token exists.
    Raw credential values are NEVER returned.
    """
    shared_pool: Any = None
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        pass

    if shared_pool is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    detail = await _fetch_single_cli_secret(shared_pool, credential_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    return ApiResponse[CliRuntimeDetail](data=detail, meta=ApiMeta())


# ---------------------------------------------------------------------------
# Audit history models
# ---------------------------------------------------------------------------

#: Valid scope values for the audit endpoint path parameter.
_VALID_SCOPES: frozenset[str] = frozenset({"user", "system", "cli"})

#: Default and maximum limit values for the audit endpoint.
_AUDIT_DEFAULT_LIMIT = 10
_AUDIT_MAX_LIMIT = 50


class AuditEvent(BaseModel):
    """A single audit event for a credential.

    Per spec §Audit history endpoint:
    - ts: server pre-formatted relative timestamp (e.g. "5 minutes ago",
      "14:21 today", "yesterday 09:08")
    - actor: identity of the actor that triggered the change
    - action: short, machine-readable verb (e.g. "rotated", "connected")
    - note: verbatim stored note; never LLM-generated (serif-italic in UI)
    """

    ts: str  # pre-formatted relative timestamp
    actor: str
    action: str
    note: str | None = None


# ---------------------------------------------------------------------------
# Audit history route
# ---------------------------------------------------------------------------


@router.get(
    "/audit/{scope}/{key}",
    response_model=ApiResponse[list[AuditEvent]],
)
async def get_audit_history(
    scope: str,
    key: str,
    limit: int = Query(
        default=_AUDIT_DEFAULT_LIMIT,
        ge=1,
        le=_AUDIT_MAX_LIMIT,
        description=(
            f"Maximum number of audit events to return "
            f"(default {_AUDIT_DEFAULT_LIMIT}, max {_AUDIT_MAX_LIMIT})."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[AuditEvent]]:
    """Return recent audit events for a single credential.

    Path parameters
    ---------------
    scope:
        Credential scope — one of ``user``, ``system``, or ``cli``.
    key:
        Credential name within the scope (e.g. ``google``,
        ``BUTLER_TELEGRAM_TOKEN``, ``claude``).

    Query parameters
    ----------------
    limit:
        Maximum events to return.  Default is 10; max is 50.
        The spec says "default limit is 10; max is 50".

    Response
    --------
    ``ApiResponse<AuditEvent[]>`` with:

    - ``data``: list of the most recent audit events ordered newest-first.
    - ``meta.deep_link``: ``/audit-log?key=<canonical-key>`` for the full reel.

    Timestamps (``ts``) are pre-formatted server-side using the same
    calendar-day relative format as probe-log LRU
    (``"HH:MM today"`` / ``"yesterday HH:MM"`` / ``"YYYY-MM-DD HH:MM"``).

    Raises HTTP 422 when ``scope`` is not one of ``user``, ``system``, ``cli``.
    Returns an empty ``data`` list (HTTP 200) when no audit rows exist.

    Query uses the ``ix_audit_log_target_ts (target, ts DESC)`` index.

    Spec anchor
    -----------
    openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
    §Audit history endpoint
    """
    if scope not in _VALID_SCOPES:
        raise HTTPException(
            status_code=422,
            detail=(f"Invalid scope {scope!r}. Expected one of: {sorted(_VALID_SCOPES)}"),
        )

    canonical_key = normalize_credential_key(scope, key)

    try:
        pool = db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Shared credential pool is not available: {exc}",
        ) from exc

    try:
        rows = await pool.fetch(
            """
            SELECT ts, actor, action, note
            FROM public.audit_log
            WHERE target = $1
            ORDER BY ts DESC
            LIMIT $2
            """,
            canonical_key,
            limit,
        )
    except UndefinedTableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Audit log is not available — migration core_092 may not have run",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to fetch audit history for %s: %s",
            canonical_key,
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail="Audit log query failed",
        ) from exc

    events = [
        AuditEvent(
            ts=_format_probe_time(row["ts"]) or str(row["ts"]),
            actor=row["actor"],
            action=row["action"],
            note=row["note"],
        )
        for row in rows
    ]

    meta = ApiMeta(deep_link=f"/audit-log?key={canonical_key}")
    return ApiResponse[list[AuditEvent]](data=events, meta=meta)


# ---------------------------------------------------------------------------
# Breaks-catalogue models
# ---------------------------------------------------------------------------


class BreakEntry(BaseModel):
    """A single feature entry from public.provider_feature_catalogue.

    Per spec §Breaks-catalogue endpoint:
    - butler: butler name or '*' for ecosystem-wide
    - feature: user-facing feature label
    - severity: one of 'high' / 'medium' / 'low'
    - required_scopes: JSONB array of OAuth scope strings
    """

    butler: str
    feature: str
    severity: str  # 'high' | 'medium' | 'low'
    required_scopes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Breaks-catalogue route
# ---------------------------------------------------------------------------


@router.get(
    "/breaks-catalogue",
    response_model=ApiResponse[list[BreakEntry]],
)
async def get_breaks_catalogue(
    provider: str | None = Query(
        default=None,
        description=(
            "Provider slug to filter by (e.g. 'google', 'telegram', 'spotify'). "
            "When omitted, returns the full catalogue; per-provider grouping is "
            "available in meta.by_provider."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[BreakEntry]]:
    """Return feature catalogue rows for the WhatBreaks affordance.

    Reads from ``public.provider_feature_catalogue`` seeded by migration
    core_107 and refreshed idempotently on every butler boot.

    When ``?provider=`` is supplied:
    - Returns only rows matching that provider.
    - Rows are sorted ``severity DESC`` (high → medium → low).

    When ``?provider=`` is omitted:
    - Returns the full catalogue (all providers) sorted severity DESC.
    - ``meta.by_provider`` contains a dict mapping each provider slug to its
      list of BreakEntry items (also sorted severity DESC within each group).

    Returns an empty list (HTTP 200) when the provider has no catalogue rows
    or the catalogue table does not yet exist.

    Spec anchor
    -----------
    openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
    §Breaks-catalogue endpoint
    openspec/changes/redesign-secrets-passport/specs/core-credentials/spec.md
    §public.provider_feature_catalogue WhatBreaks Source-of-Truth Table
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError:
        # Shared pool unavailable — return an empty catalogue rather than 503.
        logger.debug("breaks-catalogue: shared pool unavailable, returning empty response")
        return ApiResponse[list[BreakEntry]](data=[], meta=ApiMeta())

    # Build the SQL query.  When provider is supplied, add a WHERE clause.
    if provider is not None:
        query = """
            SELECT butler, feature, severity, required_scopes
            FROM public.provider_feature_catalogue
            WHERE provider = $1
            ORDER BY
                CASE severity
                    WHEN 'high'   THEN 2
                    WHEN 'medium' THEN 1
                    ELSE 0
                END DESC,
                butler ASC,
                feature ASC
        """
        params: tuple = (provider,)
    else:
        query = """
            SELECT provider, butler, feature, severity, required_scopes
            FROM public.provider_feature_catalogue
            ORDER BY
                CASE severity
                    WHEN 'high'   THEN 2
                    WHEN 'medium' THEN 1
                    ELSE 0
                END DESC,
                provider ASC,
                butler ASC,
                feature ASC
        """
        params = ()

    try:
        if params:
            rows = await pool.fetch(query, *params)
        else:
            rows = await pool.fetch(query)
    except UndefinedTableError:
        # Migration core_107 not yet run — graceful empty response.
        logger.debug(
            "breaks-catalogue: provider_feature_catalogue not found "
            "(migration core_107 may not have run)"
        )
        return ApiResponse[list[BreakEntry]](data=[], meta=ApiMeta())
    except Exception as exc:  # noqa: BLE001
        logger.warning("breaks-catalogue: query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Catalogue query failed") from exc

    entries: list[BreakEntry] = []
    by_provider: dict[str, list[dict]] = {}

    for row in rows:
        scopes = row["required_scopes"]
        # asyncpg may return JSONB as a list already or as a JSON string.
        if isinstance(scopes, str):
            scopes = json.loads(scopes)
        entry = BreakEntry(
            butler=row["butler"],
            feature=row["feature"],
            severity=row["severity"],
            required_scopes=scopes or [],
        )
        entries.append(entry)
        if provider is None:
            by_provider.setdefault(row["provider"], []).append(entry.model_dump())

    if provider is not None:
        # Single-provider path — no meta.by_provider needed.
        return ApiResponse[list[BreakEntry]](data=entries, meta=ApiMeta())

    meta = ApiMeta(by_provider=by_provider)
    return ApiResponse[list[BreakEntry]](data=entries, meta=meta)


# ---------------------------------------------------------------------------
# User credential mutation models
# ---------------------------------------------------------------------------


class RotateRequest(BaseModel):
    """Request body for POST /api/secrets/user/<provider>/rotate."""

    value: str
    """New secret value to store."""


class DisconnectStatus(BaseModel):
    """Response payload for POST /api/secrets/user/<provider>/disconnect."""

    status: str = "disconnected"


class ReauthorizeResponse(BaseModel):
    """Response payload for POST /api/secrets/user/<provider>/reauthorize."""

    redirect_url: str
    """URL the caller should redirect to, beginning the OAuth dance."""


# ---------------------------------------------------------------------------
# OAuth provider token revocation
# ---------------------------------------------------------------------------

# butler_secrets key names for GitHub OAuth app credentials.
# The repo owner must populate these in butler_secrets (or via the secrets dashboard)
# before GitHub OAuth token revocation will be attempted.
# Key names follow the GOOGLE_OAUTH_* convention established for Google.
_GITHUB_OAUTH_CLIENT_ID_KEY = "GITHUB_OAUTH_CLIENT_ID"
_GITHUB_OAUTH_CLIENT_SECRET_KEY = "GITHUB_OAUTH_CLIENT_SECRET"

# GitHub DELETE endpoint for revoking an OAuth app grant.
# Requires HTTP Basic auth with client_id:client_secret, and a JSON body
# {"access_token": <token>} per the GitHub Apps API.
# https://docs.github.com/en/rest/apps/oauth-applications#delete-an-app-grant
_GITHUB_REVOKE_URL_TEMPLATE = "https://api.github.com/applications/{client_id}/grant"

# Credential type suffixes that indicate an OAuth token (access or refresh).
# Non-OAuth types (plain API keys, etc.) skip revocation entirely.
_OAUTH_TYPE_SUFFIXES = ("_oauth_refresh", "_oauth_access")

_REVOKE_TIMEOUT_S = 5.0

# ---------------------------------------------------------------------------
# Revoke handler registry
# ---------------------------------------------------------------------------
# Each handler is an async callable with signature:
#   async def handler(old_value: str, *, shared_pool: Any | None) -> str
# returning one of: "succeeded", "failed:<reason>", "skipped".
#
# Register a handler via the @register_revoke_handler("<provider>") decorator,
# or call register_revoke_handler(provider, handler) directly.
#
# Connector modules that need to self-register may import and call
# register_revoke_handler at module load time — there is no import cycle risk
# as long as the connector does not import secrets_v2 in its __init__ chain
# (it only needs to call the function at import time after secrets_v2 is loaded).
# When in doubt, define + register the handler inside secrets_v2 (as done here
# for google and github) to keep everything self-contained.

_RevokeHandler = Callable[..., Any]  # async (old_value: str, *, shared_pool) -> str

_revoke_handler_registry: dict[str, _RevokeHandler] = {}


def register_revoke_handler(
    provider: str,
    handler: _RevokeHandler | None = None,
) -> Any:
    """Register an async revoke handler for *provider*.

    Can be used as a decorator::

        @register_revoke_handler("myprovider")
        async def _revoke_myprovider(old_value: str, *, shared_pool) -> str:
            ...

    Or called directly::

        register_revoke_handler("myprovider", _my_handler)

    The handler must be an async callable with signature
    ``async (old_value: str, *, shared_pool: Any | None) -> str`` and must
    return one of ``"succeeded"``, ``"failed:<reason>"``, or ``"skipped"``.
    """
    if handler is None:
        # Used as a decorator factory: @register_revoke_handler("provider")
        def _decorator(fn: _RevokeHandler) -> _RevokeHandler:
            _revoke_handler_registry[provider] = fn
            return fn

        return _decorator
    # Direct call: register_revoke_handler("provider", fn)
    _revoke_handler_registry[provider] = handler
    return handler


async def _revoke_oauth_token(
    provider: str,
    credential_type: str,
    old_value: str,
    shared_pool: Any | None = None,
) -> str:
    """Attempt to revoke an OAuth token at the provider after a successful local rotation.

    Returns a revoke_status string: ``"succeeded"``, ``"failed:<reason>"``, or ``"skipped"``.

    Rules:
    - If the credential type is not an OAuth type, return ``"skipped"`` immediately.
    - If the provider has no registered revoke handler, log a warning and return ``"skipped"``.
    - On HTTP 200, return ``"succeeded"``.
    - On any error (HTTP non-200, timeout, network failure), log at WARN level and return
      ``"failed:<reason>"``.  Caller must NOT propagate this as a rotation failure.

    Provider-specific notes:
    - Google: POST to revoke URL with form body ``{"token": old_value}``.  No app
      credentials required.
    - GitHub: DELETE to ``/applications/{client_id}/grant`` with HTTP Basic auth
      (client_id:client_secret) and JSON body ``{"access_token": old_value}``.
      Requires ``GITHUB_OAUTH_CLIENT_ID`` and ``GITHUB_OAUTH_CLIENT_SECRET`` to be
      stored in ``butler_secrets`` (loaded via CredentialStore from *shared_pool*).
      If those credentials are absent or *shared_pool* is None, the revoke is
      skipped with a clear log message — rotation still succeeds.
    """
    # Only OAuth credential types trigger revoke.
    if not any(credential_type.endswith(suffix) for suffix in _OAUTH_TYPE_SUFFIXES):
        return "skipped"

    handler = _revoke_handler_registry.get(provider)
    if handler is None:
        logger.warning(
            "_revoke_oauth_token: unknown provider=%s; skipping revoke",
            provider,
        )
        return "skipped"

    return await handler(old_value, shared_pool=shared_pool)


@register_revoke_handler("google")
async def _revoke_google_oauth_token(old_value: str, *, shared_pool: Any | None) -> str:
    """Revoke a Google OAuth token via POST to the Google revoke endpoint.

    Sends the token in the POST body (application/x-www-form-urlencoded) to avoid
    token leakage through proxy/server request logs.

    Returns a revoke_status string: ``"succeeded"``, ``"failed:<reason>"``, or ``"skipped"``.
    """
    _google_revoke_url = "https://oauth2.googleapis.com/revoke"
    try:
        async with httpx.AsyncClient(timeout=_REVOKE_TIMEOUT_S) as client:
            # Send the token in the POST body (application/x-www-form-urlencoded)
            # rather than as a query parameter.  Query params are routinely logged by
            # reverse proxies and web servers — sending in the body avoids token leakage.
            resp = await client.post(_google_revoke_url, data={"token": old_value})
        if resp.status_code == 200:
            logger.debug("_revoke_oauth_token: revoked provider=google (HTTP 200)")
            return "succeeded"
        reason = f"HTTP {resp.status_code}"
        logger.warning("_revoke_oauth_token: provider=google revoke failed: %s", reason)
        return f"failed:{reason}"
    except Exception as exc:  # noqa: BLE001
        reason = type(exc).__name__
        logger.warning("_revoke_oauth_token: provider=google revoke error: %s", exc, exc_info=True)
        return f"failed:{reason}"


async def _revoke_github_oauth_token(
    access_token: str,
    *,
    shared_pool: Any | None,
) -> str:
    """Revoke a GitHub OAuth app grant via DELETE /applications/{client_id}/grant.

    Loads GitHub app credentials (client_id, client_secret) from butler_secrets
    via CredentialStore.  If those credentials are absent or the shared pool is
    unavailable, the revoke is skipped with a clear log message — the caller's
    rotation still succeeds.

    Returns a revoke_status string: ``"succeeded"``, ``"failed:<reason>"``, or ``"skipped"``.

    Owner action required: the repo owner must store ``GITHUB_OAUTH_CLIENT_ID``
    and ``GITHUB_OAUTH_CLIENT_SECRET`` in butler_secrets before this revocation
    path will be attempted.  Use the secrets dashboard or
    ``CredentialStore.store()`` to provision these values.
    """
    if shared_pool is None:
        logger.warning(
            "_revoke_github_oauth_token: shared_pool not available; skipping GitHub revoke"
        )
        return "skipped"

    # Load GitHub app credentials from butler_secrets.
    cred_store = CredentialStore(shared_pool)
    try:
        client_id = await cred_store.load(_GITHUB_OAUTH_CLIENT_ID_KEY)
        client_secret = await cred_store.load(_GITHUB_OAUTH_CLIENT_SECRET_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_revoke_github_oauth_token: failed to load GitHub app credentials: %s", exc)
        return "skipped"

    if not client_id or not client_secret:
        logger.warning(
            "_revoke_github_oauth_token: GitHub app credentials not configured "
            "(set %s and %s in butler_secrets); skipping revoke",
            _GITHUB_OAUTH_CLIENT_ID_KEY,
            _GITHUB_OAUTH_CLIENT_SECRET_KEY,
        )
        return "skipped"

    revoke_url = _GITHUB_REVOKE_URL_TEMPLATE.format(client_id=quote(client_id))

    try:
        async with httpx.AsyncClient(timeout=_REVOKE_TIMEOUT_S) as client:
            resp = await client.delete(
                revoke_url,
                auth=(client_id, client_secret),
                json={"access_token": access_token},
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "ButlerSecretsManager/1.0",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        # GitHub returns 204 No Content on successful grant deletion.
        if resp.status_code in {200, 204}:
            logger.debug("_revoke_github_oauth_token: grant revoked (HTTP %s)", resp.status_code)
            return "succeeded"
        reason = f"HTTP {resp.status_code}"
        logger.warning("_revoke_github_oauth_token: revoke failed: %s", reason)
        return f"failed:{reason}"
    except Exception as exc:  # noqa: BLE001
        reason = type(exc).__name__
        logger.warning("_revoke_github_oauth_token: revoke error: %s", exc, exc_info=True)
        return f"failed:{reason}"


# Register GitHub's revoke handler.  The underlying helper uses the parameter name
# ``access_token`` for clarity in its docstring; the thin lambda adapts it to the
# registry's uniform ``(old_value, *, shared_pool)`` call convention.
register_revoke_handler(
    "github",
    lambda old_value, *, shared_pool: _revoke_github_oauth_token(
        old_value, shared_pool=shared_pool
    ),
)


# ---------------------------------------------------------------------------
# OAuth / PAT credential verification (live provider call)
# ---------------------------------------------------------------------------

_VERIFY_TIMEOUT_S = 10.0

# The Google token endpoint for exchanging a refresh token for an access token.
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# GitHub user endpoint — works for both classic PATs and fine-grained PATs.
_GITHUB_USER_URL = "https://api.github.com/user"

# Steam Web API base URL and GetPlayerSummaries endpoint.
_STEAM_API_BASE = "https://api.steampowered.com"
_STEAM_GET_PLAYER_SUMMARIES_URL = f"{_STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/"

# OwnTracks webhook token is a 64-character lowercase hex string (32 random bytes).
_OWNTRACKS_TOKEN_RE_LENGTH = 64
_OWNTRACKS_SYSTEM_KEY = "owntracks_webhook_token"

# Spotify token and verify endpoints.
_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SPOTIFY_ME_URL = "https://api.spotify.com/v1/me"

# butler_secrets key for the Spotify OAuth app client ID (PKCE flow — no client_secret).
_SPOTIFY_CLIENT_ID_KEY = "SPOTIFY_CLIENT_ID"


class _ProviderVerifyConfig:
    """Per-provider live-verify configuration.

    Attributes
    ----------
    verify_url:
        The URL to call to confirm the credential is valid.
    needs_token_exchange:
        True if the stored credential is a refresh token that must be exchanged
        for a short-lived access token before calling *verify_url* (Google
        flow).  False when the stored value itself is the bearer credential
        (GitHub PATs).
    auth_scheme:
        HTTP Authorization scheme to use in the verify call.  ``"Bearer"`` for
        standard OAuth; ``"token"`` for GitHub classic PATs.
    accepted_type_suffixes:
        Tuple of ``entity_info.type`` suffixes that this provider config
        matches.  A credential type is accepted when it ends with one of
        these suffixes.  E.g. Google accepts ``_oauth_refresh``; GitHub
        accepts ``_oauth_access`` (OAuth App user-access tokens) and ``_pat`` (PATs).
    """

    def __init__(
        self,
        *,
        verify_url: str,
        needs_token_exchange: bool,
        auth_scheme: str,
        accepted_type_suffixes: tuple[str, ...],
    ) -> None:
        self.verify_url = verify_url
        self.needs_token_exchange = needs_token_exchange
        self.auth_scheme = auth_scheme
        self.accepted_type_suffixes = accepted_type_suffixes

    def accepts_type(self, credential_type: str) -> bool:
        """Return True when *credential_type* is handled by this config."""
        return any(credential_type.endswith(suffix) for suffix in self.accepted_type_suffixes)


# Mapping of provider slug → verify config.
# Only providers with a working live-verify implementation are listed.
# Unlisted providers fall back to local-state check (skipped).
_OAUTH_VERIFY_PROVIDERS: dict[str, _ProviderVerifyConfig] = {
    "google": _ProviderVerifyConfig(
        verify_url="https://www.googleapis.com/oauth2/v1/userinfo",
        needs_token_exchange=True,
        auth_scheme="Bearer",
        accepted_type_suffixes=("_oauth_refresh",),
    ),
    "github": _ProviderVerifyConfig(
        verify_url=_GITHUB_USER_URL,
        needs_token_exchange=False,
        auth_scheme="token",
        # Classic PATs use _pat; fine-grained PATs share the same endpoint.
        # _oauth_access is the canonical suffix for GitHub OAuth App user-access tokens.
        accepted_type_suffixes=("_pat", "_oauth_access"),
    ),
}


async def _verify_home_assistant_credential(
    token: str,
    shared_pool: Any,
) -> tuple[str, int | None, str | None]:
    """Live-probe a Home Assistant long-lived access token.

    Looks up the configured HA base URL from ``public.entity_info`` (type=
    ``home_assistant_url``, owner entity), then calls ``GET {url}/api/`` with
    a ``Bearer`` authorization header.  HTTP 200 → ``live_ok``.

    Graceful-fallback rules (NEVER raises):
    - URL not configured in entity_info → ``skipped_local_check``
    - Network error / timeout                → ``skipped_local_check``
    - HTTP 401/403 (bad token)               → ``live_failed:<code>``
    - Any other non-200 status               → ``live_failed:<code>``
    """
    # Resolve the stored HA base URL from entity_info.
    ha_url: str | None = None
    try:
        row = await shared_pool.fetchrow(
            """
            SELECT ei.value
            FROM public.entity_info ei
            JOIN public.entities e ON e.id = ei.entity_id
            WHERE 'owner' = ANY(e.roles)
              AND ei.type = 'home_assistant_url'
            ORDER BY ei.created_at DESC
            LIMIT 1
            """,
        )
        if row is not None:
            ha_url = row["value"]
    except Exception as exc:  # noqa: BLE001
        logger.debug("_verify_home_assistant_credential: URL lookup failed: %s", exc)

    if not ha_url:
        logger.debug(
            "_verify_home_assistant_credential: home_assistant_url not configured; skipping"
        )
        return "skipped_local_check", None, None

    probe_url = f"{ha_url.rstrip('/')}/api/"
    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_S) as client:
            resp = await client.get(
                probe_url,
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_verify_home_assistant_credential: network error probing %s: %s", probe_url, exc
        )
        return "skipped_local_check", None, None

    if resp.status_code == 200:
        logger.debug("_verify_home_assistant_credential: live_ok (HTTP 200)")
        return "live_ok", None, None

    reason = f"GET /api/ HTTP {resp.status_code}"
    logger.debug("_verify_home_assistant_credential: live_failed: %s", reason)
    return f"live_failed:{resp.status_code}", resp.status_code, reason


async def _verify_steam_credential(
    api_key: str,
    shared_pool: Any,
) -> tuple[str, int | None, str | None]:
    """Live-probe a Steam Web API key using ISteamUser/GetPlayerSummaries.

    Looks up the primary Steam account's ``steam_id`` from
    ``public.steam_accounts``, then calls
    ``GET /ISteamUser/GetPlayerSummaries/v2/?key=<key>&steamids=<steamid>``.
    A 200 response with a non-empty ``players`` array → ``live_ok``.

    Graceful-fallback rules (NEVER raises):
    - No primary steam account configured     → ``skipped_local_check``
    - Network error / timeout                 → ``skipped_local_check``
    - HTTP 401/403 (bad key)                  → ``live_failed:<code>``
    - 200 but empty players                   → ``live_failed:no_players``
    """
    # Resolve the primary Steam account's steam_id.
    steam_id: str | None = None
    try:
        row = await shared_pool.fetchrow(
            """
            SELECT steam_id
            FROM public.steam_accounts
            WHERE is_primary = true
            LIMIT 1
            """,
        )
        if row is not None:
            steam_id = str(row["steam_id"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("_verify_steam_credential: steam_id lookup failed: %s", exc)

    if not steam_id:
        logger.debug("_verify_steam_credential: no primary Steam account configured; skipping")
        return "skipped_local_check", None, None

    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_S) as client:
            resp = await client.get(
                _STEAM_GET_PLAYER_SUMMARIES_URL,
                params={"key": api_key, "steamids": steam_id},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_verify_steam_credential: network error probing Steam API: %s", exc)
        return "skipped_local_check", None, None

    if resp.status_code != 200:
        reason = f"GetPlayerSummaries HTTP {resp.status_code}"
        logger.debug("_verify_steam_credential: live_failed: %s", reason)
        return f"live_failed:{resp.status_code}", resp.status_code, reason

    # A valid key + valid steamid returns {"response": {"players": [...]}}
    try:
        body = resp.json()
        players = body.get("response", {}).get("players", [])
    except Exception as exc:  # noqa: BLE001
        reason = f"invalid JSON response: {exc}"
        logger.warning("_verify_steam_credential: %s", reason)
        return "skipped_local_check", None, None

    if not players:
        reason = "no players returned (invalid key or steamid)"
        logger.debug("_verify_steam_credential: live_failed: %s", reason)
        return "live_failed:no_players", None, reason

    logger.debug("_verify_steam_credential: live_ok")
    return "live_ok", None, None


async def _verify_spotify_credential(
    refresh_token: str,
    shared_pool: Any,
) -> tuple[str, int | None, str | None]:
    """Live-probe a Spotify OAuth refresh token.

    Loads the Spotify client ID from ``butler_secrets`` (PKCE flow — no
    client_secret required), performs a token refresh via
    ``POST https://accounts.spotify.com/api/token``, then calls
    ``GET https://api.spotify.com/v1/me`` with the minted access token.
    HTTP 200 from /v1/me → ``live_ok``.

    Graceful-fallback rules (NEVER raises):
    - SPOTIFY_CLIENT_ID not configured in butler_secrets → ``skipped_local_check``
    - Network error / timeout on token refresh           → ``skipped_local_check``
    - Network error / timeout on /v1/me call             → ``skipped_local_check``
    - Token refresh non-200 (invalid/expired token)      → ``live_failed:<code>``
    - /v1/me 401 / 403 (bad access token)                → ``live_failed:<code>``
    - /v1/me any other non-200                           → ``live_failed:<code>``
    """
    # Load the Spotify client ID from butler_secrets (required for PKCE refresh).
    cred_store = CredentialStore(shared_pool)
    try:
        client_id = await cred_store.load(_SPOTIFY_CLIENT_ID_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_verify_spotify_credential: failed to load Spotify client_id: %s", exc)
        return "skipped_local_check", None, None

    if not client_id:
        logger.debug("_verify_spotify_credential: SPOTIFY_CLIENT_ID not configured; skipping")
        return "skipped_local_check", None, None

    # Step 1: Exchange the refresh token for a fresh access token (PKCE — no client_secret).
    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_S) as client:
            token_resp = await client.post(
                _SPOTIFY_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except Exception as exc:  # noqa: BLE001
        # Network error — cannot distinguish bad credential from connectivity issue.
        logger.warning("_verify_spotify_credential: network error during token refresh: %s", exc)
        return "skipped_local_check", None, None

    if token_resp.status_code != 200:
        # Token refresh failure IS a credential failure (expired / revoked refresh token).
        reason = f"token_refresh HTTP {token_resp.status_code}"
        logger.debug("_verify_spotify_credential: token refresh failed: %s", reason)
        return f"live_failed:{token_resp.status_code}", token_resp.status_code, reason

    try:
        token_data = token_resp.json()
    except Exception as exc:  # noqa: BLE001
        reason = f"token_refresh: invalid JSON response: {exc}"
        logger.warning("_verify_spotify_credential: %s", reason)
        return "live_failed:invalid_json", None, reason

    access_token = token_data.get("access_token")
    if not access_token:
        reason = "token_refresh: no access_token in response"
        logger.warning("_verify_spotify_credential: %s", reason)
        return "live_failed:no_access_token", None, reason

    # Step 2: Call GET /v1/me with the fresh access token.
    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_S) as client:
            me_resp = await client.get(
                _SPOTIFY_ME_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except Exception as exc:  # noqa: BLE001
        # Network error on verify call — fall back to local check.
        logger.warning("_verify_spotify_credential: network error during /v1/me call: %s", exc)
        return "skipped_local_check", None, None

    if me_resp.status_code == 200:
        logger.debug("_verify_spotify_credential: live_ok (HTTP 200)")
        return "live_ok", None, None

    reason = f"GET /v1/me HTTP {me_resp.status_code}"
    logger.debug("_verify_spotify_credential: live_failed: %s", reason)
    return f"live_failed:{me_resp.status_code}", me_resp.status_code, reason


def _verify_owntracks_token_format(token: str) -> tuple[str, int | None, str | None]:
    """Validate an OwnTracks webhook token by presence and format (no remote call).

    The token is a 64-character lowercase hex string generated by
    ``secrets.token_hex(32)``.  Since OwnTracks is a self-hosted inbound
    webhook with no remote verification endpoint, this function confirms
    the token is present and well-formed — sufficient to confirm it was
    generated by this system and is usable.

    Returns:
    - ``("live_ok", None, None)``        — token is present and 64-char hex
    - ``("live_failed:bad_format", None, message)`` — token present but wrong
    """
    if not token:
        return "live_failed:missing", None, "OwnTracks webhook token is not set"

    if len(token) != _OWNTRACKS_TOKEN_RE_LENGTH:
        reason = (
            f"token length {len(token)} != {_OWNTRACKS_TOKEN_RE_LENGTH} "
            "(expected 64-char hex from secrets.token_hex(32))"
        )
        return "live_failed:bad_format", None, reason

    if not all(c in "0123456789abcdef" for c in token):
        reason = "token contains non-hex characters (expected lowercase hex)"
        return "live_failed:bad_format", None, reason

    return "live_ok", None, None


async def _verify_oauth_credential(
    provider: str,
    credential_type: str,
    refresh_token: str,
    shared_pool: Any,
) -> tuple[str, int | None, str | None]:
    """Make a live call to the provider's verify endpoint.

    Returns ``(probe_status, http_code, message)`` where ``probe_status`` is one of:
    - ``"live_ok"`` — provider confirmed the credential is valid.
    - ``"live_failed:<code>"`` — provider rejected the credential (HTTP 401/403 etc.).
    - ``"skipped_local_check"`` — live verify was not attempted (unsupported
      provider, unsupported credential type, missing app credentials, or network error).

    Rules:
    - If the provider has no verify handler, return ``"skipped_local_check"``.
    - If the credential type is not accepted by the provider config, return
      ``"skipped_local_check"``.
    - For providers that need token exchange (Google):
      - Loads app credentials (client_id, client_secret) from butler_secrets via
        CredentialStore.  If those are missing, returns ``"skipped_local_check"``.
      - Exchanges the refresh token for an access token.
        If the exchange fails (non-200), returns ``"live_failed:<code>"``.
      - Calls the verify URL with the minted access token.
    - For PAT providers (GitHub):
      - Calls the verify URL directly with the stored value as the auth header.
      - No token exchange, no app credentials required.
    - Home Assistant: looks up the configured HA URL from entity_info and calls
      GET {url}/api/ with a Bearer token.  Network error → skipped_local_check.
    - Steam: looks up the primary SteamID from steam_accounts and calls
      ISteamUser/GetPlayerSummaries.  Network error → skipped_local_check.
    - Network errors → ``"skipped_local_check"`` (cannot distinguish bad cred from bad network).
    - Token-exchange non-200 → ``"live_failed:<code>"`` (this IS a credential failure).
    """
    # ------------------------------------------------------------------
    # Home Assistant: dispatch to custom handler
    # ------------------------------------------------------------------
    if provider == "home_assistant" and credential_type == "home_assistant_token":
        return await _verify_home_assistant_credential(refresh_token, shared_pool)

    # ------------------------------------------------------------------
    # Steam: dispatch to custom handler
    # ------------------------------------------------------------------
    if provider == "steam" and credential_type.endswith("_api_key"):
        return await _verify_steam_credential(refresh_token, shared_pool)

    # ------------------------------------------------------------------
    # Spotify: dispatch to custom handler (PKCE refresh → /v1/me)
    # ------------------------------------------------------------------
    if provider == "spotify" and credential_type.endswith("_oauth_refresh"):
        return await _verify_spotify_credential(refresh_token, shared_pool)

    provider_config = _OAUTH_VERIFY_PROVIDERS.get(provider)
    if provider_config is None:
        logger.debug(
            "_verify_oauth_credential: no verify handler for provider=%s; skipping",
            provider,
        )
        return "skipped_local_check", None, None

    if not provider_config.accepts_type(credential_type):
        logger.debug(
            "_verify_oauth_credential: credential type %s not accepted for provider=%s; skipping",
            credential_type,
            provider,
        )
        return "skipped_local_check", None, None

    # ------------------------------------------------------------------
    # Branch A: providers that require a refresh → access token exchange
    # ------------------------------------------------------------------
    if provider_config.needs_token_exchange:
        # Load app credentials (client_id, client_secret) from butler_secrets.
        cred_store = CredentialStore(shared_pool)
        try:
            client_id = await cred_store.load(KEY_CLIENT_ID)
            client_secret = await cred_store.load(KEY_CLIENT_SECRET)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_verify_oauth_credential: failed to load app credentials for provider=%s: %s",
                provider,
                exc,
            )
            return "skipped_local_check", None, None

        if not client_id or not client_secret:
            logger.debug(
                "_verify_oauth_credential: app credentials not configured for provider=%s;"
                " skipping",
                provider,
            )
            return "skipped_local_check", None, None

        try:
            async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_S) as client:
                # Step 1: Exchange refresh token for an access token.
                token_resp = await client.post(
                    _GOOGLE_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            # Network error — cannot distinguish bad credential from bad network.
            logger.warning(
                "_verify_oauth_credential: network error during token exchange for provider=%s: %s",
                provider,
                exc,
            )
            return "skipped_local_check", None, None

        if token_resp.status_code != 200:
            # Token exchange failure IS a credential failure (expired / revoked refresh token).
            reason = f"token_exchange HTTP {token_resp.status_code}"
            logger.debug(
                "_verify_oauth_credential: token exchange failed for provider=%s: %s",
                provider,
                reason,
            )
            return f"live_failed:{token_resp.status_code}", token_resp.status_code, reason

        try:
            token_data = token_resp.json()
        except Exception as exc:  # noqa: BLE001
            reason = f"token_exchange: invalid JSON response: {exc}"
            logger.warning("_verify_oauth_credential: provider=%s %s", provider, reason)
            return "live_failed:invalid_json", None, reason

        access_token = token_data.get("access_token")
        if not access_token:
            reason = "token_exchange: no access_token in response"
            logger.warning("_verify_oauth_credential: provider=%s %s", provider, reason)
            return "live_failed:no_access_token", None, reason

        bearer_value = access_token

    # ------------------------------------------------------------------
    # Branch B: PAT / direct-bearer providers (no token exchange)
    # ------------------------------------------------------------------
    else:
        # The stored credential is used directly as the auth header value.
        bearer_value = refresh_token

    # ------------------------------------------------------------------
    # Final step: call the provider's verify URL
    # ------------------------------------------------------------------
    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_S) as client:
            info_resp = await client.get(
                provider_config.verify_url,
                headers={"Authorization": f"{provider_config.auth_scheme} {bearer_value}"},
            )
    except Exception as exc:  # noqa: BLE001
        # Network error on verify call — fall back to local check.
        logger.warning(
            "_verify_oauth_credential: network error during verify call for provider=%s: %s",
            provider,
            exc,
        )
        return "skipped_local_check", None, None

    if info_resp.status_code == 200:
        logger.debug("_verify_oauth_credential: live_ok for provider=%s", provider)
        return "live_ok", None, None

    reason = f"userinfo HTTP {info_resp.status_code}"
    logger.debug("_verify_oauth_credential: live_failed for provider=%s: %s", provider, reason)
    return f"live_failed:{info_resp.status_code}", info_resp.status_code, reason


# ---------------------------------------------------------------------------
# Mutation audit helper
# ---------------------------------------------------------------------------

_OWNER_ACTOR = "owner"


async def _write_credential_audit(
    pool: Any,
    *,
    action: str,
    provider: str,
    note: str | None = None,
) -> None:
    """Append one row to public.audit_log for a user-credential mutation.

    Silently swallows errors so audit logging never blocks the primary
    operation (fire-and-forget pattern consistent with audit_emit.py).
    """
    target = normalize_credential_key("user", provider)
    try:
        await audit_router.append(
            pool,
            _OWNER_ACTOR,
            action,
            target=target,
            note=note,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to write credential audit: action=%s provider=%s",
            action,
            provider,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# POST /api/secrets/user/<provider>/rotate
# ---------------------------------------------------------------------------


@router.post(
    "/user/{provider}/rotate",
    response_model=ApiResponse[UserSecretDetail],
)
async def rotate_user_credential(
    provider: str,
    body: RotateRequest,
    identity: UUID | None = Query(
        default=None,
        description=(
            "Entity UUID for the credential to rotate. When omitted, defaults to the owner entity."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[UserSecretDetail]:
    """Rotate (replace) the stored value for a user-scoped credential.

    Writes the new value to the matching ``public.entity_info`` row,
    attempts to revoke the old OAuth token at the provider (fire-and-forget),
    and appends a ``rotated`` audit row to ``public.audit_log``.

    The spec requires this endpoint to return ``ApiResponse<UserSecret>``
    (updated).  The rotation is a direct in-place update of the
    ``entity_info.value`` column.

    For OAuth providers (e.g. Google), the OLD token is revoked at the
    provider AFTER the local DB update succeeds.  If the provider revoke call
    fails, the rotation still succeeds — revoke failure is logged at WARN level
    and recorded in the audit note under ``revoke_status``.

    Spec anchor
    -----------
    openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
    §User credential mutations — ``rotated`` audit action
    """
    shared_pool: Any = None
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        pass

    if shared_pool is None:
        raise HTTPException(status_code=503, detail="Shared credential database unavailable")

    # Locate the existing row so we can confirm it exists and capture the old token value.
    detail = await _fetch_single_user_secret(shared_pool, provider=provider, identity=identity)
    if detail is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    # Capture the old raw value before we overwrite it.  We need it for provider revocation.
    # _fetch_single_user_secret returns a UserSecretDetail which does NOT expose the raw value
    # (fingerprint only), so we re-read it directly here.
    old_raw_value: str | None = None
    try:
        _old_row = await shared_pool.fetchrow(
            "SELECT value FROM public.entity_info WHERE id = $1",
            UUID(detail.id),
        )
        if _old_row is not None:
            old_raw_value = _old_row["value"]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "rotate_user_credential: could not read old value for revoke (provider=%s): %s",
            provider,
            exc,
        )

    # Update the value in-place using the primary key from the fetched row.
    # Using id (PK) is safer than entity_id+LIKE which could match multiple rows
    # in multi-account scenarios.
    try:
        await shared_pool.execute(
            """
            UPDATE public.entity_info
            SET value = $1, updated_at = now()
            WHERE id = $2
            """,
            body.value,
            UUID(detail.id),
        )
    except Exception as exc:
        logger.warning("rotate_user_credential: update failed for provider=%s: %s", provider, exc)
        raise HTTPException(status_code=503, detail="Credential rotation failed") from exc

    # Revoke the old OAuth token at the provider — fire-and-forget.
    # Only triggered when: (a) we have the old value, (b) it differs from the new value,
    # and (c) the credential type is an OAuth type.
    revoke_status = "skipped"
    if old_raw_value is not None and old_raw_value != body.value:
        revoke_status = await _revoke_oauth_token(
            provider, detail.type, old_raw_value, shared_pool=shared_pool
        )

    # Audit — fire-and-forget.
    await _write_credential_audit(
        shared_pool,
        action="rotated",
        provider=provider,
        note=f"Value replaced via rotate endpoint; revoke_status={revoke_status}",
    )

    # Re-fetch to return the updated state with freshly computed fingerprint.
    updated = await _fetch_single_user_secret(shared_pool, provider=provider, identity=identity)
    if updated is None:
        # Should not happen; the row was just updated.
        raise HTTPException(status_code=503, detail="Credential not found after rotation")

    return ApiResponse[UserSecretDetail](data=updated, meta=ApiMeta())


# ---------------------------------------------------------------------------
# POST /api/secrets/user/<provider>/disconnect
# ---------------------------------------------------------------------------


@router.post(
    "/user/{provider}/disconnect",
    response_model=ApiResponse[DisconnectStatus],
)
async def disconnect_user_credential(
    provider: str,
    identity: UUID | None = Query(
        default=None,
        description=(
            "Entity UUID for the credential to disconnect. "
            "When omitted, defaults to the owner entity."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[DisconnectStatus]:
    """Disconnect (remove) a user-scoped credential.

    Deletes the matching ``public.entity_info`` row(s) for the provider and
    appends a ``disconnected`` audit row to ``public.audit_log``.

    This is a hard delete.  The credential value is removed from the DB.
    OAuth tokens are NOT revoked at the provider — the caller should
    separately revoke the token if needed (or use the provider's dashboard).

    Returns ``ApiResponse<{status: "disconnected"}>`` on success.
    Returns 404 when no matching credential exists.

    Spec anchor
    -----------
    openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
    §User credential mutations — ``disconnected`` audit action
    """
    shared_pool: Any = None
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        pass

    if shared_pool is None:
        raise HTTPException(status_code=503, detail="Shared credential database unavailable")

    # Confirm the credential exists before deleting.
    detail = await _fetch_single_user_secret(shared_pool, provider=provider, identity=identity)
    if detail is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    # Delete the row using the primary key from the fetched row.
    # Using id (PK) is safer than entity_id+LIKE which could match multiple rows
    # in multi-account scenarios.
    try:
        await shared_pool.execute(
            """
            DELETE FROM public.entity_info
            WHERE id = $1
            """,
            UUID(detail.id),
        )
    except Exception as exc:
        logger.warning(
            "disconnect_user_credential: delete failed for provider=%s: %s", provider, exc
        )
        raise HTTPException(status_code=503, detail="Credential disconnect failed") from exc

    # Audit — fire-and-forget.
    await _write_credential_audit(
        shared_pool,
        action="disconnected",
        provider=provider,
        note="Credential removed via disconnect endpoint",
    )

    return ApiResponse[DisconnectStatus](data=DisconnectStatus(), meta=ApiMeta())


# ---------------------------------------------------------------------------
# POST /api/secrets/user/<provider>/probe
# ---------------------------------------------------------------------------


@router.post(
    "/user/{provider}/probe",
    response_model=ApiResponse[TestResult],
)
async def probe_user_credential(
    provider: str,
    identity: UUID | None = Query(
        default=None,
        description=(
            "Entity UUID for the credential to probe. When omitted, defaults to the owner entity."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[TestResult]:
    """Probe a user-scoped credential and record the test result.

    For supported providers (currently Google OAuth and GitHub PAT), this makes a
    LIVE call to the provider's verify endpoint to confirm the credential actually
    works.  For other providers or credential types, it falls back to deriving the
    probe outcome from the current local state (is the value set, is the credential
    expired, is the last_test_ok field true?).

    In the same SQL transaction it:
    1. Inserts one row into ``public.secret_probe_log``.
    2. Updates ``last_verified``, ``last_test_ok``, ``last_test_code``,
       ``last_test_message`` on the matching ``public.entity_info`` row.

    Appends a ``verified`` (ok) or ``failed`` (not-ok) audit row.  The audit note
    includes ``probe_status=live_ok|live_failed:<code>|skipped_local_check`` so the
    audit log distinguishes between live-verified, live-failed, and fallback paths.

    Spec anchor
    -----------
    openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
    §User credential mutations — probe writes probe_log + audit
    openspec/changes/redesign-secrets-passport/specs/core-credentials/spec.md
    §Cache write on probe (same-transaction invariant)
    """
    shared_pool: Any = None
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        pass

    if shared_pool is None:
        raise HTTPException(status_code=503, detail="Shared credential database unavailable")

    # Fetch the credential to derive current state.
    detail = await _fetch_single_user_secret(shared_pool, provider=provider, identity=identity)
    if detail is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    # Attempt a live provider verification for supported OAuth providers.
    # Falls back to local-state check on any exception (network errors, missing app creds, etc.)
    # so the probe endpoint never fails with 503 due to a verify call.
    probe_ok: bool
    probe_code: int | None = None
    probe_message: str | None = None
    probe_status: str = "skipped_local_check"
    credential_key = detail.type  # e.g. 'google_oauth_refresh'

    # Fetch the raw refresh token for live verification (not exposed on UserSecretDetail).
    raw_refresh_token: str | None = None
    try:
        _token_row = await shared_pool.fetchrow(
            "SELECT value FROM public.entity_info WHERE id = $1",
            UUID(detail.id),
        )
        if _token_row is not None:
            raw_refresh_token = _token_row["value"]
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "probe_user_credential: could not read raw token for provider=%s: %s",
            provider,
            exc,
        )

    if raw_refresh_token:
        try:
            probe_status, probe_code, probe_message = await _verify_oauth_credential(
                provider,
                credential_key,
                raw_refresh_token,
                shared_pool,
            )
        except Exception as exc:  # noqa: BLE001
            # Should not happen — _verify_oauth_credential catches internally — but be safe.
            logger.warning(
                "probe_user_credential: unexpected verify error for provider=%s: %s",
                provider,
                exc,
            )
            probe_status = "skipped_local_check"

    # Resolve final probe_ok from live result or fall back to local state.
    if probe_status == "live_ok":
        probe_ok = True
        probe_code = None
        probe_message = None
    elif probe_status.startswith("live_failed"):
        probe_ok = False
        # probe_code and probe_message are already set by _verify_oauth_credential
    else:
        # skipped_local_check — derive from local state.
        probe_ok = detail.state == "ok"
        probe_code = None
        probe_message = detail.failure_tail if not probe_ok else None

    # Execute probe_log insert + entity_info cache update in one transaction.
    try:
        async with shared_pool.acquire() as conn:
            async with conn.transaction():
                # 1. Insert probe log row.
                await conn.execute(
                    """
                    INSERT INTO public.secret_probe_log
                        (credential_scope, credential_key, ok, code, message)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    "user",
                    credential_key,
                    probe_ok,
                    probe_code,
                    probe_message,
                )

                # 2. Update test-state cache columns on entity_info.
                # Use the primary key from the fetched row — safer than entity_id+LIKE
                # which could match multiple rows in multi-account scenarios.
                # last_verified is set only on success (per spec §Cache write on probe).
                await conn.execute(
                    """
                    UPDATE public.entity_info
                    SET
                        last_test_ok = $1,
                        last_test_code = $2,
                        last_test_message = $3,
                        last_verified = CASE WHEN $1 THEN now() ELSE last_verified END
                    WHERE id = $4
                    """,
                    probe_ok,
                    probe_code,
                    probe_message,
                    UUID(detail.id),
                )

    except UndefinedTableError as exc:
        raise HTTPException(
            status_code=503,
            detail="secret_probe_log table not available — migration may not have run",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "probe_user_credential: transaction failed for provider=%s: %s", provider, exc
        )
        raise HTTPException(status_code=503, detail="Probe transaction failed") from exc

    # Audit — fire-and-forget outside the data transaction so audit failure
    # never rolls back a committed probe result.
    audit_action = "verified" if probe_ok else "failed"
    probe_fail_msg = probe_message or "unknown error"
    if probe_ok:
        note = f"Probe ok; probe_status={probe_status}"
    else:
        note = f"Probe failed: {probe_fail_msg}; probe_status={probe_status}"
    await _write_credential_audit(shared_pool, action=audit_action, provider=provider, note=note)

    result = TestResult(
        ok=probe_ok,
        code=probe_code,
        message=probe_message,
        at=_format_probe_time(datetime.now(tz=UTC)),
    )
    return ApiResponse[TestResult](data=result, meta=ApiMeta())


# ---------------------------------------------------------------------------
# POST /api/secrets/user/<provider>/reauthorize
# ---------------------------------------------------------------------------


@router.post(
    "/user/{provider}/reauthorize",
    response_model=ApiResponse[ReauthorizeResponse],
)
async def reauthorize_user_credential(
    provider: str,
    identity: UUID | None = Query(
        default=None,
        description=(
            "Entity UUID for the credential to reauthorize. "
            "When omitted, defaults to the owner entity."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ReauthorizeResponse]:
    """Initiate an OAuth reauthorization dance for a user-scoped credential.

    Builds and returns a ``redirect_url`` pointing to
    ``/api/oauth/<provider>/start?page_of_origin=secrets`` (plus any
    account hint derived from the credential's stored label/email).  The
    caller is expected to redirect the browser to this URL, which begins
    the OAuth dance.  The OAuth callback will redirect back to
    ``/secrets?focus=u:<provider>&toast=connected`` on success.

    Appends an ``attempted`` audit row (because the reauth dance has been
    initiated but not yet completed).

    Multi-account note: when the stored entity_info label contains an email
    address, it is passed as ``account_hint=<email>`` so the OAuth dance
    pre-selects the correct Google account.

    Spec anchor
    -----------
    openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
    §User credential mutations — reauthorize returns redirect_url
    openspec/changes/redesign-secrets-passport/specs/butler-secrets/spec.md
    §Cross-Page Reauth Bookkeeping
    """
    shared_pool: Any = None
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        pass

    if shared_pool is None:
        raise HTTPException(status_code=503, detail="Shared credential database unavailable")

    # Look up the credential to derive account hint (label may hold email).
    detail = await _fetch_single_user_secret(shared_pool, provider=provider, identity=identity)
    if detail is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    # Build the OAuth start URL.  page_of_origin=secrets so the callback
    # routes the user back to the /secrets page on completion.
    params: dict[str, str] = {"page_of_origin": "secrets"}
    if detail.label:
        # The label field stores the account email for OAuth credentials.
        params["account_hint"] = detail.label

    redirect_url = f"/api/oauth/{provider}/start?{urlencode(params)}"

    # Audit — attempted (dance initiated, not yet completed).
    await _write_credential_audit(
        shared_pool,
        action="attempted",
        provider=provider,
        note="Reauthorize initiated from /secrets (page_of_origin=secrets)",
    )

    return ApiResponse[ReauthorizeResponse](
        data=ReauthorizeResponse(redirect_url=redirect_url),
        meta=ApiMeta(),
    )


# ---------------------------------------------------------------------------
# System credential mutation models
# ---------------------------------------------------------------------------


class SystemSetRequest(BaseModel):
    """Request body for POST /api/secrets/system/<key>."""

    value: str
    """New secret value to store."""
    target: str = "shared"
    """Write target for the credential:

    - ``'shared'`` (default) — write to the switchboard schema's butler_secrets.
    - ``'shared-public'`` — write to the public credential pool (public.butler_secrets),
      which modules read via CredentialStore.  Use this for rows surfaced by the
      inventory with ``butler='shared-public'``.
    - ``'<butler>'`` — write a per-butler override row in that butler's schema.
    """


class SystemDeleteStatus(BaseModel):
    """Response payload for DELETE /api/secrets/system/<key>."""

    status: str
    """'disconnected' (shared row deleted) or 'revoked' (override row deleted)."""


# ---------------------------------------------------------------------------
# System probe rate-limit guard
# ---------------------------------------------------------------------------

#: In-process TTL guard for system probe: maps key → last_probe_ts (epoch seconds).
#: Shared across requests within the same server process.
_system_probe_timestamps: dict[str, float] = {}

#: Rate-limit window in seconds (1 call per page-load per key).
_SYSTEM_PROBE_RATE_LIMIT_S: float = 5.0


def _check_system_probe_rate_limit(key: str) -> None:
    """Raise HTTP 429 if a probe for this key was recorded within the TTL window.

    The guard is purely in-process (not Redis/DB-backed) which is sufficient for
    the single-owner, single-process deployment assumed by v1.  The key is the
    secret_key string (not keyed on client IP) because system credential probes
    are admin-level operations.

    Raises
    ------
    HTTPException (429)
        When the same key was probed within the last ``_SYSTEM_PROBE_RATE_LIMIT_S``
        seconds.
    """
    now = time.monotonic()
    last = _system_probe_timestamps.get(key)
    if last is not None and (now - last) < _SYSTEM_PROBE_RATE_LIMIT_S:
        remaining = _SYSTEM_PROBE_RATE_LIMIT_S - (now - last)
        raise HTTPException(
            status_code=429,
            detail=(f"Probe rate limit exceeded for key {key!r}. Retry after {remaining:.1f}s."),
        )
    _system_probe_timestamps[key] = now


# ---------------------------------------------------------------------------
# System mutation audit helper
# ---------------------------------------------------------------------------


async def _write_system_audit(
    pool: Any,
    *,
    action: str,
    key: str,
    note: str | None = None,
) -> None:
    """Append one row to public.audit_log for a system-credential mutation.

    Uses normalize_credential_key("system", key) as the canonical target.
    Silently swallows errors (fire-and-forget, consistent with user audit helper).
    """
    target = normalize_credential_key("system", key)
    try:
        await audit_router.append(
            pool,
            _OWNER_ACTOR,
            action,
            target=target,
            note=note,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to write system credential audit: action=%s key=%s",
            action,
            key,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# POST /api/secrets/system/<key>  — set / rotate / override
# ---------------------------------------------------------------------------


@router.post(
    "/system/{key}",
    response_model=ApiResponse[SystemSecretDetail],
)
async def set_system_credential(
    key: str,
    body: SystemSetRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SystemSecretDetail]:
    """Set (first-time), rotate (update existing), or override (per-butler) a system credential.

    Behaviour depends on ``body.target``:

    - ``target = "shared"`` — write to the switchboard's ``butler_secrets``
      table.  If the row exists, UPDATE the value (audit ``rotated``); if not,
      INSERT a new row (audit ``set``).

    - ``target = "shared-public"`` — write to the public credential pool
      (``public.butler_secrets`` via ``credential_shared_pool()``).  This is the
      pool that modules read via CredentialStore; rows in this pool are surfaced
      in the inventory with ``butler="shared-public"`` and are fully editable.
      Same set/rotate semantics as target="shared".

    - ``target = "<butler>"`` — INSERT a new override row in that butler's
      ``butler_secrets`` table (the butler schema).  The override takes
      precedence over the shared row for that butler.  Audit ``overrode``.

    Returns ``ApiResponse<SystemSecretDetail>`` (updated) reflecting the new state.
    Returns 404 when ``target = "<butler>"`` and the butler is not registered.

    Spec anchor
    -----------
    openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
    §System credential mutations
    """
    target = body.target
    value = body.value

    if target == "shared":
        # Shared row lives in the switchboard butler schema.
        try:
            pool = db.pool("switchboard")
        except KeyError as exc:
            raise HTTPException(
                status_code=503,
                detail="Switchboard pool is not available",
            ) from exc

        # Check if an existing shared row exists.
        try:
            existing = await pool.fetchrow(
                "SELECT secret_key FROM butler_secrets WHERE secret_key = $1",
                key,
            )
        except UndefinedTableError:
            existing = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("set_system_credential: fetchrow failed key=%s: %s", key, exc)
            raise HTTPException(status_code=503, detail="Credential lookup failed") from exc

        if existing is None:
            # First-time create.
            try:
                await pool.execute(
                    """
                    INSERT INTO butler_secrets (secret_key, secret_value, updated_at)
                    VALUES ($1, $2, now())
                    """,
                    key,
                    value,
                )
            except Exception as exc:
                logger.warning("set_system_credential: INSERT failed key=%s: %s", key, exc)
                raise HTTPException(status_code=503, detail="Credential create failed") from exc
            audit_action = "set"
            audit_note = "System credential created (first-time set)"
        else:
            # Row exists — rotate the value.
            try:
                await pool.execute(
                    """
                    UPDATE butler_secrets
                    SET secret_value = $1, updated_at = now()
                    WHERE secret_key = $2
                    """,
                    value,
                    key,
                )
            except Exception as exc:
                logger.warning("set_system_credential: UPDATE failed key=%s: %s", key, exc)
                raise HTTPException(status_code=503, detail="Credential rotation failed") from exc
            audit_action = "rotated"
            audit_note = "System credential value replaced (rotated)"

        await _write_system_audit(pool, action=audit_action, key=key, note=audit_note)

        # Re-fetch to return updated state.
        detail = await _fetch_single_system_secret(pool, "switchboard", key)
        if detail is None:
            raise HTTPException(status_code=503, detail="Credential not found after write")
        return ApiResponse[SystemSecretDetail](data=detail, meta=ApiMeta())

    elif target == "shared-public":
        # Public credential pool — the pool modules read via CredentialStore.
        try:
            pool = db.credential_shared_pool()
        except KeyError as exc:
            raise HTTPException(
                status_code=503,
                detail="Shared credential pool is not available",
            ) from exc

        # Check if an existing row exists in the public pool.
        try:
            existing = await pool.fetchrow(
                "SELECT secret_key FROM butler_secrets WHERE secret_key = $1",
                key,
            )
        except UndefinedTableError:
            existing = None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "set_system_credential: fetchrow failed key=%s target=shared-public: %s",
                key,
                exc,
            )
            raise HTTPException(status_code=503, detail="Credential lookup failed") from exc

        if existing is None:
            # First-time create in the public pool.
            try:
                await pool.execute(
                    """
                    INSERT INTO butler_secrets (secret_key, secret_value, updated_at)
                    VALUES ($1, $2, now())
                    """,
                    key,
                    value,
                )
            except UndefinedTableError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="butler_secrets table not available — migration may not have run",
                ) from exc
            except Exception as exc:
                logger.warning(
                    "set_system_credential: INSERT failed key=%s target=shared-public: %s",
                    key,
                    exc,
                )
                raise HTTPException(status_code=503, detail="Credential create failed") from exc
            audit_action = "set"
            audit_note = "System credential created in public pool (first-time set)"
        else:
            # Row exists — rotate the value in the public pool.
            try:
                await pool.execute(
                    """
                    UPDATE butler_secrets
                    SET secret_value = $1, updated_at = now()
                    WHERE secret_key = $2
                    """,
                    value,
                    key,
                )
            except UndefinedTableError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="butler_secrets table not available — migration may not have run",
                ) from exc
            except Exception as exc:
                logger.warning(
                    "set_system_credential: UPDATE failed key=%s target=shared-public: %s",
                    key,
                    exc,
                )
                raise HTTPException(status_code=503, detail="Credential rotation failed") from exc
            audit_action = "rotated"
            audit_note = "System credential value replaced in public pool (rotated)"

        await _write_system_audit(pool, action=audit_action, key=key, note=audit_note)

        # Re-fetch from the public pool to return updated state.
        detail = await _fetch_single_system_secret(pool, "shared-public", key)
        if detail is None:
            raise HTTPException(status_code=503, detail="Credential not found after write")
        return ApiResponse[SystemSecretDetail](data=detail, meta=ApiMeta())

    else:
        # Per-butler override — write to the target butler's schema.
        butler_name = target
        try:
            pool = db.pool(butler_name)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Butler {butler_name!r} is not registered",
            ) from exc

        # Override row: UPSERT into the butler's butler_secrets table.
        # We always treat this as "overrode" regardless of whether a prior
        # override existed, per spec §System credential mutations.
        try:
            await pool.execute(
                """
                INSERT INTO butler_secrets (secret_key, secret_value, updated_at)
                VALUES ($1, $2, now())
                ON CONFLICT (secret_key) DO UPDATE
                    SET secret_value = EXCLUDED.secret_value,
                        updated_at = EXCLUDED.updated_at
                """,
                key,
                value,
            )
        except UndefinedTableError as exc:
            raise HTTPException(
                status_code=503,
                detail="butler_secrets table not available — migration may not have run",
            ) from exc
        except Exception as exc:
            logger.warning(
                "set_system_credential: override UPSERT failed key=%s butler=%s: %s",
                key,
                butler_name,
                exc,
            )
            raise HTTPException(status_code=503, detail="Override write failed") from exc

        await _write_system_audit(
            pool,
            action="overrode",
            key=key,
            note=f"Per-butler override created for {butler_name!r}",
        )

        detail = await _fetch_single_system_secret(pool, butler_name, key)
        if detail is None:
            raise HTTPException(status_code=503, detail="Credential not found after override write")
        # Mark the returned detail as a local (per-butler) override.
        detail.row_state = "local"
        detail.target = butler_name
        return ApiResponse[SystemSecretDetail](data=detail, meta=ApiMeta())


# ---------------------------------------------------------------------------
# POST /api/secrets/system/<key>/probe
# ---------------------------------------------------------------------------


@router.post(
    "/system/{key}/probe",
    response_model=ApiResponse[TestResult],
)
async def probe_system_credential(
    key: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[TestResult]:
    """Probe a system credential and record the test result.

    Does NOT make external provider calls.  Derives the probe outcome from
    the current credential state (is the value set? is it expired?
    is last_test_ok true?).

    Within a single SQL transaction:
    1. Inserts one row into ``public.secret_probe_log``.
    2. Updates ``last_verified``, ``last_test_ok``, ``last_test_code``,
       ``last_test_message`` on the matching ``butler_secrets`` row.

    Searches all registered butler schemas for the key (same discovery order
    as GET /api/secrets/system/<key>); probes the first matching row.

    Rate-limited to 1 call per 5 s per key (in-process guard).

    Audit: ``verified`` (ok), ``failed`` (not-ok).

    Returns 404 when no credential exists for the given key.
    Returns 429 when the rate limit is exceeded.

    Spec anchor
    -----------
    openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
    §System credential mutations — probe writes probe_log + audit
    openspec/changes/redesign-secrets-passport/specs/core-credentials/spec.md
    §Cache write on probe (same-transaction invariant)
    """
    # Rate-limit guard (raises 429 if too recent).
    _check_system_probe_rate_limit(key)

    # Locate the credential across all registered butler schemas.
    found_pool: Any = None
    found_butler: str | None = None
    detail: SystemSecretDetail | None = None
    for butler_name in db.butler_names:
        try:
            pool = db.pool(butler_name)
        except KeyError:
            continue
        detail = await _fetch_single_system_secret(pool, butler_name, key)
        if detail is not None:
            found_pool = pool
            found_butler = butler_name
            break

    # Also search the shared credential pool (public.butler_secrets) when
    # the credential was not found in any per-butler schema.
    shared_pool: Any = None
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        pass

    if found_pool is None and shared_pool is not None:
        detail = await _fetch_single_system_secret(shared_pool, "shared-public", key)
        if detail is not None:
            found_pool = shared_pool
            found_butler = "shared-public"

    if found_pool is None or found_butler is None or detail is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    # Require the shared pool for probe_log (cross-butler public table).
    if shared_pool is None:
        raise HTTPException(
            status_code=503,
            detail="Shared credential pool unavailable for probe_log write",
        )

    # ---------------------------------------------------------------------------
    # Live probe for supported system credentials (OwnTracks webhook token).
    # The raw value is read directly here — it is used only for the format check
    # and never returned to the caller.  Falls back to local-state on any error.
    # ---------------------------------------------------------------------------
    probe_status_system: str = "skipped_local_check"
    probe_ok_live: bool | None = None
    probe_code_live: int | None = None
    probe_message_live: str | None = None

    if key == _OWNTRACKS_SYSTEM_KEY:
        raw_value: str | None = None
        try:
            _raw_row = await found_pool.fetchrow(
                "SELECT secret_value FROM butler_secrets WHERE secret_key = $1",
                key,
            )
            if _raw_row is not None:
                raw_value = _raw_row["secret_value"]
        except Exception as exc:  # noqa: BLE001
            logger.debug("probe_system_credential: could not read raw value key=%s: %s", key, exc)

        if raw_value:
            probe_status_system, probe_code_live, probe_message_live = (
                _verify_owntracks_token_format(raw_value)
            )
            probe_ok_live = probe_status_system == "live_ok"

    # Derive probe outcome: live result (if available) takes precedence over local state.
    if probe_ok_live is not None:
        probe_ok = probe_ok_live
        probe_code = probe_code_live
        probe_message = probe_message_live
    else:
        # Fallback: derive from local state.
        probe_ok = detail.state == "ok"
        probe_code = None
        probe_message = None
        if not probe_ok and detail.test is not None:
            probe_message = detail.test.message

    # Execute probe_log insert + butler_secrets cache update in one transaction.
    # probe_log INSERT goes to the shared pool (public schema).
    # butler_secrets UPDATE goes to the found butler pool.
    # We run two separate transactions since they are on different pools,
    # but both succeed or we surface an error.  The probe_log write is primary;
    # the cache update is a best-effort in-place optimisation.
    try:
        async with shared_pool.acquire() as shared_conn:
            async with shared_conn.transaction():
                await shared_conn.execute(
                    """
                    INSERT INTO public.secret_probe_log
                        (credential_scope, credential_key, ok, code, message)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    "system",
                    key,
                    probe_ok,
                    probe_code,
                    probe_message,
                )
    except UndefinedTableError as exc:
        raise HTTPException(
            status_code=503,
            detail="secret_probe_log table not available — migration may not have run",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("probe_system_credential: probe_log insert failed key=%s: %s", key, exc)
        raise HTTPException(status_code=503, detail="Probe log write failed") from exc

    # Update test-state cache columns on butler_secrets (best-effort, non-transactional
    # relative to the probe_log write since it's a different pool).
    try:
        await found_pool.execute(
            """
            UPDATE butler_secrets
            SET
                last_test_ok = $1,
                last_test_code = $2,
                last_test_message = $3,
                last_verified = CASE WHEN $1 THEN now() ELSE last_verified END
            WHERE secret_key = $4
            """,
            probe_ok,
            probe_code,
            probe_message,
            key,
        )
    except Exception as exc:  # noqa: BLE001
        # Cache update failure is non-fatal; the probe_log row is the source of truth.
        logger.warning(
            "probe_system_credential: butler_secrets cache update failed key=%s: %s", key, exc
        )

    # Audit — fire-and-forget.
    audit_action = "verified" if probe_ok else "failed"
    if probe_ok:
        note = f"Probe ok; probe_status={probe_status_system}"
    else:
        _fail_msg = probe_message or "unknown error"
        note = f"Probe failed: {_fail_msg}; probe_status={probe_status_system}"
    await _write_system_audit(found_pool, action=audit_action, key=key, note=note)

    result = TestResult(
        ok=probe_ok,
        code=probe_code,
        message=probe_message,
        at=_format_probe_time(datetime.now(tz=UTC)),
    )
    return ApiResponse[TestResult](data=result, meta=ApiMeta())


# ---------------------------------------------------------------------------
# DELETE /api/secrets/system/<key>?target=<butler|shared|shared-public>
# ---------------------------------------------------------------------------


@router.delete(
    "/system/{key}",
    response_model=ApiResponse[SystemDeleteStatus],
)
async def delete_system_credential(
    key: str,
    target: str = Query(
        default="shared",
        description=(
            "'shared' to fully delete the shared (switchboard) row (audit: disconnected), "
            "'shared-public' to delete from the public credential pool (audit: disconnected), "
            "or a butler name to remove the per-butler override row (audit: revoked)."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SystemDeleteStatus]:
    """Remove a system credential row.

    Behaviour depends on ``?target=``:

    - ``target=shared`` — DELETE the shared row from the switchboard's
      ``butler_secrets`` table.  Audit action: ``disconnected``.

    - ``target=shared-public`` — DELETE from the public credential pool
      (``public.butler_secrets`` via ``credential_shared_pool()``).
      Audit action: ``disconnected``.

    - ``target=<butler>`` — DELETE the per-butler override row from that
      butler's ``butler_secrets`` table.  Audit action: ``revoked``.

    Returns 404 when:
    - the key does not exist in the targeted table,
    - or the butler specified by ``target`` is not registered.

    Spec anchor
    -----------
    openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
    §System credential mutations — DELETE
    """
    if target == "shared":
        try:
            pool = db.pool("switchboard")
        except KeyError as exc:
            raise HTTPException(
                status_code=503,
                detail="Switchboard pool is not available",
            ) from exc

        # Verify the row exists before deleting.
        try:
            existing = await pool.fetchrow(
                "SELECT secret_key FROM butler_secrets WHERE secret_key = $1",
                key,
            )
        except UndefinedTableError as exc:
            raise HTTPException(
                status_code=503,
                detail="butler_secrets table not available — migration may not have run",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail="Credential lookup failed") from exc

        if existing is None:
            raise HTTPException(status_code=404, detail="Credential not found")

        try:
            await pool.execute(
                "DELETE FROM butler_secrets WHERE secret_key = $1",
                key,
            )
        except Exception as exc:
            logger.warning("delete_system_credential: DELETE failed key=%s: %s", key, exc)
            raise HTTPException(status_code=503, detail="Credential delete failed") from exc

        await _write_system_audit(
            pool,
            action="disconnected",
            key=key,
            note="Shared system credential deleted",
        )
        return ApiResponse[SystemDeleteStatus](
            data=SystemDeleteStatus(status="disconnected"),
            meta=ApiMeta(),
        )

    elif target == "shared-public":
        # Public credential pool (public.butler_secrets) deletion.
        try:
            pool = db.credential_shared_pool()
        except KeyError as exc:
            raise HTTPException(
                status_code=503,
                detail="Shared credential pool is not available",
            ) from exc

        # Verify the row exists before deleting.
        try:
            existing = await pool.fetchrow(
                "SELECT secret_key FROM butler_secrets WHERE secret_key = $1",
                key,
            )
        except UndefinedTableError as exc:
            raise HTTPException(
                status_code=503,
                detail="butler_secrets table not available — migration may not have run",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail="Credential lookup failed") from exc

        if existing is None:
            raise HTTPException(status_code=404, detail="Credential not found")

        try:
            await pool.execute(
                "DELETE FROM butler_secrets WHERE secret_key = $1",
                key,
            )
        except Exception as exc:
            logger.warning(
                "delete_system_credential: DELETE failed key=%s target=shared-public: %s",
                key,
                exc,
            )
            raise HTTPException(status_code=503, detail="Credential delete failed") from exc

        await _write_system_audit(
            pool,
            action="disconnected",
            key=key,
            note="Public pool system credential deleted",
        )
        return ApiResponse[SystemDeleteStatus](
            data=SystemDeleteStatus(status="disconnected"),
            meta=ApiMeta(),
        )

    else:
        # Per-butler override removal.
        butler_name = target
        try:
            pool = db.pool(butler_name)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Butler {butler_name!r} is not registered",
            ) from exc

        # Verify the override row exists.
        try:
            existing = await pool.fetchrow(
                "SELECT secret_key FROM butler_secrets WHERE secret_key = $1",
                key,
            )
        except UndefinedTableError as exc:
            raise HTTPException(
                status_code=503,
                detail="butler_secrets table not available — migration may not have run",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail="Credential lookup failed") from exc

        if existing is None:
            raise HTTPException(status_code=404, detail="Credential not found")

        try:
            await pool.execute(
                "DELETE FROM butler_secrets WHERE secret_key = $1",
                key,
            )
        except Exception as exc:
            logger.warning(
                "delete_system_credential: DELETE override failed key=%s butler=%s: %s",
                key,
                butler_name,
                exc,
            )
            raise HTTPException(
                status_code=503, detail="Credential override delete failed"
            ) from exc

        await _write_system_audit(
            pool,
            action="revoked",
            key=key,
            note=f"Per-butler override removed for {butler_name!r}",
        )
        return ApiResponse[SystemDeleteStatus](
            data=SystemDeleteStatus(status="revoked"),
            meta=ApiMeta(),
        )


# ---------------------------------------------------------------------------
# CLI runtime mutation helpers
# ---------------------------------------------------------------------------


async def _write_cli_audit(
    pool: Any,
    *,
    action: str,
    credential_id: str,
    note: str | None = None,
) -> None:
    """Append one row to public.audit_log for a CLI-credential mutation.

    Silently swallows errors so audit logging never blocks the primary
    operation (fire-and-forget pattern consistent with audit_emit.py).
    """
    target = normalize_credential_key("cli", credential_id)
    try:
        await audit_router.append(
            pool,
            _OWNER_ACTOR,
            action,
            target=target,
            note=note,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to write CLI credential audit: action=%s id=%s",
            action,
            credential_id,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# CLI rotate response model
# ---------------------------------------------------------------------------


class CliRotateResult(BaseModel):
    """Response payload for POST /api/secrets/cli/<id>/rotate.

    Per spec §CLI runtime mutations: the raw value is returned EXACTLY ONCE
    in this response body.  GET endpoints never expose raw values, so this
    single response is the only opportunity for the owner to copy the value
    to their local config.
    """

    fingerprint: str
    """SHA-256 first-8 hex fingerprint of the newly-generated value."""

    value: str
    """Raw secret value — returned ONCE; not retrievable via any GET endpoint."""


class CliRevokeResult(BaseModel):
    """Response payload for POST /api/secrets/cli/<id>/revoke."""

    status: str = "revoked"


# ---------------------------------------------------------------------------
# POST /api/secrets/cli/<id>/rotate
# ---------------------------------------------------------------------------


@router.post(
    "/cli/{credential_id}/rotate",
    response_model=ApiResponse[CliRotateResult],
)
async def rotate_cli_credential(
    credential_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CliRotateResult]:
    """Rotate (regenerate) the secret value for a CLI runtime token.

    Generates a new cryptographically-random value using
    ``secrets.token_urlsafe(32)``, persists it in-place via UPDATE on
    ``butler_secrets WHERE secret_key = <id>``, and
    returns ``ApiResponse<{fingerprint: str, value: str}>``.

    The raw value is returned **exactly once** in this response body.
    No GET endpoint exposes raw values (fingerprint-only), so this is the
    sole opportunity for the owner to record the new value.

    Appends a ``rotated`` audit row to ``public.audit_log``.

    Returns 404 when no matching CLI token exists in the shared credential
    pool.

    Spec anchor
    -----------
    openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
    §CLI runtime mutations — rotate returns value exactly once
    """
    shared_pool: Any = None
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        pass

    if shared_pool is None:
        raise HTTPException(status_code=503, detail="Shared credential database unavailable")

    # Confirm the CLI token exists before generating a new value.
    existing = await _fetch_single_cli_secret(shared_pool, credential_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="CLI credential not found")

    # Generate new random secret (cryptographically secure).
    new_value = _secrets_mod.token_urlsafe(32)

    # Persist the new value in-place (scope to PK from pre-fetched row).
    try:
        await shared_pool.execute(
            """
            UPDATE butler_secrets
            SET secret_value = $1, updated_at = now()
            WHERE secret_key = $2
            """,
            new_value,
            existing.id,
        )
    except Exception as exc:
        logger.warning("rotate_cli_credential: update failed for id=%s: %s", credential_id, exc)
        raise HTTPException(status_code=503, detail="CLI credential rotation failed") from exc

    # Compute fingerprint of the newly-generated value.
    fp = _fingerprint(new_value)

    # Audit — fire-and-forget.
    await _write_cli_audit(
        shared_pool,
        action="rotated",
        credential_id=credential_id,
        note="Value regenerated via rotate endpoint",
    )

    return ApiResponse[CliRotateResult](
        data=CliRotateResult(fingerprint=fp or "", value=new_value),
        meta=ApiMeta(),
    )


# ---------------------------------------------------------------------------
# POST /api/secrets/cli/<id>/revoke
# ---------------------------------------------------------------------------


@router.post(
    "/cli/{credential_id}/revoke",
    response_model=ApiResponse[CliRevokeResult],
)
async def revoke_cli_credential(
    credential_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CliRevokeResult]:
    """Revoke (delete) a CLI runtime token.

    Deletes the matching ``butler_secrets`` row (``category='cli'``) from the
    shared credential pool and appends a ``disconnected`` audit row to
    ``public.audit_log``.

    Returns ``ApiResponse<{status: "revoked"}>`` on success.
    Returns 404 when no matching CLI token exists.

    Spec anchor
    -----------
    openspec/changes/redesign-secrets-passport/specs/dashboard-api/spec.md
    §CLI runtime mutations — revoke writes 'disconnected' audit
    """
    shared_pool: Any = None
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        pass

    if shared_pool is None:
        raise HTTPException(status_code=503, detail="Shared credential database unavailable")

    # Confirm the CLI token exists before deleting.
    existing = await _fetch_single_cli_secret(shared_pool, credential_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="CLI credential not found")

    # Hard-delete the row (scope to PK from pre-fetched row).
    try:
        await shared_pool.execute(
            """
            DELETE FROM butler_secrets
            WHERE secret_key = $1
            """,
            existing.id,
        )
    except Exception as exc:
        logger.warning("revoke_cli_credential: delete failed for id=%s: %s", credential_id, exc)
        raise HTTPException(status_code=503, detail="CLI credential revoke failed") from exc

    # Audit — fire-and-forget.
    await _write_cli_audit(
        shared_pool,
        action="disconnected",
        credential_id=credential_id,
        note="CLI token revoked via revoke endpoint",
    )

    return ApiResponse[CliRevokeResult](data=CliRevokeResult(), meta=ApiMeta())


# ---------------------------------------------------------------------------
# CLI reauthorize response model
# ---------------------------------------------------------------------------


class CliReauthorizeResponse(BaseModel):
    """Response payload for POST /api/secrets/cli/<id>/reauthorize.

    Covers both auth modes.  The caller inspects ``auth_mode`` to decide
    which fields are meaningful:

    device_code
        session_id, auth_url, device_code, message — same contract as
        POST /api/cli-auth/<provider>/start.  Poll
        GET /api/cli-auth/sessions/<session_id> for completion.

    api_key
        env_var, prompt — the caller renders a text-input for the key value
        and submits it via PUT /api/cli-auth/<provider>/api-key.
    """

    auth_mode: str
    """'device_code' or 'api_key'."""

    provider: str
    """Provider name (e.g. 'codex', 'claude')."""

    # device_code fields (None for api_key)
    session_id: str | None = None
    session_state: str | None = None
    auth_url: str | None = None
    device_code: str | None = None
    message: str | None = None

    # api_key fields (None for device_code)
    env_var: str | None = None
    prompt: str | None = None


# ---------------------------------------------------------------------------
# POST /api/secrets/cli/<id>/reauthorize
# ---------------------------------------------------------------------------


@router.post(
    "/cli/{credential_id}/reauthorize",
    response_model=ApiResponse[CliReauthorizeResponse],
)
async def reauthorize_cli_credential(
    credential_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[CliReauthorizeResponse]:
    """Initiate (or resume) re-authentication for a CLI runtime provider.

    Resolves ``<credential_id>`` against the CLI auth provider registry
    (PROVIDERS) to determine the auth mode.  Does NOT reimplement device-code
    or api-key logic — it delegates to the existing cli-auth subsystem.

    device_code providers (e.g. ``codex``, ``opencode-openai``)
        Kicks off a new device-code session via the same path as
        POST /api/cli-auth/<provider>/start, then returns the session/redirect
        payload so the caller can display the device code and poll
        GET /api/cli-auth/sessions/<session_id> for completion.

    api_key providers (e.g. ``claude``, ``opencode-go``)
        Returns the api-key prompt contract (``env_var`` + human-readable
        ``prompt``).  The caller renders a key-entry form and submits the
        value via PUT /api/cli-auth/<provider>/api-key.

    In both cases an ``attempted`` audit row is written (the re-auth dance has
    been initiated but not yet completed).

    Returns 404 when ``<credential_id>`` is not a known CLI auth provider.

    Spec anchor
    -----------
    bu-ayp6v.10: Add backend reauthorize bridge for CLI runtime credentials.
    """
    provider_def = PROVIDERS.get(credential_id)
    if provider_def is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown CLI auth provider: {credential_id!r}",
        )

    # Fetch shared pool for audit writes (best-effort — swallowed on error).
    shared_pool: Any = None
    try:
        shared_pool = db.credential_shared_pool()
    except KeyError:
        pass

    if provider_def.auth_mode == "device_code":
        # --- device_code branch: delegate to the cli-auth start subsystem ---
        if not provider_def.is_available():
            raise HTTPException(
                status_code=503,
                detail=(
                    f"CLI binary '{provider_def.binary()}' not found on PATH; "
                    "cannot start device-code flow."
                ),
            )

        session_id = _secrets_mod.token_urlsafe(16)

        # Build on_success callback that persists the token and wires DB.
        from butlers.api.routers.cli_auth import _build_on_success

        session = CLIAuthSession(
            id=session_id,
            provider=provider_def,
            on_success=_build_on_success(db),
        )
        store_session(session)

        try:
            await session.start()
            # Wait briefly for the device code to appear in stdout.
            await session.wait(timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reauthorize_cli_credential: session start failed for id=%s: %s",
                credential_id,
                exc,
            )
            raise HTTPException(
                status_code=503,
                detail=f"Failed to start device-code session: {exc}",
            ) from exc

        # Audit — attempted (initiated, not yet completed).
        if shared_pool is not None:
            await _write_cli_audit(
                shared_pool,
                action="attempted",
                credential_id=credential_id,
                note=f"Device-code reauthorize initiated; session_id={session_id}",
            )

        return ApiResponse[CliReauthorizeResponse](
            data=CliReauthorizeResponse(
                auth_mode="device_code",
                provider=provider_def.name,
                session_id=session.id,
                session_state=CLIAuthSessionState(session.state).value,
                auth_url=session.auth_url,
                device_code=session.device_code,
                message=session.message,
            ),
            meta=ApiMeta(),
        )

    else:
        # --- api_key branch: return the key-prompt contract ---
        env_var = provider_def.env_var or None
        prompt = (
            f"Enter your API key for {provider_def.display_name}."
            + (f" Set it as the {env_var} environment variable." if env_var else "")
            + f" Submit via PUT /api/cli-auth/{provider_def.name}/api-key."
        )

        # Audit — attempted.
        if shared_pool is not None:
            await _write_cli_audit(
                shared_pool,
                action="attempted",
                credential_id=credential_id,
                note="API-key reauthorize initiated from /secrets",
            )

        return ApiResponse[CliReauthorizeResponse](
            data=CliReauthorizeResponse(
                auth_mode="api_key",
                provider=provider_def.name,
                env_var=env_var,
                prompt=prompt,
            ),
            meta=ApiMeta(),
        )
