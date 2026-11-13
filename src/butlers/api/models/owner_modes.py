"""Owner mode-control models (bu-dhloi1.21 PR8).

See ``src/butlers/perception/mode_controls.py`` for the reader and options
binding, and ``mode_overlay`` / ``mode_entries`` for the writers.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from butlers.perception.modes import MAX_BREAK_THROUGH

#: The eight wire mode IDs; an alias ("sleep", "focused") is not a route key.
ModeId = Literal[
    "asleep",
    "emergency",
    "focus",
    "holiday",
    "hosting",
    "house_unattended",
    "sick",
    "travel",
]
OwnerCause = Literal["owner_declared", "owner_confirmed"]
_EDIT_FIELDS = ("holds", "break_through", "duration_s")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Versioned(_Strict):
    owner_action_id: UUID
    expected_version: int = Field(ge=0)


class OwnerModeEnter(_Versioned):
    """Enter a mode, or change an active entry's end. ``until`` None is open-ended."""

    until: AwareDatetime | None = None
    cause: OwnerCause


class OwnerModeExit(_Versioned):
    """Leave an active mode now; needs a ``mode_loosen`` step-up."""


class OwnerModeEdit(_Versioned):
    """Edit exactly one of a mode's holds, break-through senders or duration.

    ``duration_s`` null is open-ended.
    """

    holds: list[Annotated[str, Field(min_length=1, max_length=64)]] | None = Field(
        None, max_length=8
    )
    break_through: list[Annotated[str, Field(min_length=1, max_length=192)]] | None = Field(
        None, max_length=MAX_BREAK_THROUGH
    )
    duration_s: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def _exactly_one(self) -> OwnerModeEdit:
        named = [field for field in _EDIT_FIELDS if field in self.model_fields_set]
        if len(named) != 1:
            raise ValueError("name exactly one of holds, break_through or duration_s")
        if named[0] != "duration_s" and getattr(self, named[0]) is None:
            raise ValueError(f"{named[0]} is a list")
        return self


class OwnerModeEnterOptions(OwnerModeEnter):
    change: Literal["enter"]
    mode: ModeId


class OwnerModeExitOptions(OwnerModeExit):
    change: Literal["exit"]
    mode: ModeId


class OwnerModeEditOptions(OwnerModeEdit):
    change: Literal["edit"]
    mode: ModeId


OwnerModeOptions = Annotated[
    OwnerModeEnterOptions | OwnerModeExitOptions | OwnerModeEditOptions,
    Field(discriminator="change"),
]


class OwnerModeProfile(BaseModel):
    duration_s: int | None
    holds: list[str]
    break_through: list[str]


class OwnerModeEntry(BaseModel):
    since: AwareDatetime
    until: AwareDatetime | None
    cause: OwnerCause


class OwnerModeState(BaseModel):
    mode: ModeId
    profile: OwnerModeProfile
    profile_version: int
    entry: OwnerModeEntry | None
    entry_version: int


class OwnerModes(BaseModel):
    modes: list[OwnerModeState]


class OwnerModeReceipt(BaseModel):
    mode: ModeId
    version: int
    verdict: Literal["tighten", "loosen", "none"]
