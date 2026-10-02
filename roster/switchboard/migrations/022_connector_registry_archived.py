"""connector_registry: add archived_at (soft-archive) + seed dead identities.

Revision ID: sw_022
Revises: sw_021
Create Date: 2026-07-10 00:00:00.000000

bu-33dm2. Dead connector endpoint identities were cluttering fleet status and
dragging health rollups down: they show as permanently ``offline`` yet cannot
be removed because ingestion history (public.ingestion_events,
connectors.filtered_events, audit_log) still references them.

archived_at
-----------
``TIMESTAMPTZ NULL`` — soft-archive timestamp, distinct from ``deleted_at``
(the disconnect soft-delete). Semantics:

- ``deleted_at IS NOT NULL`` → disconnected: hidden from every connector list.
- ``archived_at IS NOT NULL`` (with ``deleted_at IS NULL``) → archived: still
  listed (grouped into a collapsed "archived" section in the dashboard and
  reachable for history) but EXCLUDED from fleet-health rollups and alerting,
  so a superseded identity stops dragging fleet health down.

NULL means active. A non-NULL value is the archival timestamp.

Data seed
---------
Idempotently archives the dead identities found during the 2026-07-05
ingestion history audit (bu-33dm2). Only rows currently un-archived and matching
one of those identities are touched, so re-running is a no-op and any identity
already archived / disconnected is left alone. ``endpoint_identity`` is stored in
its full connector-type-prefixed form (``google_health:degraded``,
``owntracks:unknown``, ``home_assistant:<host>:<port>``), so the WHERE clauses
match that prefixed value — not the bare tail.

The audit also found superseded ``google_health:user:<owner>:<uuid>:<resource>``
rows for one specific account. That statement was deployment-specific (it
hardcoded one owner's email, so it matched nothing on any other install) and has
been removed to keep the repo user-agnostic; the only database it applied to has
already run sw_022. It is intentionally NOT generalised to every account: that
key shape is the live per-resource cursor identity, so a generic predicate would
archive active cursors on other installs. Stale rows of that shape can be
archived per-endpoint via the dashboard archive action. This is a data-fix migration (same pattern as
sw_013's ``replay_safe=FALSE`` email seed); it deliberately does NOT delete
anything.

Downgrade
---------
Drops the partial index and the column. Any archival state is lost on downgrade
— acceptable because downgrade is only used in dev/test environments.
"""

from __future__ import annotations

import os

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_022"
down_revision = "sw_021"
branch_labels = None
depends_on = None


#: Idempotent data-seed: archive the dead endpoint identities (bu-33dm2).
#: Each statement only touches rows still un-archived and matching that exact
#: identity, so re-running the migration is a no-op.
#:
#: ``endpoint_identity`` in ``connector_registry`` is stored in its FULL,
#: connector-type-prefixed form (the value each connector emits and cursor_store
#: persists verbatim), e.g. ``google_health:degraded``,
#: ``owntracks:unknown``, ``home_assistant:<host>:<port>`` — NOT the bare tail.
#: The WHERE clauses must therefore match the prefixed value or they archive
#: nothing. (A former account-specific google_health statement was removed; see
#: the module docstring.)
_ARCHIVE_DEAD_IDENTITIES_SQL = [
    """
    UPDATE connector_registry SET archived_at = now()
     WHERE archived_at IS NULL
       AND connector_type = 'google_health'
       AND endpoint_identity = 'google_health:degraded'
    """,
    """
    UPDATE connector_registry SET archived_at = now()
     WHERE archived_at IS NULL
       AND connector_type = 'owntracks'
       AND endpoint_identity = 'owntracks:unknown'
    """,
]

#: Site-specific dead identities (full, prefixed form) come from this env var as a
#: comma-separated list, so tracked files carry no hostnames. The original run
#: archived ``home_assistant:<tailnet-host>:443`` this way; unset = nothing extra.
_EXTRA_DEAD_IDENTITIES_ENV = "BUTLERS_ARCHIVE_DEAD_IDENTITIES"

_ARCHIVE_EXTRA_IDENTITY_SQL = """
    UPDATE connector_registry SET archived_at = now()
     WHERE archived_at IS NULL
       AND endpoint_identity = :identity
"""


def upgrade() -> None:
    # Soft-archive column: NULL = active, non-NULL = archived (timestamp of archival)
    op.execute(
        """
        ALTER TABLE connector_registry
            ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL
        """
    )

    # Partial index for the "live" set (not deleted AND not archived) — the set
    # the fleet-health /api/ingestion/connectors/cross-summary scans.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_connector_registry_live
        ON connector_registry (connector_type, endpoint_identity)
        WHERE deleted_at IS NULL AND archived_at IS NULL
        """
    )

    # Idempotent data seed — archive the dead identities (bu-33dm2).
    for stmt in _ARCHIVE_DEAD_IDENTITIES_SQL:
        op.execute(stmt)
    for identity in os.environ.get(_EXTRA_DEAD_IDENTITIES_ENV, "").split(","):
        if identity.strip():
            op.execute(sa.text(_ARCHIVE_EXTRA_IDENTITY_SQL).bindparams(identity=identity.strip()))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_connector_registry_live")
    op.execute("ALTER TABLE connector_registry DROP COLUMN IF EXISTS archived_at")
