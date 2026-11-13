"""Decision sets on the goal ledger (RFC 0038 D13, M7; bu-4c7nvp.1.3).

A decision set groups effects of one goal that are already parked for the owner's per-event
approval, so the owner can decide them with one tap (bu-qlx055 = A, q3 and q11). The set is not
authority of its own: every item is one parked effect, bound by ``item_digest`` to its exact
capability, stored-arguments digest, recipient and selection class. An approved item still runs
as its own stored call through the broker.

Membership is immutable per version. A changed membership is the next version of the same set,
and the previous open version becomes ``superseded`` without being edited, so anything bound to
an old version (a rendered card, a token) can be recognised as stale. ``explicit_only`` marks an
``administer`` item (a rule or schedule change): the owner must tick it on its own, never through
an "all" selection.

This module is the immutable read contract the card consumer (bu-jv1moa.27) renders, and the
producer's write path. Every method runs on the caller's transaction connection. ``create`` takes
the goal row ``FOR UPDATE`` first, the lock Stop takes. The sw_072 triggers are the second line:
items are append-only, a version changes only its state, and nothing is deleted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import asyncpg
from asyncpg.pool import PoolConnectionProxy

from butlers.core.capability_json import arguments_digest
from butlers.core.goal_waiting import WaitLedger

MIN_SET_ITEMS = 2
MAX_SET_ITEMS = 16  # roster/switchboard/migrations/072_decision_sets.py _MAX_ITEMS
_DIGEST_VERSION = 1


class DecisionSetRefused(Exception):
    """Why a set was not created: ``goal``, ``resource`` or ``policy``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class DecisionSetItem:
    """One exact parked call of a set version, as the owner is shown it."""

    position: int
    effect_id: UUID
    capability: str
    args_digest: str
    recipient_entity: UUID | None
    recipient_channel: str | None
    explicit_only: bool
    item_digest: str


@dataclass(frozen=True, slots=True)
class DecisionSet:
    """One immutable version of a set, with its items in order."""

    set_id: UUID
    version: int
    goal_id: UUID
    epoch: int
    plan_version: int
    created_by: UUID
    digest: str
    expires_at: datetime
    state: str
    items: tuple[DecisionSetItem, ...]


def item_digest(
    effect_id: UUID,
    capability: str,
    args_digest: str,
    recipient_entity: UUID | None,
    recipient_channel: str | None,
    explicit_only: bool,
) -> str:
    """SHA-256 over the canonical JSON of exactly the facts an item binds (RFC 0038 S0)."""
    return arguments_digest(
        {
            "v": _DIGEST_VERSION,
            "kind": "decision_set_item",
            "effect_id": str(effect_id),
            "capability": capability,
            "args_digest": args_digest,
            "recipient_entity": None if recipient_entity is None else str(recipient_entity),
            "recipient_channel": recipient_channel,
            "explicit_only": explicit_only,
        }
    )


def set_digest(
    set_id: UUID,
    version: int,
    goal_id: UUID,
    epoch: int,
    plan_version: int,
    item_digests: Sequence[str],
) -> str:
    """SHA-256 over one version's identity, bindings and ordered item digests."""
    return arguments_digest(
        {
            "v": _DIGEST_VERSION,
            "kind": "decision_set",
            "set_id": str(set_id),
            "version": version,
            "goal_id": str(goal_id),
            "epoch": epoch,
            "plan_version": plan_version,
            "items": list(item_digests),
        }
    )


_VERSION_COLUMNS = (
    "set_id, version, goal_id, epoch, plan_version, created_by, digest, expires_at, state"
)
_ITEM_COLUMNS = (
    "position, effect_id, capability, args_digest, recipient_entity, recipient_channel, "
    "explicit_only, item_digest"
)


class DecisionSetLedger:
    def __init__(self, connection: asyncpg.Connection | PoolConnectionProxy) -> None:
        self.connection = connection

    # -- read contract ---------------------------------------------------------------------

    async def read(self, set_id: UUID, version: int | None = None) -> DecisionSet | None:
        """One version of a set (the latest when ``version`` is None); None when absent."""
        if version is None:
            row = await self.connection.fetchrow(
                f"SELECT {_VERSION_COLUMNS} FROM switchboard.decision_set_versions "
                "WHERE set_id = $1 ORDER BY version DESC LIMIT 1",
                set_id,
            )
        else:
            row = await self.connection.fetchrow(
                f"SELECT {_VERSION_COLUMNS} FROM switchboard.decision_set_versions "
                "WHERE set_id = $1 AND version = $2",
                set_id,
                version,
            )
        if row is None:
            return None
        items = await self.connection.fetch(
            f"SELECT {_ITEM_COLUMNS} FROM switchboard.decision_set_items "
            "WHERE set_id = $1 AND set_version = $2 ORDER BY position",
            set_id,
            row["version"],
        )
        return DecisionSet(
            set_id=row["set_id"],
            version=row["version"],
            goal_id=row["goal_id"],
            epoch=row["epoch"],
            plan_version=row["plan_version"],
            created_by=row["created_by"],
            digest=row["digest"],
            expires_at=row["expires_at"],
            state=row["state"],
            items=tuple(DecisionSetItem(**dict(item)) for item in items),
        )

    async def open_for_goal(self, goal_id: UUID) -> DecisionSet | None:
        """The goal's one open version, if any."""
        row = await self.connection.fetchrow(
            "SELECT set_id, version FROM switchboard.decision_set_versions "
            "WHERE goal_id = $1 AND state = 'open'",
            goal_id,
        )
        return None if row is None else await self.read(row["set_id"], row["version"])

    # -- producer --------------------------------------------------------------------------

    async def create(
        self,
        goal_id: UUID,
        invocation_id: UUID,
        effect_ids: Sequence[UUID],
        explicit_only: Mapping[str, bool],
    ) -> DecisionSet:
        """Propose ``effect_ids`` (in order) as the goal's open set; return its version.

        The caller authenticated the goal's live lead and passes each capability's selection
        class from the live catalog (``administer`` is explicit-only); a capability missing
        from it refuses ``resource``. Every effect must be this goal's and still ``parked``.
        The same membership as the open version returns it unchanged (a retry). A changed one
        supersedes the open version and appends the next version of the same set.
        """
        if not MIN_SET_ITEMS <= len(effect_ids) <= MAX_SET_ITEMS:
            raise DecisionSetRefused("policy")
        if len(set(effect_ids)) != len(effect_ids):
            raise DecisionSetRefused("policy")
        goal = await self.connection.fetchrow(
            "SELECT status, plan_version FROM switchboard.goals WHERE id = $1 FOR UPDATE",
            goal_id,
        )
        if goal is None or goal["status"] != "running":
            raise DecisionSetRefused("goal")
        rows = await self.connection.fetch(
            "SELECT id, capability, args_digest, recipient_entity, recipient_channel, status "
            "FROM switchboard.effects WHERE goal_id = $1 AND id = ANY($2::uuid[]) FOR UPDATE",
            goal_id,
            list(effect_ids),
        )
        effects = {row["id"]: row for row in rows}
        if len(effects) != len(effect_ids) or any(
            row["status"] != "parked" or row["capability"] not in explicit_only for row in rows
        ):
            raise DecisionSetRefused("resource")
        digests = []
        for effect_id in effect_ids:
            row = effects[effect_id]
            digests.append(
                item_digest(
                    effect_id,
                    row["capability"],
                    row["args_digest"],
                    row["recipient_entity"],
                    row["recipient_channel"],
                    explicit_only[row["capability"]],
                )
            )
        epoch = await self.connection.fetchval(
            "SELECT lead_epoch FROM switchboard.invocations WHERE goal_id = $1 AND id = $2",
            goal_id,
            invocation_id,
        )
        if epoch is None:
            raise DecisionSetRefused("goal")
        plan_version = goal["plan_version"] or await WaitLedger(self.connection).append_plan(
            goal_id, epoch, [], [], []
        )
        if plan_version is None:
            raise DecisionSetRefused("goal")

        current = await self.open_for_goal(goal_id)
        if current is not None:
            same = (current.epoch, current.plan_version) == (epoch, plan_version) and [
                item.item_digest for item in current.items
            ] == digests
            if same:
                return current
            await self.connection.execute(
                "UPDATE switchboard.decision_set_versions SET state = 'superseded', "
                "updated_at = clock_timestamp() WHERE set_id = $1 AND version = $2",
                current.set_id,
                current.version,
            )
            set_id, version = current.set_id, current.version + 1
        else:
            set_id, version = uuid4(), 1
            await self.connection.execute(
                "INSERT INTO switchboard.decision_sets (id, goal_id) VALUES ($1, $2)",
                set_id,
                goal_id,
            )
        expires_at = await self.connection.fetchval(
            "SELECT deadline FROM switchboard.budgets WHERE goal_id = $1", goal_id
        )
        if expires_at is None:
            raise DecisionSetRefused("goal")
        await self.connection.execute(
            "INSERT INTO switchboard.decision_set_versions (set_id, version, goal_id, epoch, "
            "plan_version, created_by, item_count, digest, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            set_id,
            version,
            goal_id,
            epoch,
            plan_version,
            invocation_id,
            len(effect_ids),
            set_digest(set_id, version, goal_id, epoch, plan_version, digests),
            expires_at,
        )
        await self.connection.executemany(
            "INSERT INTO switchboard.decision_set_items (set_id, set_version, position, goal_id, "
            "effect_id, capability, args_digest, recipient_entity, recipient_channel, "
            "explicit_only, item_digest) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
            [
                (
                    set_id,
                    version,
                    position,
                    goal_id,
                    effect_id,
                    effects[effect_id]["capability"],
                    effects[effect_id]["args_digest"],
                    effects[effect_id]["recipient_entity"],
                    effects[effect_id]["recipient_channel"],
                    explicit_only[effects[effect_id]["capability"]],
                    digest,
                )
                for position, (effect_id, digest) in enumerate(
                    zip(effect_ids, digests, strict=True)
                )
            ],
        )
        created = await self.read(set_id, version)
        if created is None:  # inserted above in this transaction
            raise RuntimeError("decision set vanished after insert")
        return created
