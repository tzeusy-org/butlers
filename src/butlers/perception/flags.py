"""Dark activation flags for the perception consumers (bu-dhloi1.21).

Each consumer has its own flag. All stay off until the separately authorized
RFC 0038 switch; owner release bu-276pli permits scheduling only. Turning a flag
back off disables that consumer and keeps every receipt.
"""

from __future__ import annotations

#: ``situation.read`` and ``timeline.query`` are offered as capabilities.
SITUATION_READS_LIVE = False

#: The owner's deterministic ``/now`` card is served (dashboard ``GET /api/now``).
NOW_LIVE = False

#: Owner-local time follows the owner clock instead of the legacy settings zone.
OWNER_CLOCK_LIVE = False

#: Owner mode controls (enter, exit and edit a mode) accept changes.
MODE_CONTROLS_LIVE = False

#: The owner's active mode entries are projected into ``owner.mode``.
MODE_PROJECTION_LIVE = False

#: The legacy ``user_context`` signals are compared with perception (shadow parity).
LEGACY_PARITY_LIVE = False

#: ``prune_place_transitions`` may count or delete ``owner.place`` history older than the 400-day
#: horizon. Activation also needs a connection for the ``perception_retention`` identity and a
#: schedule; neither exists, so this stays off until the owner switches it on.
PLACE_RETENTION_LIVE = False
