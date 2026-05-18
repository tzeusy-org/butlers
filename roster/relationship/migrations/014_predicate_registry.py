"""predicate_registry: seed catalog of valid RDF predicates.

Revision ID: rel_014
Revises: rel_013
Create Date: 2026-05-18 00:00:00.000000

Phase: entity-redesign (bu-hlovw).

Creates ``relationship.predicate_registry`` — the canonical catalog of valid
predicate identifiers used by ``relationship.facts``.  Seed rows cover three
families:

* **Contact** predicates (``object_kind='literal'``):
  ``has-email``, ``has-phone``, ``has-handle``, ``has-address``,
  ``has-birthday``, ``has-website``
  (per Brief §6b Amendment 1 contact-predicate catalog).

* **Relational** predicates (``object_kind='entity'``):
  ``knows``, ``family-of``, ``partner-of``, ``parent-of``, ``child-of``,
  ``colleague-of``, ``friend-of``, ``co-attended``, ``purchased-from``,
  ``subscribed-to``, ``visited``
  (per spec §"Requirement: Predicate catalog" relational set).

* **Override** predicates (``object_kind='literal'``, JSON payload):
  ``dunbar_tier_override``
  (per RFC 0013 weight-at-query decision and Phase 1 Amendment 6).

Schema
------
predicate   TEXT PK       kebab-case identifier, unique
kind        TEXT NOT NULL 'contact' | 'relational' | 'override'
object_kind TEXT NOT NULL 'literal' | 'entity' — mirrors relationship.facts
description TEXT          Human-readable summary
created_at  TIMESTAMPTZ   NOT NULL DEFAULT now()

Grants
------
SELECT, INSERT, UPDATE, DELETE on relationship.predicate_registry granted to
butler_relationship_rw only.  Other butlers resolve predicates exclusively
through the relationship butler's MCP tool surface (RFC 0006 schema isolation).

Downgrade
---------
DROP TABLE relationship.predicate_registry.
Does NOT drop the relationship schema (schema teardown owned by rel_001).
"""

from __future__ import annotations

from alembic import op

revision = "rel_014"
down_revision = "rel_013"
branch_labels = None
depends_on = None

_RELATIONSHIP_ROLE = "butler_relationship_rw"
_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
_TABLE_FQN = "relationship.predicate_registry"

# ---------------------------------------------------------------------------
# Seed data: (predicate, kind, object_kind, description)
# ---------------------------------------------------------------------------

# Contact predicates — object is a literal string value.
_CONTACT_PREDICATES: list[tuple[str, str, str, str]] = [
    ("has-email", "contact", "literal", "Email address for the entity."),
    ("has-phone", "contact", "literal", "Phone number for the entity."),
    (
        "has-handle",
        "contact",
        "literal",
        "Channel-scoped handle (e.g. telegram:<id>, discord:<id>).",
    ),
    ("has-address", "contact", "literal", "Physical mailing address for the entity."),
    ("has-birthday", "contact", "literal", "Date of birth in ISO-8601 format (YYYY-MM-DD)."),
    ("has-website", "contact", "literal", "Web URL associated with the entity."),
]

# Relational predicates — object is an entity UUID (stored as text).
_RELATIONAL_PREDICATES: list[tuple[str, str, str, str]] = [
    ("knows", "relational", "entity", "Generic acquaintance or social connection."),
    ("family-of", "relational", "entity", "Generic family relationship (undirected)."),
    ("partner-of", "relational", "entity", "Romantic or life partner."),
    (
        "parent-of",
        "relational",
        "entity",
        "Parent–child relationship (directed: subject is parent).",
    ),
    ("child-of", "relational", "entity", "Parent–child relationship (directed: subject is child)."),
    ("colleague-of", "relational", "entity", "Professional colleague or co-worker."),
    ("friend-of", "relational", "entity", "Close friendship."),
    ("co-attended", "relational", "entity", "Both entities attended the same event or place."),
    ("purchased-from", "relational", "entity", "Subject made a purchase from the object entity."),
    (
        "subscribed-to",
        "relational",
        "entity",
        "Subject holds a subscription with the object entity.",
    ),
    ("visited", "relational", "entity", "Subject visited the object entity (person or place)."),
]

# Override predicates — object is a JSON literal encoding the override value.
_OVERRIDE_PREDICATES: list[tuple[str, str, str, str]] = [
    (
        "dunbar_tier_override",
        "override",
        "literal",
        "Manual Dunbar-tier assignment that supersedes the computed weight tier. "
        "Object is a JSON number (1–5).",
    ),
]

_ALL_PREDICATES = _CONTACT_PREDICATES + _RELATIONAL_PREDICATES + _OVERRIDE_PREDICATES


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object      THEN NULL;
            WHEN undefined_table       THEN NULL;
            WHEN invalid_schema_name   THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # 1. Ensure schema exists (idempotent — rel_013 or an earlier migration may
    #    have already created it).
    op.execute("CREATE SCHEMA IF NOT EXISTS relationship")

    # 2. Create the predicate_registry table.
    op.execute("""
        CREATE TABLE IF NOT EXISTS relationship.predicate_registry (
            predicate   TEXT        NOT NULL PRIMARY KEY,
            kind        TEXT        NOT NULL CHECK (kind IN ('contact', 'relational', 'override')),
            object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
            description TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # 3. Seed rows — ON CONFLICT DO NOTHING for idempotent re-runs.
    for predicate, kind, object_kind, description in _ALL_PREDICATES:
        # Escape single-quotes in description (none present in seed data, but
        # defensive quoting is required for safe string interpolation).
        safe_desc = description.replace("'", "''")
        op.execute(f"""
            INSERT INTO relationship.predicate_registry
                (predicate, kind, object_kind, description)
            VALUES ('{predicate}', '{kind}', '{object_kind}', '{safe_desc}')
            ON CONFLICT (predicate) DO NOTHING
        """)

    # 4. Grants — relationship butler only (RFC 0006 schema isolation).
    _grant_best_effort(_TABLE_FQN, _TABLE_PRIVILEGES, _RELATIONSHIP_ROLE)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS relationship.predicate_registry")
    # NOTE: we intentionally do NOT drop the relationship schema here.
    # Other relationship-butler tables (facts, credentials) may coexist in the
    # schema, and this migration does not own them.
