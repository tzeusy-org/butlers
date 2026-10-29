"""Transaction-enlisted, ordered fenced claims for code-defined durable queues.

Memory consolidation reclaims expired leases and dead-letters exhausted work.
Runtime attention needs a no-reclaim claim: a sending episode becomes uncertain,
never another send. Adapters own their transactions, roles, transport and events.
SQL fragments are reviewed module constants, never request/row-derived strings;
identifiers are validated and every dynamic value is bound. ``now()`` deliberately
keeps the adapters' existing transaction-time semantics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import asyncpg

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*", re.ASCII)
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}", re.ASCII)


def _identifier(value: str, *, qualified: bool = False) -> None:
    if not isinstance(value, str):
        raise ValueError("queue identifiers must be strings")
    parts = value.split(".")
    if len(parts) > (2 if qualified else 1) or not all(_IDENTIFIER.fullmatch(p) for p in parts):
        raise ValueError(f"invalid queue identifier: {value!r}")


def _fragment(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or any(token in value for token in (";", "--", "/*", "*/", "\x00"))
    ):
        raise ValueError("queue fragments must be single trusted SQL expressions")


@dataclass(frozen=True)
class ClaimFailure:
    """Existing lifecycle columns and constants for a reclaimable queue."""

    attempts: str
    status: str
    last_error: str
    dead_reason: str
    next_retry: str
    max_attempts: int
    backoff_seconds: int
    failed_status: str = "failed"
    dead_status: str = "dead_letter"
    pending_status: str = "pending"

    def __post_init__(self) -> None:
        for value in (
            self.attempts,
            self.status,
            self.last_error,
            self.dead_reason,
            self.next_retry,
            self.failed_status,
            self.dead_status,
            self.pending_status,
        ):
            _identifier(value)
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("attempt cap must be positive")
        if type(self.backoff_seconds) is not int or self.backoff_seconds < 1:
            raise ValueError("backoff must be positive")


@dataclass(frozen=True)
class ClaimQueue:
    """One reviewed queue shape; instances belong at adapter module scope.

    Expressions may reference q and bound placeholders {owner}, {lease_seconds}
    and {arg0}, {arg1}, ... . They are not a public SQL construction interface.
    """

    table: str
    key: str
    eligible: str
    order_by: str
    claim_set: tuple[tuple[str, str], ...]
    returning: tuple[str, ...]
    reclaim_expired: bool
    lease_until: str | None = None
    lease_owner: str | None = None
    failure: ClaimFailure | None = None
    failure_set: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.table, qualified=True)
        for name in (
            self.key,
            *self.returning,
            *(column for column, _ in (*self.claim_set, *self.failure_set)),
        ):
            _identifier(name)
        for value in (
            self.eligible,
            self.order_by,
            *(expr for _, expr in (*self.claim_set, *self.failure_set)),
        ):
            _fragment(value)
        if type(self.reclaim_expired) is not bool:
            raise ValueError("reclaim policy must be explicit")
        if (self.lease_until is None) != (self.lease_owner is None):
            raise ValueError("lease columns must be paired")
        for name in (self.lease_until, self.lease_owner):
            if name is not None:
                _identifier(name)
        if self.reclaim_expired and self.lease_until is None:
            raise ValueError("reclaim needs lease columns")
        if self.failure is not None and (not self.reclaim_expired or self.lease_owner is None):
            raise ValueError("failure policy needs a reclaimable lease")
        columns = [column for column, _ in self.claim_set]
        if len(columns) != len(set(columns)) or self.key not in self.returning:
            raise ValueError("claim assignments must be unique and return the key")
        if not columns and self.lease_until is None:
            raise ValueError("claim must change durable state")


def _bind(sql: str, values: dict[str, Any]) -> tuple[str, list[Any]]:
    names: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ValueError(f"unbound queue value: {name}")
        if name not in names:
            names.append(name)
        return f"${names.index(name) + 1}"

    return _PLACEHOLDER.sub(replace, sql), [values[name] for name in names]


def _transaction(conn: asyncpg.Connection) -> None:
    if not conn.is_in_transaction():
        raise RuntimeError("queue claims require the caller's transaction")


async def claim_batch(
    conn: asyncpg.Connection,
    queue: ClaimQueue,
    *,
    limit: int,
    owner: str,
    lease_seconds: int,
    args: tuple[Any, ...] = (),
) -> list[asyncpg.Record]:
    """Lock eligible rows, change durable claim state and retain select order."""
    _transaction(conn)
    if type(limit) is not int or limit < 1 or type(lease_seconds) is not int or lease_seconds < 1:
        raise ValueError("claim limit and lease must be positive integers")
    values = {"limit": limit, "owner": owner, "lease_seconds": lease_seconds}
    values.update({f"arg{i}": value for i, value in enumerate(args)})
    eligible = f"({queue.eligible})"
    if queue.reclaim_expired:
        eligible += f" AND (q.{queue.lease_until} IS NULL OR q.{queue.lease_until} <= now())"
    if queue.failure:
        eligible += f" AND q.{queue.failure.attempts} < {{cap}}"
        values["cap"] = queue.failure.max_attempts
    sql, bound = _bind(
        f"SELECT q.{queue.key} FROM {queue.table} q WHERE {eligible} "
        f"ORDER BY {queue.order_by} FOR UPDATE OF q SKIP LOCKED LIMIT {{limit}}",
        values,
    )
    selected = await conn.fetch(sql, *bound)
    if not selected:
        return []
    ids = [row[queue.key] for row in selected]
    assignments = [f"{column}={expr}" for column, expr in queue.claim_set]
    if queue.lease_until:
        assignments += [
            f"{queue.lease_until}=now()+make_interval(secs=>{{lease_seconds}})",
            f"{queue.lease_owner}={{owner}}",
        ]
    values["ids"] = ids
    sql, bound = _bind(
        f"UPDATE {queue.table} q SET {', '.join(assignments)} "
        f"WHERE q.{queue.key}=ANY({{ids}}) AND {eligible} "
        f"RETURNING {', '.join('q.' + column for column in queue.returning)}",
        values,
    )
    claimed = {row[queue.key]: row for row in await conn.fetch(sql, *bound)}
    return [claimed[key] for key in ids if key in claimed]


async def fail_claimed(
    conn: asyncpg.Connection,
    queue: ClaimQueue,
    ids: list[Any],
    *,
    owner: str,
    reason: str,
) -> list[asyncpg.Record]:
    """Fence a failure/dead-letter transition; caller must enlist its audit too."""
    _transaction(conn)
    policy = queue.failure
    if policy is None:
        raise ValueError("queue has no failure policy")
    if not ids:
        return []
    values = dict(
        ids=ids,
        owner=owner,
        reason=reason,
        cap=policy.max_attempts,
        backoff=policy.backoff_seconds,
        failed=policy.failed_status,
        dead=policy.dead_status,
        pending=policy.pending_status,
    )
    terminal = f"q.{policy.attempts}+1 >= {{cap}}"
    extra = "".join(f"{column}={expr}, " for column, expr in queue.failure_set)
    sql, bound = _bind(
        f"UPDATE {queue.table} q SET {extra}{policy.attempts}=q.{policy.attempts}+1, "
        f"{policy.last_error}={{reason}}, "
        f"{policy.status}=CASE WHEN {terminal} THEN {{dead}} ELSE {{failed}} END, "
        f"{policy.dead_reason}=CASE WHEN {terminal} THEN {{reason}} ELSE NULL END, "
        f"{policy.next_retry}=CASE WHEN {terminal} THEN NULL ELSE now()+"
        f"(power(2,q.{policy.attempts}+1)*{{backoff}}*interval '1 second') END, "
        f"{queue.lease_until}=NULL, {queue.lease_owner}=NULL "
        f"WHERE q.{queue.key}=ANY({{ids}}) AND q.{queue.lease_owner}={{owner}} "
        f"AND q.{queue.lease_until}>now() AND q.{policy.attempts}<{{cap}} "
        f"AND q.{policy.status} IN ({{pending}},{{failed}}) "
        f"RETURNING {', '.join('q.' + column for column in queue.returning)}",
        values,
    )
    return list(await conn.fetch(sql, *bound))
