"""Resolve the two owner-owned verdict inputs and stamp events (REQ-module-calendar-040).

The calendar projection runs under a butler role that cannot read the identity tables or
Switchboard's ingestion rules, so both reads go through the core_370 SECURITY DEFINER functions.
Every failure becomes ``None`` for that input, which ``organizer_verdict`` turns into ``unknown``:
an unreadable store never admits an organizer.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.ingestion_policy import (
    _KNOWN_RULE_TYPES,
    IngestionEnvelope,
    IngestionPolicyEvaluator,
)
from butlers.modules.calendar.organizer_verdict import (
    SWEEP_BATCH,
    VERDICT_VERSION,
    IdentityClass,
    organizer_verdict,
)

logger = logging.getLogger(__name__)

_CONNECTOR_SCOPE_PREFIX = "connector:google_calendar:"
_CHANNEL = "calendar"
#: ``created_by`` of the rules the owner's Vouch and Block verbs write.
VOUCH_CREATED_BY = "calendar_vouch"
BLOCK_CREATED_BY = "calendar_block"
_IDENTITY_CLASSES = frozenset({"known_entity", "known_domain", "none"})


class PolicyView:
    """The connector and global ingestion rules, evaluated as the connector evaluates them."""

    def __init__(
        self,
        evaluators: Mapping[str, IngestionPolicyEvaluator],
        vouched: frozenset[str] = frozenset(),
    ) -> None:
        self._evaluators = dict(evaluators)
        self._vouched = vouched

    def vouches(self, email: str) -> bool:
        """Whether the owner vouched for this address (a Vouch rule in any calendar scope)."""
        return email.strip().casefold() in self._vouched

    def blocks(self, email: str, *, known_contact: bool) -> bool:
        envelope = IngestionEnvelope(
            sender_address=email,
            source_channel=_CHANNEL,
            raw_key=email,
            known_contact=known_contact,
        )
        for scope, evaluator in self._evaluators.items():
            decision = evaluator.evaluate(envelope)
            if scope == "global":
                if decision.action == "skip":
                    return True
            elif not decision.allowed:
                return True
        return False


def _evaluator(scope: str, rules: list[dict[str, Any]]) -> IngestionPolicyEvaluator:
    evaluator = IngestionPolicyEvaluator(scope, None, spot_check_k=0)
    evaluator.seed_rules(rules)
    return evaluator


async def load_policy_view(pool: asyncpg.Pool) -> PolicyView | None:
    """Fetch the rules through the definer; ``None`` when they could not be read."""
    try:
        rows = await pool.fetch("SELECT * FROM public.organizer_policy_rules()")
    except Exception as exc:
        logger.warning(
            "calendar organizer verdict: policy rules unreadable: %s", type(exc).__name__
        )
        return None
    by_scope: dict[str, list[dict[str, Any]]] = {"global": []}
    vouched: set[str] = set()
    for row in rows:
        rule = dict(row)
        condition = rule.get("condition")
        if isinstance(condition, str):
            try:
                condition = json.loads(condition)
            except (json.JSONDecodeError, TypeError):
                condition = None
        if not isinstance(condition, dict) or rule.get("rule_type") not in _KNOWN_RULE_TYPES:
            continue
        rule["condition"] = condition
        scope = str(rule.pop("scope"))
        if scope == "global" or scope.startswith(_CONNECTOR_SCOPE_PREFIX):
            by_scope.setdefault(scope, []).append(rule)
        if (
            scope.startswith(_CONNECTOR_SCOPE_PREFIX)
            and rule.get("created_by") == VOUCH_CREATED_BY
            and rule.get("action") == "pass_through"
            and rule.get("rule_type") == "sender_address"
            and isinstance(condition.get("address"), str)
        ):
            vouched.add(condition["address"].strip().casefold())
    return PolicyView(
        {scope: _evaluator(scope, rules) for scope, rules in by_scope.items()},
        frozenset(vouched),
    )


async def identity_class(pool: asyncpg.Pool | None, email: str) -> IdentityClass | None:
    """The organizer address's identity class, or ``None`` when the lookup failed."""
    if pool is None:
        return None
    try:
        value = await pool.fetchval("SELECT public.organizer_identity_class($1)", email)
    except Exception as exc:
        logger.warning("calendar organizer verdict: identity lookup failed: %s", type(exc).__name__)
        return None
    return value if value in _IDENTITY_CLASSES else None  # type: ignore[return-value]


def own_email_from(attendees: list[Mapping[str, Any]]) -> str | None:
    """The owner's own address: the single attendee the provider marks ``self``."""
    own = [a.get("email") for a in attendees if a.get("self") is True]
    return own[0] if len(own) == 1 and isinstance(own[0], str) else None


async def stamp_for(
    pool: asyncpg.Pool | None,
    policy: PolicyView | None,
    *,
    organizer: str | None,
    own_email: str | None,
    attendee_count: int,
    event_type: str | None,
    cache: dict[str, IdentityClass | None] | None = None,
) -> dict[str, Any]:
    """The metadata stamp for one event: verdict, badges and version."""
    cls: IdentityClass | None = None
    key = (organizer or "").strip().casefold()
    if key:
        if cache is not None and key in cache:
            cls = cache[key]
        else:
            cls = await identity_class(pool, key)
            if cache is not None:
                cache[key] = cls
    blocked = (
        None
        if policy is None or not key
        else policy.blocks(key, known_contact=cls == "known_entity")
    )
    result = organizer_verdict(
        organizer_email=organizer,
        own_email=own_email,
        identity_class=cls,
        policy_blocked=blocked,
        owner_vouched=bool(policy is not None and key and policy.vouches(key)),
        attendee_count=attendee_count,
        guests_hidden=False,
        event_type=event_type,
        conference_host=None,
        external_link_count=0,
    )
    return {
        "organizer_verdict": result.verdict,
        "lure_badges": list(result.lure_badges),
        "verdict_version": VERDICT_VERSION,
        # A transient read failure stamps ``unknown`` and asks the sweep to evaluate it again.
        "lookup_failed": bool(key) and result.verdict == "unknown",
        # When this attempt ran; failed rows are retried oldest attempt first.
        "stamped_at": datetime.now(UTC).isoformat(),
    }


_POLICY_TTL_S = 30.0
# One slot: a butler process has one pool, and a different pool simply replaces the entry.
_policy_cache: tuple[Any, float, PolicyView | None] | None = None


async def cached_policy_view(pool: asyncpg.Pool | None) -> PolicyView | None:
    """The policy view, reloaded at most every ``_POLICY_TTL_S`` seconds per pool.

    A failed read is cached too, so an unreadable store costs one query per interval and every
    event in that window stamps ``unknown``.
    """
    if pool is None:
        return None
    global _policy_cache
    now = time.monotonic()
    hit = _policy_cache
    if hit is not None and hit[0] is pool and now - hit[1] < _POLICY_TTL_S:
        return hit[2]
    view = await load_policy_view(pool)
    _policy_cache = (pool, now, view)
    return view


async def stamp_provider_metadata(
    pool: asyncpg.Pool | None,
    metadata: Mapping[str, Any],
    *,
    event_type: str | None,
    cache: dict[str, IdentityClass | None] | None = None,
    policy: PolicyView | None = None,
) -> dict[str, Any]:
    """The verdict stamp for a stored provider event's metadata (organizer and attendees)."""
    attendees = [a for a in metadata.get("attendees") or [] if isinstance(a, Mapping)]
    organizer = metadata.get("organizer")
    return await stamp_for(
        pool,
        policy if policy is not None else await cached_policy_view(pool),
        organizer=organizer if isinstance(organizer, str) else None,
        own_email=own_email_from(attendees),
        attendee_count=len(attendees),
        event_type=event_type,
        cache=cache,
    )


async def sweep_organizer_verdicts(pool: asyncpg.Pool, *, limit: int = SWEEP_BATCH) -> int:
    """Stamp provider events that carry no current verdict (REQ-module-calendar-040).

    Bounded and idempotent: only rows with no stamp or an older ``verdict_version`` are read, and
    a stamped row is never selected again. Returns the number of rows stamped.
    """
    rows = await pool.fetch(
        """
        SELECT e.id, e.metadata, e.event_type
        FROM calendar_events e
        WHERE e.metadata ->> 'source_type' = 'provider_event'
          AND (
              -- no stamp, a stale or non-integer version, or a lookup that failed last time
              CASE WHEN jsonb_typeof(e.metadata -> 'verdict_version') = 'number'
                        AND e.metadata ->> 'verdict_version' ~ '^[0-9]{1,9}$'
                   THEN (e.metadata ->> 'verdict_version')::integer < $1
                   ELSE true END
              OR e.metadata ->> 'lookup_failed' = 'true'
          )
        -- unstamped and stale rows first, so a persistent outage cannot starve them
        -- among failed rows the oldest attempt goes first, so they rotate rather than stick on id
        ORDER BY (COALESCE(e.metadata ->> 'lookup_failed', 'false') = 'true'),
                 e.metadata ->> 'stamped_at' ASC NULLS FIRST, e.id
        LIMIT $2
        """,
        VERDICT_VERSION,
        limit,
    )
    if not rows:
        return 0
    policy = await load_policy_view(pool)
    cache: dict[str, IdentityClass | None] = {}
    for row in rows:
        raw = row["metadata"]
        metadata = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        stamp = await stamp_provider_metadata(
            pool, metadata, event_type=row["event_type"], cache=cache, policy=policy
        )
        await pool.execute(
            "UPDATE calendar_events SET metadata = metadata || $2::jsonb WHERE id = $1",
            row["id"],
            stamp,
        )
    return len(rows)
