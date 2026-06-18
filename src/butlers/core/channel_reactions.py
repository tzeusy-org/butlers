"""Channel reaction constants for ingest lifecycle notifications.

Defines the canonical reaction identifiers used to signal ingest state to
interactive channel modules (e.g. Telegram).  These live in ``core`` so that
``core_tools._switchboard`` can read them without importing the telegram module.

The telegram module maps these identifiers to platform-specific emoji strings
(``modules.telegram.REACTION_DISPLAY``).
"""

from __future__ import annotations

#: Reaction sent when ingest processing begins.
REACTION_IN_PROGRESS = ":eye"

#: Reaction sent when ingest processing succeeds.
REACTION_SUCCESS = ":thumbsup"

#: Reaction sent when ingest processing fails.
REACTION_FAILURE = ":space invader"
