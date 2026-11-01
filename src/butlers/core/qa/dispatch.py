"""QA investigation dispatch engine.

Unified investigation lifecycle management for all QA-originated issues
regardless of discovery source.  Creates worktrees, spawns LLM agents,
monitors timeouts, and records outcomes.

The dispatcher applies the central investigation 10-gate sequence
but with QA-specific wiring:
 - Gate 0: QA self-recursion barrier (suppress findings from QA self-healing/investigation sessions)
 - Gate 1: No-recursion guard (trigger_source == "healing" → skip)
 - Gate 2: Opt-in gate (always on for QA dispatcher)
 - Gate 3: Fingerprint (pre-computed from QaFinding)
 - Gate 4: Fingerprint persistence (skipped — no session_id in QA path)
 - Gate 5: Severity gate
 - Gate 5.5: Infra condition suppression (bu-27dxl.6.4 — infra_state findings
   only; suppressed before any healing_attempts row is ever created)
 - Gate 6: Novelty gate (atomic check+insert via create_or_join_attempt)
 - Gate 7: Cooldown gate
 - Gate 8: Concurrency cap
 - Gate 9: Circuit breaker
 - Gate 10: Model resolution

Note: triage performs a fast non-atomic dedup check (gates 1-3 above) to
filter obvious duplicates early.  This dispatcher applies the authoritative
atomic claim via create_or_join_attempt (gate 6).

Gate 5.5 exists because ``core.qa.sources.infra_state.InfraStateSource``
reconciles every patrol tick's findings into the durable
``public.infra_conditions`` ledger (``butlers.core.infra_conditions``,
bu-27dxl.6.2/.6.4) using the SAME fingerprint a finding already carries. A
still-active condition means the underlying infra problem is already known
and aging in that ledger — dispatching yet another investigation agent for
it would just re-diagnose (and, per the "no-commits-produced" safety net,
re-conclude unfixable for) a failure that is not a code bug in the first
place. Gate 5.5 matches on the ledger's ``(source, fingerprint)`` identity
and, when active, records a decision-only ``healing_dispatch_events`` row
(no ``healing_attempts`` row, no LLM session, no worktree) instead of
proceeding to Gate 6.

Investigation agents run in sandboxed worktree environments with only PATH and
build-tool variables; they never receive a GitHub credential.  Automated
publication goes only through an injected trusted publisher
(``QaDispatchConfig.publisher``, sealed export over a closed transport); with
none configured, which is the production default until isolation is proven
(bu-mc01y5.7), nothing is pushed and the attempt fails ``publication_unavailable``.

Spec reference
--------------
openspec/changes/qa-staffer/specs/qa-investigation-dispatch/spec.md
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg

from butlers.core.git_env import FETCH_COMMAND_ARGS, harden_git_env, isolated_git_env
from butlers.core.healing.anonymizer import anonymize, validate_anonymized
from butlers.core.healing.constants import (
    CIRCUIT_BREAKER_FAILURE_STATUSES,
    UNFIXABLE_SENTINEL_FILENAME,
)
from butlers.core.healing.tracking import (
    PublicationLineageHeld,
    count_active_attempts,
    create_dispatch_event,
    create_or_join_attempt,
    delete_orphaned_attempt,
    get_publication_hold,
    get_recent_attempt,
    record_phase_session,
    update_attempt_status,
    update_phase_session_status,
)
from butlers.core.healing.worktree import (
    WorktreeCreationError,
    create_healing_worktree,
    remove_healing_worktree,
)
from butlers.core.infra_conditions import get_active_condition
from butlers.core.metrics import ButlerMetrics
from butlers.core.model_routing import Complexity, resolve_model
from butlers.core.qa.diff import parse_unified_diff
from butlers.core.qa.findings import (
    update_finding_attempt,
    update_finding_dedup_reason,
    update_finding_dispatch_queued,
)
from butlers.core.qa.journal import (
    record_escalated_event,
    record_event,
    record_pr_drafted_event,
    record_pr_merged_event,
    record_wait_event_once,
)
from butlers.core.qa.models import QaFinding
from butlers.core.qa.notes import InvestigationNotes, ParseStatus, parse_investigation_notes
from butlers.core.qa.prompts import build_investigation_prompt, build_review_followup_prompt
from butlers.core.qa.publication.export import GitIdentity
from butlers.core.qa.publication.proposal import publish_investigation
from butlers.core.qa.publication.publisher import (
    PublicationResult,
    PublicationStatus,
    QaPublisher,
)
from butlers.core.qa.repo_whitelist import RepoWhitelist
from butlers.core.qa.severity import failed_with_human_action
from butlers.core.qa.sources.infra_state import SOURCE_NAME as INFRA_STATE_SOURCE_NAME
from butlers.core.qa.sources.infra_state import condition_source_for
from butlers.core.qa.triage import TriagedFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenTelemetry — optional, graceful no-op when not configured
# ---------------------------------------------------------------------------

try:
    from opentelemetry import context as otel_context
    from opentelemetry import trace

    from butlers.core.telemetry import get_traceparent_env, tag_butler_span

    _tracer = trace.get_tracer("butlers.qa")
    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Branch prefix used by all QA investigations.
_QA_PREFIX = "qa"

#: Branch prefix used by PR review follow-up agents.
_QA_REVIEW_PREFIX = "qa-review"

#: Maximum number of follow-up dispatches per PR per patrol cycle (rate-limit).
_FOLLOW_UP_BASE_DELAY = timedelta(minutes=5)
_FOLLOW_UP_MAX_DELAY = timedelta(hours=6)

#: Maximum characters to store in review_feedback_summary.
_MAX_FEEDBACK_SUMMARY_LEN = 2000

#: Maximum characters to store in last_follow_up_error.
_MAX_FOLLOWUP_ERROR_LEN = 500

#: Review states that require a follow-up agent dispatch.
#: ``commented`` is included so unresolved review threads (even without an
#: explicit CHANGES_REQUESTED decision) also trigger follow-up dispatch.
_ACTIONABLE_REVIEW_STATES = frozenset({"changes_requested", "commented"})


async def _fetch_patrol_started_at(pool: asyncpg.Pool, patrol_id: uuid.UUID) -> datetime | None:
    return await pool.fetchval(
        "SELECT started_at FROM public.qa_patrols WHERE id = $1",
        patrol_id,
    )


def _flagged_event_detail(finding: QaFinding) -> str:
    return (
        f"{finding.exception_type} at {finding.call_site}; "
        f"source={finding.source_type}/{finding.source_butler}; "
        f"occurrences={finding.occurrence_count}; severity={finding.severity}"
    )


#: PR labels applied to QA-originated investigation PRs.
_DEFAULT_PR_LABELS = ["self-healing", "automated"]

#: CredentialStore key for the GitHub token used by QA investigations.
QA_GH_TOKEN_KEY = "BUTLERS_QA_GH_TOKEN"
QA_GIT_AUTHOR_NAME_KEY = "BUTLERS_QA_GIT_AUTHOR_NAME"
QA_GIT_AUTHOR_EMAIL_KEY = "BUTLERS_QA_GIT_AUTHOR_EMAIL"

#: Sentinel file placed by investigation agent to signal unfixable error.
_UNFIXABLE_FILE = UNFIXABLE_SENTINEL_FILENAME

#: Structured investigation notes artifact written at worktree root by the QA
#: investigation agent. Persisted before terminal worktree teardown.
_INVESTIGATION_NOTES_JSON = Path(".qa") / "investigation_notes.json"


def _get_qa_investigation_notes_parse_total():
    """Return the qa_investigation_notes_parse_total Prometheus Counter."""
    try:
        from prometheus_client import Counter

        return Counter(
            "qa_investigation_notes_parse_total",
            "Total QA investigation notes parse attempts by status",
            labelnames=["status"],
        )
    except (ImportError, ValueError):
        logger.debug(
            "Failed to initialize Prometheus counter "
            "'qa_investigation_notes_parse_total'; metric will not be exported",
            exc_info=True,
        )
        return None


_qa_investigation_notes_parse_total = _get_qa_investigation_notes_parse_total()


def _set_investigation_notes_span_attributes(
    span: Any | None,
    *,
    notes_emitted: bool,
    parse_status: ParseStatus,
) -> None:
    """Annotate the root QA investigation span with notes artifact telemetry."""
    if span is None:
        return
    try:
        span.set_attribute("qa.notes_emitted", notes_emitted)
        span.set_attribute("qa.notes_parse_status", parse_status)
    except Exception:  # noqa: BLE001
        logger.debug(
            "Failed to set QA investigation notes span attributes",
            exc_info=True,
        )


def _record_investigation_notes_parse(status: ParseStatus) -> None:
    if _qa_investigation_notes_parse_total is None:
        return
    try:
        _qa_investigation_notes_parse_total.labels(status=status).inc()
    except Exception:  # noqa: BLE001
        logger.debug(
            "Failed to record qa_investigation_notes_parse_total metric",
            exc_info=True,
        )


#: Environment variables that must never leak into investigation agent sandboxes.
_BLOCKED_ENV_PREFIXES = (
    "BUTLERS_",
    "DATABASE_",
    "POSTGRES_",
    "DB_",
    "PG",
    "TELEGRAM_",
    "GOOGLE_",
    "OPENAI_",
    "ANTHROPIC_",
    "CLAUDE_",
)

#: Environment variables that are allowed in the investigation agent sandbox.
_ALLOWED_ENV_VARS = frozenset(
    {
        "GH_TOKEN",
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "UV_CACHE_DIR",
        "UV_PYTHON_PREFERENCE",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "TMPDIR",
        "TMP",
        "TEMP",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
    }
)

_QA_AGENT_SUBDIR = Path(".tmp/qa-agent")
_QA_AGENT_OVERRIDE = """# QA Agent Override

You are running inside a QA investigation helper directory under an isolated git worktree.
The repository root is the parent worktree, and git commands still operate on that repository.

Ignore repository-level workflow instructions about beads (`bd`), generic session-close/push
requirements, and unrelated operator procedures. They do not apply to QA investigations.

Rules specific to this workspace:
- Do not run `bd`.
- Do not push branches or open PRs yourself.
- Stay within the scope of the current QA investigation or PR-review follow-up.
"""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class QaDispatchConfig:
    """Configuration for the QA investigation dispatcher.

    Parameters
    ----------
    severity_threshold:
        Maximum severity score that triggers investigation.  Lower numbers
        are MORE severe.  Default 2 (medium).  Set to 1 to only investigate
        high/critical errors.
    cooldown_minutes:
        Minutes between investigations of the same fingerprint after any
        terminal status.  Default 60.
    max_concurrent:
        Maximum number of simultaneous ``investigating`` rows.  Default 2.
    circuit_breaker_threshold:
        Number of consecutive failure statuses before all dispatch is halted.
        Default 5.  ``unfixable`` does not count as a failure.
    timeout_minutes:
        Maximum wall-clock minutes for an investigation agent session.
        Default 30.
    pr_labels:
        Fixed labels for QA investigation PRs; the publisher sanitizes them and
        the transport applies exactly these.
    dashboard_base_url:
        Optional dashboard URL for inclusion in investigation prompts and PRs.
        When ``None``, links are omitted (dashboard may be on private tailnet).
    """

    severity_threshold: int = 2
    cooldown_minutes: int = 60
    max_concurrent: int = 2
    circuit_breaker_threshold: int = 5
    timeout_minutes: int = 30
    pr_labels: list[str] = field(default_factory=lambda: list(_DEFAULT_PR_LABELS))
    dashboard_base_url: str | None = None
    repo_whitelist: RepoWhitelist | None = None
    """Repository whitelist the publisher enforces; ``None`` refuses publication."""
    publisher: QaPublisher | None = None
    """Trusted deterministic publisher.  ``None`` (the production default) disables
    publication: a committed investigation fails ``publication_unavailable``."""


# ---------------------------------------------------------------------------
# Dispatch result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QaDispatchResult:
    """Result of a single QA investigation dispatch attempt.

    Attributes
    ----------
    accepted:
        ``True`` if an investigation agent was spawned.
    fingerprint:
        The 64-character hex fingerprint, or ``None`` if dispatch was skipped
        before fingerprint access.
    reason:
        Short machine-readable reason code (``"dispatched"`` on success).
    attempt_id:
        UUID of the created ``healing_attempts`` row, or ``None``.
    """

    accepted: bool
    fingerprint: str | None
    reason: str
    attempt_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Sandbox environment builder
# ---------------------------------------------------------------------------


def build_sandbox_env(
    gh_token: str | None,
    *,
    git_author_name: str | None = None,
    git_author_email: str | None = None,
) -> dict[str, str]:
    """Build a minimal sandboxed environment for investigation agents.

    Only allows: GH_TOKEN, PATH, HOME, build-tool variables, and optional
    git author identity for non-interactive commits.
    Strips all BUTLERS_* vars, database connection strings, API keys,
    OAuth tokens, and any other butler runtime variables.

    Parameters
    ----------
    gh_token:
        GitHub token from CredentialStore.  If ``None``, GH_TOKEN is not
        included in the returned environment.

    git_author_name / git_author_email:
        Optional git author identity injected as both author and committer
        environment variables for QA-generated commits.

    Returns
    -------
    dict[str, str]
        Minimal environment dict safe for investigation agent subprocesses.
    """
    env: dict[str, str] = {}

    current_env = dict(os.environ)
    for key, value in current_env.items():
        # Check blocked prefixes
        blocked = any(key.upper().startswith(prefix) for prefix in _BLOCKED_ENV_PREFIXES)
        if blocked:
            continue

        # Allow only explicit allowlist
        if key in _ALLOWED_ENV_VARS:
            env[key] = value

    # Inject GH_TOKEN from CredentialStore (overrides any env value)
    if gh_token:
        env["GH_TOKEN"] = gh_token
    elif "GH_TOKEN" in env:
        # Remove any GH_TOKEN that snuck in from environment (not from secrets store)
        del env["GH_TOKEN"]

    author_name = (git_author_name or "").strip()
    author_email = (git_author_email or "").strip()
    if author_name:
        env["GIT_AUTHOR_NAME"] = author_name
        env["GIT_COMMITTER_NAME"] = author_name
    if author_email:
        env["GIT_AUTHOR_EMAIL"] = author_email
        env["GIT_COMMITTER_EMAIL"] = author_email

    return env


def build_agent_env(
    *,
    git_author_name: str | None = None,
    git_author_email: str | None = None,
) -> dict[str, str]:
    """Build the environment for LLM agent sessions: never any GitHub credential.

    Agents run in an agent-controlled worktree, so they get the sandbox allowlist
    without ``GH_TOKEN`` or any git credential helper variable.  Only the trusted
    dispatcher (PR-status polling) holds the token.
    """
    return build_sandbox_env(
        None, git_author_name=git_author_name, git_author_email=git_author_email
    )


def _prepare_agent_workspace(worktree_path: Path) -> Path:
    """Create an ignored helper cwd with QA-specific AGENTS.md overrides."""
    agent_dir = worktree_path / _QA_AGENT_SUBDIR
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "AGENTS.md").write_text(_QA_AGENT_OVERRIDE, encoding="utf-8")
    for name in ("src", "tests", "roster", "frontend", "pyproject.toml", "uv.lock"):
        link_path = agent_dir / name
        target_path = worktree_path / name
        if link_path.exists() or link_path.is_symlink():
            continue
        if target_path.exists():
            link_path.symlink_to(target_path)
    return agent_dir


def _build_git_prep_env() -> dict[str, str]:
    """Build a token-free, hardened, non-interactive env for dispatcher git commands."""
    env = build_sandbox_env(None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return harden_git_env(env)


_PUBLICATION_UNAVAILABLE = "publication_unavailable"
_PUBLICATION_UNAVAILABLE_DETAIL = (
    "publication_unavailable: automated QA publication is disabled until the isolated "
    "publisher lands; nothing was pushed and no PR was created or changed"
)


def _follow_up_backoff_delay(follow_up_count: int) -> timedelta:
    """Return the exponential backoff delay before the next review follow-up.

    ``follow_up_count`` is the number of previous follow-up dispatches already
    made for the PR. The first follow-up is immediate. Subsequent follow-ups
    are delayed with exponential backoff capped at ``_FOLLOW_UP_MAX_DELAY``.
    """
    if follow_up_count <= 0:
        return timedelta(0)
    delay = _FOLLOW_UP_BASE_DELAY * (2**follow_up_count)
    return min(delay, _FOLLOW_UP_MAX_DELAY)


def _follow_up_dispatch_due(
    *,
    now: datetime,
    follow_up_count: int,
    last_follow_up_at: datetime | None,
) -> bool:
    """Return True when a PR review follow-up is eligible for dispatch."""
    if follow_up_count <= 0 or last_follow_up_at is None:
        return True
    return (now - last_follow_up_at) >= _follow_up_backoff_delay(follow_up_count)


# ---------------------------------------------------------------------------
# Branch inspection
# ---------------------------------------------------------------------------


async def _detect_no_op_branch(
    repo_root: Path,
    branch_name: str,
    env: dict[str, str],
    base: str = "main",
) -> bool:
    """Return True if *branch_name* has no commits ahead of *base*.

    Uses ``git log <base>..<branch_name> --oneline`` to count ahead commits.
    Returns False on any subprocess failure (safe: lets push proceed).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "log",
            # A planted ``log.showSignature`` with ``gpg.program`` would otherwise run the
            # program for any commit carrying a ``gpgsig`` header.
            "--no-show-signature",
            f"{base}..{branch_name}",
            "--oneline",
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=harden_git_env(env),
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return False
        # No lines → no commits ahead → this is a no-op branch
        return stdout.strip() == b""
    except Exception:  # noqa: BLE001
        logger.debug("_detect_no_op_branch: subprocess failed, assuming not a no-op", exc_info=True)
        return False


async def _resolve_pr_by_head(
    repo_root: Path,
    branch_name: str,
    env: dict[str, str],
    *,
    repo: str | None = None,
) -> tuple[str | None, int | None]:
    """Look up an open PR by head branch using ``gh pr list``.

    Returns ``(pr_url, pr_number)`` if exactly one open PR is found for the
    given head branch, ``(None, None)`` otherwise.  *repo* (``owner/name``) is passed as
    ``--repo`` and the command runs from a neutral cwd, so the agent-writable ``repo_root``
    ``.git`` cannot steer the token-bearing lookup to another repository.  Without a repo
    the lookup fails closed.
    """
    import json as _json

    if not repo:
        return None, None
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            branch_name,
            "--state",
            "open",
            "--json",
            "number,url",
            cwd=tempfile.gettempdir(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None, None
        data = _json.loads(stdout.decode("utf-8", errors="replace"))
        if isinstance(data, list) and len(data) == 1:
            entry = data[0]
            if not isinstance(entry, dict):
                return None, None
            url = entry.get("url")
            number_raw = entry.get("number")
            if not isinstance(url, str):
                return None, None
            if isinstance(number_raw, bool) or number_raw is None:
                return None, None
            if isinstance(number_raw, int):
                number = number_raw
            else:
                try:
                    number = int(number_raw)
                except (TypeError, ValueError):
                    return None, None
            return url, number
        return None, None
    except Exception:  # noqa: BLE001
        logger.debug("_resolve_pr_by_head: lookup failed for branch %r", branch_name, exc_info=True)
        return None, None


async def _emit_investigation_notes_journal_events(
    pool: asyncpg.Pool,
    attempt_id: uuid.UUID,
    notes: InvestigationNotes,
) -> None:
    """Emit journal events derived from a successfully parsed notes artifact."""

    for item in notes.counter_evidence:
        await record_event(
            pool,
            attempt_id=attempt_id,
            step="considered",
            text=item.hypothesis,
            detail=f"{item.verdict}: {item.reason}",
        )

    why_this_fix = notes.why_this_fix[:80]
    await record_event(
        pool,
        attempt_id=attempt_id,
        step="concluded",
        text=notes.hypothesis,
        detail=f"proposal rationale: {why_this_fix}",
    )


async def _persist_investigation_notes(
    pool: asyncpg.Pool,
    attempt_id: uuid.UUID,
    worktree_path: Path,
    *,
    diff_snapshot: list[dict[str, str]] | None = None,
    otel_span: Any | None = None,
) -> ParseStatus:
    """Persist agent-authored structured notes for all findings on an attempt.

    Notes persistence is a terminal-cleanup side effect: parse/read/persistence
    failures are visible through logs and metrics, but must not rewrite the
    already-decided investigation terminal state.
    """
    notes_path = worktree_path / _INVESTIGATION_NOTES_JSON
    try:
        raw = notes_path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        _record_investigation_notes_parse("failed")
        _set_investigation_notes_span_attributes(
            otel_span,
            notes_emitted=False,
            parse_status="failed",
        )
        logger.info(
            "QA investigation emitted no structured notes artifact "
            "(attempt=%s path=%s); skipping notes persistence",
            attempt_id,
            notes_path,
        )
        return "failed"
    except (OSError, UnicodeDecodeError) as exc:
        _record_investigation_notes_parse("failed")
        _set_investigation_notes_span_attributes(
            otel_span,
            notes_emitted=True,
            parse_status="failed",
        )
        logger.warning(
            "Failed to read QA investigation notes artifact "
            "(attempt=%s path=%s): %s; skipping notes persistence",
            attempt_id,
            notes_path,
            exc,
        )
        return "failed"

    notes, status = parse_investigation_notes(raw)
    _record_investigation_notes_parse(status)
    _set_investigation_notes_span_attributes(
        otel_span,
        notes_emitted=True,
        parse_status=status,
    )
    if notes is None:
        logger.info(
            "QA investigation notes artifact did not parse "
            "(attempt=%s path=%s status=%s); skipping notes persistence",
            attempt_id,
            notes_path,
            status,
        )
        return status

    payload = notes.model_dump(mode="json")
    if diff_snapshot is not None:
        payload["diff_snapshot"] = diff_snapshot
    await pool.execute(
        """
        UPDATE public.qa_findings
        SET structured_evidence = jsonb_set(
                COALESCE(structured_evidence, '{}'::jsonb),
                '{investigation_notes}',
                $2,
                true
            )
        WHERE healing_attempt_id = $1
        """,
        attempt_id,
        payload,
    )
    await _emit_investigation_notes_journal_events(pool, attempt_id, notes)
    logger.info(
        "Persisted QA investigation notes artifact (attempt=%s path=%s status=%s)",
        attempt_id,
        notes_path,
        status,
    )
    return status


async def _capture_commit_diff_snapshot(worktree_path: Path) -> list[dict[str, str]]:
    """Capture the final commit's unified diff from the QA worktree."""

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(worktree_path),
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD~1..HEAD",
            "--unified=3",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # The worktree shares an agent-writable .git: no daemon secrets, no planted drivers.
            env=isolated_git_env(),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "Failed to capture QA commit diff snapshot (worktree=%s): %s",
                worktree_path,
                stderr.decode("utf-8", errors="replace").strip(),
            )
            return []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to capture QA commit diff snapshot (worktree=%s): %s",
            worktree_path,
            exc,
        )
        return []

    diff_text = stdout.decode("utf-8", errors="replace")
    return [line.model_dump(mode="json") for line in parse_unified_diff(diff_text)]


def _human_action_error_detail(error_detail: str | None) -> bool:
    if not error_detail:
        return False
    if failed_with_human_action({"status": "failed", "error_detail": error_detail}):
        return True
    lowered = error_detail.lower()
    return any(
        marker in lowered
        for marker in (
            "manual",
            "credential",
            "authorization",
            "authentication",
            "permission",
            "no_gh_token",
            "git_auth_failed",
            "repo_not_whitelisted",
            "gh_pr_create_failed",
            "publication_refused",
            "publication_failed",
            "publication_ambiguous",
        )
    )


async def _record_escalation_for_terminal(
    pool: asyncpg.Pool,
    *,
    attempt_id: uuid.UUID,
    status: str,
    error_detail: str | None,
) -> None:
    if status != "unfixable" and not _human_action_error_detail(error_detail):
        return

    reason = error_detail or "Investigation agent determined this case needs human review"
    text = reason.splitlines()[0][:120]
    try:
        await record_escalated_event(
            pool,
            attempt_id=attempt_id,
            text=text,
            detail=(
                "Review the QA case detail and underlying PR/session context for the next action."
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "QA dispatch: failed to record escalated journal event (attempt=%s): %s",
            attempt_id,
            exc,
        )


def _get_qa_anonymization_failed_total():
    """Return the qa_anonymization_failed_total Prometheus Counter."""
    try:
        from prometheus_client import Counter

        return Counter(
            "qa_anonymization_failed_total",
            "Total QA PR payloads rejected by anonymization validation",
        )
    except (ImportError, ValueError):
        logger.debug("qa_anonymization_failed_total counter unavailable", exc_info=True)
        return None


_qa_anonymization_failed_total = _get_qa_anonymization_failed_total()

_ANONYMIZATION_FAILURE_EVENT_TEXT = "anonymization validator rejected PR payload"


def _record_qa_anonymization_failed() -> None:
    if _qa_anonymization_failed_total is not None:
        _qa_anonymization_failed_total.inc()


async def _transition_anonymization_failed_with_journal(
    pool: asyncpg.Pool,
    *,
    attempt_id: uuid.UUID,
    category: str,
    healing_session_id: uuid.UUID | None,
) -> None:
    """Move to ``anonymization_failed`` and journal the escalation in one transaction.

    The escalated event carries only the fixed, content-free refusal category;
    no validator context or source content reaches the journal.
    """

    async def _record(session: Any) -> None:
        transitioned = await update_attempt_status(
            session,
            attempt_id,
            "anonymization_failed",
            error_detail="PR blocked: residual PII or credentials detected after anonymization",
            healing_session_id=healing_session_id,
        )
        if transitioned:
            await record_event(
                session,
                attempt_id=attempt_id,
                step="escalated",
                text=_ANONYMIZATION_FAILURE_EVENT_TEXT,
                detail=category,
            )

    if not isinstance(pool, asyncpg.Pool):
        await _record(pool)
        return
    try:
        async with pool.acquire() as conn, conn.transaction():
            await _record(conn)
    except Exception:  # noqa: BLE001 — the terminal status must land even if the journal fails
        logger.warning("QA anonymization journal failed (attempt=%s)", attempt_id, exc_info=True)
        await update_attempt_status(
            pool,
            attempt_id,
            "anonymization_failed",
            error_detail="PR blocked: residual PII or credentials detected after anonymization",
            healing_session_id=healing_session_id,
        )


def _diff_snapshot_stats(diff_snapshot: list[dict[str, str]]) -> tuple[int, int, int]:
    additions = sum(1 for line in diff_snapshot if line.get("kind") == "+")
    deletions = sum(1 for line in diff_snapshot if line.get("kind") == "-")
    files = sum(
        1
        for line in diff_snapshot
        if line.get("kind") == "meta" and line.get("text", "").startswith("diff --git ")
    )
    return additions, deletions, files


async def _record_publication_outcome(
    pool: asyncpg.Pool,
    publication: PublicationResult,
    *,
    attempt_id: uuid.UUID,
    branch_name: str,
    diff_snapshot: list[dict[str, str]],
    healing_session_id: uuid.UUID | None,
    phase_session_id: uuid.UUID | None,
    metrics: ButlerMetrics | None,
    elapsed_ms: float,
) -> None:
    """Record the terminal state, journal events and metrics for one publication."""
    status = publication.status
    published = status is PublicationStatus.PUBLISHED
    outcome = "pr_open" if published else status.value
    if metrics is not None:
        metrics.record_recovery_phase_duration(
            workflow="qa", phase="investigate", outcome=outcome, duration_ms=elapsed_ms
        )
        if not published:
            metrics.record_recovery_execution_failure(
                workflow="qa", phase="investigate", error_class=f"publication_{status.value}"
            )
    if phase_session_id is not None:
        try:
            if published:
                await update_phase_session_status(pool, phase_session_id, "completed")
            else:
                await update_phase_session_status(
                    pool,
                    phase_session_id,
                    "failed",
                    error_detail=f"publication_{status.value}: {publication.category}",
                )
        except Exception as _pss_exc:  # noqa: BLE001
            logger.debug("Failed to update phase session after publication: %s", _pss_exc)

    if published:
        await update_attempt_status(
            pool,
            attempt_id,
            "pr_open",
            pr_url=publication.pr_url,
            pr_number=publication.pr_number,
            healing_session_id=healing_session_id,
        )
        additions, deletions, file_count = _diff_snapshot_stats(diff_snapshot)
        try:
            await record_pr_drafted_event(
                pool,
                attempt_id=attempt_id,
                pr_number=publication.pr_number,
                branch_name=branch_name,
                additions=additions,
                deletions=deletions,
                file_count=file_count,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "QA dispatch: drafted journal event failed (attempt=%s): %s", attempt_id, exc
            )
        return

    if status is PublicationStatus.ANONYMIZATION_FAILED:
        _record_qa_anonymization_failed()
        await _transition_anonymization_failed_with_journal(
            pool,
            attempt_id=attempt_id,
            category=publication.category,
            healing_session_id=healing_session_id,
        )
        return

    # Refused, failed or ambiguous: an ambiguous ledger operation keeps holding the
    # lineage until an exact read-only reconciliation; nothing is retried or deleted.
    error_detail = f"publication_{status.value}: {publication.category}"
    await update_attempt_status(
        pool,
        attempt_id,
        "failed",
        error_detail=error_detail,
        healing_session_id=healing_session_id,
    )
    await _record_escalation_for_terminal(
        pool, attempt_id=attempt_id, status="failed", error_detail=error_detail
    )


async def _persist_diff_snapshot(
    pool: asyncpg.Pool,
    attempt_id: uuid.UUID,
    diff_snapshot: list[dict[str, str]],
) -> None:
    """Persist a commit-time diff snapshot, even when notes are missing."""

    await pool.execute(
        """
        UPDATE public.qa_findings
        SET structured_evidence = jsonb_set(
                COALESCE(structured_evidence, '{}'::jsonb),
                '{investigation_notes}',
                COALESCE(
                    COALESCE(structured_evidence, '{}'::jsonb)->'investigation_notes',
                    '{}'::jsonb
                ) || jsonb_build_object('diff_snapshot', $2::jsonb),
                true
            )
        WHERE healing_attempt_id = $1
        """,
        attempt_id,
        diff_snapshot,
    )


async def _persist_notes_and_remove_worktree(
    pool: asyncpg.Pool,
    attempt_id: uuid.UUID,
    repo_root: Path,
    branch_name: str,
    worktree_path: Path,
    *,
    delete_branch: bool,
    delete_remote: bool,
    diff_snapshot: list[dict[str, str]] | None = None,
    otel_span: Any | None = None,
) -> None:
    notes_status: ParseStatus = "failed"
    try:
        notes_status = await _persist_investigation_notes(
            pool,
            attempt_id,
            worktree_path,
            diff_snapshot=diff_snapshot,
            otel_span=otel_span,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Unexpected error while persisting QA investigation notes "
            "(attempt=%s): %s; continuing worktree teardown",
            attempt_id,
            exc,
        )
    if notes_status == "failed" and diff_snapshot is not None:
        try:
            await _persist_diff_snapshot(pool, attempt_id, diff_snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Unexpected error while persisting QA diff snapshot "
                "(attempt=%s): %s; continuing worktree teardown",
                attempt_id,
                exc,
            )
    await remove_healing_worktree(
        repo_root,
        branch_name,
        delete_branch=delete_branch,
        delete_remote=delete_remote,
    )


# ---------------------------------------------------------------------------
# Timeout watchdog
# ---------------------------------------------------------------------------


async def _qa_timeout_watchdog(
    pool: asyncpg.Pool,
    attempt_id: uuid.UUID,
    repo_root: Path,
    branch_name: str,
    worktree_path: Path,
    investigation_task: asyncio.Task[Any],
    timeout_minutes: int,
) -> None:
    """Sleep for *timeout_minutes* then cancel the investigation task if still running."""
    try:
        await asyncio.sleep(timeout_minutes * 60)
        if not investigation_task.done():
            logger.warning(
                "QA investigation timed out after %d minutes (attempt=%s); cancelling",
                timeout_minutes,
                attempt_id,
            )
            investigation_task.cancel()
            try:
                await investigation_task
            except (asyncio.CancelledError, Exception):
                pass

            await update_attempt_status(
                pool,
                attempt_id,
                "timeout",
                error_detail=f"QA investigation cancelled after {timeout_minutes} minute timeout",
            )
            await _persist_notes_and_remove_worktree(
                pool,
                attempt_id,
                repo_root,
                branch_name,
                worktree_path,
                delete_branch=True,
                delete_remote=False,
                diff_snapshot=[],
            )
    except asyncio.CancelledError:
        # Watchdog was cancelled because the investigation task completed normally
        pass


# ---------------------------------------------------------------------------
# Investigation session runner
# ---------------------------------------------------------------------------


async def _run_investigation_session(
    pool: asyncpg.Pool,
    repo_root: Path,
    attempt_id: uuid.UUID,
    finding_id: uuid.UUID,
    branch_name: str,
    worktree_path: Path,
    finding: QaFinding,
    config: QaDispatchConfig,
    spawner: Any,
    gh_token: str | None,
    git_author_name: str | None = None,
    git_author_email: str | None = None,
    metrics: ButlerMetrics | None = None,
    base_sha: str | None = None,
) -> None:
    """Run the QA investigation agent and record its terminal outcome.

    This coroutine is scheduled as an ``asyncio.Task`` and monitored by a
    separate timeout watchdog task.
    """
    import time as _time

    investigation_session_id: uuid.UUID | None = None
    phase_session_id: uuid.UUID | None = None
    _phase_start = _time.monotonic()

    if metrics is not None:
        metrics.recovery_workflow_start(workflow="qa")

    # Create an independent ROOT span for this investigation (NOT a child of the patrol span).
    # Investigations are long-running, potentially outliving the patrol cycle that spawned them.
    _inv_span = None
    _inv_span_token = None
    if _HAS_OTEL:
        _inv_span = _tracer.start_span(
            "qa.investigation",
            context=otel_context.Context(),  # fresh context — root span
            attributes={
                "qa.attempt_id": str(attempt_id),
                "qa.fingerprint": finding.fingerprint,
                "qa.source_butler": finding.source_butler,
                "qa.severity": finding.severity,
            },
        )
        tag_butler_span(_inv_span, "qa")
        _inv_span_token = otel_context.attach(trace.set_span_in_context(_inv_span))

    try:
        prompt = build_investigation_prompt(
            finding=finding,
            attempt_id=attempt_id,
            dashboard_base_url=config.dashboard_base_url,
        )
        agent_cwd = _prepare_agent_workspace(worktree_path)

        # Build sandboxed environment for the agent
        sandbox_env = build_agent_env(
            git_author_name=git_author_name,
            git_author_email=git_author_email,
        )

        # Inject the investigation root span's trace context as TRACEPARENT so the
        # spawned agent can continue this trace as a child process.
        if _HAS_OTEL and _inv_span is not None:
            sandbox_env.update(get_traceparent_env())

        # Spawn the investigation agent with sandbox env override.
        # Pass timeout_override so the spawner uses the QA watchdog timeout
        # instead of the butler's default session_timeout_s (which may be
        # shorter and would kill the session before the watchdog fires).
        result = await spawner.trigger(
            prompt=prompt,
            trigger_source="qa",
            complexity=Complexity.SPECIALTY,
            cwd=str(agent_cwd),
            bypass_butler_semaphore=True,
            env_override=sandbox_env,
            timeout_override=config.timeout_minutes * 60,
        )

        if result.session_id is not None:
            # Capture the session_id locally; do NOT call update_attempt_status here because
            # create_or_join_attempt already inserts rows in the 'investigating' state and the
            # state machine rejects 'investigating → investigating' transitions.  The session_id
            # will be attached on the next valid status transition (unfixable / failed / timeout).
            investigation_session_id = result.session_id
            # Record the phase session to link this launched session to the attempt with lineage.
            # QA investigations start with a single "investigate" phase; phased workflows
            # would record additional phases here as they are introduced.
            try:
                phase_session_id = await record_phase_session(
                    pool,
                    attempt_id,
                    "investigate",
                    investigation_session_id,
                )
            except Exception as _ps_exc:
                logger.debug(
                    "_run_investigation_session: failed to record phase session (attempt=%s): %s",
                    attempt_id,
                    _ps_exc,
                )

        if not result.success:
            raw_error_detail = result.error or "Investigation agent returned non-success result"
            no_model_available = result.session_id is None and raw_error_detail.startswith(
                "ModelResolutionError:"
            )
            error_detail = (
                f"no_model_available: {raw_error_detail}"
                if no_model_available
                else raw_error_detail
            )
            logger.warning(
                "QA investigation agent failed (attempt=%s): %s", attempt_id, error_detail
            )
            if metrics is not None:
                _elapsed = (_time.monotonic() - _phase_start) * 1000
                metrics.record_recovery_phase_duration(
                    workflow="qa",
                    phase="investigate",
                    outcome="failed",
                    duration_ms=_elapsed,
                )
                metrics.record_recovery_execution_failure(
                    workflow="qa",
                    phase="investigate",
                    error_class="no_model_available" if no_model_available else "agent_failure",
                )
            if phase_session_id is not None:
                try:
                    await update_phase_session_status(
                        pool, phase_session_id, "failed", error_detail=error_detail
                    )
                except Exception as _pss_exc:
                    logger.debug("Failed to mark phase session failed: %s", _pss_exc)
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail=error_detail,
                healing_session_id=investigation_session_id,
            )
            await _record_escalation_for_terminal(
                pool,
                attempt_id=attempt_id,
                status="failed",
                error_detail=error_detail,
            )
            await _persist_notes_and_remove_worktree(
                pool,
                attempt_id,
                repo_root,
                branch_name,
                worktree_path,
                delete_branch=True,
                delete_remote=False,
                otel_span=_inv_span,
            )
            return

        # Check for unfixable sentinel
        if (worktree_path / _UNFIXABLE_FILE).exists():
            logger.info("Investigation agent marked error as unfixable (attempt=%s)", attempt_id)
            if metrics is not None:
                _elapsed = (_time.monotonic() - _phase_start) * 1000
                metrics.record_recovery_phase_duration(
                    workflow="qa",
                    phase="investigate",
                    outcome="unfixable",
                    duration_ms=_elapsed,
                )
            if phase_session_id is not None:
                try:
                    await update_phase_session_status(pool, phase_session_id, "completed")
                except Exception as _pss_exc:
                    logger.debug("Failed to mark phase session completed: %s", _pss_exc)
            await update_attempt_status(
                pool,
                attempt_id,
                "unfixable",
                error_detail="Investigation agent determined this error is not a code bug",
                healing_session_id=investigation_session_id,
            )
            await _record_escalation_for_terminal(
                pool,
                attempt_id=attempt_id,
                status="unfixable",
                error_detail="Investigation agent determined this error is not a code bug",
            )
            await _persist_notes_and_remove_worktree(
                pool,
                attempt_id,
                repo_root,
                branch_name,
                worktree_path,
                delete_branch=True,
                delete_remote=False,
                diff_snapshot=[],
                otel_span=_inv_span,
            )
            return

        # Agent succeeded — publication fails closed: the trusted dispatcher must not
        # push or open a PR from the agent-controlled worktree until the isolated
        # publisher lands (bu-mc01y5.6-.7).  Only no-op detection (local git log,
        # no credential) still runs so empty investigations stay "unfixable".
        if await _detect_no_op_branch(repo_root, branch_name, build_agent_env()):
            # The investigation agent produced no commits — classify as unfixable
            # so the fingerprint is not left in an active state and a future
            # investigation cycle will not re-attempt the same finding.
            logger.info(
                "Investigation agent produced no commits (no-op) (attempt=%s); marking unfixable",
                attempt_id,
            )
            if metrics is not None:
                _elapsed = (_time.monotonic() - _phase_start) * 1000
                metrics.record_recovery_phase_duration(
                    workflow="qa",
                    phase="investigate",
                    outcome="unfixable",
                    duration_ms=_elapsed,
                )
            if phase_session_id is not None:
                try:
                    await update_phase_session_status(pool, phase_session_id, "completed")
                except Exception as _pss_exc:
                    logger.debug("Failed to mark phase session completed: %s", _pss_exc)
            await update_attempt_status(
                pool,
                attempt_id,
                "unfixable",
                error_detail="no_op_branch: investigation agent produced no code changes",
                healing_session_id=investigation_session_id,
            )
            await _record_escalation_for_terminal(
                pool,
                attempt_id=attempt_id,
                status="unfixable",
                error_detail="no_op_branch: investigation agent produced no code changes",
            )
            await _persist_notes_and_remove_worktree(
                pool,
                attempt_id,
                repo_root,
                branch_name,
                worktree_path,
                delete_branch=True,
                delete_remote=False,
                diff_snapshot=[],
                otel_span=_inv_span,
            )
            return

        diff_snapshot = await _capture_commit_diff_snapshot(worktree_path)
        if config.publisher is not None:
            publication = await publish_investigation(
                config.publisher,
                repo_root=repo_root,
                worktree_path=worktree_path,
                attempt_id=attempt_id,
                finding=finding,
                branch_name=branch_name,
                base_sha=base_sha,
                identity=(
                    GitIdentity(git_author_name.strip(), git_author_email.strip())
                    if git_author_name and git_author_email
                    else None
                ),
                labels=list(config.pr_labels),
                whitelist=config.repo_whitelist,
                dashboard_base_url=config.dashboard_base_url,
            )
            await _record_publication_outcome(
                pool,
                publication,
                attempt_id=attempt_id,
                branch_name=branch_name,
                diff_snapshot=diff_snapshot,
                healing_session_id=investigation_session_id,
                phase_session_id=phase_session_id,
                metrics=metrics,
                elapsed_ms=(_time.monotonic() - _phase_start) * 1000,
            )
            await _persist_notes_and_remove_worktree(
                pool,
                attempt_id,
                repo_root,
                branch_name,
                worktree_path,
                # The local branch is kept only when it backs a published PR.
                delete_branch=publication.status is not PublicationStatus.PUBLISHED,
                delete_remote=False,
                diff_snapshot=diff_snapshot,
                otel_span=_inv_span,
            )
            return

        if metrics is not None:
            _elapsed = (_time.monotonic() - _phase_start) * 1000
            metrics.record_recovery_phase_duration(
                workflow="qa",
                phase="investigate",
                outcome="failed",
                duration_ms=_elapsed,
            )
            metrics.record_recovery_execution_failure(
                workflow="qa",
                phase="investigate",
                error_class=_PUBLICATION_UNAVAILABLE,
            )
        if phase_session_id is not None:
            try:
                await update_phase_session_status(
                    pool,
                    phase_session_id,
                    "failed",
                    error_detail=_PUBLICATION_UNAVAILABLE,
                )
            except Exception as _pss_exc:
                logger.debug("Failed to mark phase session failed: %s", _pss_exc)
        await update_attempt_status(
            pool,
            attempt_id,
            "failed",
            error_detail=_PUBLICATION_UNAVAILABLE_DETAIL,
            healing_session_id=investigation_session_id,
        )
        await _record_escalation_for_terminal(
            pool,
            attempt_id=attempt_id,
            status="failed",
            error_detail=_PUBLICATION_UNAVAILABLE_DETAIL,
        )
        await _persist_notes_and_remove_worktree(
            pool,
            attempt_id,
            repo_root,
            branch_name,
            worktree_path,
            delete_branch=True,
            delete_remote=False,
            diff_snapshot=diff_snapshot,
            otel_span=_inv_span,
        )

    except asyncio.CancelledError:
        # Cancelled by watchdog — watchdog sets status to "timeout"
        if metrics is not None:
            _elapsed = (_time.monotonic() - _phase_start) * 1000
            metrics.record_recovery_phase_duration(
                workflow="qa",
                phase="investigate",
                outcome="timeout",
                duration_ms=_elapsed,
            )
            metrics.record_recovery_execution_failure(
                workflow="qa",
                phase="investigate",
                error_class="timeout",
            )
        if phase_session_id is not None:
            try:
                await update_phase_session_status(
                    pool,
                    phase_session_id,
                    "timeout",
                    error_detail="Investigation session cancelled by watchdog",
                )
            except Exception as _pss_exc:
                logger.debug("Failed to mark phase session timeout: %s", _pss_exc)
        if _HAS_OTEL and _inv_span is not None:
            _inv_span.set_status(trace.StatusCode.ERROR, "investigation cancelled (timeout)")
        raise

    except Exception as exc:
        logger.exception(
            "Unexpected error in QA investigation session (attempt=%s): %s", attempt_id, exc
        )
        if metrics is not None:
            _elapsed = (_time.monotonic() - _phase_start) * 1000
            metrics.record_recovery_phase_duration(
                workflow="qa",
                phase="investigate",
                outcome="failed",
                duration_ms=_elapsed,
            )
            metrics.record_recovery_execution_failure(
                workflow="qa",
                phase="investigate",
                error_class=type(exc).__name__,
            )
        if phase_session_id is not None:
            try:
                await update_phase_session_status(
                    pool, phase_session_id, "failed", error_detail=f"{type(exc).__name__}: {exc}"
                )
            except Exception as _pss_exc:
                logger.debug("Failed to mark phase session failed: %s", _pss_exc)
        if _HAS_OTEL and _inv_span is not None:
            _inv_span.record_exception(exc)
            _inv_span.set_status(trace.StatusCode.ERROR, str(exc))
        await update_attempt_status(
            pool,
            attempt_id,
            "failed",
            error_detail=f"{type(exc).__name__}: {exc}",
            healing_session_id=investigation_session_id,
        )
        await _persist_notes_and_remove_worktree(
            pool,
            attempt_id,
            repo_root,
            branch_name,
            worktree_path,
            delete_branch=True,
            delete_remote=False,
            otel_span=_inv_span,
        )
    finally:
        if metrics is not None:
            metrics.recovery_workflow_end(workflow="qa")
        if _HAS_OTEL and _inv_span is not None:
            _inv_span.end()
            if _inv_span_token is not None:
                otel_context.detach(_inv_span_token)


# ---------------------------------------------------------------------------
# PR status tracking
# ---------------------------------------------------------------------------


async def check_open_pr_statuses(
    pool: asyncpg.Pool,
    repo_root: Path,
    gh_token: str | None,
    git_author_name: str | None = None,
    git_author_email: str | None = None,
    spawner: Any = None,
    config: QaDispatchConfig | None = None,
    task_registry: list[asyncio.Task[Any]] | None = None,
    patrol_id: uuid.UUID | None = None,
    patrol_started_at: datetime | None = None,
) -> dict[str, int]:
    """Check GitHub status of all pr_open QA healing attempts.

        Called on each patrol cycle from the QA staffer daemon context (not inside
        an agent worktree).  Transitions pr_open → pr_merged or pr_open → failed
        based on actual GitHub PR state.

    When ``spawner`` and ``config`` are provided, also checks for unresolved
        review threads or "changes_requested" state and dispatches a follow-up
        agent to address reviewer feedback with exponential backoff between
        repeated follow-up attempts on the same PR.

        Parameters
        ----------
        pool:
            asyncpg connection pool targeting the public schema.
        repo_root:
            Absolute path to the repository root (for gh CLI invocations).
        gh_token:
            GitHub token from CredentialStore.  If ``None``, tracking is skipped.
        spawner:
            Optional QA staffer Spawner.  When provided, enables follow-up agent
            dispatch for PRs with actionable reviewer feedback.
        config:
            Optional ``QaDispatchConfig``.  Required alongside ``spawner`` for
            follow-up dispatch.
        task_registry:
            Optional list to which watchdog tasks will be appended.
        patrol_id:
            UUID of the current patrol cycle.  When provided and ``spawner`` is
            set, the cycle-scoped follow-up counter (``follow_up_cycle_count``) is
            used for rate-limiting instead of the lifetime ``follow_up_count``.

        Returns
        -------
        dict[str, int]
            Counts of transitions: ``{"merged": N, "closed": N, "errors": N,
            "follow_ups_dispatched": N}``.
    """
    counts: dict[str, int] = {
        "merged": 0,
        "closed": 0,
        "errors": 0,
        "follow_ups_dispatched": 0,
    }

    if not gh_token:
        logger.debug("check_open_pr_statuses: no gh_token, skipping PR status check")
        return counts

    # Fetch all QA pr_open attempts (have qa_patrol_id set)
    rows = await pool.fetch(
        """
        SELECT id, pr_url, pr_number, fingerprint, butler_name, follow_up_count,
               branch_name, last_follow_up_at
        FROM public.healing_attempts
        WHERE status = 'pr_open'
          AND qa_patrol_id IS NOT NULL
          AND pr_url IS NOT NULL
        """
    )

    if not rows:
        return counts

    env = harden_git_env(build_sandbox_env(gh_token))
    # gh addresses the PR by URL from a neutral cwd, so it never resolves the repository
    # through the agent-writable shared .git of repo_root.
    neutral_cwd = tempfile.gettempdir()

    for row in rows:
        attempt_id: uuid.UUID = row["id"]
        pr_number: int | None = row["pr_number"]
        pr_url: str = row["pr_url"]
        fingerprint: str = row["fingerprint"]
        butler_name: str = row["butler_name"]
        follow_up_count: int = row["follow_up_count"] or 0
        pr_branch_name: str | None = row["branch_name"]
        last_follow_up_at: datetime | None = row["last_follow_up_at"]

        if pr_number is None:
            # pr_number is missing — the attempt was persisted without it (e.g.
            # non-canonical gh stdout at PR creation time).  Attempt a repair via
            # head-branch lookup before giving up.  Fall through to full PR state
            # polling when repair succeeds; transition to failed deterministically
            # when it does not.
            if pr_branch_name:
                # Address the repository stored with the attempt, never the shared .git.
                pr_repo = re.match(r"https://github\.com/([^/\s]+/[^/\s]+)/pull/\d+", pr_url)
                repaired_url, repaired_number = await _resolve_pr_by_head(
                    repo_root, pr_branch_name, env, repo=pr_repo.group(1) if pr_repo else None
                )
                if repaired_number is not None:
                    logger.info(
                        "check_open_pr_statuses: repaired missing pr_number=%s for "
                        "attempt=%s via head-branch lookup",
                        repaired_number,
                        attempt_id,
                    )
                    pr_number = repaired_number
                    if repaired_url:
                        pr_url = repaired_url
                    # Persist the repaired values so future cycles don't re-repair.
                    try:
                        await pool.execute(
                            """
                            UPDATE public.healing_attempts
                            SET pr_number = $2,
                                pr_url    = $3,
                                updated_at = now()
                            WHERE id = $1
                            """,
                            attempt_id,
                            pr_number,
                            pr_url,
                        )
                    except Exception as _rep_exc:
                        logger.warning(
                            "check_open_pr_statuses: failed to persist repaired PR metadata "
                            "for attempt=%s: %s",
                            attempt_id,
                            _rep_exc,
                        )
                else:
                    logger.warning(
                        "check_open_pr_statuses: pr_number missing and head-branch lookup "
                        "failed for attempt=%s branch=%r; transitioning to failed",
                        attempt_id,
                        pr_branch_name,
                    )
                    try:
                        await update_attempt_status(
                            pool,
                            attempt_id,
                            "failed",
                            error_detail=(
                                "pr_number_missing: PR identity could not be resolved from "
                                f"head branch {pr_branch_name!r}"
                            ),
                        )
                    except Exception as _trans_exc:
                        logger.warning(
                            "check_open_pr_statuses: failed to transition attempt=%s to failed: %s",
                            attempt_id,
                            _trans_exc,
                        )
                        counts["errors"] += 1
                    continue
            else:
                # No branch_name either — cannot repair; transition to failed.
                logger.warning(
                    "check_open_pr_statuses: pr_number and branch_name both missing for "
                    "attempt=%s; transitioning to failed",
                    attempt_id,
                )
                try:
                    await update_attempt_status(
                        pool,
                        attempt_id,
                        "failed",
                        error_detail=(
                            "pr_number_missing: no branch_name available for repair lookup"
                        ),
                    )
                except Exception as _trans_exc:
                    logger.warning(
                        "check_open_pr_statuses: failed to transition attempt=%s to failed: %s",
                        attempt_id,
                        _trans_exc,
                    )
                    counts["errors"] += 1
                continue

        try:
            # Fetch state + review info in one call.
            # Note: "reviewThreads" was added in gh >=2.50; older versions
            # (e.g. 2.46 shipped with Debian) reject the field with exit-code 1.
            # We attempt the full field set first and fall back to the subset
            # that older gh versions support.
            _full_fields = "state,reviews,latestReviews,reviewThreads,statusCheckRollup"
            _compat_fields = "state,reviews,latestReviews"

            for json_fields in (_full_fields, _compat_fields):
                proc = await asyncio.create_subprocess_exec(
                    "gh",
                    "pr",
                    "view",
                    pr_url or str(pr_number),
                    "--json",
                    json_fields,
                    cwd=neutral_cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0:
                    break
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                # If the failure is specifically about an unknown field, retry
                # with the compat subset; otherwise break and report the error.
                if "Unknown JSON field" not in stderr_text:
                    break

            if proc.returncode != 0:
                logger.warning(
                    "check_open_pr_statuses: gh pr view failed for "
                    "attempt=%s pr_number=%s (rc=%d): %s",
                    attempt_id,
                    pr_number,
                    proc.returncode,
                    stderr_text,
                )
                counts["errors"] += 1
                continue

            import json as _json

            try:
                pr_data = _json.loads(stdout.decode("utf-8", errors="replace"))
            except _json.JSONDecodeError:
                logger.warning(
                    "check_open_pr_statuses: failed to parse gh output for attempt=%s pr_number=%s",
                    attempt_id,
                    pr_number,
                )
                counts["errors"] += 1
                continue

            state = pr_data.get("state", "").upper()

            if state == "MERGED":
                await update_attempt_status(pool, attempt_id, "pr_merged")
                try:
                    await record_pr_merged_event(
                        pool,
                        attempt_id=attempt_id,
                        detail=f"PR #{pr_number} observed merged during patrol status check",
                    )
                except Exception as _journal_exc:
                    logger.debug(
                        "check_open_pr_statuses: failed to record merged journal event "
                        "(attempt=%s): %s",
                        attempt_id,
                        _journal_exc,
                    )
                counts["merged"] += 1
                logger.info("QA PR merged: attempt=%s pr_number=%s", attempt_id, pr_number)
                continue

            if state == "CLOSED":
                await update_attempt_status(
                    pool,
                    attempt_id,
                    "failed",
                    error_detail="pr_closed_without_merge",
                )
                counts["closed"] += 1
                logger.info(
                    "QA PR closed without merge: attempt=%s pr_number=%s",
                    attempt_id,
                    pr_number,
                )
                continue

            pending_check_names = _extract_pending_check_names(pr_data)
            if pending_check_names:
                wait_cycle_start = patrol_started_at or datetime.now(UTC)
                try:
                    await record_wait_event_once(
                        pool,
                        attempt_id=attempt_id,
                        patrol_started_at=wait_cycle_start,
                        pending_count=len(pending_check_names),
                        pending_check_names=pending_check_names,
                    )
                except Exception as _journal_exc:
                    logger.debug(
                        "check_open_pr_statuses: failed to record wait journal event "
                        "(attempt=%s): %s",
                        attempt_id,
                        _journal_exc,
                    )

            # OPEN state: check for reviewer feedback
            review_state, feedback_summary = _extract_review_state(pr_data)

            # Anonymize feedback before persisting: review comments may contain
            # PII or credentials and must not be stored or served unredacted.
            safe_feedback_for_storage: str | None = None
            if feedback_summary:
                _safe = anonymize(feedback_summary, repo_root)
                _clean, _ = validate_anonymized(_safe)
                safe_feedback_for_storage = _safe[:_MAX_FEEDBACK_SUMMARY_LEN] if _clean else None

            # Update review tracking columns
            await pool.execute(
                """
                UPDATE public.healing_attempts
                SET review_state            = $2,
                    last_review_check_at    = now(),
                    review_feedback_summary = $3,
                    updated_at              = now()
                WHERE id = $1
                """,
                attempt_id,
                review_state,
                safe_feedback_for_storage,
            )

            # Dispatch follow-up if actionable and not rate-limited.
            # Trigger when review_state is explicitly actionable OR when there
            # is any feedback_summary (e.g. unresolved threads exist but
            # latestReviews is empty, so review_state is None).
            _needs_followup = review_state in _ACTIONABLE_REVIEW_STATES or (
                review_state is None and bool(feedback_summary)
            )
            now = datetime.now(UTC)
            if (
                spawner is not None
                and config is not None
                and _needs_followup
                and _follow_up_dispatch_due(
                    now=now,
                    follow_up_count=follow_up_count,
                    last_follow_up_at=last_follow_up_at,
                )
                and feedback_summary
            ):
                dispatched = await _dispatch_pr_review_followup(
                    pool=pool,
                    repo_root=repo_root,
                    attempt_id=attempt_id,
                    pr_number=pr_number,
                    pr_url=pr_url,
                    fingerprint=fingerprint,
                    butler_name=butler_name,
                    pr_branch_name=pr_branch_name,
                    feedback_summary=feedback_summary,
                    config=config,
                    spawner=spawner,
                    gh_token=gh_token,
                    git_author_name=git_author_name,
                    git_author_email=git_author_email,
                    task_registry=task_registry,
                    patrol_id=patrol_id,
                )
                if dispatched:
                    counts["follow_ups_dispatched"] += 1

        except Exception as exc:
            logger.warning(
                "Failed to check PR status for attempt=%s pr_number=%s: %s",
                attempt_id,
                pr_number,
                exc,
            )
            counts["errors"] += 1

    return counts


_PENDING_CHECK_STATUSES = {
    "ACTION_REQUIRED",
    "EXPECTED",
    "IN_PROGRESS",
    "PENDING",
    "QUEUED",
    "REQUESTED",
    "WAITING",
}


def _extract_pending_check_names(pr_data: dict[str, Any]) -> list[str]:
    """Return human-readable names for PR checks that are still pending."""

    checks = pr_data.get("statusCheckRollup") or []
    pending: list[str] = []
    seen: set[str] = set()
    if not isinstance(checks, list):
        return pending

    for check in checks:
        if not isinstance(check, dict):
            continue
        status = str(check.get("status") or "").upper()
        conclusion = str(check.get("conclusion") or "").upper()
        if status not in _PENDING_CHECK_STATUSES and conclusion:
            continue
        if conclusion in {"SUCCESS", "FAILURE", "CANCELLED", "SKIPPED", "TIMED_OUT"}:
            continue

        name = (
            check.get("name")
            or check.get("workflowName")
            or check.get("context")
            or check.get("title")
            or "unnamed check"
        )
        name = str(name).strip()
        if name and name not in seen:
            pending.append(name)
            seen.add(name)

    return pending


def _extract_review_state(
    pr_data: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Extract review state and feedback summary from gh pr view JSON output.

    Parameters
    ----------
    pr_data:
        Parsed JSON from ``gh pr view --json state,reviews,latestReviews[,reviewThreads]``.
        ``reviewThreads`` may be absent on older gh CLI versions (<2.50).

    Returns
    -------
    tuple[str | None, str | None]
        ``(review_state, feedback_summary)`` where ``review_state`` is one of
        ``"approved"``, ``"changes_requested"``, ``"commented"``,
        ``"dismissed"``, ``"pending"``, or ``None`` (no reviews), and
        ``feedback_summary`` is a newline-joined summary of unresolved comments
        (or ``None`` if nothing actionable).
    """
    # Determine aggregate review state from latestReviews (one per reviewer)
    latest_reviews: list[dict[str, Any]] = pr_data.get("latestReviews") or []

    state_priority = {
        "CHANGES_REQUESTED": 0,
        "COMMENTED": 1,
        "DISMISSED": 2,
        "APPROVED": 3,
        "PENDING": 4,
    }

    dominant_state: str | None = None
    for review in latest_reviews:
        review_state_raw = (review.get("state") or "").upper()
        if dominant_state is None:
            dominant_state = review_state_raw
        elif state_priority.get(review_state_raw, 99) < state_priority.get(dominant_state, 99):
            dominant_state = review_state_raw

    # Normalise to lowercase snake_case
    state_map = {
        "CHANGES_REQUESTED": "changes_requested",
        "COMMENTED": "commented",
        "DISMISSED": "dismissed",
        "APPROVED": "approved",
        "PENDING": "pending",
    }
    review_state: str | None = state_map.get(dominant_state) if dominant_state else None

    # Build feedback summary from unresolved review threads
    threads: list[dict[str, Any]] = pr_data.get("reviewThreads") or []
    unresolved_comments: list[str] = []
    seen_bodies: set[str] = set()

    for thread in threads:
        if thread.get("isResolved") or thread.get("isOutdated"):
            continue
        comments_in_thread: list[dict[str, Any]] = thread.get("comments", {}).get("nodes", [])
        for comment in comments_in_thread:
            body = (comment.get("body") or "").strip()
            if body and body not in seen_bodies:
                author = (comment.get("author") or {}).get("login", "reviewer")
                unresolved_comments.append(f"- [{author}]: {body}")
                seen_bodies.add(body)

    # Also include general review comments from reviews with body text
    reviews: list[dict[str, Any]] = pr_data.get("reviews") or []
    for review in reviews:
        if (review.get("state") or "").upper() in ("CHANGES_REQUESTED", "COMMENTED"):
            body = (review.get("body") or "").strip()
            if body and body not in seen_bodies:
                author = (review.get("author") or {}).get("login", "reviewer")
                unresolved_comments.append(f"- [{author} review]: {body}")
                seen_bodies.add(body)

    feedback_summary = "\n".join(unresolved_comments) if unresolved_comments else None
    if feedback_summary is None and review_state == "changes_requested":
        # Changes requested but no specific comment text available
        feedback_summary = "Reviewer requested changes (no specific comment text available)."

    return review_state, feedback_summary


async def _dispatch_pr_review_followup(
    pool: asyncpg.Pool,
    repo_root: Path,
    attempt_id: uuid.UUID,
    pr_number: int,
    pr_url: str,
    fingerprint: str,
    butler_name: str,
    pr_branch_name: str | None,
    feedback_summary: str,
    config: QaDispatchConfig,
    spawner: Any,
    gh_token: str | None,
    git_author_name: str | None = None,
    git_author_email: str | None = None,
    task_registry: list[asyncio.Task[Any]] | None = None,
    patrol_id: uuid.UUID | None = None,
) -> bool:
    """Dispatch a follow-up agent to address PR reviewer feedback.

    Checks out the existing PR head branch into a dedicated worktree, spawns a
    follow-up agent with the reviewer feedback as context, and pushes the
    resulting changes to the same PR branch so the open PR is updated.

    Repeated dispatch is exponentially staggered using ``last_follow_up_at``
    and ``follow_up_count`` on the healing_attempts row. Per-patrol cycle
    counters are still maintained for operator visibility and telemetry.
    Anonymization validation is applied before the agent runs.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    repo_root:
        Absolute path to repository root.
    attempt_id:
        UUID of the healing_attempts row.
    pr_number:
        GitHub PR number.
    pr_url:
        Full GitHub PR URL.
    fingerprint:
        Fingerprint from the original healing attempt.
    butler_name:
        Butler that originated the investigation.
    pr_branch_name:
        Existing PR head branch name recorded on the healing_attempts row.
        If ``None`` the dispatch is skipped (branch info missing).
    feedback_summary:
        Summarised reviewer feedback (will be anonymized before use in prompt).
    config:
        QA dispatch configuration.
    spawner:
        QA staffer's Spawner instance.
    gh_token:
        GitHub token.
    task_registry:
        Optional list for background task references.
    patrol_id:
        UUID of the current patrol cycle.  Used to scope the cycle follow-up
        counter: the counter resets to 1 when a new patrol_id is seen.
        ``None`` disables per-cycle tracking (lifetime counter used instead).

    Returns
    -------
    bool
        ``True`` if a follow-up agent task was dispatched, ``False`` otherwise.
    """
    if not pr_branch_name:
        logger.warning(
            "PR review follow-up: no existing branch recorded for attempt=%s — skipping",
            attempt_id,
        )
        return False

    try:
        # Anonymize reviewer feedback before including in agent prompt
        safe_feedback = anonymize(feedback_summary, repo_root)
        feedback_clean, feedback_violations = validate_anonymized(safe_feedback)
        if not feedback_clean:
            logger.warning(
                "PR review follow-up: anonymization validation failed for attempt=%s "
                "(%d violations) — skipping dispatch",
                attempt_id,
                len(feedback_violations),
            )
            return False

        # Build follow-up prompt
        prompt = build_review_followup_prompt(
            pr_number=pr_number,
            pr_url=pr_url,
            fingerprint=fingerprint,
            source_butler=butler_name,
            attempt_id=attempt_id,
            feedback_summary=safe_feedback,
            dashboard_base_url=config.dashboard_base_url,
        )

        # Create a worktree on the existing PR branch so that the follow-up
        # agent's commits continue to update the open PR head branch.
        followup_branch = pr_branch_name
        branch_slug = pr_branch_name.replace("/", "-")
        worktree_path = (
            repo_root
            / ".healing-worktrees"
            / _QA_REVIEW_PREFIX
            / f"{branch_slug}-{uuid.uuid4().hex[:8]}"
        )
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        # Branch-prep git runs in repo_root, whose shared .git an earlier agent may have
        # written (hooks, config).  It therefore never holds the GitHub token: the env is
        # token-free and hardened, so a private remote fails the fetch closed (publication
        # is unavailable anyway) instead of exposing the token to agent-written config.
        git_prep_env = _build_git_prep_env()

        async def _run_git_here(*args: str) -> tuple[int, str]:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=str(repo_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=git_prep_env,
            )
            stdout, _ = await proc.communicate()
            return proc.returncode, stdout.decode("utf-8", errors="replace")

        # Materialize the exact remote-tracking ref that the worktree command
        # anchors below. A branch-only fetch can leave only FETCH_HEAD behind.
        remote_refspec = f"+refs/heads/{followup_branch}:refs/remotes/origin/{followup_branch}"
        fetch_rc, fetch_out = await _run_git_here(
            "fetch", *FETCH_COMMAND_ARGS, "origin", remote_refspec
        )
        if fetch_rc != 0:
            logger.warning(
                "PR review follow-up: git fetch failed for branch %s (attempt=%s): %s",
                followup_branch,
                attempt_id,
                fetch_out.strip(),
            )
            raise WorktreeCreationError(
                f"git fetch failed for branch {followup_branch}: {fetch_out.strip()}"
            )

        # Always anchor the worktree to origin/<branch> so that a stale or
        # diverged local branch of the same name cannot drive the follow-up
        # session.  ``-B`` creates the local tracking branch if it does not
        # exist, or resets it to the specified commit-ish if it does — giving
        # us remote-head semantics in both cases without a separate show-ref
        # check.
        add_rc, add_out = await _run_git_here(
            "worktree",
            "add",
            "-B",
            followup_branch,
            str(worktree_path),
            f"origin/{followup_branch}",
        )

        if add_rc != 0:
            raise WorktreeCreationError(
                f"git worktree add failed for branch {followup_branch}: {add_out.strip()}"
            )

        if not worktree_path.is_dir():
            raise WorktreeCreationError(f"Worktree directory not created at {worktree_path}")

        # Update follow-up counters and record dispatch start.
        # - follow_up_count: lifetime monotonic telemetry (always increments)
        # - follow_up_cycle_patrol_id / follow_up_cycle_count: per-cycle budget
        #   (cycle_count resets to 1 when patrol_id changes)
        # - last_follow_up_status / last_follow_up_at: outcome markers (set to
        #   'dispatched' here; overwritten with 'succeeded'/'failed' by the
        #   background session task)
        await pool.execute(
            """
            UPDATE public.healing_attempts
            SET follow_up_count           = follow_up_count + 1,
                follow_up_cycle_patrol_id = COALESCE($2, follow_up_cycle_patrol_id),
                follow_up_cycle_count     = CASE
                    WHEN $2 IS NOT NULL AND follow_up_cycle_patrol_id IS DISTINCT FROM $2
                        THEN 1
                    WHEN $2 IS NOT NULL
                        THEN follow_up_cycle_count + 1
                    ELSE follow_up_cycle_count
                END,
                last_follow_up_status     = 'dispatched',
                last_follow_up_at         = now(),
                last_follow_up_error      = NULL,
                updated_at                = now()
            WHERE id = $1
            """,
            attempt_id,
            patrol_id,
        )

        sandbox_env = build_agent_env(
            git_author_name=git_author_name,
            git_author_email=git_author_email,
        )

        # Inject trace context if available
        if _HAS_OTEL:
            try:
                sandbox_env.update(get_traceparent_env())
            except Exception:
                pass

        followup_task: asyncio.Task[None] = asyncio.create_task(
            _run_review_followup_session(
                pool=pool,
                repo_root=repo_root,
                attempt_id=attempt_id,
                pr_number=pr_number,
                followup_branch=followup_branch,
                worktree_path=worktree_path,
                prompt=prompt,
                config=config,
                spawner=spawner,
                sandbox_env=sandbox_env,
            ),
            name=f"qa-review-followup-{attempt_id}",
        )

        if task_registry is not None:
            task_registry.append(followup_task)

        logger.info(
            "QA PR review follow-up dispatched: attempt=%s pr_number=%s branch=%s",
            attempt_id,
            pr_number,
            followup_branch,
        )
        return True

    except WorktreeCreationError as wt_exc:
        logger.warning(
            "PR review follow-up: worktree creation failed (attempt=%s): %s",
            attempt_id,
            wt_exc,
        )
        return False
    except Exception as exc:
        logger.warning(
            "PR review follow-up dispatch failed (attempt=%s): %s",
            attempt_id,
            exc,
            exc_info=True,
        )
        return False


async def _run_review_followup_session(
    pool: asyncpg.Pool,
    repo_root: Path,
    attempt_id: uuid.UUID,
    pr_number: int,
    followup_branch: str,
    worktree_path: Path,
    prompt: str,
    config: QaDispatchConfig,
    spawner: Any,
    sandbox_env: dict[str, str],
) -> None:
    """Run the PR review follow-up agent session.

    Spawns the agent in the follow-up worktree, then pushes any new commits
    to the origin branch (which backs the open PR).  Persists the outcome
    (``last_follow_up_status``, ``last_follow_up_session_id``,
    ``last_follow_up_error``) on the healing_attempts row regardless of
    success or failure.
    Cleans up the local worktree after completion (branch is kept for the PR).
    """
    followup_session_id: uuid.UUID | None = None
    try:
        agent_cwd = _prepare_agent_workspace(worktree_path)
        result = await spawner.trigger(
            prompt=prompt,
            trigger_source="qa",
            complexity=Complexity.SPECIALTY,
            cwd=str(agent_cwd),
            bypass_butler_semaphore=True,
            env_override=sandbox_env,
            timeout_override=config.timeout_minutes * 60,
        )

        followup_session_id = getattr(result, "session_id", None)

        if not result.success:
            error_msg = (result.error or "non-success result")[:_MAX_FOLLOWUP_ERROR_LEN]
            logger.warning(
                "QA review follow-up agent failed (attempt=%s): %s",
                attempt_id,
                error_msg,
            )
            await pool.execute(
                """
                UPDATE public.healing_attempts
                SET last_follow_up_status     = 'failed',
                    last_follow_up_session_id = $2,
                    last_follow_up_error      = $3,
                    updated_at                = now()
                WHERE id = $1
                """,
                attempt_id,
                followup_session_id,
                error_msg,
            )
            await remove_healing_worktree(
                repo_root, followup_branch, delete_branch=True, delete_remote=False
            )
            return

        # Publication fails closed: no push from the agent-controlled worktree and the
        # open PR is left untouched until the isolated publisher lands.
        logger.warning(
            "QA review follow-up: %s (attempt=%s pr_number=%s)",
            _PUBLICATION_UNAVAILABLE,
            attempt_id,
            pr_number,
        )
        await pool.execute(
            """
            UPDATE public.healing_attempts
            SET last_follow_up_status     = 'failed',
                last_follow_up_session_id = $2,
                last_follow_up_error      = $3,
                updated_at                = now()
            WHERE id = $1
            """,
            attempt_id,
            followup_session_id,
            _PUBLICATION_UNAVAILABLE_DETAIL,
        )

        # Clean up the local worktree; keep the branch for the PR
        await remove_healing_worktree(
            repo_root, followup_branch, delete_branch=False, delete_remote=False
        )

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "Unexpected error in QA review follow-up session (attempt=%s): %s",
            attempt_id,
            exc,
        )
        try:
            await pool.execute(
                """
                UPDATE public.healing_attempts
                SET last_follow_up_status     = 'failed',
                    last_follow_up_session_id = $2,
                    last_follow_up_error      = $3,
                    updated_at                = now()
                WHERE id = $1
                """,
                attempt_id,
                followup_session_id,
                error_msg[:_MAX_FOLLOWUP_ERROR_LEN],
            )
        except Exception as _db_exc:
            logger.debug(
                "Failed to persist follow-up error state for attempt=%s: %s",
                attempt_id,
                _db_exc,
            )
        await remove_healing_worktree(
            repo_root, followup_branch, delete_branch=True, delete_remote=False
        )


# ---------------------------------------------------------------------------
# Circuit breaker helper
# ---------------------------------------------------------------------------


async def _is_circuit_breaker_tripped(
    pool: asyncpg.Pool,
    threshold: int,
) -> bool:
    """Return True if the last *threshold* terminal QA attempts are all failures.

    Fetches the last *threshold* terminal QA attempts ordered by ``closed_at``
    (regardless of their status), then checks whether all of them are failure
    statuses.  This correctly captures "N consecutive failures" semantics:
    if any of the last N terminal attempts was a success (e.g., ``pr_merged``),
    the breaker stays open.

    Only launched executions (``healing_session_id IS NOT NULL``) that closed
    *after* the latest manual reset (``public.breaker_resets`` for breaker
    ``'qa'``) are counted. An operator reset records a timestamp there, which
    excludes every prior failure from the window and re-admits dispatches
    WITHOUT fabricating a clean patrol. Gate rejections that were cleaned up as
    dispatch events do not have a ``healing_session_id`` and remain excluded
    from the circuit-breaker signal.

    Kept byte-identical to the dashboard filter in
    ``butlers.api.routers.qa._QA_CIRCUIT_BREAKER_WHERE_CLAUSE``.
    """
    # Check only QA-originated terminal attempts where an investigation actually
    # launched (healing_session_id IS NOT NULL).  Admission-control rejections
    # were never inserted as attempts (they use healing_dispatch_events instead),
    # so this filter is defensive against any legacy rows.
    rows = await pool.fetch(
        """
        SELECT status
        FROM public.healing_attempts
        WHERE qa_patrol_id IS NOT NULL
          AND closed_at IS NOT NULL
          AND healing_session_id IS NOT NULL
          AND closed_at > COALESCE(
                (SELECT max(reset_at) FROM public.breaker_resets WHERE breaker = 'qa'),
                '-infinity'::timestamptz
              )
        ORDER BY closed_at DESC
        LIMIT $1
        """,
        threshold,
    )
    if len(rows) < threshold:
        return False
    return all(row["status"] in CIRCUIT_BREAKER_FAILURE_STATUSES for row in rows)


# ---------------------------------------------------------------------------
# Main dispatch function
# ---------------------------------------------------------------------------


class MainRefreshError(RuntimeError):
    """Trusted initial preparation could not bind a fresh main commit."""


async def _refresh_main_commit(repo_root: Path) -> str:
    """Fetch canonical main and return a verified immutable commit identity."""
    output = b""
    for args in (
        (
            "fetch",
            *FETCH_COMMAND_ARGS,
            "--no-tags",
            "origin",
            "+refs/heads/main:refs/remotes/origin/main",
        ),
        ("rev-parse", "--verify", "refs/remotes/origin/main^{commit}"),
    ):
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=str(repo_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                # Allowlisted env, no credentials: agent-writable config cannot run code here.
                env=isolated_git_env(),
            )
            output, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode != 0:
                raise MainRefreshError("main refresh unavailable")
        except (OSError, TimeoutError) as exc:
            raise MainRefreshError("main refresh unavailable") from exc
        finally:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
    commit = output.decode("ascii", errors="replace").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise MainRefreshError("main commit identity unavailable")
    return commit


async def dispatch_qa_investigation(
    pool: asyncpg.Pool,
    triaged_finding: TriagedFinding,
    patrol_id: uuid.UUID,
    config: QaDispatchConfig,
    repo_root: Path,
    spawner: Any,
    gh_token: str | None = None,
    git_author_name: str | None = None,
    git_author_email: str | None = None,
    task_registry: list[asyncio.Task[Any]] | None = None,
    metrics: ButlerMetrics | None = None,
) -> QaDispatchResult:
    """Evaluate gates and, if all pass, spawn a QA investigation agent.

    Applies the authoritative gate sequence for a single novel finding.
    Triage has already performed a fast non-atomic dedup check; this function
    performs the atomic novelty claim and subsequent gate checks.

    This function is non-fatal: all internal exceptions are caught and logged.

    Parameters
    ----------
    pool:
        asyncpg connection pool for healing_attempts and related tables.
    triaged_finding:
        A novel ``TriagedFinding`` from the triage layer.
    patrol_id:
        UUID of the current qa_patrols row (for qa_patrol_id linkage).
    config:
        ``QaDispatchConfig`` with thresholds, timeout, and PR labels.
    repo_root:
        Absolute path to the repository root (for worktree creation).
    spawner:
        The QA staffer's ``Spawner`` instance.
    gh_token:
        GitHub token from CredentialStore.resolve("BUTLERS_QA_GH_TOKEN").
    task_registry:
        Optional list to which watchdog tasks will be appended.

    Returns
    -------
    QaDispatchResult
        Always returned — never raises.
    """
    finding = triaged_finding.finding
    finding_id = triaged_finding.finding_id
    fp = finding.fingerprint

    try:
        # ---------------------------------------------------------------
        # Gate 0: QA self-recursion barrier
        # ---------------------------------------------------------------
        # Suppress autonomous investigation when the finding originated from a
        # QA or healing session spawned by the QA butler.  Applies only when
        # source_butler == "qa".  Findings from other butlers are never blocked.
        #
        # Recursive trigger_source values (per TRIGGER_SOURCES in sessions.py):
        #   "healing" — sessions launched by the healing dispatcher
        #   "qa"      — sessions launched by the QA investigation dispatcher
        #               (both _run_investigation_session and follow-up agents
        #                use trigger_source="qa")
        if finding.source_butler == "qa":
            trigger_src = finding.source_session_trigger_source
            if trigger_src in {"healing", "qa"}:
                logger.info(
                    "QA dispatch suppressed: self-recursion barrier triggered "
                    "(source_session_trigger_source=%r, fingerprint=%s) — routing to meta-review",
                    trigger_src,
                    fp[:12],
                )
                return QaDispatchResult(
                    accepted=False,
                    fingerprint=fp,
                    reason="qa_self_recursion",
                )
            else:
                # Unknown or null trigger_source from QA butler — treat as
                # potentially recursive; route to meta-review as a precaution.
                logger.warning(
                    "QA finding from QA butler with unrecognized trigger_source; "
                    "routing to meta-review (source_session_trigger_source=%r, fingerprint=%s)",
                    trigger_src,
                    fp[:12],
                )
                return QaDispatchResult(
                    accepted=False,
                    fingerprint=fp,
                    reason="qa_self_recursion_precaution",
                )

        # ---------------------------------------------------------------
        # Gate 5: Severity gate
        # ---------------------------------------------------------------
        if finding.severity > config.severity_threshold:
            logger.debug(
                "QA dispatch skipped: severity=%d > threshold=%d fingerprint=%s",
                finding.severity,
                config.severity_threshold,
                fp[:12],
            )
            if metrics is not None:
                metrics.record_recovery_dispatch_decision(
                    workflow="qa", decision="severity_above_threshold"
                )
            return QaDispatchResult(
                accepted=False,
                fingerprint=fp,
                reason="severity_above_threshold",
            )

        # ---------------------------------------------------------------
        # Gate 5.5: Infra condition suppression (bu-27dxl.6.4)
        # ---------------------------------------------------------------
        # Only infra_state findings carry a fingerprint that means anything
        # to the infra_conditions ledger (source="infra_state" reuses the
        # exact same fingerprint — see InfraStateSource's module docstring);
        # every other discovery source is untouched by this gate. The one
        # fleet-linked finding names the controller's fleet episode instead,
        # so a fleet outage is one suppression decision, not one per daemon.
        if finding.source_type == INFRA_STATE_SOURCE_NAME:
            condition = await get_active_condition(
                pool, source=condition_source_for(finding.call_site), fingerprint=fp
            )
            if condition is not None:
                logger.debug(
                    "QA dispatch skipped: active infra condition fingerprint=%s "
                    "(state=%s escalation=%s)",
                    fp[:12],
                    condition["state"],
                    condition["escalation_level"],
                )
                reason = (
                    f"Infrastructure condition already active since "
                    f"{condition['first_detected_at'].isoformat()} "
                    f"(state={condition['state']}, escalation={condition['escalation_level']}): "
                    f"{condition['summary'] or finding.event_summary}"
                )
                # Neither write is optional. A persistence failure uses the
                # existing internal_error boundary below, before any attempt
                # claim. Retry the same finding with the same event identity:
                # an insert may have committed even if its response was lost.
                await update_finding_dedup_reason(pool, finding_id, "infra_condition_open")
                await create_dispatch_event(
                    pool,
                    fingerprint=fp,
                    butler_name=finding.source_butler,
                    decision="infra_condition_open",
                    reason=reason,
                    attempt_id=None,
                    event_id=uuid.uuid5(uuid.NAMESPACE_URL, f"qa:infra-condition:{finding_id}"),
                )
                if metrics is not None:
                    metrics.record_recovery_dispatch_decision(
                        workflow="qa", decision="infra_condition_open"
                    )
                return QaDispatchResult(
                    accepted=False,
                    fingerprint=fp,
                    reason="infra_condition_open",
                )

        # ---------------------------------------------------------------
        # Gate 6: Novelty gate — atomic check+insert
        # ---------------------------------------------------------------
        # Use a synthetic session_id since QA dispatch has no session_id.
        synthetic_session_id = uuid.uuid4()

        try:
            attempt_id, is_new = await create_or_join_attempt(
                pool=pool,
                fingerprint=fp,
                butler_name=finding.source_butler,
                severity=finding.severity,
                exception_type=finding.exception_type,
                call_site=finding.call_site,
                session_id=synthetic_session_id,
                sanitized_msg=finding.event_summary,
                qa_patrol_id=patrol_id,
                enforce_publication_hold=True,
            )
        except PublicationLineageHeld:
            # An unresolved publication holds this lineage (REQ-qa-publication-
            # authority-006): no attempt row exists, so nothing needs cleanup.
            logger.warning("QA dispatch held: ambiguous publication for fingerprint=%s", fp[:12])
            try:
                await update_finding_dedup_reason(pool, finding_id, "publication_held")
            except Exception as _dr_exc:
                logger.debug("QA dispatch: failed to update finding dedup_reason: %s", _dr_exc)
            if metrics is not None:
                metrics.record_recovery_dispatch_decision(
                    workflow="qa", decision="publication_held"
                )
            return QaDispatchResult(accepted=False, fingerprint=fp, reason="publication_held")

        if not is_new:
            logger.debug("QA dispatch skipped: already investigating fingerprint=%s", fp[:12])
            # Link the finding to the existing active attempt so the dashboard can trace it.
            try:
                await update_finding_attempt(pool, finding_id, attempt_id)
            except Exception as _link_exc:
                logger.debug(
                    "QA dispatch: failed to link finding to existing attempt: %s", _link_exc
                )
            # Write back the authoritative rejection reason to the finding record.
            try:
                await update_finding_dedup_reason(pool, finding_id, "already_investigating")
            except Exception as _dr_exc:
                logger.debug("QA dispatch: failed to update finding dedup_reason: %s", _dr_exc)
            # Record a dispatch event (no new attempt row — the existing one is the authority).
            try:
                await create_dispatch_event(
                    pool,
                    fingerprint=fp,
                    butler_name=finding.source_butler,
                    decision="novelty_join",
                    reason="Active investigation already exists for this fingerprint",
                    attempt_id=attempt_id,
                )
            except Exception as _evt_exc:
                logger.debug("QA dispatch: failed to record novelty_join event: %s", _evt_exc)
            if metrics is not None:
                metrics.record_recovery_dispatch_decision(workflow="qa", decision="novelty_join")
            return QaDispatchResult(
                accepted=False,
                fingerprint=fp,
                reason="already_investigating",
                attempt_id=attempt_id,
            )

        # From here on, we have an 'investigating' row with no session yet.
        # Any early exit that is an admission-control rejection (not a launch
        # failure) MUST delete the row and record a dispatch event rather than
        # marking the row "failed", so gate rejections do not poison the
        # circuit-breaker history.

        # Link the finding to this attempt immediately (best-effort; do not abort on failure).
        try:
            await update_finding_attempt(pool, finding_id, attempt_id)
        except Exception as _link_exc:
            logger.debug("QA dispatch: failed to link finding to attempt: %s", _link_exc)

        try:
            await record_event(
                pool,
                attempt_id=attempt_id,
                finding_id=finding_id,
                step="flagged",
                text=finding.event_summary,
                detail=_flagged_event_detail(finding),
                data={
                    "fingerprint": fp,
                    "source_type": finding.source_type,
                    "source_butler": finding.source_butler,
                    "severity": finding.severity,
                    "exception_type": finding.exception_type,
                    "call_site": finding.call_site,
                    "occurrence_count": finding.occurrence_count,
                },
                ts=await _fetch_patrol_started_at(pool, patrol_id),
            )
        except Exception as _journal_exc:
            logger.debug("QA dispatch: failed to record flagged journal event: %s", _journal_exc)

        # ---------------------------------------------------------------
        # Gate 7: Cooldown gate
        # ---------------------------------------------------------------
        # An unresolved publication outlives the cooldown window, so it is checked
        # explicitly and never inferred from the (expired) attempt window.
        publication_held = await get_publication_hold(pool, fp)
        recent = (
            None
            if publication_held
            else await get_recent_attempt(pool, fp, config.cooldown_minutes)
        )
        if publication_held or recent is not None:
            logger.debug(
                "QA dispatch skipped: %s for fingerprint=%s",
                "publication hold" if publication_held else "cooldown active",
                fp[:12],
            )
            await delete_orphaned_attempt(pool, attempt_id)
            try:
                await update_finding_dedup_reason(pool, finding_id, "cooldown")
            except Exception as _dr_exc:
                logger.debug("QA dispatch: failed to update finding dedup_reason: %s", _dr_exc)
            try:
                await create_dispatch_event(
                    pool,
                    fingerprint=fp,
                    butler_name=finding.source_butler,
                    decision="cooldown",
                    reason=(
                        f"Cooldown active: recent attempt closed within {config.cooldown_minutes}m"
                    ),
                )
            except Exception as _evt_exc:
                logger.debug("QA dispatch: failed to record cooldown event: %s", _evt_exc)
            if metrics is not None:
                metrics.record_recovery_dispatch_decision(workflow="qa", decision="cooldown")
            return QaDispatchResult(
                accepted=False,
                fingerprint=fp,
                reason="cooldown",
            )

        # ---------------------------------------------------------------
        # Gate 8: Concurrency cap
        # ---------------------------------------------------------------
        # The batch dispatcher pre-checks the active count before dispatching
        # each novel finding so capped work is queued for a later patrol
        # rather than being inserted and terminally failed here. If another
        # concurrent dispatcher slips past that pre-check, prefer a small
        # transient cap overshoot over dropping the investigation attempt.
        active_count = await count_active_attempts(pool, qa_only=True)
        if active_count > config.max_concurrent:
            logger.warning(
                "QA dispatch exceeded concurrency cap after claim "
                "(active=%d, max=%d, fingerprint=%s); proceeding",
                active_count,
                config.max_concurrent,
                fp[:12],
            )

        # ---------------------------------------------------------------
        # Gate 9: Circuit breaker
        # ---------------------------------------------------------------
        if config.circuit_breaker_threshold > 0:
            tripped = await _is_circuit_breaker_tripped(pool, config.circuit_breaker_threshold)
            if tripped:
                logger.warning(
                    "QA dispatch skipped: circuit breaker tripped (threshold=%d, fingerprint=%s)",
                    config.circuit_breaker_threshold,
                    fp[:12],
                )
                await delete_orphaned_attempt(pool, attempt_id)
                try:
                    await update_finding_dedup_reason(pool, finding_id, "circuit_breaker")
                except Exception as _dr_exc:
                    logger.debug("QA dispatch: failed to update finding dedup_reason: %s", _dr_exc)
                try:
                    await create_dispatch_event(
                        pool,
                        fingerprint=fp,
                        butler_name=finding.source_butler,
                        decision="circuit_breaker",
                        reason=(
                            f"Circuit breaker tripped:"
                            f" {config.circuit_breaker_threshold} consecutive failures"
                        ),
                    )
                except Exception as _evt_exc:
                    logger.debug(
                        "QA dispatch: failed to record circuit_breaker event: %s", _evt_exc
                    )
                if metrics is not None:
                    metrics.record_recovery_dispatch_decision(
                        workflow="qa", decision="circuit_breaker"
                    )
                return QaDispatchResult(
                    accepted=False,
                    fingerprint=fp,
                    reason="circuit_breaker",
                )

        # ---------------------------------------------------------------
        # Gate 10: Model resolution
        # ---------------------------------------------------------------
        model_result = None
        try:
            model_result = await resolve_model(pool, finding.source_butler, Complexity.SPECIALTY)
        except Exception as model_exc:
            logger.warning(
                "Model resolution failed for specialty tier (butler=%s): %s",
                finding.source_butler,
                model_exc,
            )

        if model_result is None:
            logger.warning(
                "QA dispatch skipped: no self_healing tier model available "
                "(butler=%s, fingerprint=%s)",
                finding.source_butler,
                fp[:12],
            )
            await delete_orphaned_attempt(pool, attempt_id)
            try:
                await update_finding_dedup_reason(pool, finding_id, "no_model")
            except Exception as _dr_exc:
                logger.debug("QA dispatch: failed to update finding dedup_reason: %s", _dr_exc)
            try:
                await create_dispatch_event(
                    pool,
                    fingerprint=fp,
                    butler_name=finding.source_butler,
                    decision="no_model",
                    reason="No self_healing tier model available",
                )
            except Exception as _evt_exc:
                logger.debug("QA dispatch: failed to record no_model event: %s", _evt_exc)
            if metrics is not None:
                metrics.record_recovery_dispatch_decision(workflow="qa", decision="no_model")
            return QaDispatchResult(
                accepted=False,
                fingerprint=fp,
                reason="no_model",
            )

        # ---------------------------------------------------------------
        # All gates passed — create worktree
        # ---------------------------------------------------------------

        # Freshness is admission, never a local-main fallback (REQ-testing-043).
        try:
            _base_ref = await _refresh_main_commit(repo_root)
        except MainRefreshError:
            logger.warning("QA dispatch refused: main refresh unavailable")
            await update_attempt_status(
                pool, attempt_id, "failed", error_detail="Main refresh unavailable"
            )
            return QaDispatchResult(
                accepted=False, fingerprint=fp, reason="main_refresh_failed", attempt_id=attempt_id
            )
        logger.info("QA preparation: attempt=%s base_sha=%s", attempt_id, _base_ref)

        try:
            worktree_path, branch_name = await create_healing_worktree(
                repo_root,
                finding.source_butler,
                fp,
                prefix=_QA_PREFIX,
                base_ref=_base_ref,
            )
        except WorktreeCreationError as wt_exc:
            logger.warning(
                "QA dispatch failed: worktree creation error (fingerprint=%s): %s",
                fp[:12],
                wt_exc,
            )
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail=f"Worktree creation failed: {wt_exc.git_output or str(wt_exc)}",
            )
            return QaDispatchResult(
                accepted=False,
                fingerprint=fp,
                reason="worktree_creation_failed",
                attempt_id=attempt_id,
            )

        # Store worktree path and branch on the attempt row via a direct metadata update.
        # update_attempt_status enforces state machine transitions and rejects
        # 'investigating → investigating', so use a targeted UPDATE instead.
        await pool.execute(
            """
            UPDATE public.healing_attempts
            SET branch_name   = $2,
                worktree_path = $3,
                updated_at    = now()
            WHERE id = $1
            """,
            attempt_id,
            branch_name,
            str(worktree_path),
        )

        logger.info(
            "QA dispatch accepted: butler=%s fingerprint=%s attempt=%s branch=%s",
            finding.source_butler,
            fp[:12],
            attempt_id,
            branch_name,
        )

        # ---------------------------------------------------------------
        # Spawn investigation agent + timeout watchdog as background tasks
        # ---------------------------------------------------------------
        investigation_task: asyncio.Task[None] = asyncio.create_task(
            _run_investigation_session(
                pool=pool,
                repo_root=repo_root,
                attempt_id=attempt_id,
                finding_id=finding_id,
                branch_name=branch_name,
                worktree_path=worktree_path,
                finding=finding,
                config=config,
                spawner=spawner,
                gh_token=gh_token,
                git_author_name=git_author_name,
                git_author_email=git_author_email,
                metrics=metrics,
                base_sha=_base_ref,
            ),
            name=f"qa-investigation-{attempt_id}",
        )

        watchdog_task: asyncio.Task[None] = asyncio.create_task(
            _qa_timeout_watchdog(
                pool=pool,
                attempt_id=attempt_id,
                repo_root=repo_root,
                branch_name=branch_name,
                worktree_path=worktree_path,
                investigation_task=investigation_task,
                timeout_minutes=config.timeout_minutes,
            ),
            name=f"qa-watchdog-{attempt_id}",
        )

        if task_registry is not None:
            task_registry.append(watchdog_task)

        return QaDispatchResult(
            accepted=True,
            fingerprint=fp,
            reason="dispatched",
            attempt_id=attempt_id,
        )

    except Exception as dispatch_exc:
        logger.warning(
            "Unexpected error in QA dispatcher (fingerprint=%s): %s",
            fp[:12],
            dispatch_exc,
            exc_info=True,
        )
        return QaDispatchResult(
            accepted=False,
            fingerprint=fp,
            reason="internal_error",
        )


# ---------------------------------------------------------------------------
# Batch dispatch helper
# ---------------------------------------------------------------------------


async def dispatch_novel_findings(
    pool: asyncpg.Pool,
    novel_findings: list[TriagedFinding],
    patrol_id: uuid.UUID,
    config: QaDispatchConfig,
    repo_root: Path,
    spawner: Any,
    gh_token: str | None = None,
    git_author_name: str | None = None,
    git_author_email: str | None = None,
    task_registry: list[asyncio.Task[Any]] | None = None,
    metrics: ButlerMetrics | None = None,
) -> list[QaDispatchResult]:
    """Dispatch investigations for a list of novel findings from triage.

    Processes findings in priority order (already sorted by triage layer).
    Stops dispatching new investigations when the concurrency cap is reached
    (subsequent findings are skipped for this patrol cycle).

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    novel_findings:
        Priority-sorted list of novel TriagedFindings from triage.
    patrol_id:
        UUID of the current qa_patrols row.
    config:
        QA dispatch configuration.
    repo_root:
        Absolute path to repository root.
    spawner:
        QA staffer's Spawner instance.
    gh_token:
        GitHub token from CredentialStore.
    task_registry:
        Optional list for watchdog task references.

    Returns
    -------
    list[QaDispatchResult]
        One result per input finding, in the same order.
    """
    results: list[QaDispatchResult] = []
    cap_skipped = 0

    for triaged in novel_findings:
        active_count = await count_active_attempts(pool, qa_only=True)
        if active_count >= config.max_concurrent:
            results.append(
                QaDispatchResult(
                    accepted=False,
                    fingerprint=triaged.finding.fingerprint,
                    reason="concurrency_cap",
                )
            )
            cap_skipped += 1
            continue

        # Once the concurrency cap is reached, stop calling dispatch for remaining
        # findings.  Continuing would cause each subsequent finding to go through
        # create_or_join_attempt (inserting a new row) before being rejected — these
        # orphaned 'failed' rows can then trigger cooldown for the next patrol cycle.
        if cap_skipped > 0:
            results.append(
                QaDispatchResult(
                    accepted=False,
                    fingerprint=triaged.finding.fingerprint,
                    reason="concurrency_cap",
                )
            )
            cap_skipped += 1
            # Record authoritative suppression reason and queue finding for retry
            # in the next patrol cycle.  Without this the qa_findings row stays
            # with dedup_reason=NULL (misleading) and the finding is silently lost
            # for one-shot errors that won't be re-discovered by a source.
            try:
                await update_finding_dedup_reason(pool, triaged.finding_id, "concurrency_cap")
            except Exception as _dr_exc:
                logger.debug(
                    "QA dispatch: failed to update finding dedup_reason (cap skip): %s", _dr_exc
                )
            try:
                await update_finding_dispatch_queued(pool, triaged.finding_id, True)
            except Exception as _dq_exc:
                logger.debug(
                    "QA dispatch: failed to mark finding dispatch_queued (cap skip): %s", _dq_exc
                )
            continue

        result = await dispatch_qa_investigation(
            pool=pool,
            triaged_finding=triaged,
            patrol_id=patrol_id,
            config=config,
            repo_root=repo_root,
            spawner=spawner,
            gh_token=gh_token,
            git_author_name=git_author_name,
            git_author_email=git_author_email,
            task_registry=task_registry,
            metrics=metrics,
        )
        results.append(result)

        if result.reason == "concurrency_cap":
            cap_skipped += 1

    if cap_skipped > 0:
        logger.info(
            "QA dispatch: %d finding(s) skipped due to concurrency cap "
            "(will be retried next patrol cycle)",
            cap_skipped,
        )

    dispatched = sum(1 for r in results if r.accepted)
    if dispatched > 0:
        logger.info(
            "QA dispatch batch complete: dispatched=%d total_novel=%d patrol=%s",
            dispatched,
            len(novel_findings),
            patrol_id,
        )

    return results
