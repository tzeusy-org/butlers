"""Add canonical answer citations and per-message butler attribution."""

from __future__ import annotations

import re

from alembic import op

revision = "core_247"
down_revision = "core_246"
branch_labels = None
depends_on = None

_SHARED_DDL_LOCK = "butlers:core_247:dashboard-message-provenance"
_PROTECTED_ROLLBACK_ERROR = (
    "core_247 cannot remove shared dashboard message provenance because the protected "
    "core_198 rollback preflight failed"
)


def _core_247_installation_count() -> int:
    """Count schema-local core chains that still depend on the shared columns."""
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
                    THEN substring(version_num FROM 6)::integer >= 247
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def _downgrade_crosses_core_198() -> bool:
    context = op.get_context()
    environment = context.environment_context
    script = context.script
    if environment is None or script is None:
        return True
    destination = environment.get_revision_argument()
    if isinstance(destination, str):
        relative = re.fullmatch(r"-(\d+)", destination)
        if relative is not None:
            revisions_above = sum(1 for _step in script.iterate_revisions(revision, "core_198"))
            return int(relative.group(1)) > revisions_above
    return any(
        step.revision == "core_198" for step in script.iterate_revisions(revision, destination)
    )


def _protected_rollback_preflight_passes(bind) -> bool:
    script = op.get_context().script
    if script is None:
        return False
    return script.get_revision("core_198").module.protected_rollback_preflight_passes(bind)


def upgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_SHARED_DDL_LOCK}', 0))")
    op.execute(
        "ALTER TABLE public.dashboard_messages "
        "ADD COLUMN IF NOT EXISTS citations JSONB NULL, "
        "ADD COLUMN IF NOT EXISTS routed_butler TEXT NULL"
    )
    # Legacy sources are names, not links. Preserve that distinction during
    # the additive rollout and leave malformed rows unknown for inspection.
    # Nested CASE branches are deliberate: jsonb_array_elements* must never be
    # evaluated against a malformed, non-array legacy value.
    op.execute(
        """
        UPDATE public.dashboard_messages
        SET citations = CASE
            WHEN jsonb_typeof(sources) <> 'array' OR jsonb_array_length(sources) = 0
                THEN NULL
            WHEN EXISTS (
                SELECT 1 FROM jsonb_array_elements(sources) AS entry
                WHERE jsonb_typeof(entry) <> 'string'
            ) THEN NULL
            WHEN EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(sources) AS entry(item)
                WHERE btrim(item) = ''
                   OR char_length(btrim(item)) > 200
                   OR item ~ '[[:cntrl:]]'
            ) THEN NULL
            ELSE (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'label', btrim(item), 'target', NULL, 'kind', 'unlinked'
                    )
                    ORDER BY ordinality
                )
                FROM jsonb_array_elements_text(sources)
                     WITH ORDINALITY AS entries(item, ordinality)
            )
        END
        WHERE citations IS NULL
          AND sources IS NOT NULL
        """
    )


def downgrade() -> None:
    if _downgrade_crosses_core_198() and not _protected_rollback_preflight_passes(op.get_bind()):
        raise RuntimeError(_PROTECTED_ROLLBACK_ERROR)
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_SHARED_DDL_LOCK}', 0))")
    if _core_247_installation_count() > 1:
        return
    op.execute(
        "ALTER TABLE public.dashboard_messages "
        "DROP COLUMN IF EXISTS citations, DROP COLUMN IF EXISTS routed_butler"
    )
