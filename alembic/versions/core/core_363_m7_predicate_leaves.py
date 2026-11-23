"""The perception binding validator accepts the M7 condition leaves (bu-4c7nvp.1.5 S2).

Revision ID: core_363
Revises: core_360
Create Date: 2026-11-23 00:00:00.000000

RFC 0038 S4.M7 and REQ-owner-watches-001 add six leaves to the one closed predicate AST:
``expectation``, ``routine_deviation``, ``condition``, ``mode``, ``read_check`` and ``bundle``.
``butlers.conditions.predicate`` validates them, and ``perception_private.valid_predicate``
mirrors that validator so the dark ``perception_bind`` store and the evaluator accept and refuse
the same documents. The definition lives in the fixed ``scripts/init-db.sql`` installer, as for
core_322, core_328 and core_333; this revision re-runs that installer and then proves, read-only,
that the bootstrap really applied the new body. A database whose bootstrap predates this change
fails here, loudly.

The body also counts leaves across the whole tree, measures the 8KiB bound as compact JSON, accepts
only canonical lowercase hyphenated UUIDs, and requires ``expectation_kind`` to be a registered
``expect.*`` kind, so it accepts exactly what the Python validator accepts.

Nothing else changes: no table, grant, role or registry row, and no source is registered for a
new leaf, so a binding naming one still refuses at ``perception_bind``'s source boundary until a
later change registers its source. The installer is idempotent and shared across schemas.

Downgrade is a no-op. The shared interface is additive and installed once for every schema, so
this revision must not roll back what core_322 installed, and one schema's downgrade must not
change a definition the others still run. The older body comes back with the older bootstrap, and
core_322's downgrade remains the history-preserving rollback of the whole interface.

The number follows main at the time of writing (core_360 is the tip; core_361 and core_362 are
claimed by open changes); renumber and re-point down_revision on rebase if they merge first.
"""

from __future__ import annotations

from alembic import op

revision = "core_363"
down_revision = "core_360"
branch_labels = None
depends_on = None

_VALID_PREDICATE_SHA = "303bd722b1c161f1e97b81f8021b0bc4243d038a67303bf657ef90b0127bc26f"
_APPLIED_SQL = f"""
SELECT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               WHERE n.nspname = 'perception_private' AND p.proname = 'valid_predicate'
                 AND encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') = '{_VALID_PREDICATE_SHA}')
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("SELECT perception_admin.install_interface()")
    if not op.get_bind().exec_driver_sql(_APPLIED_SQL).scalar():
        raise RuntimeError(
            "M7 predicate leaves not applied: re-run the fixed bootstrap "
            "(scripts/init-db.sql) before migrating"
        )


def downgrade() -> None:
    """Nothing to undo: see the module docstring."""
