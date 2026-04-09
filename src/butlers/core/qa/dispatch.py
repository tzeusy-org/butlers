"""QA investigation dispatch engine.

Unified investigation lifecycle management for all QA-originated issues
regardless of discovery source.  Creates worktrees, spawns LLM agents,
monitors timeouts, creates anonymized PRs, and records outcomes.

The dispatcher applies the same 10-gate sequence as the self-healing dispatcher
but with QA-specific wiring:
 - Gate 1: No-recursion guard (trigger_source == "healing" → skip)
 - Gate 2: Opt-in gate (always on for QA dispatcher)
 - Gate 3: Fingerprint (pre-computed from QaFinding)
 - Gate 4: Fingerprint persistence (skipped — no session_id in QA path)
 - Gate 5: Severity gate
 - Gate 6: Novelty gate (atomic check+insert via create_or_join_attempt)
 - Gate 7: Cooldown gate
 - Gate 8: Concurrency cap
 - Gate 9: Circuit breaker
 - Gate 10: Model resolution

Note: triage performs a fast non-atomic dedup check (gates 1-3 above) to
filter obvious duplicates early.  This dispatcher applies the authoritative
atomic claim via create_or_join_attempt (gate 6).

Investigation agents run in sandboxed worktree environments with only
GH_TOKEN (from CredentialStore), PATH, and build-tool variables.

Spec reference
--------------
openspec/changes/qa-staffer/specs/qa-investigation-dispatch/spec.md
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg

from butlers.core.healing.anonymizer import anonymize, validate_anonymized
from butlers.core.healing.dispatch import (
    CIRCUIT_BREAKER_FAILURE_STATUSES,
    UNFIXABLE_SENTINEL_FILENAME,
)
from butlers.core.healing.tracking import (
    count_active_attempts,
    create_dispatch_event,
    create_or_join_attempt,
    delete_orphaned_attempt,
    get_recent_attempt,
    update_attempt_status,
)
from butlers.core.healing.worktree import (
    WorktreeCreationError,
    create_healing_worktree,
    remove_healing_worktree,
)
from butlers.core.model_routing import Complexity, resolve_model
from butlers.core.qa.findings import update_finding_attempt, update_finding_dedup_reason
from butlers.core.qa.models import QaFinding
from butlers.core.qa.prompts import build_investigation_prompt, build_review_followup_prompt
from butlers.core.qa.repo_whitelist import RepoWhitelist, parse_repo_url
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
_MAX_FOLLOW_UP_PER_CYCLE = 1

#: Maximum characters to store in review_feedback_summary.
_MAX_FEEDBACK_SUMMARY_LEN = 2000

#: Review states that require a follow-up agent dispatch.
#: ``commented`` is included so unresolved review threads (even without an
#: explicit CHANGES_REQUESTED decision) also trigger follow-up dispatch.
_ACTIONABLE_REVIEW_STATES = frozenset({"changes_requested", "commented"})

#: PR labels applied to QA-originated investigation PRs.
_DEFAULT_PR_LABELS = ["self-healing", "automated"]

#: CredentialStore key for the GitHub token used by QA investigations.
QA_GH_TOKEN_KEY = "BUTLERS_QA_GH_TOKEN"

#: Sentinel file placed by investigation agent to signal unfixable error.
_UNFIXABLE_FILE = UNFIXABLE_SENTINEL_FILENAME

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
        Labels to apply to QA investigation PRs.
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
    """Repository whitelist instance for PR creation enforcement.

    When ``None``, a new ``RepoWhitelist(db_pool=None)`` is used, which
    blocks all PR creation (fail-closed with no DB pool).  Callers should
    supply a pre-loaded ``RepoWhitelist`` backed by the DB pool.
    """


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


def build_sandbox_env(gh_token: str | None) -> dict[str, str]:
    """Build a minimal sandboxed environment for investigation agents.

    Only allows: GH_TOKEN, PATH, HOME, and build-tool variables.
    Strips all BUTLERS_* vars, database connection strings, API keys,
    OAuth tokens, and any other butler runtime variables.

    Parameters
    ----------
    gh_token:
        GitHub token from CredentialStore.  If ``None``, GH_TOKEN is not
        included in the returned environment.

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

    return env


# ---------------------------------------------------------------------------
# PR creation
# ---------------------------------------------------------------------------


async def _get_remote_owner_repo(repo_root: Path, env: dict[str, str]) -> str | None:
    """Return the ``owner/repo`` string from the ``origin`` remote, or ``None``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "remote",
            "get-url",
            "origin",
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        remote_url = stdout.decode("utf-8", errors="replace").strip()
        parsed = parse_repo_url(remote_url)
        if parsed is None:
            return None
        owner, repo = parsed
        return f"{owner}/{repo}"
    except Exception:  # noqa: BLE001
        logger.warning("_get_remote_owner_repo: failed to get origin URL", exc_info=True)
        return None


async def _create_qa_pr(
    repo_root: Path,
    branch_name: str,
    finding: QaFinding,
    attempt_id: uuid.UUID,
    labels: list[str],
    gh_token: str | None,
    dashboard_base_url: str | None = None,
    whitelist: RepoWhitelist | None = None,
) -> tuple[str | None, int | None, str | None]:
    """Push branch and create a QA investigation GitHub PR.

    Returns
    -------
    tuple[str | None, int | None, str | None]
        ``(pr_url, pr_number, error_message)``  — *error_message* is:
        - ``None`` on success,
        - ``"anonymization_failed"`` when PII validation blocks the PR,
        - ``"no_gh_token"`` when no GitHub token is available,
        - ``"repo_not_whitelisted:remote_unavailable"`` when the origin
          remote URL cannot be resolved; PR creation is blocked fail-closed,
        - ``"repo_not_whitelisted:{reason}:{owner/repo}"`` when whitelist
          enforcement blocks PR creation for a resolved repository; ``reason``
          is the whitelist failure code (e.g. ``whitelist_empty`` or
          ``not_in_whitelist``),
        - Any other string for push/gh failures.
    """
    if not gh_token:
        return None, None, "no_gh_token"

    env: dict[str, str] = build_sandbox_env(gh_token)

    # Step 0: Whitelist enforcement — check before git push
    # Fail-closed: if whitelist is None, create a no-pool instance (blocks all).
    effective_whitelist = whitelist if whitelist is not None else RepoWhitelist(db_pool=None)
    # Ensure the whitelist has been loaded at least once (idempotent, guarded by lock).
    await effective_whitelist.ensure_loaded()
    owner_repo = await _get_remote_owner_repo(repo_root, env)
    if owner_repo is None:
        logger.warning(
            "_create_qa_pr: could not determine repository from origin remote; "
            "blocking PR creation (fail-closed)"
        )
        return None, None, "repo_not_whitelisted:remote_unavailable"

    allowed, wl_reason = effective_whitelist.is_allowed(owner_repo)
    if not allowed:
        logger.info(
            "_create_qa_pr: PR blocked for repo %r — whitelist reason: %s",
            owner_repo,
            wl_reason,
        )
        # Return a reason code that the caller can use for owner notification.
        return None, None, f"repo_not_whitelisted:{wl_reason}:{owner_repo}"

    # Step 1: git push
    push_proc = await asyncio.create_subprocess_exec(
        "git",
        "push",
        "origin",
        branch_name,
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    _, push_stderr = await push_proc.communicate()
    if push_proc.returncode != 0:
        push_err = push_stderr.decode("utf-8", errors="replace").strip()
        return None, None, f"git push failed: {push_err}"

    # Step 2: Build PR content
    fp_short = finding.fingerprint[:12]

    # Build dashboard link for PR body
    dashboard_link = ""
    if dashboard_base_url:
        dashboard_url = f"{dashboard_base_url.rstrip('/')}/qa/investigations/{attempt_id}"
        dashboard_link = f"\n\n[View investigation details]({dashboard_url})"

    raw_title = f"fix(qa): {finding.exception_type} in {finding.call_site} [{fp_short}]"

    raw_body = f"""\
## QA Investigation Fix: {fp_short}

**Butler:** {finding.source_butler}
**Error:** {finding.exception_type}
**Call site:** {finding.call_site}
**Fingerprint:** `{finding.fingerprint}`
**Attempt ID:** `{attempt_id}`
**Discovery source:** {finding.source_type}
**Occurrences:** {finding.occurrence_count}
**First seen:** {finding.first_seen.isoformat()}
**Last seen:** {finding.last_seen.isoformat()}

### Root Cause
*(Filled in by the investigation agent's commit message and PR description.)*

### Fix Summary
*(Filled in by the investigation agent.)*

### Test Coverage
*(Filled in by the investigation agent.)*

---
*Automated fix proposed by QA Staffer. Review carefully before merging.*

*Fingerprint: `{finding.fingerprint}`*{dashboard_link}
"""

    # Step 3: Anonymize
    pr_title = anonymize(raw_title, repo_root)
    pr_body = anonymize(raw_body, repo_root)

    # Step 4: Validate for residual PII
    title_clean, title_violations = validate_anonymized(pr_title)
    body_clean, body_violations = validate_anonymized(pr_body)
    if not title_clean or not body_clean:
        violations = title_violations + body_violations
        logger.warning(
            "Anonymization validation failed for QA investigation PR (attempt=%s): "
            "%d violation(s): %s",
            attempt_id,
            len(violations),
            violations[:3],
        )
        # Delete the remote branch (await to avoid leaking the child process)
        delete_proc = await asyncio.create_subprocess_exec(
            "git",
            "push",
            "origin",
            "--delete",
            branch_name,
            cwd=str(repo_root),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _, delete_stderr = await delete_proc.communicate()
        if delete_proc.returncode != 0:
            logger.warning(
                "Failed to delete remote branch %s after anonymization failure: %s",
                branch_name,
                delete_stderr.decode("utf-8", errors="replace").strip(),
            )
        return None, None, "anonymization_failed"

    # Step 5: gh pr create
    label_args: list[str] = []
    for label in labels:
        label_args.extend(["--label", label])

    gh_cmd = [
        "gh",
        "pr",
        "create",
        "--base",
        "main",
        "--head",
        branch_name,
        "--title",
        pr_title,
        "--body",
        pr_body,
        *label_args,
    ]

    gh_proc = await asyncio.create_subprocess_exec(
        *gh_cmd,
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    gh_stdout, gh_stderr = await gh_proc.communicate()
    if gh_proc.returncode != 0:
        err = gh_stderr.decode("utf-8", errors="replace").strip()
        return None, None, f"gh pr create failed: {err}"

    pr_url = gh_stdout.decode("utf-8", errors="replace").strip()
    pr_number: int | None = None
    try:
        pr_number = int(pr_url.rstrip("/").split("/")[-1])
    except (ValueError, IndexError):
        pass

    return pr_url, pr_number, None


# ---------------------------------------------------------------------------
# Timeout watchdog
# ---------------------------------------------------------------------------


async def _qa_timeout_watchdog(
    pool: asyncpg.Pool,
    attempt_id: uuid.UUID,
    repo_root: Path,
    branch_name: str,
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
            await remove_healing_worktree(
                repo_root, branch_name, delete_branch=True, delete_remote=False
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
) -> None:
    """Run the QA investigation agent and handle PR creation.

    This coroutine is scheduled as an ``asyncio.Task`` and monitored by a
    separate timeout watchdog task.
    """
    investigation_session_id: uuid.UUID | None = None

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

        # Build sandboxed environment for the agent
        sandbox_env = build_sandbox_env(gh_token)

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
            complexity=Complexity.SELF_HEALING,
            cwd=str(worktree_path),
            bypass_butler_semaphore=True,
            env_override=sandbox_env,
            timeout_override=config.timeout_minutes * 60,
        )

        if result.session_id is not None:
            # Capture the session_id locally; do NOT call update_attempt_status here because
            # create_or_join_attempt already inserts rows in the 'investigating' state and the
            # state machine rejects 'investigating → investigating' transitions.  The session_id
            # will be attached on the next valid status transition (pr_open / failed / timeout).
            investigation_session_id = result.session_id

        if not result.success:
            error_detail = result.error or "Investigation agent returned non-success result"
            logger.warning(
                "QA investigation agent failed (attempt=%s): %s", attempt_id, error_detail
            )
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail=error_detail,
                healing_session_id=investigation_session_id,
            )
            await remove_healing_worktree(
                repo_root, branch_name, delete_branch=True, delete_remote=False
            )
            return

        # Check for unfixable sentinel
        if (worktree_path / _UNFIXABLE_FILE).exists():
            logger.info("Investigation agent marked error as unfixable (attempt=%s)", attempt_id)
            await update_attempt_status(
                pool,
                attempt_id,
                "unfixable",
                error_detail="Investigation agent determined this error is not a code bug",
                healing_session_id=investigation_session_id,
            )
            await remove_healing_worktree(
                repo_root, branch_name, delete_branch=True, delete_remote=False
            )
            return

        # Agent succeeded — create PR
        pr_url, pr_number, pr_error = await _create_qa_pr(
            repo_root=repo_root,
            branch_name=branch_name,
            finding=finding,
            attempt_id=attempt_id,
            labels=config.pr_labels,
            gh_token=gh_token,
            dashboard_base_url=config.dashboard_base_url,
            whitelist=config.repo_whitelist,
        )

        if pr_error == "anonymization_failed":
            await update_attempt_status(
                pool,
                attempt_id,
                "anonymization_failed",
                error_detail="PR blocked: residual PII or credentials detected after anonymization",
                healing_session_id=investigation_session_id,
            )
            await remove_healing_worktree(
                repo_root, branch_name, delete_branch=True, delete_remote=False
            )
            return

        if pr_error == "no_gh_token":
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail="no_gh_token: BUTLERS_QA_GH_TOKEN not found in CredentialStore",
                healing_session_id=investigation_session_id,
            )
            await remove_healing_worktree(
                repo_root, branch_name, delete_branch=True, delete_remote=False
            )
            return

        if pr_error is not None and pr_error.startswith("repo_not_whitelisted"):
            # Whitelist enforcement: PR blocked for this repository.
            # Error formats:
            #   "repo_not_whitelisted:remote_unavailable"  — remote URL could not be resolved
            #   "repo_not_whitelisted:{reason}:{owner/repo}" — whitelist check failed
            parts = pr_error.split(":", 2)
            if len(parts) == 3:
                wl_detail = (
                    f"repository '{parts[2]}' is not in the QA whitelist (reason: {parts[1]})"
                )
            elif len(parts) == 2 and parts[1] == "remote_unavailable":
                wl_detail = "could not determine repository from origin remote URL"
            else:
                wl_detail = "repository is not in the QA whitelist"
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail=f"PR blocked: {wl_detail}",
                healing_session_id=investigation_session_id,
            )
            await remove_healing_worktree(
                repo_root, branch_name, delete_branch=True, delete_remote=False
            )
            return

        if pr_error is not None:
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail=pr_error,
                healing_session_id=investigation_session_id,
            )
            await remove_healing_worktree(
                repo_root, branch_name, delete_branch=True, delete_remote=False
            )
            return

        # PR created successfully
        await update_attempt_status(
            pool,
            attempt_id,
            "pr_open",
            pr_url=pr_url,
            pr_number=pr_number,
            healing_session_id=investigation_session_id,
        )
        # Note: finding was already linked to attempt in dispatch_qa_investigation
        # (via update_finding_attempt at gate 6). No redundant call needed here.

        logger.info("QA investigation PR created: attempt=%s pr_url=%s", attempt_id, pr_url)
        # Remove worktree; keep branch (backs the open PR)
        await remove_healing_worktree(
            repo_root, branch_name, delete_branch=False, delete_remote=False
        )

    except asyncio.CancelledError:
        # Cancelled by watchdog — watchdog sets status to "timeout"
        if _HAS_OTEL and _inv_span is not None:
            _inv_span.set_status(trace.StatusCode.ERROR, "investigation cancelled (timeout)")
        raise

    except Exception as exc:
        logger.exception(
            "Unexpected error in QA investigation session (attempt=%s): %s", attempt_id, exc
        )
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
        await remove_healing_worktree(
            repo_root, branch_name, delete_branch=True, delete_remote=False
        )
    finally:
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
    spawner: Any = None,
    config: QaDispatchConfig | None = None,
    task_registry: list[asyncio.Task[Any]] | None = None,
) -> dict[str, int]:
    """Check GitHub status of all pr_open QA healing attempts.

    Called on each patrol cycle from the QA staffer daemon context (not inside
    an agent worktree).  Transitions pr_open → pr_merged or pr_open → failed
    based on actual GitHub PR state.

    When ``spawner`` and ``config`` are provided, also checks for unresolved
    review threads or "changes_requested" state and dispatches a follow-up
    agent to address reviewer feedback (rate-limited to
    ``_MAX_FOLLOW_UP_PER_CYCLE`` per PR per patrol cycle).

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
        SELECT id, pr_url, pr_number, fingerprint, butler_name, follow_up_count, branch_name
        FROM public.healing_attempts
        WHERE status = 'pr_open'
          AND qa_patrol_id IS NOT NULL
          AND pr_url IS NOT NULL
        """
    )

    if not rows:
        return counts

    env = build_sandbox_env(gh_token)

    for row in rows:
        attempt_id: uuid.UUID = row["id"]
        pr_number: int | None = row["pr_number"]
        pr_url: str = row["pr_url"]
        fingerprint: str = row["fingerprint"]
        butler_name: str = row["butler_name"]
        follow_up_count: int = row["follow_up_count"] or 0
        pr_branch_name: str | None = row["branch_name"]

        if pr_number is None:
            continue

        try:
            # Fetch state + review info in one call
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "state,reviews,latestReviews,reviewThreads",
                cwd=str(repo_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                counts["errors"] += 1
                continue

            import json as _json

            try:
                pr_data = _json.loads(stdout.decode("utf-8", errors="replace"))
            except _json.JSONDecodeError:
                counts["errors"] += 1
                continue

            state = pr_data.get("state", "").upper()

            if state == "MERGED":
                await update_attempt_status(pool, attempt_id, "pr_merged")
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
            if (
                spawner is not None
                and config is not None
                and _needs_followup
                and follow_up_count < _MAX_FOLLOW_UP_PER_CYCLE
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
                    task_registry=task_registry,
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


def _extract_review_state(
    pr_data: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Extract review state and feedback summary from gh pr view JSON output.

    Parameters
    ----------
    pr_data:
        Parsed JSON from ``gh pr view --json reviews,latestReviews,reviewThreads``.

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
    task_registry: list[asyncio.Task[Any]] | None = None,
) -> bool:
    """Dispatch a follow-up agent to address PR reviewer feedback.

    Checks out the existing PR head branch into a dedicated worktree, spawns a
    follow-up agent with the reviewer feedback as context, and pushes the
    resulting changes to the same PR branch so the open PR is updated.

    Rate-limited: increments ``follow_up_count`` on the healing_attempts row.
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

        async def _run_git_here(*args: str) -> tuple[int, str]:
            proc = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=str(repo_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            return proc.returncode, stdout.decode("utf-8", errors="replace")

        # Ensure the remote branch is available locally
        fetch_rc, fetch_out = await _run_git_here("fetch", "origin", followup_branch)
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

        # Check if a local tracking branch already exists
        branch_check_rc, _ = await _run_git_here(
            "show-ref", "--verify", "--quiet", f"refs/heads/{followup_branch}"
        )

        if branch_check_rc == 0:
            add_rc, add_out = await _run_git_here(
                "worktree", "add", str(worktree_path), followup_branch
            )
        else:
            add_rc, add_out = await _run_git_here(
                "worktree",
                "add",
                "-b",
                followup_branch,
                "--track",
                str(worktree_path),
                f"origin/{followup_branch}",
            )

        if add_rc != 0:
            raise WorktreeCreationError(
                f"git worktree add failed for branch {followup_branch}: {add_out.strip()}"
            )

        if not worktree_path.is_dir():
            raise WorktreeCreationError(f"Worktree directory not created at {worktree_path}")

        # Increment follow_up_count atomically
        await pool.execute(
            """
            UPDATE public.healing_attempts
            SET follow_up_count = follow_up_count + 1,
                updated_at      = now()
            WHERE id = $1
            """,
            attempt_id,
        )

        sandbox_env = build_sandbox_env(gh_token)

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
    to the origin branch (which backs the open PR).
    Cleans up the local worktree after completion (branch is kept for the PR).
    """
    try:
        result = await spawner.trigger(
            prompt=prompt,
            trigger_source="qa",
            complexity=Complexity.SELF_HEALING,
            cwd=str(worktree_path),
            bypass_butler_semaphore=True,
            env_override=sandbox_env,
            timeout_override=config.timeout_minutes * 60,
        )

        if not result.success:
            logger.warning(
                "QA review follow-up agent failed (attempt=%s): %s",
                attempt_id,
                result.error or "non-success result",
            )
            await remove_healing_worktree(
                repo_root, followup_branch, delete_branch=True, delete_remote=False
            )
            return

        # Push the follow-up commits to origin
        push_proc = await asyncio.create_subprocess_exec(
            "git",
            "push",
            "origin",
            followup_branch,
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=sandbox_env,
        )
        _, push_stderr = await push_proc.communicate()
        if push_proc.returncode != 0:
            err = push_stderr.decode("utf-8", errors="replace").strip()
            logger.warning(
                "QA review follow-up: git push failed (attempt=%s): %s",
                attempt_id,
                err,
            )
        else:
            logger.info(
                "QA review follow-up: pushed to origin/%s (attempt=%s pr_number=%s)",
                followup_branch,
                attempt_id,
                pr_number,
            )

        # Clean up the local worktree; keep the branch for the PR
        await remove_healing_worktree(
            repo_root, followup_branch, delete_branch=False, delete_remote=False
        )

    except Exception as exc:
        logger.exception(
            "Unexpected error in QA review follow-up session (attempt=%s): %s",
            attempt_id,
            exc,
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

    Only launched executions (``healing_session_id IS NOT NULL``) are counted.
    Gate rejections that were cleaned up as dispatch events do not have a
    ``healing_session_id`` and are excluded from the circuit-breaker signal.
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


async def dispatch_qa_investigation(
    pool: asyncpg.Pool,
    triaged_finding: TriagedFinding,
    patrol_id: uuid.UUID,
    config: QaDispatchConfig,
    repo_root: Path,
    spawner: Any,
    gh_token: str | None = None,
    task_registry: list[asyncio.Task[Any]] | None = None,
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
        # Gate 5: Severity gate
        # ---------------------------------------------------------------
        if finding.severity > config.severity_threshold:
            logger.debug(
                "QA dispatch skipped: severity=%d > threshold=%d fingerprint=%s",
                finding.severity,
                config.severity_threshold,
                fp[:12],
            )
            return QaDispatchResult(
                accepted=False,
                fingerprint=fp,
                reason="severity_above_threshold",
            )

        # ---------------------------------------------------------------
        # Gate 6: Novelty gate — atomic check+insert
        # ---------------------------------------------------------------
        # Use a synthetic session_id since QA dispatch has no session_id.
        synthetic_session_id = uuid.uuid4()

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
        )

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

        # ---------------------------------------------------------------
        # Gate 7: Cooldown gate
        # ---------------------------------------------------------------
        recent = await get_recent_attempt(pool, fp, config.cooldown_minutes)
        if recent is not None:
            logger.debug("QA dispatch skipped: cooldown active for fingerprint=%s", fp[:12])
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
            return QaDispatchResult(
                accepted=False,
                fingerprint=fp,
                reason="cooldown",
            )

        # ---------------------------------------------------------------
        # Gate 8: Concurrency cap
        # ---------------------------------------------------------------
        active_count = await count_active_attempts(pool)
        # active_count includes the row we just inserted
        if active_count > config.max_concurrent:
            logger.debug(
                "QA dispatch skipped: concurrency cap reached (active=%d, max=%d, fingerprint=%s)",
                active_count,
                config.max_concurrent,
                fp[:12],
            )
            await delete_orphaned_attempt(pool, attempt_id)
            try:
                await update_finding_dedup_reason(pool, finding_id, "concurrency_cap")
            except Exception as _dr_exc:
                logger.debug("QA dispatch: failed to update finding dedup_reason: %s", _dr_exc)
            try:
                await create_dispatch_event(
                    pool,
                    fingerprint=fp,
                    butler_name=finding.source_butler,
                    decision="concurrency_cap",
                    reason=(
                        f"Concurrency cap reached: {active_count} active"
                        f" / {config.max_concurrent} max"
                    ),
                )
            except Exception as _evt_exc:
                logger.debug("QA dispatch: failed to record concurrency_cap event: %s", _evt_exc)
            return QaDispatchResult(
                accepted=False,
                fingerprint=fp,
                reason="concurrency_cap",
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
            model_result = await resolve_model(pool, finding.source_butler, Complexity.SELF_HEALING)
        except Exception as model_exc:
            logger.warning(
                "Model resolution failed for self_healing tier (butler=%s): %s",
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
            return QaDispatchResult(
                accepted=False,
                fingerprint=fp,
                reason="no_model",
            )

        # ---------------------------------------------------------------
        # All gates passed — create worktree
        # ---------------------------------------------------------------

        # Fetch latest main before creating the worktree branch
        try:
            fetch_proc = await asyncio.create_subprocess_exec(
                "git",
                "fetch",
                "origin",
                "main",
                cwd=str(repo_root),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, fetch_stderr = await fetch_proc.communicate()
            if fetch_proc.returncode != 0:
                logger.warning(
                    "git fetch origin main failed (non-fatal): %s",
                    fetch_stderr.decode("utf-8", errors="replace").strip(),
                )
        except Exception as fetch_exc:
            logger.warning("git fetch origin main failed (non-fatal): %s", fetch_exc)

        try:
            worktree_path, branch_name = await create_healing_worktree(
                repo_root,
                finding.source_butler,
                fp,
                prefix=_QA_PREFIX,
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
            ),
            name=f"qa-investigation-{attempt_id}",
        )

        watchdog_task: asyncio.Task[None] = asyncio.create_task(
            _qa_timeout_watchdog(
                pool=pool,
                attempt_id=attempt_id,
                repo_root=repo_root,
                branch_name=branch_name,
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
    task_registry: list[asyncio.Task[Any]] | None = None,
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
            continue

        result = await dispatch_qa_investigation(
            pool=pool,
            triaged_finding=triaged,
            patrol_id=patrol_id,
            config=config,
            repo_root=repo_root,
            spawner=spawner,
            gh_token=gh_token,
            task_registry=task_registry,
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
