"""Place transitions older than the 400-day history horizon may be purged (bu-dhloi1.350).

Revision ID: core_332
Revises: core_331
Create Date: 2026-11-15 00:00:00.000000

RFC 0038 keeps transition history for 400 days. Until now nothing could delete a perception
history row: ``perception_private.immutable_event`` refused every DELETE for every role. The
definitions live in the fixed ``scripts/init-db.sql`` installer, as for core_322 and core_328; this
revision re-runs that installer and then proves, read-only, that the bootstrap really applied the
new bodies. A database whose bootstrap predates this change fails here, loudly.

What the installer adds, and what stays closed:

* ``perception_retention``: a NOLOGIN service identity like the writers. It holds no table grant
  and no policy; its only privilege is EXECUTE on ``public.perception_purge_place_history``.
* That function is owned by ``perception_owner`` (SECURITY DEFINER, pinned ``search_path``). It
  deletes ``owner.place`` transition rows with ``at`` strictly older than 400 days (the column and
  horizon ``perception_timeline`` enforces), their retraction rows and their receipts, in a bounded
  batch, in foreign-key order.
* ``immutable_event`` still refuses every UPDATE and every DELETE, except
  one: a DELETE issued as ``perception_owner`` with the function's transaction-local marker set,
  of an ``owner.place`` row (or its receipt) past the horizon. A caller cannot forge that:
  ``perception_owner`` is NOLOGIN and no other function it owns issues a DELETE, so the trigger
  also holds even if the purge function were wrong about a row's age or kind.

Nothing is scheduled and no connection for ``perception_retention`` exists: activating the purge is
a separate owner act (a login or connection for that identity, a schedule, and
``perception.flags.PLACE_RETENTION_LIVE``).

Downgrade is the existing history-preserving rollback: it refuses while any perception history
exists, and otherwise leaves the shared additive objects in place. The older bodies come back with
the older bootstrap, not from this revision.
"""

from __future__ import annotations

from alembic import op

revision = "core_332"
down_revision = "core_331"
branch_labels = None
depends_on = None

_IMMUTABLE_EVENT_SHA = "a5fa96f360ac49d101e671df202939067d585f1eef649fe81b343199b0bc60c7"
_PURGE_SHA = "ec09ff302d5e29d571fe57508a76a6fe9044d28e86755a02cf30b4f8170cb6bf"
_APPLIED_SQL = f"""
SELECT (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE (n.nspname = 'perception_private' AND p.proname = 'immutable_event'
               AND encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') = '{_IMMUTABLE_EVENT_SHA}')
           OR (n.nspname = 'public' AND p.proname = 'perception_purge_place_history'
               AND encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') = '{_PURGE_SHA}')) = 2
   AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'perception_retention'
               AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolbypassrls)
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("SELECT perception_admin.install_interface()")
    if not op.get_bind().exec_driver_sql(_APPLIED_SQL).scalar():
        raise RuntimeError(
            "place history retention not applied: re-run the fixed bootstrap "
            "(scripts/init-db.sql) before migrating"
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute("SELECT perception_admin.rollback_interface()")
