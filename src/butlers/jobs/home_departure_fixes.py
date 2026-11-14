"""Home departure fixes: one exact, owner-approved fix per departure-card item (bu-jv1moa.115).

REQ-home-deterministic-jobs-008 and -009. The owner declares, on the dashboard, the single Home
Assistant call that would fix one watched item (``home.departure_fixes``). When the departure card
is queued, :func:`park_fixes` parks one ``origin='prepared'`` approval per listed, fixable item.
It never calls Home Assistant: the approvals executor replays an *approved* row through the Home
module's ``ha_call_service``, which consults :func:`replay_refusal` first.

Hard rules:

* A fix is the owner's declaration. Nothing here composes a call, and there is no "secure all".
* Every fix waits for an owner approval, whatever its RFC 0028 risk class (decided D5).
* The away premise is bound to the *originating leave edge* (the edge id in the dedup key): it
  holds only while that edge is the latest confirmed, unretracted ``owner.place`` edge. A reading
  of "away" after a return and a new leave does not revive it. An unreadable premise never
  dispatches and never rejects.
* A still-pending fix is withdrawn by a system rejection (:data:`CANCEL_ACTOR`) with one of four
  content-blind reasons. A receipt is never edited, and no text says "undone".

Dark: :data:`DEPARTURE_FIX_LIVE` is off and no roster schedules anything that parks or cancels.
The replay gate is not behind the flag: an approved fix row can only exist if something parked it,
and the gate only ever refuses.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal
from uuid import UUID

from butlers.jobs.home_prepared_status import CANCEL_ACTOR, prepared_action_status

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

#: Off by default; activation turns it on with the departure card and the presence reducer.
DEPARTURE_FIX_LIVE = False

FIX_PREFIX: Final = "home:departure-fix:"
#: How long a parked fix may wait for an approval before the approvals sweep expires it.
FIX_LIFETIME = timedelta(hours=6)
_NAMESPACE = uuid.UUID("5b6b7b60-6f55-4c43-9d77-6ad1f6e4c115")
_ENTITY_RE = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
_DATA_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MARKDOWN_CHARS = re.compile(r"[*_`\[\]\x00-\x1f\x7f]")

Premise = Literal["holds", "owner_returned", "premise_retracted", "unknown"]
CancelReason = Literal["owner_returned", "premise_retracted", "already_secure", "watch_removed"]
#: Why a declaration was refused; the route answers with this code and nothing else.
Refusal = Literal["not_watched", "not_explicit_entity", "unclassified", "data_not_allowed"]

#: The pairs a fix may use: RFC 0028-classified, never ``safe`` (nothing to approve) and with the
#: ``data`` keys each pair may carry. Anything else is refused; there is no wildcard.
_DATA_KEYS: dict[tuple[str, str], frozenset[str]] = {
    ("light", "turn_on"): frozenset(
        {"brightness", "brightness_pct", "color_temp_kelvin", "rgb_color", "transition"}
    ),
    ("light", "turn_off"): frozenset({"transition"}),
    ("climate", "set_temperature"): frozenset({"temperature"}),
}
_DATALESS_PAIRS = frozenset(
    {
        ("switch", "turn_on"),
        ("switch", "turn_off"),
        ("fan", "turn_on"),
        ("fan", "turn_off"),
        ("humidifier", "turn_on"),
        ("humidifier", "turn_off"),
        ("cover", "close_cover"),
        ("cover", "open_cover"),
        ("lock", "lock"),
        ("lock", "unlock"),
        ("scene", "turn_on"),
        ("script", "turn_on"),
    }
)

#: What each resolver status reads as on a card. None of these claims an undone effect.
STATUS_WORDS: Final[dict[str, str]] = {
    "awaiting_approval": "fix waiting for your approval",
    "approved_not_run": "fix approved, not run",
    "succeeded": "fix ran and was checked",
    "unverified": "fix unclear, check the item",
    "failed": "fix did not run",
    "rejected": "fix declined",
    "cancelled": "fix not run",
    "expired": "fix expired, not run",
    "abandoned": "fix abandoned, not run",
    "unknown": "fix status unknown",
}


@dataclass(frozen=True)
class Declaration:
    """One declared fix: the exact call, for one watched entity."""

    entity_id: str
    domain: str
    service: str
    target_entity_id: str
    data: dict[str, Any]


def fix_key(edge_id: UUID, entity_id: str) -> str:
    return f"{FIX_PREFIX}{edge_id}:{entity_id}"


def parse_fix_key(key: str | None) -> tuple[UUID, str] | None:
    """The leave edge and entity of a fix dedup key, or ``None`` when it is not one."""
    if not key or not key.startswith(FIX_PREFIX):
        return None
    edge, _, entity = key[len(FIX_PREFIX) :].partition(":")
    try:
        return UUID(edge), entity if _ENTITY_RE.fullmatch(entity) else ""
    except ValueError:
        return None


def fix_action_id(key: str) -> UUID:
    return uuid.uuid5(_NAMESPACE, key)


def _risk_module() -> Any:
    from butlers.roster_imports import roster_module

    return roster_module("home", "modules/actuation")


def refusal(
    domain: str,
    service: str,
    target_entity_id: str,
    data: dict[str, Any],
    *,
    watched: bool,
) -> Refusal | None:
    """Why a declaration must be stored as nothing, or ``None`` when it may be stored."""
    if not watched:
        return "not_watched"
    if not _ENTITY_RE.fullmatch(target_entity_id):
        return "not_explicit_entity"
    pair = (domain, service)
    if pair not in _DATA_KEYS and pair not in _DATALESS_PAIRS:
        return "unclassified"
    if target_entity_id.split(".", 1)[0] != domain:
        return "not_explicit_entity"
    allowed = _DATA_KEYS.get(pair, frozenset())
    if any(not _DATA_KEY_RE.fullmatch(k) or k not in allowed for k in data):
        return "data_not_allowed"
    mod = _risk_module()
    snapshot_target, snapshot_data = mod.snapshot_actuation_arguments(
        {"entity_id": target_entity_id}, data
    )
    del snapshot_target
    if mod.DATA_TARGET_SELECTORS.intersection(snapshot_data or {}):
        return "data_not_allowed"
    return None


def risk_class(declaration: Declaration) -> str:
    """The RFC 0028 effective class the stored call would have; it never waives approval."""
    mod = _risk_module()
    target, data = mod.snapshot_actuation_arguments(
        {"entity_id": declaration.target_entity_id}, declaration.data
    )
    return str(
        mod.effective_actuation_risk(declaration.domain, declaration.service, target, data).risk
    )


def _clean(text: str, limit: int = 80) -> str:
    return _MARKDOWN_CHARS.sub("", text).strip()[:limit]


async def load_declarations(pool: asyncpg.Pool) -> dict[str, Declaration]:
    rows = await pool.fetch(
        "SELECT entity_id, domain, service, target_entity_id, data FROM departure_fixes"
    )
    out: dict[str, Declaration] = {}
    for r in rows:
        data = r["data"]
        if isinstance(data, str):
            data = json.loads(data)
        out[r["entity_id"]] = Declaration(
            r["entity_id"], r["domain"], r["service"], r["target_entity_id"], dict(data)
        )
    return out


def tool_args(declaration: Declaration) -> dict[str, Any]:
    """The exact call stored on the prepared action: what the executor replays, unchanged."""
    return {
        "domain": declaration.domain,
        "service": declaration.service,
        "target": {"entity_id": declaration.target_entity_id},
        "data": dict(declaration.data),
    }


def fixable_entities(line_keys: Collection[str]) -> set[str]:
    """Entities a departure card listed, from its line keys (``entity=state``)."""
    out: set[str] = set()
    for key in line_keys:
        entity, sep, _state = key.partition("=")
        if sep and _ENTITY_RE.fullmatch(entity):
            out.add(entity)
    return out


async def park_fixes(
    pool: asyncpg.Pool,
    leave_id: UUID,
    listed: Collection[str],
    labels: dict[str, str],
    *,
    now: datetime,
) -> int:
    """Park one prepared action per listed item that has a declared fix; return how many are new."""
    from butlers.modules.approvals.park import park_prepared_action

    declarations = await load_declarations(pool)
    parked = 0
    for entity in sorted(set(listed) & declarations.keys()):
        declaration = declarations[entity]
        key = fix_key(leave_id, entity)
        action_id = fix_action_id(key)
        label = _clean(labels.get(entity, "")) or "Watched item"
        admission = await park_prepared_action(
            pool,
            action_id=action_id,
            tool_name="ha_call_service",
            tool_args=tool_args(declaration),
            agent_summary=f"Departure fix: {label}",
            requested_at=now,
            expires_at=now + FIX_LIFETIME,
            why=f"You left with {label} not secure; approve to run the fix you declared.",
            blast_radius="self",
            reversibility="irreversible"
            if risk_class(declaration) == "protected"
            else "compensable",
            origin_butler="home",
            deduplication_key=key,
        )
        parked += int(not admission.duplicate)
    return parked


# ---------------------------------------------------------------------------
# The away premise, bound to the originating leave edge
# ---------------------------------------------------------------------------

ReadState = Callable[..., Awaitable[Any]]
ReadSubject = Callable[[Any], Awaitable[UUID | None]]


def premise_of(edges: Sequence[Any], leave_id: UUID) -> Premise:
    """``holds`` only while ``leave_id`` is still the latest confirmed, unretracted edge.

    ``edges`` come from the presence timeline, retracted ones included. A leave that is not in
    view reads ``unknown``: neither dispatch nor rejection follows from it.
    """
    origin = next((e for e in edges if e.id == leave_id), None)
    if origin is None:
        return "unknown"
    if origin.retracted or origin.late:
        return "premise_retracted"
    for edge in edges:
        if (
            edge.id != leave_id
            and not edge.retracted
            and not edge.late
            and edge.confirmed_at > origin.confirmed_at
        ):
            return "owner_returned"
    return "holds"


async def away_premise(
    pool: asyncpg.Pool,
    leave_id: UUID,
    *,
    now: datetime,
    read_state: ReadState | None = None,
    read_subject: ReadSubject | None = None,
) -> Premise:
    """Re-read the presence timeline; any failure to read is ``unknown``, never ``holds``."""
    import asyncpg as _asyncpg

    from butlers.jobs.home_arrival import _owner_subject
    from butlers.jobs.home_departure import read_presence_state

    try:
        subject = await (read_subject or _owner_subject)(pool)
        if subject is None:
            return "unknown"
        state = await (read_state or read_presence_state)(now, subject=subject, wanted=[leave_id])
    except (_asyncpg.PostgresError, _asyncpg.InterfaceError, OSError):
        logger.warning("home departure fix: presence read failed")
        return "unknown"
    if state is None:
        return "unknown"
    return premise_of(state.edges, leave_id)


async def replay_refusal(
    pool: asyncpg.Pool,
    action_id: UUID,
    *,
    now: datetime,
    read_state: ReadState | None = None,
    read_subject: ReadSubject | None = None,
) -> str | None:
    """Why an approved replay must dispatch nothing, or ``None`` when it may proceed.

    Rows that are not departure fixes proceed untouched. A fix row whose key cannot be read, or
    whose premise has ended or cannot be read, is refused: for actuation unknown fails closed.
    """
    key = await pool.fetchval(
        "SELECT deduplication_key FROM pending_actions WHERE id = $1", action_id
    )
    if not isinstance(key, str) or not key.startswith(FIX_PREFIX):
        return None
    parsed = parse_fix_key(key)
    if parsed is None or not parsed[1]:
        return "fix_key_unreadable"
    premise = await away_premise(
        pool, parsed[0], now=now, read_state=read_state, read_subject=read_subject
    )
    return None if premise == "holds" else f"premise_{premise}"


# ---------------------------------------------------------------------------
# Cancellation of still-pending fixes
# ---------------------------------------------------------------------------


async def _still_not_secure(pool: asyncpg.Pool, entity_id: str, now: datetime) -> bool | None:
    """Whether the item is still listed on the card; ``None`` when Home cannot see."""
    from butlers.jobs.home_departure import WatchItem, _current_lines

    row = await pool.fetchrow(
        "SELECT entity_id, owner_label, secure_states FROM departure_watch WHERE entity_id = $1",
        entity_id,
    )
    if row is None:
        return None
    item = WatchItem(
        row["entity_id"], row["owner_label"], tuple(s.lower() for s in row["secure_states"])
    )
    lines = await _current_lines(pool, [item], now)
    if any(key.startswith("ha=") or key.endswith("=unreadable") for key, _ in lines):
        return None
    return bool(lines)


async def cancel_fixes(
    pool: asyncpg.Pool,
    *,
    now: datetime,
    read_state: ReadState | None = None,
    read_subject: ReadSubject | None = None,
) -> dict[str, int]:
    """Reject each pending fix whose premise ended, whose item is secure or whose fix is gone.

    A premise that cannot be read leaves the row pending. Approved, executed and settled rows
    are never touched here, and no receipt is ever edited.
    """
    from butlers.modules.approvals.delivery_lifecycle import transition_pending_action
    from butlers.modules.approvals.models import ActionStatus

    rows = await pool.fetch(
        "SELECT id, tool_name, deduplication_key FROM pending_actions "
        "WHERE status = 'pending' AND origin = 'prepared' AND deduplication_key LIKE $1",
        f"{FIX_PREFIX}%",
    )
    counts = {"rejected": 0, "held": 0}
    declared = await load_declarations(pool)
    premises: dict[UUID, Premise] = {}
    for row in rows:
        parsed = parse_fix_key(row["deduplication_key"])
        if parsed is None or not parsed[1]:
            continue
        edge_id, entity = parsed
        if edge_id not in premises:
            premises[edge_id] = await away_premise(
                pool, edge_id, now=now, read_state=read_state, read_subject=read_subject
            )
        premise = premises[edge_id]
        reason: CancelReason | None = None
        if premise == "unknown":
            counts["held"] += 1
            continue
        if premise != "holds":
            reason = premise
        elif entity not in declared:
            reason = "watch_removed"
        elif await _still_not_secure(pool, entity, now) is False:
            reason = "already_secure"
        if reason is None:
            continue
        await transition_pending_action(
            pool,
            action_id=row["id"],
            target_status=ActionStatus.REJECTED,
            decided_by=CANCEL_ACTOR,
            event_actor=CANCEL_ACTOR,
            event_reason=reason,
            event_metadata={"tool_name": row["tool_name"]},
            now=now,
        )
        counts["rejected"] += 1
    return counts


# ---------------------------------------------------------------------------
# Card text
# ---------------------------------------------------------------------------

CardLine = tuple[str, str]


async def fix_lines(pool: asyncpg.Pool, leave_id: UUID) -> tuple[CardLine, ...]:
    """One line per fix of this leave edge, from the resolver's status only (arrival card)."""
    rows = await pool.fetch(
        "SELECT p.id, p.deduplication_key, w.owner_label FROM pending_actions p "
        "LEFT JOIN departure_watch w ON w.entity_id = split_part(p.deduplication_key, ':', 4) "
        "WHERE p.origin = 'prepared' AND p.deduplication_key LIKE $1 "
        "ORDER BY p.deduplication_key",
        f"{FIX_PREFIX}{leave_id}:%",
    )
    lines: list[CardLine] = []
    for row in rows:
        parsed = parse_fix_key(row["deduplication_key"])
        if parsed is None or not parsed[1]:
            continue
        status = await prepared_action_status(pool, row["id"])
        word = STATUS_WORDS.get(status, STATUS_WORDS["unknown"])
        label = _clean(row["owner_label"] or "") or "Watched item"
        lines.append((f"fix:{parsed[1]}={status}", f"{label}: {word}"))
    return tuple(lines)


def fix_hint(declaration: Declaration | None) -> str:
    """The suffix a departure-card line gets when its item has a declared fix."""
    if declaration is None:
        return ""
    if risk_class(declaration) == "protected":
        return " (a protected fix is waiting for your approval on the dashboard)"
    return " (a fix is waiting for your approval on the dashboard)"
