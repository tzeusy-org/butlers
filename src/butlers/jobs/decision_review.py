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
returns ``available=False`` and the weekly job records a ``failed``
attention-ledger row (reason ``data_unavailable:*``) instead of silently
reporting "0 decisions waiting" --
that would be exactly the fabricated-calm failure mode this codebase has
repeatedly hardened against (see ``src/butlers/api/degraded.py`` and its
callers). A *genuine* zero (file readable, zero beads matched) is a real
all-clear and is reported as such without sending an owner notification.

Decision-bead detection: convention first, title regex as legacy fallback
---------------------------------------------------------------------------
bu-ckkpz.1 (the structured decision-bead convention: ``decision`` label +
``metadata.decision.{options,default}`` + native ``due_at`` deadline,
enforced by ``scripts/lint_decision_beads.py`` -- see AGENTS.md
"Decision-bead convention") has shipped (PR #3141). :func:`_is_decision_bead`
checks the convention's own marker first -- the ``decision`` label, the same
field ``bd list --label decision`` and the linter key off (see
:data:`_DECISION_LABEL`) -- and only falls back to
:data:`_DECISION_TITLE_MARKERS` (a title-text regex) for beads that predate
the convention and were never labeled. Empirically, the epic's original seed
queue (bu-v4ipc, bu-zhfd0, bu-wyftz, bu-4qfhl, bu-w6jca, bu-4pq0s) is
title-marker-only today ("DECISION REQUIRED (owner)", "[OWNER-GATED]",
"ARCHITECTURAL DECISION", "OWNER:") and is the reason the fallback stays
supported rather than being deleted outright; any *new* decision bead should
carry the ``decision`` label instead of relying on title text. Both the
label check and the title-regex fallback exclude ``issue_type == "epic"``: a
container epic (e.g. bu-ckkpz itself, "Owner Decision Desk: decision beads
become first-class attention citizens") is not itself a single decision the
owner resolves in one step, even when its title or label happens to match.

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
import subprocess
import sys
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

# The convention's own marker (bu-ckkpz.1) -- mirrors DECISION_LABEL in
# scripts/lint_decision_beads.py. Checked first; see "Decision-bead
# detection" above.
_DECISION_LABEL = "decision"

# Legacy fallback for beads that predate the `decision` label (see
# "Decision-bead detection" above). Validated against the live
# `.beads/issues.export.jsonl` seed queue: these five alternatives are the
# real title shapes pre-convention owner-decision beads use (DECISION
# REQUIRED (owner): ...; ...[OWNER-GATED]; OWNER DECISION ...; ARCHITECTURAL
# DECISION (owner): ...; OWNER: decide ...) -- bu-v4ipc, bu-zhfd0, bu-wyftz,
# bu-4qfhl, bu-w6jca, bu-4pq0s all match one of these.
_DECISION_TITLE_MARKERS = re.compile(
    r"DECISION REQUIRED|OWNER[- ]GATED|OWNER DECISION|ARCHITECTURAL DECISION|\bOWNER:",
    re.IGNORECASE,
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

# scripts/lint_decision_beads.py, resolved relative to this file rather than
# a hardcoded "/app" so it also works from a plain repo checkout in tests --
# parents[3] from src/butlers/jobs/decision_review.py is the repo root,
# where the Dockerfile COPYs scripts/ alongside src/ (see Dockerfile).
_LINT_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "lint_decision_beads.py"
_LINT_SUBPROCESS_TIMEOUT_S = 30


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
    """Result of one digest computation. Never represents a fabricated all-clear.

    ``export_as_of`` (bu-hmdqz.6) is the export file's own mtime -- set
    whenever the file could be stat'd, even on the ``export_stale``
    unavailable branch (so a caller can report exactly how old the data is),
    ``None`` only when the file is missing or was never reached. This is what
    lets a consumer (``GET /api/decisions``'s ``meta.export_as_of``) render an
    honest "as of" plaque instead of trusting hour-precision computed ages
    against a single-file bind-mount that may silently freeze at
    container-start inode if bd's auto-export replaces it by atomic rename.
    """

    checked_at: datetime
    available: bool
    unavailable_reason: str | None
    open_decisions: tuple[DecisionBead, ...]
    escalations: tuple[EscalationHit, ...]
    export_as_of: datetime | None = None


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
    # A container epic (e.g. bu-ckkpz itself, "Owner Decision Desk: decision
    # beads become first-class attention citizens") is not itself a single
    # decision the owner resolves in one step, even if its title or label
    # happens to match below.
    if issue.get("issue_type") == "epic":
        return False
    # Convention first (bu-ckkpz.1): the `decision` label is the canonical
    # marker -- same field `bd list --label decision` and
    # scripts/lint_decision_beads.py key off.
    labels = issue.get("labels")
    if isinstance(labels, list) and _DECISION_LABEL in labels:
        return True
    # Legacy fallback for beads that predate the convention and were never
    # labeled -- see "Decision-bead detection" in the module docstring.
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
    # Same rationale as _is_decision_bead's epic exclusion: a container epic
    # whose title happens to mention "deploy" is not itself a deploy blocked
    # on a decision.
    if issue.get("issue_type") == "epic":
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
            return DecisionDigest(checked_at, False, "export_missing", (), (), None)
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if checked_at - mtime > _STALE_EXPORT_AGE:
            return DecisionDigest(checked_at, False, "export_stale", (), (), mtime)
        issues = _load_issues(path)
    except Exception as exc:  # noqa: BLE001 - degraded-mode contract: never raise
        logger.warning("decision_review: failed to read beads export: %s", exc, exc_info=True)
        return DecisionDigest(checked_at, False, f"export_read_error:{exc}", (), (), None)

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
    return DecisionDigest(checked_at, True, None, ordered_decisions, ordered_escalations, mtime)


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
# Convention-lint integration (bu-hmdqz.6) -- makes lint_decision_beads.py's
# --check-unlabeled-markers mode a live, automated door instead of a manual
# `make lint-decision-beads` a human has to remember to run. Runs the script
# as a subprocess (never imported) so this module's own import chain stays
# untouched by the lint script's independence from the `butlers` package --
# see that script's own docstring for the rationale in the other direction.
# ---------------------------------------------------------------------------


def _run_unlabeled_marker_lint(export_path: Path) -> list[dict[str, Any]]:
    """Run ``scripts/lint_decision_beads.py --check-unlabeled-markers`` against
    *export_path* and return the failing entries. Never raises.

    Degraded-honesty contract mirrors the rest of this module: a lint
    subprocess failure (missing script, unexpected crash, malformed output)
    is logged and treated as "nothing to report" rather than sinking the
    weekly digest job -- this check augments the digest, it does not gate
    it. A real violation is only ever reported when the lint genuinely ran
    and found one.
    """
    if not _LINT_SCRIPT_PATH.is_file():
        logger.warning("decision_review: lint script not found at %s", _LINT_SCRIPT_PATH)
        return []

    cmd = [
        sys.executable,
        str(_LINT_SCRIPT_PATH),
        "--issues-json-file",
        str(export_path),
        "--check-unlabeled-markers",
        "--json",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_LINT_SUBPROCESS_TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("decision_review: lint subprocess failed (%s): %s", cmd, exc)
        return []

    # Exit codes: 0 = clean, 1 = violations found, 2 = could not obtain data
    # -- both 0 and 1 carry valid --json output on stdout.
    if proc.returncode not in (0, 1):
        logger.warning(
            "decision_review: lint subprocess exited %d: %s",
            proc.returncode,
            (proc.stderr or proc.stdout or "").strip()[-2000:],
        )
        return []

    try:
        results = json.loads(proc.stdout)
    except json.JSONDecodeError:
        logger.warning("decision_review: lint subprocess returned non-JSON stdout")
        return []
    if not isinstance(results, list):
        return []
    return [r for r in results if isinstance(r, dict) and not r.get("ok", True)]


def _compose_lint_violation_message(violations: list[dict[str, Any]]) -> str:
    count = len(violations)
    header = (
        f"\U0001f3f7️ Decision-bead convention: {count} bead"
        f"{'s' if count != 1 else ''} need migration (title matches a decision "
        "marker but missing the 'decision' label)"
    )
    lines = [header + "."]
    for v in violations[:_DIGEST_MAX_LISTED]:
        lines.append(f"- {v.get('id', '?')}: {v.get('title', '')}")
    remaining = count - _DIGEST_MAX_LISTED
    if remaining > 0:
        lines.append(f"... and {remaining} more.")
    return "\n".join(lines)


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
    failed / delivered) so an owner-decision push is never silently
    dropped -- mirrors ``secrets_lifecycle.run_secrets_lifecycle_check``.
    "failed" (not "deferred") for no_recipient_configured/delivery_error --
    bu-hmdqz.3: those are genuine terminal failures, not a benign hold that
    resolves on its own. Returns the outcome string.
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
            outcome="failed",
            channel="telegram",
            intent="send",
            priority=priority,
            reason="no_recipient_configured",
            dedup_key=dedup_key,
        )
        return "failed"

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
            outcome="failed",
            channel="telegram",
            intent="send",
            priority=priority,
            reason=f"delivery_error:{result.get('error', 'unknown')}",
            dedup_key=dedup_key,
        )
        return "failed"

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
    *,
    _now: datetime | None = None,
) -> dict[str, Any]:
    """Weekly digest of open owner-decision beads. Never fabricates an all-clear.

    Also runs the convention lint's ``--check-unlabeled-markers`` mode
    against the same export (bu-hmdqz.6) and delivers a separate low-priority
    message when it finds beads that still need migrating to the `decision`
    label -- independent of whether any decisions are *currently* open, so
    label-migration nudges keep firing even during a genuine all-clear week.

    ``_now`` is a test-only clock override (see ``compute_decision_digest``'s
    ``now``); the scheduler never passes it, so production uses the real wall
    clock. Injecting it lets faketime-matrix tests anchor the staleness check
    to the same fixed reference the export mtime is stamped to.
    """
    del job_args
    digest = compute_decision_digest(now=_now)

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
            # A beads-export-unavailable skip is a terminal FAILED run, not a
            # "deferred" hold: "deferred" means THIS notification is queued for
            # redelivery (a deferred envelope with a deliver_at exists — the
            # quiet-hours pattern). Here there is no envelope and no redelivery
            # of this attempt; the weekly cron's next run is a brand-new attempt.
            # A run that could not do its job because a source was down is a
            # failed run (bu-xnusv; same all-terminal-failure-paths convention as
            # bu-hmdqz.3's no_recipient/delivery_error). The distinct cause lives
            # in the queryable ``reason`` column, so no new outcome value is
            # needed.
            outcome="failed",
            channel="telegram",
            intent="send",
            priority="low",
            reason=f"data_unavailable:{digest.unavailable_reason}",
            dedup_key="decision_review_digest",
        )
        return {"available": False, "reason": digest.unavailable_reason}

    lint_violations = _run_unlabeled_marker_lint(_DEFAULT_EXPORT_PATH)
    if lint_violations:
        lint_message = _compose_lint_violation_message(lint_violations)
        await _deliver(
            pool, message=lint_message, dedup_key="decision_lint_violations", priority="low"
        )

    if not digest.open_decisions:
        return {
            "available": True,
            "open_decisions": 0,
            "outcome": "no_decisions",
            "lint_violations": len(lint_violations),
        }

    message = _compose_weekly_digest_message(digest)
    outcome = await _deliver(
        pool, message=message, dedup_key="decision_review_digest", priority="medium"
    )
    return {
        "available": True,
        "open_decisions": len(digest.open_decisions),
        "outcome": outcome,
        "lint_violations": len(lint_violations),
    }


async def run_decision_escalation_check(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    _now: datetime | None = None,
) -> dict[str, Any]:
    """Escalate once per (decision, blocked-issue) pair once a block exceeds 48h.

    Debounced via ``public.audit_log`` (no new migration), keyed by
    ``"<decision_id>:<blocked_id>"`` -- mirrors
    ``butlers.jobs.deploy_drift.maybe_escalate_drift``'s fingerprint pattern.
    Only a successfully *delivered* escalation writes the marker, so a
    quiet-hours-suppressed escalation is retried on the next tick instead of
    being silently dropped forever.

    ``_now`` is a test-only clock override (see ``compute_decision_digest``'s
    ``now``); the scheduler never passes it, so production uses the real wall
    clock. Injecting it lets faketime-matrix tests anchor the staleness check
    to the same fixed reference the export mtime is stamped to.
    """
    del job_args
    digest = compute_decision_digest(now=_now)

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
