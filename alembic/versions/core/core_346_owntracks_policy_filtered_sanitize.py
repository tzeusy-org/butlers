"""OwnTracks: earlier policy-blocked filtered_events rows lose their coordinates (bu-s11n0s.73).

Revision ID: core_346
Revises: core_345
Create Date: 2026-11-17 00:00:00.000000

The connector now records an event its ingestion policy blocks without its raw body: such an
event never reaches ``connectors.owntracks_points``, so no retention grant can name it, while its
``connectors.filtered_events`` row lives for twelve months. Rows recorded before that change still
hold the raw body (exact coordinates, SSID). This one-shot pass gives each of them the shape the
connector writes now:

* ``full_payload.payload`` becomes ``{"raw": null, "normalized_text": <coordinate-free text>}``,
  the text built from the raw body as ``butlers.connectors.owntracks.build_filtered_event_text``
  does (region names for a location, region and direction for a transition, the waypoint count
  for a waypoints sync);
* ``full_payload.replay_unavailable`` is set, so the replay drain refuses the row.

Rows are OwnTracks policy rows only (``connector_type = 'owntracks'`` and a
``connector_rule:`` filter reason); parked rows and other connectors are untouched. The pass is
idempotent: a sanitized row no longer matches. Every per-schema core chain runs it; after the
first, it matches nothing. The downgrade changes nothing: a removed raw body cannot come back.
"""

from __future__ import annotations

from alembic import op

revision = "core_346"
down_revision = "core_345"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_346:owntracks-filtered', 0))"
#: Kept in step with ``POLICY_FILTERED_NOT_REPLAYABLE`` in ``butlers.connectors.owntracks``
#: (a test pins the two equal).
_NOT_REPLAYABLE = "a policy-blocked OwnTracks event keeps no coordinates"

_RAW = "fe.full_payload #> '{payload,raw}'"
_TEXT = f"""
CASE {_RAW} ->> '_type'
    WHEN 'location' THEN
        'Location update (blocked by policy; coordinates not kept)' || coalesce(
            ' (in: ' || (
                SELECT string_agg(region, ', ')
                FROM jsonb_array_elements_text(
                    CASE WHEN jsonb_typeof({_RAW} -> 'inregions') = 'array'
                        THEN {_RAW} -> 'inregions' END
                ) AS region
            ) || ')',
            ''
        )
    WHEN 'transition' THEN
        CASE {_RAW} ->> 'event'
            WHEN 'enter' THEN 'Entered region: '
            WHEN 'leave' THEN 'Left region: '
            ELSE 'Transition (' || coalesce({_RAW} ->> 'event', '') || '): '
        END || coalesce({_RAW} ->> 'desc', 'unknown region')
    WHEN 'waypoints' THEN
        'Waypoint sync: ' || CASE WHEN jsonb_typeof({_RAW} -> 'waypoints') = 'array'
            THEN jsonb_array_length({_RAW} -> 'waypoints') ELSE 0 END || ' regions'
    ELSE 'OwnTracks event (blocked by policy; coordinates not kept)'
END
"""
_SANITIZE = f"""
UPDATE connectors.filtered_events AS fe
SET full_payload = fe.full_payload || jsonb_build_object(
    'payload',
    CASE WHEN jsonb_typeof({_RAW}) IS DISTINCT FROM 'null' AND {_RAW} IS NOT NULL
        THEN jsonb_build_object('raw', NULL, 'normalized_text', {_TEXT})
        ELSE fe.full_payload -> 'payload' END,
    'replay_unavailable', '{_NOT_REPLAYABLE}'
)
WHERE fe.connector_type = 'owntracks'
  AND fe.filter_reason LIKE 'connector_rule:%'
  AND (
    ({_RAW} IS NOT NULL AND jsonb_typeof({_RAW}) <> 'null')
    OR NOT fe.full_payload ? 'replay_unavailable'
  )
"""


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(_SANITIZE)


def downgrade() -> None:
    # A removed raw body cannot be restored; the sanitized rows stay as they are.
    pass
