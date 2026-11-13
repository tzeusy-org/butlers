"""Owner mode entries: entering and leaving a mode, exits spent with step-up (bu-dhloi1.21 PR5).

``switchboard.owner_mode_entries`` (sw_077) holds one versioned row per mode the
owner has entered: its start, its scheduled end (or open-ended), the owner
cause, and when the owner exited it. A mode without a row was never entered, at
version 0. An entry has ended once exited or once its end is reached
(``modes.active_entry``); an ended entry is passed on as None.

Every judgement uses the database's ``now()`` read in the writer's own
transaction, the same clock sw_077's trigger reads. Entering a mode, or
extending an active entry, is written with no step-up. Exiting, or ending an
active entry sooner, is written only in the transaction that admits a
``mode_loosen`` dashboard step-up bound to the exact resulting entry
(``modes.entry_text``) at the new version, and retains the step-up result
(``rule_id`` is ``mode:version``) in that same transaction. The database
enforces the same rule on any owner SQL that bypasses this writer.

Only owner causes are written; nothing here infers an entry. The ``owner.mode``
projection of the active entries is a separate writer on its own identity.

Dark: ``flags.MODE_CONTROLS_LIVE`` is off, only the dark dashboard
``/api/owner-modes`` routes call this, and the runtime role has no privilege
on the table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from butlers.core.dashboard_stepup import StepUpBinding, consume_stepup, dashboard_stepup_proof
from butlers.perception import flags
from butlers.perception.mode_overlay import ModeConflict, ModeOverlayUnavailable, read_profile
from butlers.perception.modes import (
    MODE_LOOSEN_KIND,
    Enter,
    Exit,
    ModeChange,
    ModeControlsDark,
    ModeEntry,
    ModeProfile,
    ModeStepUpRequired,
    active_entry,
    classify_change,
    mode_binding,
    mode_id,
    resulting_entry,
)

_NAMESPACE = "SELECT current_database() || ':' || current_schema()"
_NOW = "SELECT now()"
_READ = (
    "SELECT version, since, until, cause, exited_at FROM switchboard.owner_mode_entries "
    "WHERE mode = $1"
)


async def _state(
    connection: Any, mode: str, *, lock: bool
) -> tuple[datetime, ModeEntry | None, int, ModeProfile]:
    """The database's now, the mode's active entry and version, and its profile."""
    if mode_id(mode) != mode:
        raise ValueError("wire mode ID required")
    now = await connection.fetchval(_NOW)
    if not isinstance(now, datetime):
        raise ModeOverlayUnavailable("database clock unavailable")
    row = await connection.fetchrow(_READ + (" FOR UPDATE" if lock else ""), mode)
    version = 0 if row is None else row["version"]
    profile, _ = await read_profile(connection, mode)
    return now, active_entry(mode, row, now), version, profile


async def read_entry(connection: Any, mode: str) -> tuple[ModeEntry | None, int]:
    """A mode's active entry (None once ended or never entered) and its version."""
    _, active, version, _ = await _state(connection, mode, lock=False)
    return active, version


async def _binding(
    connection: Any,
    change: Enter | Exit,
    *,
    owner_action_id: UUID,
    now: datetime,
    active: ModeEntry | None,
    profile: ModeProfile,
    expected_version: int,
) -> StepUpBinding:
    namespace = await connection.fetchval(_NAMESPACE)
    if not isinstance(namespace, str) or not namespace:
        raise ModeOverlayUnavailable("mode entry namespace unavailable")
    return mode_binding(
        change,
        {change.mode: profile},
        owner_action_id=owner_action_id,
        expected_version=expected_version,
        namespace=namespace,
        now=now,
        active=active,
    )


async def entry_binding(
    connection: Any,
    change: ModeChange,
    *,
    owner_action_id: UUID,
    expected_version: int,
) -> StepUpBinding:
    """The exact exit or early end a ``mode_loosen`` step-up confirms, from this database.

    The dashboard derives it to issue the confirmation; the writer re-derives
    it with the row locked to spend it.
    """
    if not isinstance(change, Enter | Exit):
        raise ValueError("only entering or leaving a mode")
    now, active, version, profile = await _state(connection, change.mode, lock=False)
    if version != expected_version:
        raise ModeConflict("mode version changed")
    return await _binding(
        connection,
        change,
        owner_action_id=owner_action_id,
        now=now,
        active=active,
        profile=profile,
        expected_version=expected_version,
    )


class ModeEntryWriter:
    """Owner-only mode entries and exits; the owner dashboard is the one intended caller."""

    def __init__(self, connection: Any):
        self.connection = connection

    async def change(
        self, owner_action_id: UUID, change: ModeChange, expected_version: int
    ) -> dict[str, Any]:
        """Apply one entry or exit at its expected version, in its own transaction.

        Returns ``{mode, version, verdict}``. A change that leaves the entry as
        it is writes nothing. Exiting a mode with no active entry refuses. An
        exit or early end without a step-up proof, or with one the trusted store
        does not admit for exactly this result, writes nothing and raises
        ModeStepUpRequired.
        """
        if not flags.MODE_CONTROLS_LIVE:
            raise ModeControlsDark("mode controls are dark")
        if not isinstance(change, Enter | Exit):
            raise ValueError("only entering or leaving a mode")
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version")
        try:
            async with self.connection.transaction():
                now, active, version, profile = await _state(
                    self.connection, change.mode, lock=True
                )
                if version != expected_version:
                    raise ModeConflict("mode version changed")
                verdict = classify_change(change, {change.mode: profile}, now=now, active=active)
                receipt = {"mode": change.mode, "version": version, "verdict": verdict}
                if verdict == "none":
                    return receipt
                entry, exited = resulting_entry(change, now=now, active=active)
                binding = None
                if verdict == "loosen":
                    if dashboard_stepup_proof.get() is None:
                        raise ModeStepUpRequired("leaving a mode early needs dashboard step-up")
                    binding = await _binding(
                        self.connection,
                        change,
                        owner_action_id=owner_action_id,
                        now=now,
                        active=active,
                        profile=profile,
                        expected_version=expected_version,
                    )
                    admission = await consume_stepup(self.connection, "begin", binding)
                    if admission.get("state") != "admitted":
                        raise ModeStepUpRequired("dashboard step-up not admitted")
                await self._write(owner_action_id, entry, now if exited else None, version + 1)
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
                        raise ModeOverlayUnavailable("Step-up receipt unavailable")
                return {**receipt, "version": version + 1}
        except (
            asyncpg.InvalidSchemaNameError,
            asyncpg.UndefinedTableError,
            asyncpg.UndefinedFunctionError,
            asyncpg.InsufficientPrivilegeError,
        ) as exc:
            # An unavailable trusted interface never falls back to an unconfirmed write.
            raise ModeStepUpRequired("dashboard step-up unavailable") from exc
        except asyncpg.UniqueViolationError as exc:
            # A concurrent first entry inserted the mode's row: this change is stale.
            raise ModeConflict("mode version changed") from exc

    async def _write(
        self,
        owner_action_id: UUID,
        entry: ModeEntry,
        exited_at: datetime | None,
        version: int,
    ) -> None:
        values = (
            entry.mode,
            version,
            entry.since,
            entry.until,
            entry.cause,
            exited_at,
            owner_action_id,
        )
        if version == 1:
            await self.connection.execute(
                "INSERT INTO switchboard.owner_mode_entries (mode, version, since, until, "
                "cause, exited_at, owner_action_id) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                *values,
            )
            return
        await self.connection.execute(
            "UPDATE switchboard.owner_mode_entries SET version = $2, since = $3, until = $4, "
            "cause = $5, exited_at = $6, owner_action_id = $7, "
            "updated_at = clock_timestamp() WHERE mode = $1",
            *values,
        )


__all__ = [
    "ModeEntryWriter",
    "entry_binding",
    "read_entry",
]
