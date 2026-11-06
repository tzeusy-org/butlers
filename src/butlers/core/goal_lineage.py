"""Verified goal lineage: the ambient context of one broker-forwarded call (RFC 0038 D7).

A domain's assertion verifier (``butlers.capability_broker.verification``)
binds a ``VerifiedLineage`` around exactly one ``tools/call`` after checking the
broker's signed assertion against the raw wire arguments. Handlers and the
stores they write read lineage only from here:

* RFC 0028's executor binding and RFC 0021's approval replay
  (``modules.approvals.execution_context``) treat a lineage whose
  ``approval_ref`` covers the exact call as the approval;
* memory authority stamping derives authority from ``origin``/``exposed``
  instead of the spawner-derived routing context;
* every D6 store stamps ``taint_goal_id``/``taint_invocation_id`` from
  ``taint_stamp()`` when the call is exposed, and its readers report those
  stamps back to the broker (``core.stamp_account``);
* domain events and audit writes read ``event_lineage()``.

When the process enforces verification (the default-off switch), a
model-reachable call with no lineage bound for that tool is refused by the
core tool wrapper before its handler runs.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from butlers.core.capability_json import arguments_digest

#: Refusal returned by the core tool wrapper when enforcement finds no lineage.
UNVERIFIED_CALL_REFUSAL = {
    "status": "refused",
    "reason": "authentication",
    "retryable": False,
}


@dataclass(frozen=True, slots=True)
class CarriedSource:
    """The stamp and authority of a row whose text a new row keeps."""

    goal_id: uuid.UUID | None
    invocation_id: uuid.UUID | None
    content_authority: str | None
    authority_entity_id: uuid.UUID | None


_carried: contextvars.ContextVar[CarriedSource | None] = contextvars.ContextVar(
    "d6_carried_source", default=None
)


@dataclass(frozen=True, slots=True)
class VerifiedLineage:
    """The verified D7 assertion fields for one call."""

    goal_id: uuid.UUID
    invocation_id: uuid.UUID
    depth: int
    origin: str
    exposed: bool
    capability: str
    args_digest: str
    call_id: uuid.UUID
    effect_id: uuid.UUID | None = None
    approval_ref: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> VerifiedLineage:
        effect = payload.get("effect_id")
        return cls(
            goal_id=uuid.UUID(payload["goal_id"]),
            invocation_id=uuid.UUID(payload["invocation_id"]),
            depth=payload["depth"],
            origin=payload["origin"],
            exposed=payload["exposed"],
            capability=payload["capability"],
            args_digest=payload["args_digest"],
            call_id=uuid.UUID(payload["call_id"]),
            effect_id=uuid.UUID(effect) if effect else None,
            approval_ref=payload.get("approval_ref"),
        )

    def covers(self, tool_name: str, tool_args: dict[str, Any] | None) -> bool:
        """True when this lineage was verified for exactly this tool and arguments."""
        if tool_name != self.capability or tool_args is None:
            return False
        try:
            safe = json.loads(json.dumps(tool_args, default=str))
            return arguments_digest(safe) == self.args_digest
        except (TypeError, ValueError):
            return False

    def approves(self, tool_name: str, tool_args: dict[str, Any] | None) -> bool:
        """True when the verified ``approval_ref`` covers this exact call."""
        return self.approval_ref is not None and self.covers(tool_name, tool_args)


_current: contextvars.ContextVar[VerifiedLineage | None] = contextvars.ContextVar(
    "verified_goal_lineage", default=None
)
# The task the call was verified in. Taint and lineage follow child tasks;
# approval authority does not (the RFC 0021 exact-task binding).
_verified_task: contextvars.ContextVar[asyncio.Task[Any] | None] = contextvars.ContextVar(
    "verified_goal_lineage_task", default=None
)
_enforced = False


def current_lineage() -> VerifiedLineage | None:
    """The lineage bound for the running call, or ``None``."""
    return _current.get()


def approving_lineage(
    tool_name: str | None, tool_args: dict[str, Any] | None
) -> VerifiedLineage | None:
    """The lineage whose ``approval_ref`` covers this exact call in its own task."""
    lineage = _current.get()
    if lineage is None or tool_name is None or not lineage.approves(tool_name, tool_args):
        return None
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return None
    return lineage if task is not None and task is _verified_task.get() else None


@contextmanager
def lineage_scope(lineage: VerifiedLineage | None) -> Iterator[None]:
    """Bind ``lineage`` (or explicitly none) for the duration of one call."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    token = _current.set(lineage)
    task_token = _verified_task.set(task if lineage is not None else None)
    try:
        yield
    finally:
        _verified_task.reset(task_token)
        _current.reset(token)


def set_enforcement(enabled: bool) -> None:
    """Process-wide switch: refuse model-reachable calls without lineage."""
    global _enforced
    _enforced = enabled is True


def enforcement_enabled() -> bool:
    return _enforced


def unverified_call_refusal(tool_name: str) -> dict[str, Any] | None:
    """The wrapper's first step: refuse when enforcing and the call is unverified."""
    if not _enforced:
        return None
    lineage = _current.get()
    if lineage is not None and lineage.capability == tool_name:
        return None
    return dict(UNVERIFIED_CALL_REFUSAL)


def taint_stamp() -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """``(taint_goal_id, taint_invocation_id)`` for a write.

    Set under exposure, or when the write carries forward a stamped row's text.
    """
    lineage = _current.get()
    if lineage is not None and lineage.exposed:
        return lineage.goal_id, lineage.invocation_id
    carried = _carried.get()
    if carried is not None:
        return carried.goal_id, carried.invocation_id
    return None, None


@dataclass(frozen=True, slots=True)
class TaintInsert:
    """The taint part of one INSERT, empty unless the write is exposed.

    Shared ``public`` stores are also built by pinned-revision and stand-in
    schemas that predate core_313, so an unexposed write names no taint column
    and stays byte-identical to the pre-switch statement.
    """

    columns: str = ""
    placeholders: str = ""
    values: tuple[uuid.UUID, ...] = ()


def taint_insert(first_param: int) -> TaintInsert:
    """``, taint_goal_id, taint_invocation_id`` and ``, $n, $n+1`` under exposure only."""
    goal_id, invocation_id = taint_stamp()
    if goal_id is None or invocation_id is None:
        return TaintInsert()
    return TaintInsert(
        ", taint_goal_id, taint_invocation_id",
        f", ${first_param}, ${first_param + 1}",
        (goal_id, invocation_id),
    )


@dataclass(frozen=True, slots=True)
class TaintUpdate:
    """The taint part of one text-writing UPDATE, empty unless the write is exposed."""

    assignments: str = ""
    values: tuple[uuid.UUID, ...] = ()


def taint_update(first_param: int) -> TaintUpdate:
    """``, taint_goal_id = $n, taint_invocation_id = $n+1`` under exposure only.

    The row's text is now this writer's, so it takes the pointer from any earlier
    writer. An unexposed update names neither column and leaves a pointer in place.
    """
    goal_id, invocation_id = taint_stamp()
    if goal_id is None or invocation_id is None:
        return TaintUpdate()
    return TaintUpdate(
        f", taint_goal_id = ${first_param}, taint_invocation_id = ${first_param + 1}",
        (goal_id, invocation_id),
    )


@contextmanager
def carrying_forward(source: Mapping[str, Any]) -> Iterator[None]:
    """Facts written in this block keep text copied from the ``source`` row.

    A successor must not launder its source: an unexposed writer keeps the source's
    writer pointer, and its authority follows ``content_authority.successor_authority``.
    ``source`` must have selected both stamp columns and both authority columns.
    """
    token = _carried.set(
        CarriedSource(
            source["taint_goal_id"],
            source["taint_invocation_id"],
            source["content_authority"],
            source["authority_entity_id"],
        )
    )
    try:
        yield
    finally:
        _carried.reset(token)


def carried_source() -> CarriedSource | None:
    """The source row of the enclosing ``carrying_forward`` block, if any."""
    return _carried.get()


def taint_read(row_ref: str) -> str:
    """Select-list fragment returning the D6 stamp of ``row_ref``'s row, NULL before core_313.

    The same pinned-revision and stand-in schemas are read too, so a reader
    takes the stamp from the whole row (``to_jsonb``) instead of naming the
    columns: a table without them reads null rather than failing.
    """
    return ", ".join(
        f"(to_jsonb({row_ref}) ->> '{column}')::uuid AS {column}"
        for column in ("taint_goal_id", "taint_invocation_id")
    )


def event_lineage() -> dict[str, Any]:
    """Lineage columns for a domain event or audit row written by this call."""
    lineage = _current.get()
    if lineage is None:
        return {
            "goal_id": None,
            "goal_invocation_id": None,
            "goal_effect_id": None,
            "goal_exposed": None,
        }
    return {
        "goal_id": lineage.goal_id,
        "goal_invocation_id": lineage.invocation_id,
        "goal_effect_id": lineage.effect_id,
        "goal_exposed": lineage.exposed,
    }


class ExposedOwnerAuthorityError(PermissionError):
    """An owner-authority write was attempted under an exposed lineage."""


def refuse_owner_authority_under_exposure(authority: str | None) -> None:
    """Exposed content never wears owner authority (D6)."""
    lineage = _current.get()
    if lineage is not None and lineage.exposed and authority in {"owner", "owner_device"}:
        raise ExposedOwnerAuthorityError("owner authority is refused under an exposed goal")


__all__ = [
    "ExposedOwnerAuthorityError",
    "UNVERIFIED_CALL_REFUSAL",
    "VerifiedLineage",
    "approving_lineage",
    "current_lineage",
    "enforcement_enabled",
    "event_lineage",
    "lineage_scope",
    "refuse_owner_authority_under_exposure",
    "set_enforcement",
    "taint_stamp",
    "unverified_call_refusal",
]
