"""system.dep carries {state, cause, observed_at}; its writer profile is activated.

Revision ID: core_328
Revises: core_327
Create Date: 2026-11-11 00:00:00.000000

RFC 0038 S6 (bu-dhloi1.14). Capability health needs a closed cause and a source
age next to the six-state enum, so the ``system.dep`` perception value changes
from a bare state string to ``{state, cause, observed_at}``, and the
``health.dependencies`` source profile (finite freshness, one subject and one
evidence reference per declared dependency) is activated while it is still
unconfigured. The definitions live in the fixed ``scripts/init-db.sql``
installer, as for core_322; this revision re-runs that installer and then
proves, read-only, that the bootstrap really applied the new ``valid_value``
body (the kind schema is written by the same installer call; the migration role
cannot read ``perception_private``). A database whose bootstrap predates this change
fails here, loudly, instead of silently keeping the old value contract.

Nothing is converted. The installer refuses to run over a ``system.dep`` slot or
event still holding the old bare string (none can exist: the profile was
unconfigured, so no writer could publish one); such history needs forward
remediation, never a rewrite.

Downgrade is the existing history-preserving rollback: it refuses while any
perception history exists, and otherwise leaves the shared additive objects in
place. The older function bodies come back with the older bootstrap, not from
this revision.
"""

from __future__ import annotations

from alembic import op

revision = "core_328"
down_revision = "core_327"
branch_labels = None
depends_on = None

_VALID_VALUE_SHA = "d6234350324359400b437d4635c07ba6c6da8728945da935c2cb41fd4170a05a"
_APPLIED_SQL = f"""
SELECT EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'perception_private' AND p.proname = 'valid_value'
      AND encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') = '{_VALID_VALUE_SHA}')
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("SELECT perception_admin.install_interface()")
    if not op.get_bind().exec_driver_sql(_APPLIED_SQL).scalar():
        raise RuntimeError(
            "system.dep health value contract not applied: re-run the fixed bootstrap "
            "(scripts/init-db.sql) before migrating"
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("SELECT perception_admin.rollback_interface()")
