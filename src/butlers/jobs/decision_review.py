"""Decision-review digest + P1/deploy age escalation.

bu-ckkpz.4 (epic bu-ckkpz "Owner Decision Desk", move 8 of the 2026-07-10
JARVIS pursuit). See ``docs/redesigns/2026-07-10-jarvis-pursuit.md`` §8 and
``about/legends-and-lore/rfcs/0011-proactive-insight-delivery.md`` Amendment 1
for the attention-ledger egress-path contract this module participates in.

What this is
------------
Two deterministic Switchboard schedule jobs:

- :func:`run_decision_review_digest` — weekly digest of open owner-decision
  beads ("N decisions waiting, oldest Xd").
- :func:`run_decision_escalation_check` — runs a few times a day; escalates
  once when an open decision bead has blocked a P1 bug or a deploy-marked
  bead for more than 48 hours.

Why Switchboard, and why a file, not a live query
---------------------------------------------------
Beads (``bd``) issue data lives in a Dolt server bound to the *host's*
loopback interface (``127.0.0.1:3307``, see ``.beads/config.yaml``), reached
today only by the ``bd`` CLI running directly on the host. No butler daemon
or dashboard-api process can reach it: ``docker-compose.yml``'s ``egress``
network explicitly DROPs all RFC1918/loopback/CGNAT traffic
(``scripts/egress-firewall.sh``) by design, and even without that firewall,
``127.0.0.1`` inside a container's network namespace is the container itself,
never the host. There is no bd MCP tool, no bd-to-Postgres bridge, and no
existing precedent for a butler reaching beads data live (verified by
grepping the whole tree for a bd client/HTTP surface — none exists).

Building a live bridge (a new egress allowlist exception, a Dolt bind-address
change, a full sync pipeline into Postgres) is a bigger architectural call
than this task owns -- see the epic notes / worker report for bu-ckkpz.4.
What *is* real and buildable today: ``bd``'s own git-tracked-adjacent JSONL
mirror (``.beads/issues.export.jsonl``, kept fresh by bd's own auto-export
config -- see ``export.path`` in ``.beads/config.yaml``) already lives on the
same host that runs ``docker compose``. ``docker-compose.yml`` bind-mounts
*only that one file*, read-only, into the ``butlers-up`` container at
``/app/.beads/issues.export.jsonl`` -- deliberately not the whole ``.beads/``
directory, which also holds ``.beads-credential-key`` (Dolt admin
credentials) that must never enter a container. Switchboard was chosen over
a domain butler because it already owns the attention/insight machinery
(``roster/switchboard/tools/insight/broker.py``) and runs entirely inside the
same ``butlers-up`` process/container as every other butler daemon, so the
mount is visible to it without any per-butler wiring.

Never fabricate an all-clear
-----------------------------
If the export file is missing, unreadable, or older than
:data:`_STALE_EXPORT_AGE` (bd hasn't run in this checkout in a long time, or
a fresh clone hasn't populated the mount yet), :func:`compute_decision_digest`
returns ``available=False`` and the weekly job records a ``deferred``
attention-ledger row instead of silently reporting "0 decisions waiting" --
that would be exactly the fabricated-calm failure mode this codebase has
repeatedly hardened against (see ``src/butlers/api/degraded.py`` and its
callers). A *genuine* zero (file readable, zero beads matched) is a real
all-clear and is reported as such without sending an owner notification.

Decision-bead detection is a heuristic, not yet a convention
---------------------------------------------------------------
bu-ckkpz.1 (the structured decision-bead convention: options + default +
deadline, enforced by a linter) has not shipped yet, so there is no
``labels`` or metadata field that reliably marks "this bead is a decision."
Empirically, today's real owner-decision beads (bu-v4ipc, bu-zhfd0, bu-wyftz,
bu-4qfhl -- named in the epic's own seed queue) are NOT ``issue_type:
decision``; they are ordinary tasks/bugs whose *titles* carry a marker
("DECISION REQUIRED (owner)", "[OWNER-GATED]"). :data:`_DECISION_TITLE_MARKERS`
matches on that convention. This is intentionally narrow (favors missing a
decision bead over mislabeling an unrelated one as a decision) and should be
replaced by a real field lookup once bu-ckkpz.1 ships -- tracked as a
follow-up, not solved here.

P1-bug / deploy-block detection
--------------------------------
``bd export``'s JSONL includes full dependency edges (not just counts): an
edge ``{"issue_id": X, "depends_on_id": Y, "type": "blocks", "created_at":
...}`` means *Y blocks X* (verified against the live export: bu-wzbu9, a real
P1 bug, carries exactly this edge pointing at decision bead bu-v4ipc). A
decision bead is escalation-worthy when some OTHER open issue with
``priority == 1 and issue_type == "bug"`` (a P1 bug), or whose title contains
"deploy" (the seed queue's only concrete "deploy" example is bu-zhfd0, whose
title is a plain-English deploy description -- there is no dedicated bd
label for this either), carries a ``blocks`` edge back to it, and that edge's
own ``created_at`` is more than 48h old.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from butlers.api.routers import audit as audit_router
from butlers.core.approvals_policy import (
    get_approvals_policy_quiet_hours,
    should_suppress_by_policy,
)
from butlers.core.attention_ledger import get_suppressing_context_signal, record_attention_event
from butlers.credential_store import resolve_owner_telegram_recipient

logger = logging.getLogger(__name__)

_ACTOR = "decision_review"
_ESCALATED_ACTION = "decision_escalation_notified"
_ESCALATION_THRESHOLD = timedelta(hours=48)

# See "Decision-bead detection is a heuristic" above -- pending bu-ckkpz.1.
_DECISION_TITLE_MARKERS = re.compile(
    r"DECISION REQUIRED|OWNER[- ]GATED|OWNER DECISION", re.IGNORECASE
)
_DEPLOY_TITLE_MARKER = re.compile(r"\bdeploy", re.IGNORECASE)
_OPEN_STATUSES = frozenset({"open", "in_progress", "blocked"})
_DIGEST_MAX_LISTED = 10

# Overridable for tests / non-default deploy layouts; defaults to the
# docker-compose bind-mount target (see module docstring).
_DEFAULT_EXPORT_PATH = Path(
    os.environ.get("BUTLERS_BEADS_EXPORT_PATH", "/app/.beads/issues.export.jsonl")
)
# Beyond this age the export is treated as stale rather than trusted --
# either bd hasn't run on the host checkout in a long time, or (fresh clone)
# the mount target is an empty placeholder Docker created for a missing file.
_STALE_EXPORT_AGE = timedelta(days=14)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionBead:
    """One open, decision-marked bead."""

    id: str
    title: str
    priority: int | None
    created_at: datetime
    age: timedelta


@dataclass(frozen=True)
class EscalationHit:
    """One open decision bead blocking a P1 bug or a deploy for >48h."""

    decision_id: str
    decision_title: str
    blocked_id: str
    blocked_title: str
    blocked_kind: str  # "p1_bug" | "deploy"
    blocked_since: datetime
    block_age: timedelta


@dataclass(frozen=True)
class DecisionDigest:
    """Result of one digest computation. Never represents a fabricated all-clear."""

    checked_at: datetime
    available: bool
    unavailable_reason: str | None
    open_decisions: tuple[DecisionBead, ...]
    escalations: tuple[EscalationHit, ...]


# ---------------------------------------------------------------------------
# Export parsing
# ---------------------------------------------------------------------------


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _is_decision_bead(issue: dict[str, Any]) -> bool:
    if issue.get("status") not in _OPEN_STATUSES:
        return False
    title = issue.get("title") or ""
    return bool(_DECISION_TITLE_MARKERS.search(title))


def _is_p1_bug(issue: dict[str, Any]) -> bool:
    return (
        issue.get("status") in _OPEN_STATUSES
        and issue.get("issue_type") == "bug"
        and issue.get("priority") == 1
    )


def _is_deploy_bead(issue: dict[str, Any]) -> bool:
    if issue.get("status") not in _OPEN_STATUSES:
        return False
    return bool(_DEPLOY_TITLE_MARKER.search(issue.get("title") or ""))


def _load_issues(path: Path) -> dict[str, dict[str, Any]]:
    """Parse the JSONL export into ``{issue_id: record}``. Raises on I/O/parse failure."""
    issues: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                continue
            issue_id = record.get("id")
            if issue_id:
                issues[issue_id] = record
    return issues


def compute_decision_digest(
    export_path: Path | None = None,
    *,
    now: datetime | None = None,
) -> DecisionDigest:
    """Compute the current decision digest + escalation set. Never raises.

    Returns ``available=False`` (never a fabricated empty digest) when the
    export file is missing, unreadable, or stale -- see module docstring.
    """
    checked_at = now or datetime.now(UTC)
    path = export_path or _DEFAULT_EXPORT_PATH

    try:
        if not path.is_file():
            return DecisionDigest(checked_at, False, "export_missing", (), ())
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if checked_at - mtime > _STALE_EXPORT_AGE:
            return DecisionDigest(checked_at, False, "export_stale", (), ())
        issues = _load_issues(path)
    except Exception as exc:  # noqa: BLE001 - degraded-mode contract: never raise
        logger.warning("decision_review: failed to read beads export: %s", exc, exc_info=True)
        return DecisionDigest(checked_at, False, f"export_read_error:{exc}", (), ())

    decisions: dict[str, DecisionBead] = {}
    for issue_id, issue in issues.items():
        if not _is_decision_bead(issue):
            continue
        created_at = _parse_timestamp(issue.get("created_at"))
        if created_at is None:
            continue
        decisions[issue_id] = DecisionBead(
            id=issue_id,
            title=issue.get("title") or issue_id,
            priority=issue.get("priority"),
            created_at=created_at,
            age=checked_at - created_at,
        )

    escalations: list[EscalationHit] = []
    if decisions:
        for issue_id, issue in issues.items():
            if _is_p1_bug(issue):
                kind = "p1_bug"
            elif _is_deploy_bead(issue):
                kind = "deploy"
            else:
                continue
            dependencies = issue.get("dependencies")
            if not isinstance(dependencies, list):
                continue
            for edge in dependencies:
                if not isinstance(edge, dict):
                    continue
                if edge.get("type") != "blocks":
                    continue
                decision = decisions.get(edge.get("depends_on_id"))
                if decision is None:
                    continue
                edge_created_at = _parse_timestamp(edge.get("created_at"))
                if edge_created_at is None:
                    continue
                block_age = checked_at - edge_created_at
                if block_age < _ESCALATION_THRESHOLD:
                    continue
                escalations.append(
                    EscalationHit(
                        decision_id=decision.id,
                        decision_title=decision.title,
                        blocked_id=issue_id,
                        blocked_title=issue.get("title") or issue_id,
                        blocked_kind=kind,
                        blocked_since=edge_created_at,
                        block_age=block_age,
                    )
                )

    ordered_decisions = tuple(sorted(decisions.values(), key=lambda d: d.created_at))
    ordered_escalations = tuple(sorted(escalations, key=lambda e: e.block_age, reverse=True))
    return DecisionDigest(checked_at, True, None, ordered_decisions, ordered_escalations)


# ---------------------------------------------------------------------------
# Message composition
# ---------------------------------------------------------------------------


def _format_age(delta: timedelta) -> str:
    days = delta.days
    if days >= 1:
        return f"{days}d"
    hours = max(int(delta.total_seconds() // 3600), 0)
    return f"{hours}h"


def _compose_weekly_digest_message(digest: DecisionDigest) -> str:
    count = len(digest.open_decisions)
    oldest = digest.open_decisions[0] if digest.open_decisions else None
    header = f"\U0001f5c2️ Decision review: {count} decision{'s' if count != 1 else ''} waiting"
    if oldest is not None:
        header += f", oldest {_format_age(oldest.age)}"
    lines = [header + "."]
    for bead in digest.open_decisions[:_DIGEST_MAX_LISTED]:
        lines.append(f"- {bead.id} ({_format_age(bead.age)}): {bead.title}")
    remaining = count - _DIGEST_MAX_LISTED
    if remaining > 0:
        lines.append(f"... and {remaining} more.")
    return "\n".join(lines)


def _compose_escalation_message(hit: EscalationHit) -> str:
    kind_label = "a P1 bug" if hit.blocked_kind == "p1_bug" else "a deploy"
    return (
        f"⚠️ Decision {hit.decision_id} has blocked {kind_label} {hit.blocked_id} "
        f"for {_format_age(hit.block_age)} (>48h).\n"
        f"Decision: {hit.decision_title}\n"
        f"Blocked: {hit.blocked_title}"
    )


# ---------------------------------------------------------------------------
# Notify boundary (mirrors butlers.jobs.secrets_lifecycle's composition of
# the same gating + attention-ledger primitives notify() itself applies --
# see attention_ledger.py's module docstring for the full egress-path list).
# ---------------------------------------------------------------------------


async def _check_suppression(pool: asyncpg.Pool) -> str | None:
    """Mirrors notify()'s owner-default gate (quiet hours, then context bus)."""
    try:
        policy = await get_approvals_policy_quiet_hours(pool)
    except Exception:
        logger.debug("decision_review: quiet-hours policy lookup failed", exc_info=True)
        policy = None

    if policy is not None:
        tz_name = policy.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        now_local = datetime.now(UTC).astimezone(tz)
        if should_suppress_by_policy(policy, current_hour=now_local.hour):
            return "quiet_hours"

    context_signal = await get_suppressing_context_signal(pool)
    if context_signal is not None:
        return f"context_bus:{context_signal}"

    return None


async def _deliver(
    pool: asyncpg.Pool,
    *,
    message: str,
    dedup_key: str,
    priority: str,
) -> str:
    """Send one owner-facing message via the notify boundary.

    Records a terminal attention_ledger row on every branch (suppressed /
    deferred / delivered) so an owner-decision push is never silently
    dropped -- mirrors ``secrets_lifecycle.run_secrets_lifecycle_check``.
    Returns the outcome string.
    """
    suppress_reason = await _check_suppression(pool)
    if suppress_reason is not None:
        await record_attention_event(
            pool,
            origin_butler=_ACTOR,
            source="notify",
            outcome="suppressed",
            channel="telegram",
            intent="send",
            priority=priority,
            reason=suppress_reason,
            dedup_key=dedup_key,
        )
        return "suppressed"

    recipient = await resolve_owner_telegram_recipient(pool)
    if not recipient:
        await record_attention_event(
            pool,
            origin_butler=_ACTOR,
            source="notify",
            outcome="deferred",
            channel="telegram",
            intent="send",
            priority=priority,
            reason="no_recipient_configured",
            dedup_key=dedup_key,
        )
        return "deferred"

    # Local import: mirrors butlers.scheduled_jobs._build_switchboard_insight_notify_fn
    # and secrets_lifecycle.run_secrets_lifecycle_check -- roster/ modules
    # aren't always importable at collection time.
    from butlers.tools.switchboard.notification.deliver import deliver

    result = await deliver(
        pool,
        channel="telegram",
        message=message,
        recipient=recipient,
        source_butler="switchboard",
        metadata={"origin": _ACTOR},
    )

    if result.get("status") == "failed":
        await record_attention_event(
            pool,
            origin_butler=_ACTOR,
            source="notify",
            outcome="deferred",
            channel="telegram",
            intent="send",
            priority=priority,
            reason=f"delivery_error:{result.get('error', 'unknown')}",
            dedup_key=dedup_key,
        )
        return "deferred"

    await record_attention_event(
        pool,
        origin_butler=_ACTOR,
        source="notify",
        outcome="delivered",
        channel="telegram",
        intent="send",
        priority=priority,
        dedup_key=dedup_key,
    )
    return "delivered"


# ---------------------------------------------------------------------------
# Schedule job entry points (registered under "switchboard" in
# butlers.scheduled_jobs; wired via roster/switchboard/butler.toml)
# ---------------------------------------------------------------------------


async def run_decision_review_digest(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Weekly digest of open owner-decision beads. Never fabricates an all-clear."""
    del job_args
    digest = compute_decision_digest()

    if not digest.available:
        logger.warning(
            "decision_review_digest: beads export unavailable (%s) -- skipping digest "
            "rather than reporting a fabricated all-clear",
            digest.unavailable_reason,
        )
        await record_attention_event(
            pool,
            origin_butler=_ACTOR,
            source="notify",
            outcome="deferred",
            channel="telegram",
            intent="send",
            priority="low",
            reason=f"data_unavailable:{digest.unavailable_reason}",
            dedup_key="decision_review_digest",
        )
        return {"available": False, "reason": digest.unavailable_reason}

    if not digest.open_decisions:
        return {"available": True, "open_decisions": 0, "outcome": "no_decisions"}

    message = _compose_weekly_digest_message(digest)
    outcome = await _deliver(
        pool, message=message, dedup_key="decision_review_digest", priority="medium"
    )
    return {"available": True, "open_decisions": len(digest.open_decisions), "outcome": outcome}


async def run_decision_escalation_check(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Escalate once per (decision, blocked-issue) pair once a block exceeds 48h.

    Debounced via ``public.audit_log`` (no new migration), keyed by
    ``"<decision_id>:<blocked_id>"`` -- mirrors
    ``butlers.jobs.deploy_drift.maybe_escalate_drift``'s fingerprint pattern.
    Only a successfully *delivered* escalation writes the marker, so a
    quiet-hours-suppressed escalation is retried on the next tick instead of
    being silently dropped forever.
    """
    del job_args
    digest = compute_decision_digest()

    if not digest.available:
        logger.warning(
            "decision_escalation_check: beads export unavailable (%s)",
            digest.unavailable_reason,
        )
        return {"available": False, "reason": digest.unavailable_reason}

    escalated = 0
    skipped = 0
    for hit in digest.escalations:
        fingerprint = f"{hit.decision_id}:{hit.blocked_id}"
        try:
            already = await pool.fetchrow(
                "SELECT 1 FROM public.audit_log WHERE target = $1 AND action = $2 LIMIT 1",
                fingerprint,
                _ESCALATED_ACTION,
            )
            if already is not None:
                skipped += 1
                continue

            message = _compose_escalation_message(hit)
            outcome = await _deliver(pool, message=message, dedup_key=fingerprint, priority="high")
            if outcome != "delivered":
                continue

            await audit_router.append(
                pool,
                _ACTOR,
                _ESCALATED_ACTION,
                target=fingerprint,
                note=f"blocked {hit.blocked_kind} for {_format_age(hit.block_age)}",
            )
            escalated += 1
        except Exception as exc:  # noqa: BLE001 - one bad escalation must not sink the tick
            logger.error(
                "decision_escalation_check: failed to process escalation for %s: %s",
                fingerprint,
                exc,
                exc_info=True,
            )

    return {
        "available": True,
        "escalations_found": len(digest.escalations),
        "escalated": escalated,
        "skipped": skipped,
    }
