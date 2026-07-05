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

from butlers.chronicler.adapters.activitywatch import ActivityWatchWindowAdapter
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.adapters.calendar import CalendarCompletedAdapter
from butlers.chronicler.adapters.comms import CommsSocialAdapter
from butlers.chronicler.adapters.exercise import ExerciseInferredAdapter
from butlers.chronicler.adapters.focus import FocusInferredAdapter
from butlers.chronicler.adapters.google_health import (
    GoogleHealthHeartRateAdapter,
    GoogleHealthSleepAdapter,
    GoogleHealthStepsAdapter,
    GoogleHealthWorkoutAdapter,
)
from butlers.chronicler.adapters.home_assistant import HomeAssistantHistoryAdapter
from butlers.chronicler.adapters.home_assistant_sensor_activity import (
    HomeAssistantSensorActivityAdapter,
)
from butlers.chronicler.adapters.meals import MealsAdapter
from butlers.chronicler.adapters.occupation import OccupationInferredAdapter
from butlers.chronicler.adapters.owner_outbound import OwnerOutboundMessageAdapter
from butlers.chronicler.adapters.owntracks import OwnTracksPointAdapter
from butlers.chronicler.adapters.reading import ReadingInferredAdapter
from butlers.chronicler.adapters.sessions import CoreSessionsAdapter
from butlers.chronicler.adapters.spotify import SpotifySessionAdapter
from butlers.chronicler.adapters.steam import SteamPlayAdapter

__all__ = [
    "ActivityWatchWindowAdapter",
    "AdapterResult",
    "CalendarCompletedAdapter",
    "CommsSocialAdapter",
    "CoreSessionsAdapter",
    "ExerciseInferredAdapter",
    "FocusInferredAdapter",
    "GoogleHealthHeartRateAdapter",
    "GoogleHealthSleepAdapter",
    "GoogleHealthStepsAdapter",
    "GoogleHealthWorkoutAdapter",
    "HomeAssistantHistoryAdapter",
    "HomeAssistantSensorActivityAdapter",
    "MealsAdapter",
    "OccupationInferredAdapter",
    "OwnTracksPointAdapter",
    "OwnerOutboundMessageAdapter",
    "ProjectionAdapter",
    "ReadingInferredAdapter",
    "SpotifySessionAdapter",
    "SteamPlayAdapter",
]
