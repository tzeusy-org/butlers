"""Provider feature catalogue bootstrap for the Butler daemon.

Ensures ``public.provider_feature_catalogue`` is seeded with the canonical
known-provider rows on daemon startup.  This mirrors the migration seed but is
idempotent and runs on every boot so that the catalogue stays current as the
roster grows.

The UPSERT uses ``ON CONFLICT (provider, butler, feature) DO UPDATE`` to refresh
``updated_at`` on every run.  Net row count is unchanged after the first boot
(spec requirement: running the boot sequence twice produces zero net row changes
after the first run).
"""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical seed rows
#
# Each tuple: (provider, butler, feature, severity, required_scopes)
#
# `required_scopes` is a native Python list, bound directly (no json.dumps,
# no ::jsonb cast) at the INSERT below: every asyncpg pool in this codebase
# registers register_jsonb_codec() (src/butlers/db.py), whose encoder expects
# a Python object and calls json.dumps() on it itself. Binding an
# already-JSON-formatted string here (the previous approach) makes that
# encoder fire a SECOND time, double-encoding required_scopes into a
# jsonb-typed STRING instead of an ARRAY (bu-cymc4) — exactly the corruption
# the defensive `isinstance(scopes, str)` guards in api/routers/secrets_v2.py
# (_fetch_scopes_required_by_provider, breaks-catalogue) already work around.
#
# Extends the initial seed in
# alembic/versions/core/core_107_provider_feature_catalogue.py (that seed is a
# raw SQL literal executed via Alembic's op.execute(), so it is parsed
# server-side and is NOT subject to this bug). This bootstrap is the living
# source of truth: it UPSERTs on every boot, so rows added here (e.g. the
# ``email`` / ``general`` system-category rows for BUTLER_EMAIL_* / BLOB_S3_*,
# bu-pza41) land in the live catalogue on the next daemon start without a
# migration. core_107 is the historical initial seed and is intentionally not
# edited; the two need not be byte-identical.
# ---------------------------------------------------------------------------

_CATALOGUE_SEED: tuple[tuple[str, str, str, str, list[str]], ...] = (
    # google × health
    (
        "google",
        "health",
        "Google Health ingestion",
        "high",
        [
            "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
            "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
            "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
        ],
    ),
    (
        "google",
        "health",
        "Google Calendar sync",
        "medium",
        ["https://www.googleapis.com/auth/calendar"],
    ),
    # google × messenger
    (
        "google",
        "messenger",
        "Gmail read and compose",
        "high",
        ["https://www.googleapis.com/auth/gmail.modify"],
    ),
    # google × general
    (
        "google",
        "general",
        "Google Drive access",
        "medium",
        ["https://www.googleapis.com/auth/drive"],
    ),
    # google × lifestyle
    (
        "google",
        "lifestyle",
        "Google Calendar sync",
        "medium",
        ["https://www.googleapis.com/auth/calendar"],
    ),
    # google × * (ecosystem-wide)
    (
        "google",
        "*",
        "Google account connection",
        "high",
        [],
    ),
    # telegram × * (ecosystem-wide)
    (
        "telegram",
        "*",
        "Telegram messaging",
        "high",
        [],
    ),
    # spotify × lifestyle
    (
        "spotify",
        "lifestyle",
        "Spotify listening history",
        "high",
        [],
    ),
    # home_assistant × home
    (
        "home_assistant",
        "home",
        "Home device control",
        "high",
        [],
    ),
    # whatsapp × messenger
    (
        "whatsapp",
        "messenger",
        "WhatsApp messaging",
        "high",
        [],
    ),
    # owntracks × home
    (
        "owntracks",
        "home",
        "Location tracking",
        "medium",
        [],
    ),
    # steam × lifestyle
    (
        "steam",
        "lifestyle",
        "Steam game library",
        "low",
        [],
    ),
    # email × messenger / travel (BUTLER_EMAIL_* system creds, category 'email').
    # The email module (modules/email.py) is enabled by the messenger and travel
    # butlers; without the mailbox credentials, email send + IMAP inbox polling
    # stop for those butlers. Not OAuth-scoped, so required_scopes is empty.
    (
        "email",
        "messenger",
        "Email send + IMAP inbox polling",
        "high",
        [],
    ),
    (
        "email",
        "travel",
        "Email send + IMAP inbox polling",
        "medium",
        [],
    ),
    # general × * (BLOB_S3_* system creds, category 'general'). The S3-compatible
    # blob store is core cross-butler infra wired at daemon startup (lifecycle.py);
    # without it, blob operations fail fleet-wide — message-attachment persistence
    # and document rendering (modules/document_renderer) among them.
    (
        "general",
        "*",
        "Blob storage (message attachments, rendered documents)",
        "high",
        [],
    ),
)


async def upsert_provider_feature_catalogue(pool: asyncpg.Pool) -> None:
    """UPSERT canonical seed rows into public.provider_feature_catalogue.

    Idempotent: running twice is a no-op for row count — the second call
    updates ``updated_at`` on existing rows but does not add new ones.

    Silently skips when:
    - ``public.provider_feature_catalogue`` does not exist yet (migration not
      yet run — e.g. test-DB or first-boot before migration).
    - Any DB error occurs (best-effort: startup must not fail because of this).
    """
    try:
        async with pool.acquire() as conn:
            table_exists = await conn.fetchval(
                "SELECT to_regclass('public.provider_feature_catalogue') IS NOT NULL"
            )
            if not table_exists:
                logger.debug(
                    "provider_feature_catalogue: table not found — "
                    "skipping catalogue UPSERT (migration core_107 not yet run)"
                )
                return

            # Single multi-row UPSERT — one round-trip regardless of roster size.
            # ON CONFLICT DO UPDATE refreshes updated_at to signal a fresh boot
            # while leaving provider/butler/feature/severity/required_scopes intact.
            await conn.executemany(
                """
                INSERT INTO public.provider_feature_catalogue
                    (provider, butler, feature, severity, required_scopes)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (provider, butler, feature)
                DO UPDATE SET
                    severity        = EXCLUDED.severity,
                    required_scopes = EXCLUDED.required_scopes,
                    updated_at      = now()
                """,
                _CATALOGUE_SEED,
            )
            logger.debug(
                "provider_feature_catalogue: UPSERT complete (%d rows)", len(_CATALOGUE_SEED)
            )

    except Exception:  # noqa: BLE001
        logger.warning("provider_feature_catalogue UPSERT skipped (non-fatal)", exc_info=True)
