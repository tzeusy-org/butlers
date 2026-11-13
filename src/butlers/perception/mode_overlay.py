"""The owner mode overlay: owner edits to a mode, spent with their step-up (bu-dhloi1.21 PR4b).

``switchboard.owner_mode_overlay`` (sw_076) holds one versioned row per mode
the owner has edited; a mode without a row has its recorded default
(``modes.DEFAULT_PROFILES``) at version 0. Only edits live here: duration, held
classes and break-through senders. Entering and leaving a mode is the
``owner.mode`` projection, not this table.

A tightening is written with no step-up. A loosening is written only in the
transaction that admits a ``mode_loosen`` dashboard step-up bound to the exact
resulting state at the new version, and retains the step-up result
(``rule_id`` is ``mode:version``) in that same transaction. The database
enforces the same rule on any owner SQL that bypasses this writer.

Dark: ``flags.MODE_CONTROLS_LIVE`` is off, no route or tool calls this, and the
runtime role has no privilege on the table.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import asyncpg

from butlers.core.dashboard_stepup import StepUpBinding, consume_stepup, dashboard_stepup_proof
from butlers.perception import flags
from butlers.perception.modes import (
    DEFAULT_PROFILES,
    MODE_LOOSEN_KIND,
    ModeChange,
    ModeControlsDark,
    ModeProfile,
    ModeStepUpRequired,
    OverlayEdit,
    apply_edit,
    classify_change,
    mode_binding,
    mode_id,
)

_NAMESPACE = "SELECT current_database() || ':' || current_schema()"
_READ = (
    "SELECT version, duration_s, holds, break_through FROM switchboard.owner_mode_overlay "
    "WHERE mode = $1"
)


class ModeConflict(ValueError):
    """The mode changed since the expected version."""


class ModeOverlayUnavailable(RuntimeError):
    """The overlay store or its namespace could not be read."""


def _profile(mode: str, row: Any) -> tuple[ModeProfile, int]:
    if row is None:
        return DEFAULT_PROFILES[mode], 0
    duration = None if row["duration_s"] is None else timedelta(seconds=row["duration_s"])
    profile = ModeProfile(mode, duration, frozenset(row["holds"]), frozenset(row["break_through"]))
    return profile, row["version"]


async def read_profile(
    connection: Any, mode: str, *, lock: bool = False
) -> tuple[ModeProfile, int]:
    """A mode's current profile and version (its default at version 0 if never edited)."""
    if mode_id(mode) != mode:
        raise ValueError("wire mode ID required")
    row = await connection.fetchrow(_READ + (" FOR UPDATE" if lock else ""), mode)
    return _profile(mode, row)


async def overlay_binding(
    connection: Any,
    change: ModeChange,
    *,
    owner_action_id: UUID,
    expected_version: int,
    lock: bool = False,
) -> StepUpBinding:
    """The exact loosening a ``mode_loosen`` step-up confirms, from this database.

    The dashboard derives it unlocked to issue the confirmation; the writer
    re-derives it with the row locked to spend it.
    """
    if not isinstance(change, OverlayEdit):
        raise ValueError("only an edit of duration, holds or break-through")
    profile, version = await read_profile(connection, change.mode, lock=lock)
    if version != expected_version:
        raise ModeConflict("mode version changed")
    namespace = await connection.fetchval(_NAMESPACE)
    if not isinstance(namespace, str) or not namespace:
        raise ModeOverlayUnavailable("mode overlay namespace unavailable")
    return mode_binding(
        change,
        {change.mode: profile},
        owner_action_id=owner_action_id,
        expected_version=expected_version,
        namespace=namespace,
    )


class ModeOverlayWriter:
    """Owner-only mode edits; the owner dashboard is the one intended caller."""

    def __init__(self, connection: Any):
        self.connection = connection

    async def edit(
        self, owner_action_id: UUID, change: ModeChange, expected_version: int
    ) -> dict[str, Any]:
        """Apply one edit at its expected version, in its own transaction.

        Returns ``{mode, version, verdict}``. A change that leaves the mode as
        it is writes nothing. A loosening without a step-up proof, or with one
        the trusted store does not admit for exactly this result, writes nothing
        and raises ModeStepUpRequired.
        """
        if not flags.MODE_CONTROLS_LIVE:
            raise ModeControlsDark("mode controls are dark")
        if not isinstance(change, OverlayEdit):
            raise ValueError("only an edit of duration, holds or break-through")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version")
        try:
            async with self.connection.transaction():
                profile, version = await read_profile(self.connection, change.mode, lock=True)
                if version != expected_version:
                    raise ModeConflict("mode version changed")
                resulting = apply_edit(change, profile)
                verdict = classify_change(change, {change.mode: profile})
                receipt = {"mode": change.mode, "version": version, "verdict": verdict}
                if verdict == "none":
                    return receipt
                binding = None
                if verdict == "loosen":
                    if dashboard_stepup_proof.get() is None:
                        raise ModeStepUpRequired("loosening a mode needs dashboard step-up")
                    binding = await overlay_binding(
                        self.connection,
                        change,
                        owner_action_id=owner_action_id,
                        expected_version=expected_version,
                    )
                    admission = await consume_stepup(self.connection, "begin", binding)
                    if admission.get("state") != "admitted":
                        raise ModeStepUpRequired("dashboard step-up not admitted")
                await self._write(owner_action_id, resulting, version + 1)
                if binding is not None:
                    retained = await consume_stepup(
                        self.connection,
                        "finish",
                        binding,
                        {
                            "version": 1,
                            "kind": MODE_LOOSEN_KIND,
                            "id": str(owner_action_id),
                            "rule_id": f"{change.mode}:{version + 1}",
                        },
                    )
                    if retained.get("state") != "completed":
                        raise RuntimeError("Step-up receipt unavailable")
                return {**receipt, "version": version + 1}
        except (
            asyncpg.InvalidSchemaNameError,
            asyncpg.UndefinedTableError,
            asyncpg.UndefinedFunctionError,
            asyncpg.InsufficientPrivilegeError,
        ) as exc:
            # An unavailable trusted interface never falls back to an unconfirmed write.
            raise ModeStepUpRequired("dashboard step-up unavailable") from exc

    async def _write(self, owner_action_id: UUID, profile: ModeProfile, version: int) -> None:
        duration = None if profile.duration is None else profile.duration // timedelta(seconds=1)
        values = (
            profile.mode,
            version,
            duration,
            sorted(profile.holds),
            sorted(profile.break_through),
            owner_action_id,
        )
        if version == 1:
            await self.connection.execute(
                "INSERT INTO switchboard.owner_mode_overlay (mode, version, duration_s, holds, "
                "break_through, owner_action_id) VALUES ($1, $2, $3, $4::text[], $5::text[], $6)",
                *values,
            )
            return
        await self.connection.execute(
            "UPDATE switchboard.owner_mode_overlay SET version = $2, duration_s = $3, "
            "holds = $4::text[], break_through = $5::text[], owner_action_id = $6, "
            "updated_at = clock_timestamp() WHERE mode = $1",
            *values,
        )


__all__ = [
    "ModeConflict",
    "ModeOverlayUnavailable",
    "ModeOverlayWriter",
    "overlay_binding",
    "read_profile",
]
