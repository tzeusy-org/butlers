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
