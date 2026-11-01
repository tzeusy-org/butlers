"""Let a held ``pr_create`` reconcile by adopting the PR it created (bu-mc01y5.29).

Revision ID: core_298
Revises: core_296
Create Date: 2026-11-02 00:00:00.000000

REQ-qa-publication-authority-006 (change confine-qa-publication-authority).
core_267's ``ck_qa_publication_operations_exact_reconciliation`` lets a reconciled row
say ``resource_found`` only when ``reconciled_object_id`` equals the bound
``expected_object_id``.  A ``pr_create`` cannot know its PR number before egress, so it
binds ``expected_object_id`` NULL and a lost create response could never be recorded as
found: the attempt's lineage stayed held forever.

The CHECK is replaced under the same name, keeping every core_267 rule, and adds one
adoption arm: a ``pr_create`` that bound no expected object and recorded no new object
may reconcile ``resource_found`` naming a PR number (``^[1-9][0-9]{0,9}$``), and its
``new_object_id`` must equal that number so a resumed publisher binds exactly the
adopted PR.  The database checks the shape; the read-only reconciler
(``butlers.core.qa.publication.reconcile``) supplies the proof that the number is the
only PR from the bound head branch onto the bound base.

Revises core_297 (dashboard job health), which landed first.

The core chain runs once per butler schema over the shared table, so only the last
schema to leave core_298 restores the core_267 CHECK (core_246 pattern).  An adopted row
cannot satisfy the core_267 rule, so if one exists the restored CHECK is added
NOT VALID: it binds new writes while the adopted rows keep their recorded outcome.
"""

from __future__ import annotations

from alembic import op

revision = "core_298"
down_revision = "core_297"
branch_labels = None
depends_on = None

_TABLE = "public.qa_publication_operations"
_CONSTRAINT = "ck_qa_publication_operations_exact_reconciliation"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_298:qa-publication', 0))"

_EXACT = """
                    (reconciliation = 'resource_found'
                        AND reconciled_object_id IS NOT NULL
                        AND reconciled_object_id = expected_object_id)"""
_ADOPTED = """
                    OR (reconciliation = 'resource_found'
                        AND kind = 'pr_create'
                        AND expected_object_id IS NULL
                        AND reconciled_object_id ~ '^[1-9][0-9]{0,9}$'
                        AND new_object_id = reconciled_object_id)"""
_ABSENT = """
                    OR (reconciliation = 'resource_absent'
                        AND reconciled_object_id IS NULL)"""


def _check(found_arms: str) -> str:
    # COALESCE: a CHECK passes on NULL, and an unknown must not be exact.
    return f"""
        CHECK (
            COALESCE(
                CASE
                    WHEN stage = 'reconciled' THEN {found_arms}{_ABSENT}
                    ELSE reconciliation IS NULL AND reconciled_object_id IS NULL
                END,
                false
            )
        )"""


def _core_298_installation_count() -> int:
    """Count schema-local core chains that still depend on the adoption CHECK (core_246)."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND namespace.nspname NOT LIKE 'pg_%%'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 298
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT} {_check(_EXACT + _ADOPTED)}")


def downgrade() -> None:
    op.execute(_LOCK)
    # One shared CHECK serves every schema still at core_298; only the last one restores it.
    if _core_298_installation_count() > 1:
        return
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT} {_check(_EXACT)} NOT VALID")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM {_TABLE}
                WHERE stage = 'reconciled' AND reconciliation = 'resource_found'
                  AND expected_object_id IS NULL
            ) THEN
                ALTER TABLE {_TABLE} VALIDATE CONSTRAINT {_CONSTRAINT};
            END IF;
        END
        $$
        """
    )
