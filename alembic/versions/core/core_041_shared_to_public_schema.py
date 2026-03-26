"""shared_to_public_schema: move all tables from shared schema to public

Revision ID: core_041
Revises: core_040
Create Date: 2026-03-26 00:00:00.000000

Moves every table from the ``shared`` PostgreSQL schema to ``public``, then
drops the shared schema entirely.  This eliminates the per-butler schema-
isolation layer and consolidates all cross-butler infrastructure into the
default search_path.

Tables are moved in dependency order (parents first) so that FK constraints
remain valid throughout the migration:

  1.  contacts
  2.  entities
  3.  model_catalog
  4.  provider_config
  5.  ingestion_events
  6.  contact_info           (FK -> contacts)
  7.  entity_info            (FK -> entities)
  8.  google_accounts        (FK -> entities)
  9.  memory_catalog         (FK -> entities, nullable)
  10. butler_model_overrides (FK -> model_catalog)
  11. token_limits           (FK -> model_catalog)
  12. token_usage_ledger     (FK -> model_catalog, PARTITIONED)
  13. dashboard_conversations (standalone)
  14. dashboard_messages     (FK -> dashboard_conversations)
  15. healing_attempts       (standalone)

token_usage_ledger is range-partitioned on ``recorded_at``.  Child partitions
must be moved before the parent, and pg_partman configuration (if present) is
updated to reflect the new schema.

The shared schema is left empty (not dropped) because historical core
migrations reference it in guarded DDL blocks, and each butler runs the
full core chain independently.

downgrade() recreates the shared schema and moves all tables back.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_041"
down_revision = "core_040"
branch_labels = None
depends_on = None

log = logging.getLogger(__name__)

# Tables in dependency order (parents first).
# Partitioned tables are marked with is_partitioned=True.
_SHARED_TABLES = [
    ("contacts", False),
    ("entities", False),
    ("model_catalog", False),
    ("provider_config", False),
    ("ingestion_events", False),
    ("contact_info", False),
    ("entity_info", False),
    ("google_accounts", False),
    ("memory_catalog", False),
    ("butler_model_overrides", False),
    ("token_limits", False),
    ("token_usage_ledger", True),
    ("dashboard_conversations", False),
    ("dashboard_messages", False),
    ("healing_attempts", False),
]

# Butler roles that had GRANT access to shared-schema tables.
_ALL_BUTLER_ROLES = (
    "butler_switchboard_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_relationship_rw",
    "butler_messenger_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_home_rw",
    "butler_travel_rw",
)


def upgrade() -> None:
    conn = op.get_bind()

    # -------------------------------------------------------------------------
    # Guard: bail out if the shared schema does not exist (fresh install that
    # never had the shared schema, or already migrated).
    # -------------------------------------------------------------------------
    has_shared = conn.execute(
        text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.schemata"
            "  WHERE schema_name = 'shared'"
            ")"
        )
    ).scalar()
    if not has_shared:
        log.info("shared schema does not exist — nothing to migrate (fresh install)")
        return

    # -------------------------------------------------------------------------
    # 1. Move token_usage_ledger partitions first (before moving the parent).
    #    PostgreSQL requires child partitions to reside in the same schema as
    #    the parent OR be moved before the parent is moved.  We move children
    #    first so SET SCHEMA on the parent succeeds without orphaned refs.
    # -------------------------------------------------------------------------
    partitions = conn.execute(
        text(
            "SELECT c.relname "
            "FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE i.inhparent = to_regclass('shared.token_usage_ledger') "
            "  AND n.nspname = 'shared' "
            "ORDER BY c.relname"
        )
    ).fetchall()

    for (part_name,) in partitions:
        # Guard: skip if already in public
        already_in_public = conn.execute(
            text("SELECT to_regclass(:fqn) IS NOT NULL"), {"fqn": f"public.{part_name}"}
        ).scalar()
        if already_in_public:
            log.info("partition %s already in public — skipping", part_name)
            continue
        log.info("moving partition shared.%s to public", part_name)
        conn.execute(text(f'ALTER TABLE shared."{part_name}" SET SCHEMA public'))

    # -------------------------------------------------------------------------
    # 2. Move each table from shared to public.
    # -------------------------------------------------------------------------
    for table_name, _is_partitioned in _SHARED_TABLES:
        # Guard: skip tables that don't exist in shared (partial installs).
        exists_in_shared = conn.execute(
            text("SELECT to_regclass(:fqn) IS NOT NULL"), {"fqn": f"shared.{table_name}"}
        ).scalar()
        if not exists_in_shared:
            log.info("shared.%s does not exist — skipping", table_name)
            continue

        # Guard: skip if a table with this name already exists in public
        # (e.g. name collision from a butler-local table).
        exists_in_public = conn.execute(
            text("SELECT to_regclass(:fqn) IS NOT NULL"), {"fqn": f"public.{table_name}"}
        ).scalar()
        if exists_in_public:
            log.warning(
                "public.%s already exists — cannot move shared.%s; skipping",
                table_name,
                table_name,
            )
            continue

        log.info("moving shared.%s to public", table_name)
        conn.execute(text(f'ALTER TABLE shared."{table_name}" SET SCHEMA public'))

    # -------------------------------------------------------------------------
    # 3. Update pg_partman configuration if present.
    #    Change parent_table reference from shared.token_usage_ledger to
    #    public.token_usage_ledger.
    # -------------------------------------------------------------------------
    has_partman = conn.execute(
        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman')")
    ).scalar()
    if has_partman:
        conn.execute(
            text(
                "UPDATE partman.part_config "
                "SET parent_table = 'public.token_usage_ledger' "
                "WHERE parent_table = 'shared.token_usage_ledger'"
            )
        )

    # NOTE: Do NOT drop the shared schema here.  Historical core migrations
    # (core_007, core_014, etc.) reference it in guarded DO $$ blocks, and each
    # butler runs the full core chain independently.  If the first butler's
    # core_041 drops 'shared', subsequent butlers' core_007 fails because the
    # schema no longer exists for to_regclass() lookups.  Leaving the empty
    # schema is harmless — alembic/env.py already creates it as part of the
    # migration search_path.
    log.info("shared schema is now empty — tables moved to public")


def downgrade() -> None:
    conn = op.get_bind()

    # -------------------------------------------------------------------------
    # 1. Recreate the shared schema.
    # -------------------------------------------------------------------------
    conn.execute(text("CREATE SCHEMA IF NOT EXISTS shared"))

    # -------------------------------------------------------------------------
    # 2. Move token_usage_ledger partitions back to shared first.
    # -------------------------------------------------------------------------
    parent_exists = conn.execute(
        text("SELECT to_regclass('public.token_usage_ledger') IS NOT NULL")
    ).scalar()
    if parent_exists:
        partitions = conn.execute(
            text(
                "SELECT c.relname "
                "FROM pg_inherits i "
                "JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE i.inhparent = to_regclass('public.token_usage_ledger') "
                "  AND n.nspname = 'public' "
                "ORDER BY c.relname"
            )
        ).fetchall()

        for (part_name,) in partitions:
            already_in_shared = conn.execute(
                text("SELECT to_regclass(:fqn) IS NOT NULL"), {"fqn": f"shared.{part_name}"}
            ).scalar()
            if already_in_shared:
                continue
            conn.execute(text(f'ALTER TABLE public."{part_name}" SET SCHEMA shared'))

    # -------------------------------------------------------------------------
    # 3. Move tables back to shared (reverse dependency order = children first).
    # -------------------------------------------------------------------------
    for table_name, _is_partitioned in reversed(_SHARED_TABLES):
        exists_in_public = conn.execute(
            text("SELECT to_regclass(:fqn) IS NOT NULL"), {"fqn": f"public.{table_name}"}
        ).scalar()
        if not exists_in_public:
            continue

        exists_in_shared = conn.execute(
            text("SELECT to_regclass(:fqn) IS NOT NULL"), {"fqn": f"shared.{table_name}"}
        ).scalar()
        if exists_in_shared:
            continue

        conn.execute(text(f'ALTER TABLE public."{table_name}" SET SCHEMA shared'))

    # -------------------------------------------------------------------------
    # 4. Update pg_partman configuration back to shared schema.
    # -------------------------------------------------------------------------
    has_partman = conn.execute(
        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_partman')")
    ).scalar()
    if has_partman:
        conn.execute(
            text(
                "UPDATE partman.part_config "
                "SET parent_table = 'shared.token_usage_ledger' "
                "WHERE parent_table = 'public.token_usage_ledger'"
            )
        )

    # -------------------------------------------------------------------------
    # 5. Re-grant USAGE ON SCHEMA shared and table privileges to butler roles.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        role_exists = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"), {"role": role}
        ).scalar()
        if not role_exists:
            continue

        conn.execute(text(f'GRANT USAGE ON SCHEMA shared TO "{role}"'))

        for table_name, _is_partitioned in _SHARED_TABLES:
            table_exists = conn.execute(
                text("SELECT to_regclass(:fqn) IS NOT NULL"), {"fqn": f"shared.{table_name}"}
            ).scalar()
            if table_exists:
                conn.execute(
                    text(
                        f"GRANT SELECT, INSERT, UPDATE, DELETE "
                        f'ON TABLE shared."{table_name}" TO "{role}"'
                    )
                )
