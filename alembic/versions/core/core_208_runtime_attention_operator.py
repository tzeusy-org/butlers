"""Install the runtime-attention operator projection and reissue operation.

Revision ID: core_208
Revises: core_207
Create Date: 2026-08-26 00:00:00.000000

The migration login invokes a bootstrap-owned fixed upgrader and never receives
raw access to durable attention rows.  Downgrade is deliberately non-destructive:
it disables new reissues while preserving the table, functions, and evidence.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "core_208"
down_revision = "core_207"
branch_labels = None
depends_on = None

_LOCK = """
SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_208:runtime_attention_operator', 0))
"""


def _installed(bind: sa.Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT to_regclass('public.runtime_attention_operator_control') IS NOT NULL
                   AND to_regprocedure('public.observe_runtime_attention_models()') IS NOT NULL
                   AND to_regprocedure('public.observe_runtime_attention_fleet_halt()') IS NOT NULL
                   AND to_regprocedure('public.reissue_runtime_attention_episode(uuid)') IS NOT NULL
                """
            )
        ).scalar_one()
    )


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_LOCK))
    if _installed(bind):
        return
    trusted = bool(
        bind.execute(
            sa.text(
                """
                SELECT COALESCE(
                    has_function_privilege(
                        current_user,
                        'public.runtime_attention_upgrade_operator_v3()'::regprocedure,
                        'EXECUTE'
                    ), false
                )
                """
            )
        ).scalar_one()
    )
    if not trusted:
        raise RuntimeError(
            "runtime-attention operator bootstrap upgrader is unavailable; "
            "run scripts/init-db.sql as the privileged bootstrap first"
        )
    bind.execute(sa.text("SELECT public.runtime_attention_upgrade_operator_v3()"))
    if not _installed(bind):
        raise RuntimeError("runtime-attention operator upgrade lacks catalog proof")


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_LOCK))
    trusted = bool(
        bind.execute(
            sa.text(
                """
                SELECT COALESCE(
                    has_function_privilege(
                        current_user,
                        'public.runtime_attention_deactivate_operator_v3()'::regprocedure,
                        'EXECUTE'
                    ), false
                )
                """
            )
        ).scalar_one()
    )
    if not trusted:
        raise RuntimeError(
            "core_208 downgrade requires the managed privileged bootstrap deactivator"
        )
    bind.execute(sa.text("SELECT public.runtime_attention_deactivate_operator_v3()"))
