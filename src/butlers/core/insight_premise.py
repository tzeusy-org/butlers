"""Typed premises for proactive insight candidates (bu-q7vx1q.5).

An insight asserts a fact ("a bill is due tomorrow"). A *premise* names that
fact in a form the Switchboard broker can re-check without an LLM:

- ``{"kind": "owner_condition", "source", "fingerprint"}`` -- the fact is "this
  owner-condition episode is still open"; the broker reads the condition
  ledger, and the ledger's ``post_write`` hook amends already-delivered
  candidates when it resolves.
- ``{"kind": "probe", "butler", "probe", "args"}`` -- the fact is answered by a
  registered zero-LLM probe (see ``butlers.tools.switchboard.insight.premises``).

A candidate with no premise (``None``) behaves exactly as it did before this
module existed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

import asyncpg

from butlers.core.condition_ledger import ConditionTransition

logger = logging.getLogger(__name__)

PREMISE_KIND_OWNER_CONDITION = "owner_condition"
PREMISE_KIND_PROBE = "probe"

_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    PREMISE_KIND_OWNER_CONDITION: ("source", "fingerprint"),
    PREMISE_KIND_PROBE: ("butler", "probe"),
}


def owner_condition_premise(source: str, fingerprint: str) -> dict[str, Any]:
    """Premise: the owner-condition episode ``(source, fingerprint)`` is still active."""
    return normalize_premise(
        {"kind": PREMISE_KIND_OWNER_CONDITION, "source": source, "fingerprint": fingerprint}
    )  # type: ignore[return-value]


def probe_premise(butler: str, probe: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Premise: the registered ``butler``/``probe`` answers true for ``args``."""
    return normalize_premise(
        {"kind": PREMISE_KIND_PROBE, "butler": butler, "probe": probe, "args": dict(args or {})}
    )  # type: ignore[return-value]


def normalize_premise(premise: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Validate ``premise`` and return its canonical dict; ``None`` stays ``None``.

    Raises ``ValueError`` for an unknown kind, a missing or non-string required
    key, or probe ``args`` that are not an object. Unknown extra keys are
    dropped so a stored premise only ever carries the typed contract.
    """
    if premise is None:
        return None
    if not isinstance(premise, Mapping):
        raise ValueError("premise must be an object")
    kind = premise.get("kind")
    required = _REQUIRED_KEYS.get(kind) if isinstance(kind, str) else None
    if required is None:
        raise ValueError("premise.kind must be 'owner_condition' or 'probe'")
    out: dict[str, Any] = {"kind": kind}
    for key in required:
        value = premise.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"premise.{key} must be a non-empty string")
        out[key] = value
    if kind == PREMISE_KIND_PROBE:
        args = premise.get("args", {})
        if not isinstance(args, Mapping):
            raise ValueError("premise.args must be an object")
        out["args"] = dict(args)
    return out


async def enqueue_premise_amendments(
    conn: asyncpg.Connection, transitions: Sequence[ConditionTransition]
) -> int:
    """``reconcile_snapshot`` ``post_write`` hook: amend delivered insights on resolution.

    For every ``resolved`` transition, enqueue one amendment per delivered
    candidate whose premise names that condition, through the narrow
    ``public.enqueue_premise_amendments`` definer (so a producer role needs no
    access to the Switchboard-owned tables). Runs inside the reconcile
    transaction: the amendment and the resolution commit or roll back together.
    The definer is idempotent on ``(candidate, fingerprint@resolved_at)``, so a
    replayed reconcile never queues a second amendment. Returns the number of
    amendments newly queued.
    """
    queued = 0
    for transition in transitions:
        if transition.transition != "resolved" or transition.resolved_at is None:
            continue
        # A savepoint, not the bare call: a failed enqueue (definer not yet
        # migrated, say) must cost the amendment, never the resolution it
        # describes -- an exception here would roll back the whole reconcile.
        try:
            async with conn.transaction():
                count = await conn.fetchval(
                    "SELECT public.enqueue_premise_amendments($1, $2, $3, $4)",
                    transition.source,
                    transition.fingerprint,
                    transition.resolved_at,
                    None,
                )
        except Exception:
            logger.warning(
                "insight premise amendment enqueue failed for source=%s",
                transition.source,
                exc_info=True,
            )
            continue
        queued += int(count or 0)
    return queued
