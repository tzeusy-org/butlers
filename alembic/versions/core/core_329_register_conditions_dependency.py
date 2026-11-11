"""Register ``conditions`` as a declared capability-health dependency.

Revision ID: core_329
Revises: core_328
Create Date: 2026-11-11 00:00:00.000000

RFC 0038 S6 (bu-dhloi1.14). The conditions service already reports its own store
health as ``system.dep.conditions``; that name becomes a declared dependency
(``butlers.core.capability_dependencies``), so the ``health.dependencies`` profile
needs its subject and evidence reference. The fixed installer in
``scripts/init-db.sql`` registers every declared dependency idempotently, and only
for a profile it owns; this revision re-runs it and proves, read-only, that the
bootstrap which ran predates nothing it needs (the installer text names the new
dependency). The migration role cannot read ``perception_private``, so the
registration itself is covered by the installer's own tests.

Downgrade is the history-preserving ``rollback_interface()``: it refuses while any
perception history exists and leaves the additive registration in place.
"""

from __future__ import annotations

from alembic import op

revision = "core_329"
down_revision = "core_328"
branch_labels = None
depends_on = None

_INSTALLER_NAMES_CONDITIONS = """
SELECT EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'perception_admin' AND p.proname = 'install_interface'
      AND position('''conditions'',''google''' in p.prosrc) > 0)
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("SELECT perception_admin.install_interface()")
    if not op.get_bind().exec_driver_sql(_INSTALLER_NAMES_CONDITIONS).scalar():
        raise RuntimeError(
            "conditions dependency not registered: re-run the fixed bootstrap "
            "(scripts/init-db.sql) before migrating"
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("SELECT perception_admin.rollback_interface()")
