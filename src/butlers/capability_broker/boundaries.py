"""SQL for the M7 typed boundaries (sw_058), inside the broker's transaction.

Each method runs on a ``BrokerLedger`` connection that already holds, or first
takes, the goal row lock, so a handle read, an extraction, a return or an
await serialises with the goal's calls and its Stop like any other call.
Labels only join (sw_057's single-statement union); nothing here narrows one.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol
from uuid import UUID, uuid4

from butlers.core.capability_json import canonical_bytes
from butlers.core.goal_labels import Label

from .contracts import BrokerRefused, Principal, Reason
from .ledger import BrokerLedger
from .typed import TypedSchema, parse_schema


class Extractor(Protocol):
    """The quarantined extraction model: content and a closed schema in, a value out.

    It sees no goal, grant, tool or caller context, and its output is only a
    candidate: the broker validates it exactly against the schema and never
    lets it grant, approve or instruct anything.
    """

    #: Stable model identity, part of the cache key.
    identity: str

    async def extract(self, content: object, schema: dict[str, Any]) -> object: ...


def content_bytes(value: object) -> bytes:
    """Canonical bytes of quarantined content (raises ``InvalidArguments``)."""
    return canonical_bytes({"content": value})


def extraction_key(content_digest: str, label: Label, schema: TypedSchema, extractor: str) -> str:
    """Source content, source label, schema and model identity; never the caller."""
    return hashlib.sha256(
        canonical_bytes(
            {
                "v": 1,
                "content": content_digest,
                "label": label.canonical(),
                "schema": schema.digest,
                "extractor": extractor,
            }
        )
    ).hexdigest()


def _label(row) -> Label:
    return Label.from_row(
        row["label_version"], row["label_instr"], row["label_data"], row["label_sens"]
    )


class BoundaryLedger:
    def __init__(self, ledger: BrokerLedger):
        self.ledger = ledger
        self.connection = ledger.connection

    async def _locked(self, goal_id: UUID) -> None:
        goal = await self.ledger._lock_goal(goal_id)
        if goal is None or goal["status"] != "running":
            raise BrokerRefused(Reason.GOAL)

    async def charge(self, principal: Principal) -> BrokerRefused | None:
        """One goal-tool call against the D10 budget; exhaustion is *returned*."""
        return await self.ledger.charge_call(principal)

    async def join(self, principal: Principal, label: Label, cause: str) -> None:
        if not await self.ledger._join(principal.goal_id, principal.invocation_id, label, cause):
            raise BrokerRefused(Reason.UNAVAILABLE)

    # -- handles ------------------------------------------------------------------

    async def quarantine(
        self, principal: Principal, effect_id: UUID | None, label: Label, raw: bytes
    ) -> UUID:
        """Hold a result the reader does not yet cover; the goal is locked."""
        handle_id = uuid4()
        version, instr, data, sens = label.to_row()
        await self.connection.execute(
            "INSERT INTO switchboard.goal_handles (id, goal_id, invocation_id, effect_id, "
            "content, content_digest, label_version, label_instr, label_data, label_sens) "
            "VALUES ($1, $2, $3, $4, ($5::text)::jsonb, $6, $7, $8, $9, $10)",
            handle_id,
            principal.goal_id,
            principal.invocation_id,
            effect_id,
            raw.decode(),
            hashlib.sha256(raw).hexdigest(),
            version,
            instr,
            data,
            sens,
        )
        return handle_id

    async def handle(self, principal: Principal, handle_id: Any) -> tuple[object, str, Label]:
        """A handle of the caller's own goal: (content, digest, label).

        A handle of another goal, a malformed id or an unreadable label is the
        same refusal, so a handle id never probes another goal's store.
        """
        await self._locked(principal.goal_id)
        try:
            identity = UUID(handle_id) if type(handle_id) is str else None
        except ValueError:
            identity = None
        row = None
        if identity is not None and str(identity) == handle_id:
            row = await self.connection.fetchrow(
                "SELECT content::text AS content, content_digest, label_version, label_instr, "
                "label_data, label_sens FROM switchboard.goal_handles "
                "WHERE goal_id = $1 AND id = $2",
                principal.goal_id,
                identity,
            )
        if row is None:
            raise BrokerRefused(Reason.RESOURCE)
        try:
            label = _label(row)
        except ValueError:
            raise BrokerRefused(Reason.UNAVAILABLE) from None
        return json.loads(row["content"])["content"], row["content_digest"], label

    async def goal_handles(self, goal_id: UUID, refs: frozenset[UUID]) -> bool:
        """Whether every handle reference names a handle of this goal."""
        if not refs:
            return True
        count = await self.connection.fetchval(
            "SELECT count(*) FROM switchboard.goal_handles WHERE goal_id = $1 "
            "AND id = ANY($2::uuid[])",
            goal_id,
            sorted(refs),
        )
        return count == len(refs)

    # -- extraction cache -----------------------------------------------------------

    async def cached(self, key: str) -> tuple[dict[str, Any], Label] | None:
        row = await self.connection.fetchrow(
            "SELECT value::text AS value, label_version, label_instr, label_data, label_sens "
            "FROM switchboard.extraction_cache WHERE key = $1",
            key,
        )
        if row is None:
            return None
        try:
            return json.loads(row["value"]), _label(row)
        except ValueError:
            raise BrokerRefused(Reason.UNAVAILABLE) from None

    async def remember(
        self, key: str, schema: TypedSchema, extractor: str, value: dict, label: Label
    ) -> tuple[dict[str, Any], Label]:
        """Insert once; a concurrent writer's identical-key entry wins unchanged."""
        version, instr, data, sens = label.to_row()
        await self.connection.execute(
            "INSERT INTO switchboard.extraction_cache (key, schema_digest, extractor, value, "
            "label_version, label_instr, label_data, label_sens) "
            "VALUES ($1, $2, $3, ($4::text)::jsonb, $5, $6, $7, $8) ON CONFLICT (key) DO NOTHING",
            key,
            schema.digest,
            extractor,
            canonical_bytes(value).decode(),
            version,
            instr,
            data,
            sens,
        )
        stored = await self.cached(key)
        if stored is None:
            raise BrokerRefused(Reason.UNAVAILABLE)
        return stored

    # -- child results ----------------------------------------------------------------

    async def fix_result_schema(self, goal_id: UUID, child_id: UUID, schema: TypedSchema) -> None:
        """Runner-private, at child launch: the brief's result schema, once."""
        await self._locked(goal_id)
        row = await self.connection.fetchrow(
            "SELECT depth, status FROM switchboard.invocations WHERE goal_id = $1 AND id = $2",
            goal_id,
            child_id,
        )
        if row is None or row["depth"] != 1 or row["status"] != "launching":
            raise BrokerRefused(Reason.AUTHENTICATION)
        inserted = await self.connection.fetchval(
            "INSERT INTO switchboard.goal_child_results "
            "(goal_id, invocation_id, result_schema, schema_digest) "
            "VALUES ($1, $2, ($3::text)::jsonb, $4) ON CONFLICT DO NOTHING RETURNING true",
            goal_id,
            child_id,
            canonical_bytes(schema.document).decode(),
            schema.digest,
        )
        if inserted is not True:
            raise BrokerRefused(Reason.POLICY)

    async def result_schema(self, principal: Principal) -> TypedSchema:
        row = await self.connection.fetchrow(
            "SELECT result_schema::text AS result_schema, schema_digest, result IS NOT NULL "
            "AS returned FROM switchboard.goal_child_results "
            "WHERE goal_id = $1 AND invocation_id = $2",
            principal.goal_id,
            principal.invocation_id,
        )
        if row is None or row["returned"]:
            # No fixed schema: there is no closed shape to accept, so nothing is.
            raise BrokerRefused(Reason.POLICY)
        schema = parse_schema(json.loads(row["result_schema"]))
        if schema.digest != row["schema_digest"]:
            raise BrokerRefused(Reason.UNAVAILABLE)
        return schema

    async def record_return(self, principal: Principal, value: dict) -> None:
        """Store the validated result and end the child in one transaction."""
        stored = await self.connection.fetchval(
            "UPDATE switchboard.goal_child_results SET result = ($3::text)::jsonb, "
            "returned_at = clock_timestamp() WHERE goal_id = $1 AND invocation_id = $2 "
            "AND result IS NULL RETURNING true",
            principal.goal_id,
            principal.invocation_id,
            canonical_bytes(value).decode(),
        )
        ended = await self.connection.fetchval(
            "UPDATE switchboard.invocations SET status = 'returned', ended_at = clock_timestamp(), "
            "updated_at = now() WHERE goal_id = $1 AND id = $2 AND status = 'running' "
            "RETURNING true",
            principal.goal_id,
            principal.invocation_id,
        )
        if stored is not True or ended is not True:
            raise BrokerRefused(Reason.UNAVAILABLE)

    async def child_outcome(
        self, principal: Principal, child_id: Any
    ) -> tuple[str, dict | None, Label, list[dict[str, str]]]:
        """(status, result, child label, effect statuses) of the lead's own child."""
        await self._locked(principal.goal_id)
        try:
            identity = UUID(child_id) if type(child_id) is str else None
        except ValueError:
            identity = None
        row = None
        if identity is not None and str(identity) == child_id:
            row = await self.connection.fetchrow(
                "SELECT i.status, i.label_version, i.label_instr, i.label_data, i.label_sens, "
                "r.result::text AS result FROM switchboard.invocations i "
                "LEFT JOIN switchboard.goal_child_results r "
                "ON (r.goal_id, r.invocation_id) = (i.goal_id, i.id) "
                # Any child of the goal, not only the caller's own: children of a
                # replaced lead report to its successor (D14). The caller is an
                # authenticated current-epoch lead (``goal_await``).
                "WHERE i.goal_id = $1 AND i.id = $2 AND i.depth = 1",
                principal.goal_id,
                identity,
            )
        if row is None:
            raise BrokerRefused(Reason.RESOURCE)
        try:
            label = _label(row)
        except ValueError:
            raise BrokerRefused(Reason.UNAVAILABLE) from None
        effects = await self.connection.fetch(
            "SELECT id, capability, status FROM switchboard.effects "
            "WHERE goal_id = $1 AND invocation_id = $2 ORDER BY created_at, id",
            principal.goal_id,
            identity,
        )
        result = json.loads(row["result"]) if row["result"] is not None else None
        statuses = [
            {"effect_id": str(e["id"]), "capability": e["capability"], "status": e["status"]}
            for e in effects
        ]
        return row["status"], result, label, statuses
