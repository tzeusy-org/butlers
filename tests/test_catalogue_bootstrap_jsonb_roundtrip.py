"""Real-Postgres regression: provider_feature_catalogue.required_scopes must not
double-encode JSONB.

bu-cymc4: ``_CATALOGUE_SEED`` in butlers.catalogue_bootstrap used to hold
hand-authored JSON-string literals (e.g. ``'["scope"]'``) for
``required_scopes``, bound through ``upsert_provider_feature_catalogue()``
with an explicit ``$5::jsonb`` cast. Every asyncpg pool in this codebase
registers a JSONB type codec (``register_jsonb_codec``, src/butlers/db.py)
whose encoder calls ``json.dumps()`` itself, so the old code path
double-encoded ``required_scopes`` into a jsonb-typed STRING instead of an
ARRAY (see tests/relationship/test_jsonb_codec.py). This was a LIVE bug, not
a dormant one: readers in api/routers/secrets_v2.py
(``_fetch_scopes_required_by_provider``, the breaks-catalogue endpoint)
already carry defensive ``isinstance(scopes, str): json.loads(scopes)``
workarounds for exactly this corruption. This test writes via the real
``upsert_provider_feature_catalogue()`` code path against a migrated-shape
Postgres table and reads rows back directly, proving ``required_scopes``
lands as a native list.
"""

from __future__ import annotations

import shutil

import pytest

from butlers.catalogue_bootstrap import _CATALOGUE_SEED, upsert_provider_feature_catalogue

docker_available = shutil.which("docker") is not None
# NOTE: pytest.mark.asyncio is applied per-test (not in this module-level
# pytestmark) because test_catalogue_seed_entries_are_native_lists_not_json_strings
# below is a plain sync test; asyncio-mode=auto still respects an explicit
# marker mismatch and warns on a sync function carrying it.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

# Mirrors core_107's provider_feature_catalogue DDL closely enough to
# exercise the real UPSERT.
_PROVIDER_FEATURE_CATALOGUE_DDL = """
CREATE TABLE IF NOT EXISTS public.provider_feature_catalogue (
    id               BIGSERIAL PRIMARY KEY,
    provider         TEXT NOT NULL,
    butler           TEXT NOT NULL,
    feature          TEXT NOT NULL,
    severity         TEXT NOT NULL,
    required_scopes  JSONB NOT NULL DEFAULT '[]',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, butler, feature)
)
"""


def test_catalogue_seed_entries_are_native_lists_not_json_strings() -> None:
    """Regression guard: seed data must never regress to hand-authored JSON strings."""
    for provider, butler, feature, severity, required_scopes in _CATALOGUE_SEED:
        assert isinstance(required_scopes, list), (
            f"{provider}/{butler}/{feature!r} required_scopes is "
            f"{type(required_scopes).__name__!r}, not a list — reintroducing a "
            "hand-authored JSON string here reintroduces the bu-cymc4 double-encoding bug."
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_upsert_required_scopes_roundtrips_as_list_not_double_encoded_string(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVIDER_FEATURE_CATALOGUE_DDL)

        await upsert_provider_feature_catalogue(pool)

        rows = await pool.fetch(
            "SELECT provider, butler, feature, required_scopes "
            "FROM public.provider_feature_catalogue "
            "WHERE provider = 'google' AND feature = 'Google Health ingestion'"
        )
        assert len(rows) == 1
        stored_scopes = rows[0]["required_scopes"]
        assert isinstance(stored_scopes, list), (
            f"required_scopes arrived as {type(stored_scopes).__name__!r}, not a list — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored_scopes == [
            "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
            "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
            "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
        ]

        # Idempotent re-run (daemon restart) must not change row count and must
        # keep re-normalizing required_scopes on every boot.
        await upsert_provider_feature_catalogue(pool)
        count = await pool.fetchval("SELECT COUNT(*) FROM public.provider_feature_catalogue")
        assert count == len(_CATALOGUE_SEED)


@pytest.mark.asyncio(loop_scope="session")
async def test_email_and_general_system_categories_have_catalogue_rows(
    provisioned_postgres_pool,
) -> None:
    """bu-pza41: the ``email`` (BUTLER_EMAIL_*) and ``general`` (BLOB_S3_*) system
    categories must have catalogue rows so WhatBreaks / ConfirmImpact render real
    impact instead of the 'usage not tracked' coverage-gap state.

    The passport queries ``GET /api/secrets/breaks-catalogue?provider=<category>``,
    which filters ``provider_feature_catalogue`` by ``provider``; a system secret's
    category (frontend/src/lib/secret-templates.ts) is that provider slug
    (BUTLER_EMAIL_* -> 'email', BLOB_S3_* -> 'general').
    """
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_PROVIDER_FEATURE_CATALOGUE_DDL)
        await upsert_provider_feature_catalogue(pool)

        for provider in ("email", "general"):
            # Mirrors the breaks-catalogue endpoint's per-provider filter.
            rows = await pool.fetch(
                "SELECT butler, feature, severity FROM public.provider_feature_catalogue "
                "WHERE provider = $1",
                provider,
            )
            assert rows, (
                f"provider_feature_catalogue has no rows for the {provider!r} system "
                "category — WhatBreaks/ConfirmImpact would render 'usage not tracked'."
            )
            for r in rows:
                assert r["butler"], f"{provider} row has empty butler"
                assert r["feature"], f"{provider} row has empty feature"
                assert r["severity"] in {"high", "medium", "low"}, (
                    f"{provider} row has bad severity {r['severity']!r}"
                )

        # Email is enabled by the messenger and travel butlers; both must be
        # represented so the operator sees which butlers lose email.
        email_butlers = {
            r["butler"]
            for r in await pool.fetch(
                "SELECT butler FROM public.provider_feature_catalogue WHERE provider = 'email'"
            )
        }
        assert {"messenger", "travel"} <= email_butlers, (
            f"email catalogue rows should cover messenger + travel; got {email_butlers}"
        )
