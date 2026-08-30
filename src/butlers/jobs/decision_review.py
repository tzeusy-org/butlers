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
Beads (``bd``) issue data lives in the shared Dolt server at
``dolt.parrot-hen.ts.net:3307`` (see ``.beads/config.yaml``), reached today
only by the ``bd`` CLI running directly on the host. No butler daemon or
dashboard-api process can reach it: ``docker-compose.yml``'s ``egress``
network explicitly DROPs all RFC1918/loopback/CGNAT traffic
(``scripts/egress-firewall.sh``) by design, including the tracker endpoint's
Tailscale CGNAT address. There is no bd MCP tool, no bd-to-Postgres bridge,
and no existing precedent for a butler reaching beads data live (verified by
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

Decision-bead detection: convention label only
---------------------------------------------------------------------------
bu-ckkpz.1 (the structured decision-bead convention: ``decision`` label +
``metadata.decision.{options,default}`` + native ``due_at`` deadline,
enforced by ``scripts/lint_decision_beads.py`` -- see AGENTS.md
"Decision-bead convention") has shipped (PR #3141). :func:`_is_decision_bead`
classifies a bead solely by the convention's own marker -- the ``decision``
label, the same field ``bd list --label decision`` and the linter key off
(see :data:`_DECISION_LABEL`).

The legacy title-text regex fallback (for beads that predated the label and
were never labeled) was retired in bu-uo37y once the fleet finished migrating:
the label backfill landed and ``scripts/lint_decision_beads.py`` runs clean,
so every open decision bead now carries the label. The linter keeps its own
mirror of the title-marker regex as the safety net that catches a
decision-shaped bead filed WITHOUT the label -- that lint runs as part of the
weekly digest (:func:`_run_unlabeled_marker_lint`) and surfaces such a bead to
the owner rather than silently misclassifying it at runtime.

The label check excludes ``issue_type == "epic"``: a container epic (e.g.
bu-ckkpz itself, "Owner Decision Desk: decision beads become first-class
attention citizens") is not itself a single decision the owner resolves in one
step, even when its label happens to match.

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
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg

from butlers.api.routers import audit as audit_router
from butlers.beads_snapshot import DEFAULT_BEADS_EXPORT_PATH, STALE_BEADS_EXPORT_AGE
from butlers.core.approvals_policy import (
    get_approvals_policy_quiet_hours,
    is_policy_quiet_now,
)
from butlers.core.attention_ledger import get_suppressing_context_signal, record_attention_event
from butlers.credential_store import resolve_owner_telegram_recipient

logger = logging.getLogger(__name__)

_ACTOR = "decision_review"
_ESCALATED_ACTION = "decision_escalation_notified"
_ESCALATION_THRESHOLD = timedelta(hours=48)

# The convention's own marker (bu-ckkpz.1) -- mirrors DECISION_LABEL in
# scripts/lint_decision_beads.py. The sole runtime classifier now that the
# legacy title-marker fallback is retired (bu-uo37y); see "Decision-bead
# detection" above.
_DECISION_LABEL = "decision"

_DEPLOY_TITLE_MARKER = re.compile(r"\bdeploy", re.IGNORECASE)
_OPEN_STATUSES = frozenset({"open", "in_progress", "blocked"})
_DIGEST_MAX_LISTED = 10

# Overridable for tests / non-default deploy layouts; defaults to the
# docker-compose bind-mount target (see module docstring).
_DEFAULT_EXPORT_PATH = DEFAULT_BEADS_EXPORT_PATH
# Beyond this age the export is treated as stale rather than trusted --
# either bd hasn't run on the host checkout in a long time, or (fresh clone)
# the mount target is an empty placeholder Docker created for a missing file.
_STALE_EXPORT_AGE = STALE_BEADS_EXPORT_AGE

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
    description: str | None
    options: tuple[str, ...] | None
    default: str | None
    due_at: datetime | None
    structured_details_available: bool
    structured_details_unavailable_reason: str | None


@dataclass(frozen=True)
class StructuredDecisionDetails:
    """Validated read-only structured context from one exported Beads record."""

    description: str | None
    options: tuple[str, ...] | None
    default: str | None
    due_at: datetime | None
    available: bool
    unavailable_reason: str | None


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


@dataclass(frozen=True)
class DecisionLintResult:
    """Result of the scheduled convention-lint subprocess.

    A clean lint result and an unavailable lint subprocess are intentionally
    distinct: treating both as an empty violation list would let the weekly
    digest fabricate a calm audit when its strict migration check never ran.
    """

    available: bool
    unavailable_reason: str | None
    violations: tuple[dict[str, Any], ...]


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
    # The `decision` label is the sole runtime classifier (bu-ckkpz.1;
    # bu-uo37y retired the legacy title-marker fallback) -- same field
    # `bd list --label decision` and scripts/lint_decision_beads.py key off. A
    # decision-shaped bead filed without the label is caught by the digest's
    # unlabeled-marker lint (_run_unlabeled_marker_lint), not misclassified here.
    labels = issue.get("labels")
    return isinstance(labels, list) and _DECISION_LABEL in labels


def _structured_decision_details(issue: dict[str, Any]) -> StructuredDecisionDetails:
    """Return only trusted decision context without inventing a fallback value."""
    description = issue.get("description")
    if not isinstance(description, str):
        description = None

    raw_due_at = issue.get("due_at")
    if raw_due_at is None:
        due_at = None
        due_at_reason = "due_at_missing"
    else:
        due_at = _parse_timestamp(raw_due_at)
        due_at_reason = "due_at_malformed" if due_at is None else None

    if "metadata" not in issue:
        return StructuredDecisionDetails(description, None, None, due_at, False, "metadata_missing")
    metadata = issue["metadata"]
    if not isinstance(metadata, dict):
        return StructuredDecisionDetails(
            description, None, None, due_at, False, "metadata_malformed"
        )

    if "decision" not in metadata:
        return StructuredDecisionDetails(description, None, None, due_at, False, "metadata_missing")
    decision_metadata = metadata["decision"]
    if not isinstance(decision_metadata, dict):
        return StructuredDecisionDetails(
            description, None, None, due_at, False, "metadata_malformed"
        )

    raw_options = decision_metadata.get("options")
    if (
        not isinstance(raw_options, list)
        or not raw_options
        or any(not isinstance(option, str) or not option.strip() for option in raw_options)
        or len(set(raw_options)) != len(raw_options)
    ):
        return StructuredDecisionDetails(
            description, None, None, due_at, False, "metadata_malformed"
        )
    options = tuple(raw_options)

    default = decision_metadata.get("default")
    if not isinstance(default, str) or not default.strip() or default not in options:
        return StructuredDecisionDetails(
            description, None, None, due_at, False, "metadata_malformed"
        )
    if due_at_reason is not None:
        return StructuredDecisionDetails(description, options, default, None, False, due_at_reason)

    return StructuredDecisionDetails(description, options, default, due_at, True, None)


def _is_p1_bug(issue: dict[str, Any]) -> bool:
    return (
        issue.get("status") in _OPEN_STATUSES
        and issue.get("issue_type") == "bug"
        and issue.get("priority") == 1
    )


def _is_deploy_bead(issue: dict[str, Any]) -> bool:
    if issue.get("status") not in _OPEN_STATUSES:
        return False
    # Unlike _is_decision_bead, epics are intentionally NOT excluded here: the
    # deploy-blocked side of a blocks-edge legitimately can be a container epic
    # ("Epic: ship v2 [deploy pending decision]"), and silently dropping a >48h
    # deploy-blocked escalation is strictly worse than an occasional extra
    # escalation on a container (bu-pnofc).
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
    except Exception as exc:  # noqa: BLE001 - degraded-mode contract: never raise
        logger.warning("decision_review: failed to stat beads export: %s", exc, exc_info=True)
        return DecisionDigest(checked_at, False, f"export_read_error:{exc}", (), (), None)

    try:
        if checked_at - mtime > _STALE_EXPORT_AGE:
            return DecisionDigest(checked_at, False, "export_stale", (), (), mtime)
    except Exception as exc:  # noqa: BLE001 - degraded-mode contract: never raise
        logger.warning(
            "decision_review: failed to assess beads export freshness: %s", exc, exc_info=True
        )
        return DecisionDigest(checked_at, False, f"export_read_error:{exc}", (), (), mtime)

    try:
        issues = _load_issues(path)
    except Exception as exc:  # noqa: BLE001 - degraded-mode contract: never raise
        logger.warning("decision_review: failed to read beads export: %s", exc, exc_info=True)
        return DecisionDigest(checked_at, False, f"export_read_error:{exc}", (), (), mtime)

    decisions: dict[str, DecisionBead] = {}
    for issue_id, issue in issues.items():
        if not _is_decision_bead(issue):
            continue
        created_at = _parse_timestamp(issue.get("created_at"))
        if created_at is None:
            continue
        details = _structured_decision_details(issue)
        decisions[issue_id] = DecisionBead(
            id=issue_id,
            title=issue.get("title") or issue_id,
            priority=issue.get("priority"),
            created_at=created_at,
            age=checked_at - created_at,
            description=details.description,
            options=details.options,
            default=details.default,
            due_at=details.due_at,
            structured_details_available=details.available,
            structured_details_unavailable_reason=details.unavailable_reason,
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


def _run_unlabeled_marker_lint(export_path: Path) -> DecisionLintResult:
    """Run ``scripts/lint_decision_beads.py --check-unlabeled-markers`` against
    *export_path* and return its availability plus failing entries. Never raises.

    A clean result is available with no violations. A missing script/input,
    subprocess failure, unexpected exit, or malformed result is unavailable
    so the caller can record the existing failed scheduled-audit path rather
    than fabricating a calm ``no_decisions`` outcome.
    """
    try:
        lint_script_exists = _LINT_SCRIPT_PATH.is_file()
    except OSError as exc:
        logger.warning(
            "decision_review: unable to inspect lint script at %s: %s", _LINT_SCRIPT_PATH, exc
        )
        return DecisionLintResult(False, f"lint_script_unavailable:{type(exc).__name__}", ())

    if not lint_script_exists:
        logger.warning("decision_review: lint script not found at %s", _LINT_SCRIPT_PATH)
        return DecisionLintResult(False, "lint_script_missing", ())

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
    except subprocess.TimeoutExpired as exc:
        logger.warning("decision_review: lint subprocess failed (%s): %s", cmd, exc)
        return DecisionLintResult(False, "lint_subprocess_timeout", ())
    except UnicodeError as exc:
        logger.warning("decision_review: lint subprocess returned undecodable output: %s", exc)
        return DecisionLintResult(False, "lint_subprocess_output_decode_error", ())
    except OSError as exc:
        logger.warning("decision_review: lint subprocess failed (%s): %s", cmd, exc)
        return DecisionLintResult(False, f"lint_subprocess_error:{type(exc).__name__}", ())

    # Exit codes: 0 = every result is clean, 1 = at least one result has
    # violations, 2 = could not obtain data. Both 0 and 1 carry valid --json
    # output on stdout.
    if proc.returncode == 2:
        logger.warning(
            "decision_review: lint subprocess could not obtain input: %s",
            (proc.stderr or proc.stdout or "").strip()[-2000:],
        )
        return DecisionLintResult(False, "lint_input_unavailable", ())
    if proc.returncode not in (0, 1):
        logger.warning(
            "decision_review: lint subprocess exited %d: %s",
            proc.returncode,
            (proc.stderr or proc.stdout or "").strip()[-2000:],
        )
        return DecisionLintResult(False, f"lint_subprocess_exit:{proc.returncode}", ())

    try:
        results = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        logger.warning("decision_review: lint subprocess returned non-JSON stdout")
        return DecisionLintResult(False, "lint_output_non_json", ())
    if not isinstance(results, list) or any(
        not isinstance(result, dict)
        or not isinstance(result.get("id"), str)
        or not isinstance(result.get("title"), str)
        or not isinstance(result.get("ok"), bool)
        or not isinstance(result.get("violations"), list)
        or any(not isinstance(violation, str) for violation in result["violations"])
        or result["ok"] != (not result["violations"])
        for result in results
    ):
        logger.warning("decision_review: lint subprocess returned malformed JSON result")
        return DecisionLintResult(False, "lint_output_invalid_shape", ())
    if (proc.returncode == 0) != all(result["ok"] for result in results):
        logger.warning("decision_review: lint subprocess exit code disagreed with JSON results")
        return DecisionLintResult(False, "lint_output_invalid_shape", ())
    return DecisionLintResult(
        True,
        None,
        tuple(result for result in results if not result["ok"]),
    )


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

    if is_policy_quiet_now(policy, now=datetime.now(UTC)):
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

    lint_result = _run_unlabeled_marker_lint(_DEFAULT_EXPORT_PATH)
    if not lint_result.available:
        logger.warning(
            "decision_review_digest: convention lint unavailable (%s) -- skipping digest "
            "rather than reporting a fabricated all-clear",
            lint_result.unavailable_reason,
        )
        await record_attention_event(
            pool,
            origin_butler=_ACTOR,
            source="notify",
            outcome="failed",
            channel="telegram",
            intent="send",
            priority="low",
            reason=f"data_unavailable:{lint_result.unavailable_reason}",
            dedup_key="decision_review_digest",
        )
        return {"available": False, "reason": lint_result.unavailable_reason}

    lint_violations = lint_result.violations
    if lint_violations:
        lint_message = _compose_lint_violation_message(list(lint_violations))
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
                result="delivered",
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
