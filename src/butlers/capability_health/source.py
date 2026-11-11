"""What a consumer needs from health: the current values of some dependencies.

The broker holds one of these and consults it only for the dependencies a
capability declares. An implementation reads from published health (see
``read_health``); a missing source, a fault or a timeout all mean "unknown" to the
caller, which then proceeds under its normal checks.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from butlers.capability_health.read import Health


class HealthSource(Protocol):
    async def current(self, dependencies: Iterable[str]) -> list[Health]: ...
