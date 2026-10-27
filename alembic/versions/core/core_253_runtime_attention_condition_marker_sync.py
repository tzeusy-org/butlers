"""Keep the condition marker's delivery category in step with every transition.

Revision ID: core_253
Revises: core_252
Create Date: 2026-09-29 00:00:00.000000

bu-giazn6, REQ-butler-control-plane-liveness-007 ("Outbox retention cannot
re-page an active condition").  The v4 marker's ``last_delivery_state`` was
refreshed only while the controller re-appended an *active* condition, so a
condition that resolved before delivery finished kept ``pending``/``sending``
and surfaced that stale category once the outbox row was gone.

The migration login invokes the bootstrap-owned fixed v5 upgrader
(``scripts/init-db.sql``), which installs an owner-run AFTER UPDATE trigger on
``public.runtime_attention_outbox`` that mirrors every condition-episode
lifecycle change into the marker in the same transaction, then backfills any
marker already stale.  The Switchboard worker gains no marker privilege.

Downgrade never refuses.  Run with the bootstrap superuser it removes the
trigger and its function and keeps every marker row; run as the ordinary
migration login it only restamps and leaves the (truth-preserving) trigger in
place.  A re-upgrade after a bootstrap downgrade is a plain migration step:
``runtime_attention_deactivate_condition_marker_v5()`` itself re-offers the
upgrader to the migration login.  Only a first install on an existing
database needs ``scripts/init-db.sql`` rerun first, to offer the upgrader.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

revision = "core_253"
down_revision = "core_252"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

UPGRADER = "public.runtime_attention_upgrade_condition_marker_v5()"
DEACTIVATOR = "public.runtime_attention_deactivate_condition_marker_v5()"
SYNC_FUNCTION = "public.runtime_attention_sync_condition_marker()"
SYNC_TRIGGER = "runtime_attention_condition_marker_sync_trigger"

_LOCK = """
SELECT pg_advisory_xact_lock(
    hashtextextended('butlers:core_253:runtime_attention_condition_marker_sync', 0)
)
"""


def _installed(bind: sa.Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT to_regprocedure(:function) IS NOT NULL
                   AND EXISTS (
                       SELECT 1
                       FROM pg_catalog.pg_trigger AS trigger_row
                       WHERE trigger_row.tgrelid = to_regclass('public.runtime_attention_outbox')
                         AND trigger_row.tgname = :trigger
                         AND NOT trigger_row.tgisinternal
                   )
                """
            ),
            {"function": SYNC_FUNCTION, "trigger": SYNC_TRIGGER},
        ).scalar_one()
    )


def _may_execute(bind: sa.Connection, signature: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT COALESCE(
                    has_function_privilege(current_user, to_regprocedure(:signature), 'EXECUTE'),
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
    if not _may_execute(bind, UPGRADER):
        raise RuntimeError(
            "runtime-attention condition marker upgrader is unavailable; "
            "run scripts/init-db.sql as the privileged bootstrap first"
        )
    bind.execute(sa.text(f"SELECT {UPGRADER}"))
    if not _installed(bind):
        raise RuntimeError("runtime-attention condition marker upgrade lacks catalog proof")


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
            "core_253 downgrade left the runtime-attention condition marker sync installed; "
            "call %s as the bootstrap superuser to remove it",
            DEACTIVATOR,
        )
        return
    bind.execute(sa.text(f"SELECT {DEACTIVATOR}"))
