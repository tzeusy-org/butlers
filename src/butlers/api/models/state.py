"""State-specific Pydantic models.

Provides ``StateEntry`` for state read responses and ``StateSetRequest``
for the state write (PUT) endpoint.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class StateEntry(BaseModel):
    """A single key-value entry from the butler's state store.

    ``value`` accepts any JSON-serialisable type (object, array, scalar, or
    null) because the underlying JSONB column places no shape restrictions on
    stored values.
    """

    key: str
    value: Any
    updated_at: datetime


class StateSetRequest(BaseModel):
    """Request body for setting a state value.

    ``value`` accepts any JSON-serialisable type, matching the same contract
    as ``StateEntry.value``.
    """

    value: Any
