"""taste_ledger: works, taste_signals, verdicts tables; predicate seeding;
legacy prose-fact migration.

Revision ID: lifestyle_002
Revises: lifestyle_001
Create Date: 2026-09-10 00:00:00.000000

bu-2jtfw.10 ("the taste ledger"): replaces prose-only taste memory with a
deterministic ledger derived from connector evidence.

Tables
------
``works``
    One row per distinct thing the owner consumed (track, artist, album, show,
    book, ...). ``external_ids`` is a JSONB bag of stable identifiers; the
    ``primary`` key is used for dedup. Rows created from evidence that carries
    no stable id (e.g. a bare track name from a listening-session summary)
    keep ``external_ids = '{}'`` rather than fabricating one — every such
    mention gets its own row, which is honest given the source has no better
    identity to offer.

``taste_signals``
    One row per resolved unit of evidence for a work (a play, a skip, a
    listening-pattern observation, ...), never asserted by an LLM.
    ``idempotency_key`` is ``<source_table>:<source_ref>:<signal_kind>`` so a
    replayed projector pass is a no-op (``ON CONFLICT (idempotency_key) DO
    NOTHING``).

``verdicts``
    Owner assertions about taste (durable opinions), keyed loosely to a work
    when one is known. The pre-ledger ``facts`` rows (``scope='lifestyle'``)
    are migrated here as ``source='legacy_fact'`` verdicts so the 61 existing
    prose facts survive the cutover as ``verdict_text``.

Downgrade exports directly-created verdicts as temporal ``facts`` rows keyed
by ``taste-ledger-verdict:<verdict id>`` before dropping the ledger. The fact
metadata retains verdict provenance and a snapshot of the linked work, making
the rollback path durable and backward-readable rather than lossy.

Predicate registry
-------------------
Seeds every predicate the memory-taxonomy skill defines (16 total) so the
skill and the registry can no longer diverge silently.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "lifestyle_002"
down_revision = "lifestyle_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # 1. works
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS works (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            kind            TEXT NOT NULL,
            title           TEXT,
            external_ids    JSONB NOT NULL DEFAULT '{}'::jsonb,
            metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_works_external_ids_object
                CHECK (jsonb_typeof(external_ids) = 'object')
        )
    """)
    # Dedup on (kind, external_ids->>'primary'). A NULL primary (unresolved
    # evidence) never conflicts with another NULL, so every unresolved
    # mention keeps its own row instead of being silently merged.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_works_kind_primary_id
            ON works (kind, (external_ids ->> 'primary'))
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_works_kind_created
            ON works (kind, created_at DESC)
    """)

    # =========================================================================
    # 2. taste_signals
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS taste_signals (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            work_id             UUID NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            signal_kind         TEXT NOT NULL,
            source_table        TEXT NOT NULL,
            source_ref          TEXT NOT NULL,
            idempotency_key     TEXT NOT NULL UNIQUE,
            occurred_at         TIMESTAMPTZ NOT NULL,
            strength            DOUBLE PRECISION,
            metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_taste_signals_work_occurred
            ON taste_signals (work_id, occurred_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_taste_signals_source
            ON taste_signals (source_table, source_ref)
    """)

    # =========================================================================
    # 3. verdicts
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS verdicts (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            work_id         UUID REFERENCES works(id) ON DELETE SET NULL,
            predicate       TEXT NOT NULL,
            verdict_text    TEXT NOT NULL,
            source          TEXT NOT NULL DEFAULT 'owner_assertion',
            legacy_fact_id  UUID UNIQUE,
            metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_verdicts_work
            ON verdicts (work_id) WHERE work_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_verdicts_predicate
            ON verdicts (predicate)
    """)

    # =========================================================================
    # 4. Migrate the 61 legacy prose facts (scope='lifestyle') into verdicts.
    # Idempotent via the legacy_fact_id UNIQUE constraint + ON CONFLICT.
    # =========================================================================
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('facts') IS NOT NULL THEN
                INSERT INTO verdicts (work_id, predicate, verdict_text, source, legacy_fact_id, metadata)
                SELECT
                    NULL,
                    f.predicate,
                    f.content,
                    'legacy_fact',
                    f.id,
                    jsonb_build_object(
                        'subject', f.subject,
                        'permanence', f.permanence,
                        'created_at', f.created_at
                    )
                FROM facts f
                WHERE f.scope = 'lifestyle'
                  AND f.validity = 'active'
                ON CONFLICT (legacy_fact_id) DO NOTHING;
            END IF;
        END;
        $$;
    """)

    # =========================================================================
    # 5. Seed predicate_registry with every memory-taxonomy predicate.
    # ON CONFLICT (name) DO NOTHING — idempotent, tolerates predicates already
    # seeded by another migration.
    # =========================================================================
    _stable_person_predicates = [
        ("likes_genre", "Music genre preferences and dislikes."),
        ("likes_artist", "Favourite artists or acts."),
        ("likes_cuisine", "Cuisine types the user enjoys."),
        ("favorite_restaurant", "Preferred dining spots and why."),
        ("favorite_recipe", "Beloved recipes or dishes."),
        ("hobby", "Active hobbies and leisure interests."),
        ("food_preference", "Dietary patterns, ingredient preferences."),
        ("food_dislike", "Foods to avoid (allergies, aversions, dislikes)."),
        ("routine", "Daily routine patterns (morning rituals, evening wind-downs, focus modes)."),
    ]
    _volatile_person_predicates = [
        ("watches", "Currently watching (TV shows, films)."),
        ("reads", "Currently reading (books, articles, comics)."),
        ("plays", "Currently playing (video games, board games)."),
        ("listens_to", "Current listening focus (album, artist rotation, playlist)."),
    ]
    _spotify_predicates = [
        ("listening_pattern", "Rotation intensity and frequency over time for a Spotify artist."),
        ("purpose", "What a Spotify playlist is for (focus, commute, party, etc.)."),
        ("context", "When/where/why a Spotify playlist is used."),
    ]

    for name, description in _stable_person_predicates + _volatile_person_predicates:
        desc_escaped = description.replace("'", "''")
        op.execute(
            f"INSERT INTO predicate_registry"
            f" (name, expected_subject_type, expected_object_type, is_edge, is_temporal,"
            f"  description, scope, status)"
            f" VALUES"
            f" ('{name}', 'person', NULL, false, false, '{desc_escaped}', 'global', 'active')"
            f" ON CONFLICT (name) DO NOTHING"
        )
    for name, description in _spotify_predicates:
        desc_escaped = description.replace("'", "''")
        op.execute(
            f"INSERT INTO predicate_registry"
            f" (name, expected_subject_type, expected_object_type, is_edge, is_temporal,"
            f"  description, scope, status)"
            f" VALUES"
            f" ('{name}', NULL, NULL, false, false, '{desc_escaped}', 'global', 'active')"
            f" ON CONFLICT (name) DO NOTHING"
        )


def downgrade() -> None:
    # Legacy-sourced verdicts may have been edited (verdict_text amended)
    # since the cutover; write the current text back onto the originating
    # fact's metadata before the ledger tables are dropped, so a downgrade
    # never silently loses an owner edit made through the ledger.
    # Direct owner verdicts have no originating fact, so export each as a
    # temporal lifestyle fact before dropping the ledger. Temporal identity
    # preserves multiple owner verdicts with the same predicate, while the
    # deterministic idempotency key makes a manually retried downgrade safe.
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('facts') IS NOT NULL AND to_regclass('verdicts') IS NOT NULL THEN
                UPDATE facts f
                SET metadata = COALESCE(f.metadata, '{}'::jsonb)
                    || jsonb_build_object('taste_ledger_verdict_text', v.verdict_text)
                FROM verdicts v
                WHERE v.legacy_fact_id = f.id;

                INSERT INTO facts (
                    subject,
                    predicate,
                    content,
                    permanence,
                    source_butler,
                    validity,
                    scope,
                    created_at,
                    last_confirmed_at,
                    metadata,
                    valid_at,
                    idempotency_key,
                    observed_at
                )
                SELECT
                    COALESCE(NULLIF(v.metadata ->> 'subject', ''), 'user'),
                    v.predicate,
                    v.verdict_text,
                    'stable',
                    'lifestyle',
                    'active',
                    'lifestyle',
                    v.created_at,
                    v.created_at,
                    COALESCE(v.metadata, '{}'::jsonb) || jsonb_build_object(
                        'taste_ledger_verdict_id', v.id,
                        'taste_ledger_source', v.source,
                        'taste_ledger_work', CASE
                            WHEN w.id IS NULL THEN NULL
                            ELSE jsonb_build_object(
                                'id', w.id,
                                'kind', w.kind,
                                'title', w.title,
                                'external_ids', w.external_ids,
                                'metadata', w.metadata
                            )
                        END
                    ),
                    v.created_at,
                    'taste-ledger-verdict:' || v.id::text,
                    v.created_at
                FROM verdicts v
                LEFT JOIN works w ON w.id = v.work_id
                WHERE v.legacy_fact_id IS NULL
                ON CONFLICT (tenant_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    DO NOTHING;
            END IF;
        END;
        $$;
    """)
    op.execute("DROP TABLE IF EXISTS verdicts CASCADE")
    op.execute("DROP TABLE IF EXISTS taste_signals CASCADE")
    op.execute("DROP TABLE IF EXISTS works CASCADE")
