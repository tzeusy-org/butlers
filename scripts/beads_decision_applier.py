#!/usr/bin/env python3
"""Apply recorded Decision Desk intents to the Beads tracker with ``bd``.

bu-ckkpz.3, ``REQ-owner-decision-desk-002`` (OpenSpec change
``owner-decision-desk-write-bridge``). Runs inside the beads CronJob pod, the
one management workload that holds the tracker credential, before the export
step (``deploy/helm/butlers/files/beads_cycle.sh``). The runtime only records
``switchboard.decision_intents`` rows; this script is their only consumer.

Contract (at most once per intent):

1. Reconcile ``applying`` rows a previous run left behind, using ``bd show``
   as evidence: our ``decision-intent <id>`` marker on a closed bead is
   ``applied``, another close is ``failed``/``bead_closed_elsewhere``, a still
   open bead goes back to ``pending``.
2. Claim a bounded batch of ``pending`` rows (oldest first), committed before
   any tracker call.
3. Re-validate each bead (exists, open, ``decision`` label, option offered),
   then ``bd close`` it with a reason carrying the option and the marker.
4. A failed close is re-read before it is classified. ``bd_close_failed`` is
   terminal after :data:`MAX_CLOSE_ATTEMPTS`; an unreachable tracker returns
   the batch to ``pending`` without spending attempts and stops the run.

Failure reasons are categorical. Raw ``bd`` output can carry host names, so it
is never stored or logged.

Standalone by design: the bridge image carries ``bd``, this file and
``asyncpg``, not the Butlers package. Env: ``POSTGRES_HOST``, ``POSTGRES_PORT``,
``POSTGRES_DB``, ``POSTGRES_USER``, ``POSTGRES_PASSWORD``, ``POSTGRES_SSLMODE``,
``BD_BIN`` (default ``bd``), ``DECISION_APPLIER_BATCH`` (default 20). Exit
status is non-zero only when the tracker or the database was unreachable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

logger = logging.getLogger("beads_decision_applier")

MAX_CLOSE_ATTEMPTS = 3
DEFAULT_BATCH = 20
_BD_TIMEOUT_SECONDS = 60
_OPEN_STATUSES = frozenset({"open", "in_progress", "blocked"})
_DECISION_LABEL = "decision"
_COLUMNS = "id, bead_id, option, source, attempts"


class TrackerUnavailable(Exception):
    """``bd`` could not read the tracker. Nothing about the bead is known."""


class CloseFailed(Exception):
    """``bd close`` exited non-zero. The bead's state is unknown until re-read."""


class Tracker(Protocol):
    def show(self, bead_id: str) -> dict[str, Any] | None: ...

    def close(self, bead_id: str, reason: str) -> None: ...


class BdTracker:
    """The real tracker, through the pinned ``bd`` binary."""

    def __init__(self, binary: str = "bd") -> None:
        self._binary = binary

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self._binary, *args],
            capture_output=True,
            text=True,
            timeout=_BD_TIMEOUT_SECONDS,
            check=False,
        )

    def show(self, bead_id: str) -> dict[str, Any] | None:
        try:
            result = self._run("show", bead_id, "--json")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TrackerUnavailable from exc
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise TrackerUnavailable from exc
        if result.returncode == 0 and isinstance(payload, list) and payload:
            issue = payload[0]
            if isinstance(issue, dict) and issue.get("id") == bead_id:
                return issue
        if isinstance(payload, dict) and "no issues found" in str(payload.get("error", "")):
            return None
        raise TrackerUnavailable

    def close(self, bead_id: str, reason: str) -> None:
        try:
            result = self._run("close", bead_id, "--reason", reason)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CloseFailed from exc
        if result.returncode != 0:
            raise CloseFailed


@dataclass(frozen=True)
class Claimed:
    id: Any
    bead_id: str
    option: str
    source: str
    attempts: int

    @property
    def marker(self) -> str:
        return f"decision-intent {self.id}"

    @property
    def close_reason(self) -> str:
        return f"Decision: {self.option} ({self.marker}, via {self.source})"


def _offered_options(issue: dict[str, Any]) -> list[str]:
    metadata = issue.get("metadata")
    decision = metadata.get("decision") if isinstance(metadata, dict) else None
    options = decision.get("options") if isinstance(decision, dict) else None
    return [o for o in options if isinstance(o, str)] if isinstance(options, list) else []


def _closed_by(issue: dict[str, Any], intent: Claimed) -> bool:
    return issue.get("status") == "closed" and intent.marker in str(issue.get("close_reason") or "")


def _drift_reason(issue: dict[str, Any] | None, intent: Claimed) -> str | None:
    """The categorical reason this bead may not be closed for *intent*, if any."""
    if issue is None:
        return "bead_not_found"
    if issue.get("status") not in _OPEN_STATUSES:
        return "bead_not_open"
    labels = issue.get("labels")
    if (
        issue.get("issue_type") == "epic"
        or not isinstance(labels, list)
        or _DECISION_LABEL not in labels
    ):
        return "not_a_decision"
    if intent.option not in _offered_options(issue):
        return "option_not_offered"
    return None


class Applier:
    def __init__(self, conn: asyncpg.Connection, tracker: Tracker) -> None:
        self._conn = conn
        self._tracker = tracker
        self.outcomes: Counter[str] = Counter()
        self.tracker_unavailable = False

    async def _show(self, bead_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._tracker.show, bead_id)

    async def _close(self, bead_id: str, reason: str) -> None:
        await asyncio.to_thread(self._tracker.close, bead_id, reason)

    async def _finish(self, intent: Claimed, status: str, reason: str | None = None) -> None:
        await self._conn.execute(
            """
            UPDATE switchboard.decision_intents
            SET status = $2, failure_reason = $3, finished_at = now(), updated_at = now()
            WHERE id = $1 AND status = 'applying'
            """,
            intent.id,
            status,
            reason,
        )
        self.outcomes[status if reason is None else f"failed:{reason}"] += 1

    async def _release(self, intent: Claimed, last_error: str | None) -> None:
        await self._conn.execute(
            """
            UPDATE switchboard.decision_intents
            SET status = 'pending', last_error = $2, claimed_at = NULL, updated_at = now()
            WHERE id = $1 AND status = 'applying'
            """,
            intent.id,
            last_error,
        )
        self.outcomes[f"pending:{last_error or 'reconciled'}"] += 1

    async def _rows(self, sql: str, *args: Any) -> list[Claimed]:
        return [Claimed(**dict(row)) for row in await self._conn.fetch(sql, *args)]

    async def reconcile(self) -> None:
        stranded = await self._rows(
            f"SELECT {_COLUMNS} FROM switchboard.decision_intents "
            "WHERE status = 'applying' ORDER BY created_at"
        )
        for intent in stranded:
            try:
                issue = await self._show(intent.bead_id)
            except TrackerUnavailable:
                # Leave it applying: only tracker evidence may settle it.
                self.tracker_unavailable = True
                return
            if issue is None:
                await self._finish(intent, "failed", "bead_not_found")
            elif _closed_by(issue, intent):
                await self._finish(intent, "applied")
            elif issue.get("status") not in _OPEN_STATUSES:
                await self._finish(intent, "failed", "bead_closed_elsewhere")
            else:
                await self._release(intent, None)

    async def claim(self, batch: int) -> list[Claimed]:
        # RETURNING has no order of its own; the outer SELECT restores oldest first.
        return await self._rows(
            f"""
            WITH picked AS (
                SELECT id FROM switchboard.decision_intents
                WHERE status = 'pending'
                ORDER BY created_at
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            ), claimed AS (
                UPDATE switchboard.decision_intents AS intent
                SET status = 'applying', claimed_at = now(), updated_at = now()
                FROM picked
                WHERE intent.id = picked.id
                RETURNING intent.*
            )
            SELECT {_COLUMNS} FROM claimed ORDER BY created_at
            """,
            batch,
        )

    async def apply(self, intent: Claimed) -> None:
        """Raises :class:`TrackerUnavailable` with *intent* still ``applying``."""
        issue = await self._show(intent.bead_id)
        if issue is not None and _closed_by(issue, intent):
            await self._finish(intent, "applied")
            return
        reason = _drift_reason(issue, intent)
        if reason is not None:
            await self._finish(intent, "failed", reason)
            return

        attempts = intent.attempts + 1
        await self._conn.execute(
            "UPDATE switchboard.decision_intents SET attempts = $2, updated_at = now() "
            "WHERE id = $1 AND status = 'applying'",
            intent.id,
            attempts,
        )
        try:
            await self._close(intent.bead_id, intent.close_reason)
        except CloseFailed:
            reread = await self._show(intent.bead_id)
            if reread is not None and _closed_by(reread, intent):
                await self._finish(intent, "applied")
            elif attempts >= MAX_CLOSE_ATTEMPTS:
                await self._finish(intent, "failed", "bd_close_failed")
            else:
                await self._release(intent, "bd_close_failed")
            return
        await self._finish(intent, "applied")

    async def run(self, batch: int = DEFAULT_BATCH) -> None:
        await self.reconcile()
        if self.tracker_unavailable:
            return
        claimed = await self.claim(batch)
        for index, intent in enumerate(claimed):
            try:
                await self.apply(intent)
            except TrackerUnavailable:
                self.tracker_unavailable = True
                for remaining in claimed[index:]:
                    await self._release(remaining, "tracker_unavailable")
                return


async def _connect() -> asyncpg.Connection:
    sslmode = os.environ.get("POSTGRES_SSLMODE") or None
    return await asyncpg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ.get("POSTGRES_DB", "butlers"),
        user=os.environ.get("POSTGRES_USER"),
        password=os.environ.get("POSTGRES_PASSWORD"),
        ssl=sslmode,
    )


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    batch = int(os.environ.get("DECISION_APPLIER_BATCH", str(DEFAULT_BATCH)))
    try:
        conn = await _connect()
    except Exception as exc:  # noqa: BLE001 - the message can carry the DSN
        logger.error("database unreachable (%s)", type(exc).__name__)
        return 1
    try:
        applier = Applier(conn, BdTracker(os.environ.get("BD_BIN", "bd")))
        await applier.run(batch)
    finally:
        await conn.close()
    summary = {
        "outcomes": dict(applier.outcomes),
        "tracker_unavailable": applier.tracker_unavailable,
    }
    logger.info("summary %s", json.dumps(summary, sort_keys=True))
    return 1 if applier.tracker_unavailable else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
