"""Withhold legacy unknown-sender names from the public MCP projection.

Revision ID: core_368
Revises: core_367

Recompute placeholder_name under the existing row filter. Logical replication
withdraws previously published rows as their filter flag changes; a later
rename to a real name still makes the row eligible. Restore neither the old
function nor the old flags on downgrade, as doing so could republish identifiers.
"""

import sqlalchemy as sa

from alembic import op

revision = "core_368"
down_revision = "core_367"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_318:projection-source', 0))"
_UUID = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_LEGACY_IDENTIFIER = r"[0-9]{7,}|@(s[.]whatsapp[.]net|lid|c[.]us|g[.]us)([^[:alnum:].-]|$)"


def _placeholder_name(row: str) -> str:
    return f"""COALESCE(
        {row}canonical_name = 'Unknown (' || ({row}metadata ->> 'source_channel')
            || ' ' || ({row}metadata ->> 'source_value') || ')'
        OR ({row}canonical_name LIKE 'Unknown %' AND (
            {row}canonical_name ~* '{_LEGACY_IDENTIFIER}'
            OR {row}metadata ?| ARRAY['jid', 'jids', 'channel']
        )), false
    )"""


_BODY = f"""
BEGIN
    NEW.merged_into := CASE WHEN (NEW.metadata ->> 'merged_into') ~ '{_UUID}'
        THEN (NEW.metadata ->> 'merged_into')::uuid END;
    NEW.deleted := (
        NEW.metadata ->> 'deleted_at' IS NOT NULL
        OR NEW.metadata ->> 'archived_at' IS NOT NULL
        OR COALESCE((NEW.metadata ->> 'tombstone') = 'true', false)
        OR COALESCE((NEW.metadata ->> 'archived') = 'true', false)
        OR NOT NEW.listed
        OR COALESCE((NEW.metadata ->> 'merged_into') !~ '{_UUID}', false)
    );
    NEW.unidentified := COALESCE((NEW.metadata ->> 'unidentified') = 'true', false);
    NEW.placeholder_name := {_placeholder_name("NEW.")};
    RETURN NEW;
END
"""


def upgrade() -> None:
    op.execute(_LOCK)
    current_body = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT prosrc FROM pg_catalog.pg_proc "
                "WHERE oid = 'public.entities_sync_projection_flags()'::regprocedure"
            )
        )
        .scalar_one()
    )
    if current_body == _BODY:
        return

    # Do not let a concurrent insert use the old rule after the backfill scan.
    op.execute("LOCK TABLE public.entities IN SHARE ROW EXCLUSIVE MODE")
    op.execute(
        "CREATE OR REPLACE FUNCTION public.entities_sync_projection_flags() "
        "RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp "
        f"AS $body${_BODY}$body$"
    )
    placeholder = _placeholder_name("entity.")
    op.execute(
        f"""
        UPDATE public.entities AS entity
        SET placeholder_name = {placeholder}
        WHERE entity.placeholder_name IS DISTINCT FROM {placeholder}
        """
    )


def downgrade() -> None:
    # The narrower historical rule would republish raw channel identities.
    pass
