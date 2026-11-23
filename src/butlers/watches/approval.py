"""The owner-watch approval card on the approvals spine (REQ-owner-watches-003; dark).

A model proposes a watch card; nothing is installed. ``park_watch_card`` parks one
approval-gated pending action for ``owner_watch_apply_card`` whose call carries the card's
canonical JSON and digest, so the decision-loop push shows the owner exactly the card that
will be applied. Approve replays the call; Reject or expiry does nothing.

* The action id is derived from the digest, so a replayed proposal finds the existing action.
* The park writes the canonical card JSON to the Switchboard ``state`` table in the same
  transaction. The apply refuses any call that is not byte-for-byte that snapshot and whose
  digest is not the digest of its own body, so only a card Switchboard parked can change a
  watch.
* The apply is the owner's approval (``OwnerApproval.origin == "owner"``): the approvals gate
  parks every other invocation of the tool.
* Applying needs a ``WatchService`` and a snapshot provider (``butlers.watches.providers``,
  resolved only while ``OWNER_WATCHES_LIVE`` is on). Without them, or when the live grant source
  cannot be read, the apply refuses with ``owner_watches_unavailable`` and changes nothing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid5

from butlers.conditions import Snapshot
from butlers.conditions import predicate as ast
from butlers.core.delivery_rendering import defang_delivery_text
from butlers.modules.approvals.command_contracts import OWNER_WATCH_APPLY_COMMAND
from butlers.modules.approvals.park import park_pending_action
from butlers.modules.approvals.rollout import read_approval_delivery_rollout
from butlers.perception.model import canonical
from butlers.watches.model import OWNER, Action, OwnerApproval, WatchCard, WatchRefused
from butlers.watches.service import WatchService

TOOL_NAME: Final = OWNER_WATCH_APPLY_COMMAND.name
SNAPSHOT_KEY_PREFIX: Final = "owner_watch:card:"
DEDUP_KEY_PREFIX: Final = "switchboard.owner_watch.card:"
#: Matches Switchboard's ``default_expiry_hours``; expiry changes nothing.
CARD_TTL: Final = timedelta(hours=72)
_ACTION_NAMESPACE: Final = UUID("6f1d2a52-3c1e-4f43-9a55-0b5e6c1d7a02")

ServiceFor = Callable[[], WatchService | None]
Prepare = Callable[[datetime], Awaitable[bool]]
SnapshotFor = Callable[[datetime], Snapshot | None]

_VERBS: Final = {
    Action.CREATE: "Start watching",
    Action.UPDATE: "Change a watch",
    Action.RENEW: "Renew a watch for another 90 days",
    Action.DELETE: "Delete a watch",
}


def _no_service() -> WatchService | None:
    return None


def _no_snapshot(now: datetime) -> Snapshot | None:
    return None


async def _prepared(now: datetime) -> bool:
    return True


@dataclass(frozen=True)
class Providers:
    """What an apply and a tick need from production. All default to absent or no-op.

    ``service_for`` and ``snapshot_for`` default to absent, which refuses the apply and blinds the
    tick. ``prepare`` runs first (the production providers re-read the live grant source there);
    False means the sources are unreadable, which is the same as absent.
    """

    service_for: ServiceFor = _no_service
    snapshot_for: SnapshotFor = _no_snapshot
    prepare: Prepare = _prepared


PROVIDERS = Providers()


def card_action_id(digest: str) -> UUID:
    """The one pending-action id a card digest can ever have."""
    return uuid5(_ACTION_NAMESPACE, f"{DEDUP_KEY_PREFIX}{digest}")


def card_args(card: WatchCard) -> dict[str, Any]:
    """The exact parked call: the canonical card and its digest, and nothing else."""
    return OWNER_WATCH_APPLY_COMMAND.materialize(
        {"card_json": canonical(card.body), "digest": card.digest}
    )


_BARE = re.compile(r"[a-z0-9][a-z0-9_:\-]{0,127}")


def _literal(key: str, value: Any) -> str:
    """One value as the owner reads it on the card.

    A registered name or id (lowercase, no spaces) is bare. Everything else, and every
    ``value`` field whatever it holds, is a quoted, escaped literal with its URL and address
    separators neutralised, so text a model or a source supplied cannot read as card prose.
    """
    if key != "value" and type(value) is str and _BARE.fullmatch(value):
        return value
    return defang_delivery_text(json.dumps(value, ensure_ascii=True))


def _leaf_line(leaf: ast.Leaf) -> str:
    fields = ", ".join(
        f"{key}={_literal(key, leaf.body[key])}" for key in sorted(leaf.body) if key != "tag"
    )
    return f"{leaf.tag}: {fields}" if fields else leaf.tag


def card_lines(card: WatchCard) -> list[str]:
    """What the owner reads. Ids and closed vocabulary only; no model prose, no message text."""
    lines = [f"{_VERBS[card.action]} ({_literal('watch_id', card.watch_id[:8])})"]
    if card.action in (Action.CREATE, Action.UPDATE):
        root = card.predicate.get("tag")
        lines.append(
            f"Notifies when {'all' if root == 'all' else 'any' if root == 'any' else 'this'}:"
        )
        lines.extend(f"  {_leaf_line(leaf)}" for leaf in ast.leaves(card.predicate))
    lines.append(f"Delivery: {card.delivery}; it tells only you.")
    if card.categories:
        lines.append(f"Sensitivity categories: {', '.join(card.categories)}")
    lines.append(f"Reads: {', '.join(_literal('source', source) for source in card.sources)}")
    if card.lifetime is not None:
        lines.append(f"Lifetime: {card.lifetime.days} days from approval")
    elif card.expires_at is not None:
        lines.append(f"Expires: {card.expires_at.isoformat()}")
    if card.resume:
        lines.append("Starts watching again.")
    lines.append(f"Card {card.digest[:12]}, version {card.target_version}")
    return lines


def card_dossier(card: WatchCard) -> dict[str, Any]:
    """Server-written approval context."""
    destructive = card.action is Action.DELETE
    return {
        "agent_summary": _VERBS[card.action][:300],
        "why": " ".join(card_lines(card))[:2000],
        "evidence": [{"type": "watch", "ref": card.watch_id, "note": "Owner watch."}],
        "blast_radius": "self",
        "reversibility": "irreversible" if destructive else "reversible",
    }


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value


async def _put_state(conn: Any, key: str, value: dict[str, Any]) -> None:
    await conn.execute(
        """
        INSERT INTO state (key, value, updated_at, version)
        VALUES ($1, $2, now(), 1)
        ON CONFLICT (key) DO NOTHING
        """,
        key,
        value,
    )


async def park_watch_card(pool: Any, card: WatchCard, *, now: datetime | None = None) -> bool:
    """Park the one approval card for a proposed watch change.

    Returns True only when this call parked a card the delivery worker will push. A duplicate
    park, or a disabled rollout, returns False: the pending action is on the dashboard.
    """
    args = card_args(card)
    moment = now or datetime.now(UTC)
    async with pool.acquire() as conn, conn.transaction():
        await _put_state(conn, f"{SNAPSHOT_KEY_PREFIX}{card.digest}", dict(args))
        admission = await park_pending_action(
            conn,
            action_id=card_action_id(card.digest),
            tool_name=TOOL_NAME,
            tool_args=args,
            requested_at=moment,
            expires_at=moment + CARD_TTL,
            origin_butler="switchboard",
            deduplication_key=f"{DEDUP_KEY_PREFIX}{card.digest}",
            **card_dossier(card),
        )
        rollout = await read_approval_delivery_rollout(conn)
    return not admission.duplicate and admission.intent_id is not None and rollout.worker_enabled


async def sweep_card_state(pool: Any, *, now: datetime | None = None) -> int:
    """Delete the parked-card snapshots whose approval window has long passed.

    A snapshot outlives its card by one more window, so a retried approval still finds it; after
    that nothing can apply that card and the row is only clutter. Returns the rows removed.
    """
    cutoff = (now or datetime.now(UTC)) - 2 * CARD_TTL
    status = await pool.execute(
        "DELETE FROM state WHERE left(key, $2) = $1 AND updated_at < $3",
        SNAPSHOT_KEY_PREFIX,
        len(SNAPSHOT_KEY_PREFIX),
        cutoff,
    )
    return int(str(status).rsplit(" ", 1)[-1]) if status else 0


async def apply_watch_card(
    pool: Any,
    *,
    card_json: str,
    digest: str,
    service_for: ServiceFor,
    snapshot_for: SnapshotFor,
    prepare: Prepare = _prepared,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run the approved card: apply exactly the card Switchboard parked, as the owner.

    Idempotent: ``WatchService.approve`` replays an applied card. Refuses (with an ``error``,
    so the executor records a failure) a call Switchboard did not park, a digest that is not
    its body's, and a card the live grant or the watch's version no longer matches.
    """
    try:
        body = json.loads(card_json)
        card = WatchCard.from_body(body)
    except (TypeError, ValueError):
        return {"error": "watch_card_invalid"}
    if card.digest != digest or canonical(card.body) != card_json:
        return {"error": "watch_card_digest_mismatch"}
    parked = await pool.fetchrow(
        "SELECT value FROM state WHERE key = $1", f"{SNAPSHOT_KEY_PREFIX}{digest}"
    )
    stored = None if parked is None else _json(parked["value"])
    if stored != {"card_json": card_json, "digest": digest}:
        return {"error": "watch_card_unknown"}
    service, moment = service_for(), now or datetime.now(UTC)
    if service is not None and not await prepare(moment):
        return {"error": "owner_watches_unavailable"}
    snapshot = snapshot_for(moment) if service is not None else None
    if service is None or snapshot is None:
        return {"error": "owner_watches_unavailable"}
    try:
        watch = await service.approve(card, OwnerApproval(digest, OWNER, moment), snapshot)
    except WatchRefused as exc:
        return {"error": f"watch_card_refused: {exc}"}
    return {"status": "applied", "watch_id": watch.id, "state": str(watch.state)}


__all__ = ["apply_watch_card", "card_lines", "park_watch_card", "sweep_card_state"]
