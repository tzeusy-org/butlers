"""secrets BE-6: public.provider_feature_catalogue table with seed rows.

Revision ID: core_107
Revises: core_106
Create Date: 2026-05-26 00:00:00.000000

Implements the ``provider_feature_catalogue`` requirement from
``openspec/changes/redesign-secrets-passport/specs/core-credentials/spec.md``
(§ public.provider_feature_catalogue WhatBreaks Source-of-Truth Table).

The table backs the WhatBreaks affordance on the ``/secrets`` page: when a
user OAuth credential is sick, the frontend fetches
``GET /api/secrets/breaks-catalogue?provider=<p>`` and renders one row per
``(butler, feature)`` returned, sorted by severity DESC.

Schema
------
id               BIGSERIAL PRIMARY KEY
provider         TEXT NOT NULL           Provider slug (e.g. 'google', 'telegram')
butler           TEXT NOT NULL           Butler name or '*' for ecosystem-wide
feature          TEXT NOT NULL           User-facing feature label
severity         TEXT NOT NULL           CHECK (severity IN ('high', 'medium', 'low'))
required_scopes  JSONB NOT NULL DEFAULT '[]'  OAuth scope strings the feature needs
updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()

Unique constraint: (provider, butler, feature) — same butler cannot register
the same feature twice for the same provider.

Index: (provider, butler) for fast ?provider= filtering.

Seed rows
---------
Known providers at change-implementation time (7 total), each with one or more
feature rows:

google        health      "Google Health ingestion"   high    [googlehealth scopes]
google        health      "Google Calendar sync"      medium  [calendar scope]
google        lifestyle   "Spotify listening history" low     []
google        messenger   "Gmail read and compose"    high    [gmail scope]
google        general     "Google Drive access"       medium  [drive scope]
google        *           "Google account connection" high    []
telegram      *           "Telegram messaging"        high    []
spotify       lifestyle   "Spotify listening history" high    []
home_assistant home        "Home device control"       high    []
whatsapp      messenger   "WhatsApp messaging"        high    []
owntracks     home        "Location tracking"         medium  []
steam         lifestyle   "Steam game library"        low     []

Downgrade
---------
DROP TABLE public.provider_feature_catalogue (CASCADE — drops seed rows too).
"""

from __future__ import annotations

from alembic import op

revision = "core_107"
down_revision = "core_106"
branch_labels = None
depends_on = None

_ALL_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create public.provider_feature_catalogue
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.provider_feature_catalogue (
            id              BIGSERIAL    PRIMARY KEY,
            provider        TEXT         NOT NULL,
            butler          TEXT         NOT NULL,
            feature         TEXT         NOT NULL,
            severity        TEXT         NOT NULL
                CHECK (severity IN ('high', 'medium', 'low')),
            required_scopes JSONB        NOT NULL DEFAULT '[]',
            updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            UNIQUE (provider, butler, feature)
        )
    """)

    # Fast ?provider= filtering — spec requirement (provider, butler) index.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_provider_feature_catalogue_provider_butler
        ON public.provider_feature_catalogue (provider, butler)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.provider_feature_catalogue", _TABLE_PRIVILEGES, role)

    # ------------------------------------------------------------------
    # 2. Seed initial rows for the 7 known providers
    #
    # Rows are inserted with ON CONFLICT DO NOTHING so that this seed block
    # is idempotent when upgrade() is re-run against a DB that already has
    # the table (e.g. after a failed mid-migration retry).
    # ------------------------------------------------------------------
    op.execute("""
        INSERT INTO public.provider_feature_catalogue
            (provider, butler, feature, severity, required_scopes)
        VALUES
            -- google × health: Google Health ingestion (restricted scopes)
            ('google', 'health', 'Google Health ingestion', 'high',
             '["https://www.googleapis.com/auth/googlehealth.sleep",
               "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
               "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements"]'::jsonb),

            -- google × health: calendar sync
            ('google', 'health', 'Google Calendar sync', 'medium',
             '["https://www.googleapis.com/auth/calendar"]'::jsonb),

            -- google × messenger: Gmail read and compose
            ('google', 'messenger', 'Gmail read and compose', 'high',
             '["https://www.googleapis.com/auth/gmail.modify"]'::jsonb),

            -- google × general: Google Drive access
            ('google', 'general', 'Google Drive access', 'medium',
             '["https://www.googleapis.com/auth/drive"]'::jsonb),

            -- google × lifestyle: no specific feature, but calendar/contacts used
            ('google', 'lifestyle', 'Google Calendar sync', 'medium',
             '["https://www.googleapis.com/auth/calendar"]'::jsonb),

            -- google × * (ecosystem-wide): account connection required by all butlers
            ('google', '*', 'Google account connection', 'high', '[]'::jsonb),

            -- telegram × * (ecosystem-wide): messaging used across all notification paths
            ('telegram', '*', 'Telegram messaging', 'high', '[]'::jsonb),

            -- spotify × lifestyle: listening history ingestion
            ('spotify', 'lifestyle', 'Spotify listening history', 'high', '[]'::jsonb),

            -- home_assistant × home: device control and sensor access
            ('home_assistant', 'home', 'Home device control', 'high', '[]'::jsonb),

            -- whatsapp × messenger: WhatsApp messaging channel
            ('whatsapp', 'messenger', 'WhatsApp messaging', 'high', '[]'::jsonb),

            -- owntracks × home: location tracking / presence detection
            ('owntracks', 'home', 'Location tracking', 'medium', '[]'::jsonb),

            -- steam × lifestyle: game library and playtime ingestion
            ('steam', 'lifestyle', 'Steam game library', 'low', '[]'::jsonb)

        ON CONFLICT (provider, butler, feature) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_provider_feature_catalogue_provider_butler")
    op.execute("DROP TABLE IF EXISTS public.provider_feature_catalogue")
