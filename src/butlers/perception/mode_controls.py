"""Owner mode controls: the read and the step-up binding the dashboard asks for (PR8).

``read_modes`` is every mode's profile and active entry with their versions.
``loosen_binding`` is the exact ``mode_loosen`` step-up a change needs, or None
when the change is not a loosening. It reads the ledger in one transaction and
returns the same binding the writer re-derives when it spends the step-up: the
binding is built from the mode, the expected version, the change and the
current profile and active entry, never from the clock. The clock only decides
the classification; if it flips before the spend (the entry ended, so there is
nothing left to exit), the writer refuses or reclassifies and never spends the
proof against another binding.

Dark: ``flags.MODE_CONTROLS_LIVE`` is off and the dashboard routes answer 404.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from butlers.core.dashboard_stepup import StepUpBinding
from butlers.perception import flags
from butlers.perception.mode_entries import entry_binding, read_entry
from butlers.perception.mode_overlay import (
    ModeConflict,
    ModeOverlayUnavailable,
    overlay_binding,
    read_profile,
)
from butlers.perception.model import MODES
from butlers.perception.modes import (
    Enter,
    Exit,
    ModeChange,
    ModeControlsDark,
    ModeProfile,
    OverlayEdit,
    classify_change,
)


def _seconds(profile: ModeProfile) -> int | None:
    return None if profile.duration is None else profile.duration // timedelta(seconds=1)


async def read_modes(connection: Any) -> list[dict[str, Any]]:
    """Every mode's profile and active entry, with their versions, in one snapshot."""
    if not flags.MODE_CONTROLS_LIVE:
        raise ModeControlsDark("mode controls are dark")
    modes = []
    async with connection.transaction():
        for mode in sorted(MODES):
            profile, profile_version = await read_profile(connection, mode)
            entry, entry_version = await read_entry(connection, mode)
            modes.append(
                {
                    "mode": mode,
                    "profile": {
                        "duration_s": _seconds(profile),
                        "holds": sorted(profile.holds),
                        "break_through": sorted(profile.break_through),
                    },
                    "profile_version": profile_version,
                    "entry": None
                    if entry is None
                    else {"since": entry.since, "until": entry.until, "cause": entry.cause},
                    "entry_version": entry_version,
                }
            )
    return modes


async def loosen_binding(
    connection: Any, change: ModeChange, *, owner_action_id: UUID, expected_version: int
) -> StepUpBinding | None:
    """The ``mode_loosen`` binding the change needs, or None when it is not a loosening.

    A stale ``expected_version`` raises ModeConflict; a change the writer would
    refuse raises ValueError. Nothing is written.
    """
    if not flags.MODE_CONTROLS_LIVE:
        raise ModeControlsDark("mode controls are dark")
    async with connection.transaction():
        profile, profile_version = await read_profile(connection, change.mode)
        if isinstance(change, OverlayEdit):
            if profile_version != expected_version:
                raise ModeConflict("mode version changed")
            if classify_change(change, {change.mode: profile}) != "loosen":
                return None
            return await overlay_binding(
                connection,
                change,
                owner_action_id=owner_action_id,
                expected_version=expected_version,
            )
        if not isinstance(change, Enter | Exit):
            raise ValueError("unknown mode change")
        # now() is fixed for the transaction, so the entry read and the binding agree.
        now = await connection.fetchval("SELECT now()")
        if not isinstance(now, datetime):
            raise ModeOverlayUnavailable("database clock unavailable")
        active, entry_version = await read_entry(connection, change.mode)
        if entry_version != expected_version:
            raise ModeConflict("mode version changed")
        verdict = classify_change(change, {change.mode: profile}, now=now, active=active)
        if verdict != "loosen":
            return None
        return await entry_binding(
            connection,
            change,
            owner_action_id=owner_action_id,
            expected_version=expected_version,
        )


__all__ = ["loosen_binding", "read_modes"]
