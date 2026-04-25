"""Chronicler projection adapters.

Adapters run under the Chronicler butler's scheduler (``dispatch_mode=job``)
and project approved source surfaces into ``point_events`` and/or
``episodes``. Every adapter:

- Reads only from its declared ``read_surface``.
- Writes only to the ``chronicler`` schema.
- Produces stable ``source_ref`` values so replays are idempotent.
- Updates ``projection_checkpoints`` on every run.
- Degrades gracefully if an optional read surface is missing.
- Never invokes an LLM per event (guardrail-tested).
"""

from __future__ import annotations

from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.adapters.calendar import CalendarCompletedAdapter
from butlers.chronicler.adapters.owntracks import OwnTracksPointAdapter
from butlers.chronicler.adapters.sessions import CoreSessionsAdapter
from butlers.chronicler.adapters.spotify import SpotifySessionAdapter

__all__ = [
    "AdapterResult",
    "CalendarCompletedAdapter",
    "CoreSessionsAdapter",
    "OwnTracksPointAdapter",
    "ProjectionAdapter",
    "SpotifySessionAdapter",
]
