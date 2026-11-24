"""Durable M7 selection receipts and authenticated finite surface activity.

Revision ID: core_373
Revises: core_372
Create Date: 2026-11-24 00:00:00.000000

The existing append-only receipts hold the plan, uniquely per subject generation.
Activity contains no room, location or message content; only the API login writes it.
Runtime roles can read activity but cannot assert owner presence. Rollback retains
receipts and activity, and disables consumers in source instead of deleting history.
"""

from alembic import op

revision = "core_373"
down_revision = "core_372"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_317:owner-delivery', 0))"
    )
    op.execute(
        "ALTER TABLE public.owner_delivery_receipts "
        "DROP CONSTRAINT IF EXISTS owner_delivery_receipts_kind_check"
    )
    op.execute(
        "ALTER TABLE public.owner_delivery_receipts "
        "ADD CONSTRAINT owner_delivery_receipts_kind_check CHECK (kind IN ("
        "'opened', 'presented', 'sent', 'send_failed', 'send_unknown', 'acknowledged', "
        "'decided', 'closed', 'refused', 'retracted', 'retract_failed', 'bridged', 'selected'))"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_owner_delivery_selection "
        "ON public.owner_delivery_receipts (subject_id, generation) WHERE kind = 'selected'"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.owner_delivery_surface_evidence (
            owner_entity UUID NOT NULL REFERENCES public.entities(id),
            surface TEXT NOT NULL CHECK (surface IN ('dashboard', 'telegram')),
            source TEXT NOT NULL CHECK (
                (surface = 'dashboard' AND source = 'dashboard_focus') OR
                (surface = 'telegram' AND source = 'owner_inbound')),
            observed_at TIMESTAMPTZ NOT NULL,
            valid_until TIMESTAMPTZ NOT NULL CHECK (valid_until > observed_at),
            active BOOLEAN NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (owner_entity, surface)
        )
    """)
    op.execute("ALTER TABLE public.owner_delivery_surface_evidence ENABLE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS owner_delivery_activity_read "
        "ON public.owner_delivery_surface_evidence"
    )
    op.execute(
        "CREATE POLICY owner_delivery_activity_read "
        "ON public.owner_delivery_surface_evidence FOR SELECT USING (true)"
    )
    # init-db may re-grant public table writes: absence of a write policy is the fence.
    op.execute("""
        DO $$ DECLARE r record; BEGIN
            FOR r IN SELECT rolname FROM pg_roles WHERE rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\'
            LOOP
                EXECUTE format('GRANT SELECT ON public.owner_delivery_surface_evidence TO %I',
                               r.rolname);
            END LOOP;
        END $$
    """)


def downgrade() -> None:
    # Shared core replay: retained consumers may still use selected receipts.
    upgrade()
