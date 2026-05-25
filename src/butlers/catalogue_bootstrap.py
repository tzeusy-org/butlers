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
# Each tuple: (provider, butler, feature, severity, required_scopes_json)
#
# Mirrors the seed in alembic/versions/core/core_107_provider_feature_catalogue.py.
# Keep these two in sync when adding new providers or features.
# ---------------------------------------------------------------------------

_CATALOGUE_SEED: tuple[tuple[str, str, str, str, str], ...] = (
    # google × health
    (
        "google",
        "health",
        "Google Health ingestion",
        "high",
        (
            '["https://www.googleapis.com/auth/googlehealth.sleep",'
            ' "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",'
            ' "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements"]'
        ),
    ),
    (
        "google",
        "health",
        "Google Calendar sync",
        "medium",
        '["https://www.googleapis.com/auth/calendar"]',
    ),
    # google × messenger
    (
        "google",
        "messenger",
        "Gmail read and compose",
        "high",
        '["https://www.googleapis.com/auth/gmail.modify"]',
    ),
    # google × general
    (
        "google",
        "general",
        "Google Drive access",
        "medium",
        '["https://www.googleapis.com/auth/drive"]',
    ),
    # google × lifestyle
    (
        "google",
        "lifestyle",
        "Google Calendar sync",
        "medium",
        '["https://www.googleapis.com/auth/calendar"]',
    ),
    # google × * (ecosystem-wide)
    (
        "google",
        "*",
        "Google account connection",
        "high",
        "[]",
    ),
    # telegram × * (ecosystem-wide)
    (
        "telegram",
        "*",
        "Telegram messaging",
        "high",
        "[]",
    ),
    # spotify × lifestyle
    (
        "spotify",
        "lifestyle",
        "Spotify listening history",
        "high",
        "[]",
    ),
    # home_assistant × home
    (
        "home_assistant",
        "home",
        "Home device control",
        "high",
        "[]",
    ),
    # whatsapp × messenger
    (
        "whatsapp",
        "messenger",
        "WhatsApp messaging",
        "high",
        "[]",
    ),
    # owntracks × home
    (
        "owntracks",
        "home",
        "Location tracking",
        "medium",
        "[]",
    ),
    # steam × lifestyle
    (
        "steam",
        "lifestyle",
        "Steam game library",
        "low",
        "[]",
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
                VALUES ($1, $2, $3, $4, $5::jsonb)
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
