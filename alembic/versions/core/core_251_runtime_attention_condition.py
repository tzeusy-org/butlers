"""Install owner attention for the fleet-control and QA-patrol-overdue conditions.

Revision ID: core_251
Revises: core_250
Create Date: 2026-09-29 00:00:00.000000

REQ-butler-control-plane-liveness-007.  The migration login invokes the
bootstrap-owned fixed v4 upgrader (``scripts/init-db.sql``) and never receives
raw access to durable attention rows.  The upgrader admits one new outbox
source keyed by condition episode, a durable condition-side emission marker,
the Switchboard-only producer, and a content-blind owner projection.

Downgrade is deliberately non-destructive and never refuses.  Run with the
bootstrap superuser, it also clears ``producer_enabled``; run as the ordinary
migration login it only restamps, leaving the installed interface inert,
because only this revision's controller calls the producer.  Every emitted
episode, marker, and its delivery truth survives, so a later re-enable cannot
re-page a condition that already paged.  Not refusing keeps this head revision
from becoming a trusted-bootstrap downgrade boundary for every test that rolls
the core chain back.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

revision = "core_251"
down_revision = "core_250"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_LOCK = """
SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_251:runtime_attention_condition', 0))
"""


def _installed(bind: sa.Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT to_regclass('public.runtime_attention_condition_control') IS NOT NULL
                   AND to_regclass('public.runtime_attention_condition_episodes') IS NOT NULL
                   AND to_regprocedure('public.append_runtime_attention_condition(uuid)')
                       IS NOT NULL
                   AND to_regprocedure('public.observe_runtime_attention_conditions()')
                       IS NOT NULL
                """
            )
        ).scalar_one()
    )


def _may_execute(bind: sa.Connection, signature: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT COALESCE(
                    has_function_privilege(current_user, CAST(:signature AS regprocedure),
                                           'EXECUTE'),
                    false
                )
                """
            ),
            {"signature": signature},
        ).scalar_one()
    )


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_LOCK))
    if _installed(bind):
        return
    if not _may_execute(bind, "public.runtime_attention_upgrade_condition_v4()"):
        raise RuntimeError(
            "runtime-attention condition bootstrap upgrader is unavailable; "
            "run scripts/init-db.sql as the privileged bootstrap first"
        )
    bind.execute(sa.text("SELECT public.runtime_attention_upgrade_condition_v4()"))
    if not _installed(bind):
        raise RuntimeError("runtime-attention condition upgrade lacks catalog proof")


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(_LOCK))
    if not _installed(bind):
        return
    is_superuser = bool(
        bind.execute(
            sa.text("SELECT rolsuper FROM pg_roles WHERE rolname = session_user")
        ).scalar_one_or_none()
    )
    if not is_superuser:
        logger.warning(
            "core_251 downgrade left the runtime-attention condition producer enabled; "
            "call public.runtime_attention_deactivate_condition_v4() as the bootstrap "
            "superuser to clear it"
        )
        return
    bind.execute(sa.text("SELECT public.runtime_attention_deactivate_condition_v4()"))
