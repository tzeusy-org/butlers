"""Owner anchor-address geocodes.

Revision ID: rel_038
Revises: rel_037
Create Date: 2026-11-03 00:00:00.000000

Phase: chronicler situational inference (bu-r3fmja.3; openspec change
``chronicler-situational-inference``).

``relationship.address_geocodes`` holds one geocode per ``has-address`` fact that is an
owner anchor address: the owner's own address, the address of an entity the owner
``works-at``, or the address of a place the owner ``rental-location``s or ``lives-at``.
The deterministic ``relationship_geocode_owner_addresses`` job is its only writer.

``address_sha256`` is the SHA-256 of the fact's stripped literal ``object`` at geocode
time, so a reader (Chronicler's anchor resolver) can tell a current geocode from one
made for text that has since changed. Coordinates are present exactly when the status
is ``resolved``. ``not_found`` and ``failed`` rows carry ``next_attempt_at`` for the
job's backoff.

Read access beyond Relationship is granted to ``butler_chronicler_rw`` only, and only
SELECT. The row follows its fact on delete.
"""

from __future__ import annotations

from alembic import op

revision = "rel_038"
down_revision = "rel_037"
branch_labels = None
depends_on = None

_RELATIONSHIP_ROLE = "butler_relationship_rw"
_CHRONICLER_ROLE = "butler_chronicler_rw"
_TABLE = "relationship.address_geocodes"


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _grant_best_effort(statement: str, role: str) -> None:
    """Run a GRANT/REVOKE only when *role* exists; tolerate older databases."""
    role_exists = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {role_exists} THEN
                {statement};
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
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            fact_id         UUID             PRIMARY KEY
                                REFERENCES relationship.entity_facts(id) ON DELETE CASCADE,
            address_sha256  TEXT             NOT NULL,
            status          TEXT             NOT NULL
                                CHECK (status IN ('resolved', 'not_found', 'failed')),
            lat             NUMERIC(7, 5),
            lon             NUMERIC(8, 5),
            provider        TEXT             CHECK (provider IN ('onemap', 'nominatim')),
            confidence      DOUBLE PRECISION,
            attempts        INTEGER          NOT NULL DEFAULT 0,
            geocoded_at     TIMESTAMPTZ      NOT NULL DEFAULT now(),
            next_attempt_at TIMESTAMPTZ,
            CONSTRAINT ck_address_geocodes_coordinates CHECK (
                (status = 'resolved') = (lat IS NOT NULL AND lon IS NOT NULL)
                AND (lat IS NULL) = (lon IS NULL)
            )
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_address_geocodes_retry
            ON {_TABLE} (next_attempt_at)
            WHERE status IN ('not_found', 'failed')
        """
    )
    _grant_best_effort(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {_TABLE} TO "{_RELATIONSHIP_ROLE}"',
        _RELATIONSHIP_ROLE,
    )
    _grant_best_effort(
        f'GRANT SELECT ON TABLE {_TABLE} TO "{_CHRONICLER_ROLE}"',
        _CHRONICLER_ROLE,
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
