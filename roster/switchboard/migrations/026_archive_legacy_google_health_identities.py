"""Archive superseded Google Health per-resource connector identities.

Revision ID: sw_026
Revises: sw_025
Create Date: 2026-07-13 00:00:00.000000

Google Health now emits one canonical heartbeat identity per account:
``google_health:user:<account>``. Older connector versions emitted cursor-shaped
identities with an account UUID and resource suffix:
``google_health:user:<account>:<uuid>:<resource>``. Those historical rows can
never receive another heartbeat after the identity-model migration, so leaving
them active produces permanent offline connector findings.

Migration ``sw_022`` archived this legacy shape only for the account observed
during the original fleet audit. This follow-up applies the same data repair to
every account without embedding account identifiers. The UUID shape and the
closed set of legacy resource suffixes distinguish superseded cursor identities
from canonical account heartbeat identities.

Downgrade is intentionally a no-op. Archival is a historical classification,
and clearing it would reactivate identities that no current connector emits.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_026"
down_revision = "sw_025"
branch_labels = None
depends_on = None


_LEGACY_GOOGLE_HEALTH_IDENTITY_RE = (
    r"^google_health:user:[^:]+:"
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}:"
    r"(sleep|activity|resting_hr|hrv|spo2|breathing_rate|vo2_max)$"
)

_ARCHIVE_LEGACY_GOOGLE_HEALTH_IDENTITIES_SQL = f"""
    UPDATE connector_registry
       SET archived_at = now()
     WHERE archived_at IS NULL
       AND deleted_at IS NULL
       AND connector_type = 'google_health'
       AND endpoint_identity ~ '{_LEGACY_GOOGLE_HEALTH_IDENTITY_RE}'
"""


def upgrade() -> None:
    op.execute(_ARCHIVE_LEGACY_GOOGLE_HEALTH_IDENTITIES_SQL)


def downgrade() -> None:
    pass
