"""QA investigation dispatch engine.

Unified investigation lifecycle management for all QA-originated issues
regardless of discovery source.  Creates worktrees, spawns LLM agents,
monitors timeouts, creates anonymized PRs, and records outcomes.

The dispatcher applies the same 10-gate sequence as the self-healing dispatcher
but with QA-specific wiring:
 - Gate 0: QA self-recursion barrier (suppress findings from QA self-healing/investigation sessions)
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
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import asyncpg

from butlers.core.healing.anonymizer import anonymize, sanitize_labels, validate_anonymized
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
    record_phase_session,
    update_attempt_status,
    update_phase_session_status,
)
from butlers.core.healing.worktree import (
    WorktreeCreationError,
    create_healing_worktree,
    remove_healing_worktree,
)
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
from butlers.core.qa.repo_whitelist import RepoWhitelist, parse_repo_url
from butlers.core.qa.severity import failed_with_human_action
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

#: Temporary environment variable consumed by the git askpass helper.
_GIT_AUTH_TOKEN_ENV_VAR = "BUTLERS_QA_GIT_TOKEN"

#: Sentinel file placed by investigation agent to signal unfixable error.
_UNFIXABLE_FILE = UNFIXABLE_SENTINEL_FILENAME

#: Filename written by the investigation agent (in its CWD, which is
#: ``{worktree}/.tmp/qa-agent``) to populate the PR body's Root Cause / Fix
#: Summary / Test Coverage sections.  The agent is instructed to write here
#: by ``build_investigation_prompt``.  See ``_load_investigation_notes``.
_INVESTIGATION_NOTES_FILE = "INVESTIGATION_NOTES.md"

#: Structured investigation notes artifact written at worktree root by the QA
#: investigation agent. Persisted before terminal worktree teardown.
_INVESTIGATION_NOTES_JSON = Path(".qa") / "investigation_notes.json"

#: Mapping from H2 header text (lowercased, no leading "## ") to the section
#: key returned by ``_load_investigation_notes``.
_NOTES_HEADER_TO_KEY: dict[str, str] = {
    "root cause": "root_cause",
    "fix summary": "fix_summary",
    "test coverage": "test_coverage",
}

#: Markers that indicate raw evidence-collection structures, not harmless
#: prose that merely names an implementation detail.
_FORBIDDEN_QA_PR_BODY_MARKERS = ("\n### Evidence\n", "\n## Evidence\n", "evidence_lines:")

#: Substituted into the PR body for any section the agent did not provide.
_NOTES_PLACEHOLDER = "*(The investigation agent did not provide this section.)*"


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


def _get_qa_anonymization_failed_total():
    """Return the qa_anonymization_failed_total Prometheus Counter."""
    try:
        from prometheus_client import Counter

        return Counter(
            "qa_anonymization_failed_total",
            "Total QA PR payloads rejected by anonymization validation",
        )
    except (ImportError, ValueError):
        logger.debug(
            "Failed to initialize Prometheus counter "
            "'qa_anonymization_failed_total'; metric will not be exported",
            exc_info=True,
        )
        return None


_qa_investigation_notes_parse_total = _get_qa_investigation_notes_parse_total()
_qa_anonymization_failed_total = _get_qa_anonymization_failed_total()


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


def _record_qa_anonymization_failed() -> None:
    if _qa_anonymization_failed_total is None:
        return
    try:
        _qa_anonymization_failed_total.inc()
    except Exception:  # noqa: BLE001
        logger.debug(
            "Failed to record qa_anonymization_failed_total metric",
            exc_info=True,
        )


_ANONYMIZATION_FAILED_ERROR = "anonymization_failed"
_ANONYMIZATION_FAILURE_EVENT_TEXT = "anonymization validator rejected PR payload"


def _is_anonymization_failure_error(error: str | None) -> bool:
    return error == _ANONYMIZATION_FAILED_ERROR or bool(
        error and error.startswith(f"{_ANONYMIZATION_FAILED_ERROR}:")
    )


def _clean_validator_reason(reason: str) -> str:
    """Keep validator reason metadata while dropping raw surrounding context."""
    one_line = " ".join(reason.split())
    return one_line.split("; context:", 1)[0].strip()


def _format_anonymization_validator_detail(violations: list[str]) -> str:
    if not violations:
        return "validator reported residual sensitive content"

    cleaned = [
        detail for violation in violations[:3] if (detail := _clean_validator_reason(violation))
    ]
    if not cleaned:
        return "validator reported residual sensitive content"

    if len(violations) > len(cleaned):
        cleaned.append(f"{len(violations) - len(cleaned)} additional validator reason(s)")

    return "; ".join(cleaned)


def _anonymization_failure_error(detail: str | None = None) -> str:
    if not detail:
        return _ANONYMIZATION_FAILED_ERROR
    return f"{_ANONYMIZATION_FAILED_ERROR}: {detail}"


def _anonymization_failure_detail(error: str | None) -> str:
    if error and error.startswith(f"{_ANONYMIZATION_FAILED_ERROR}:"):
        return _clean_validator_reason(
            error.removeprefix(f"{_ANONYMIZATION_FAILED_ERROR}:").strip()
        )
    return "validator reported residual sensitive content"


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


@lru_cache(maxsize=1)
def _git_askpass_script() -> Path:
    """Create and cache a minimal askpass helper for git HTTPS auth."""
    script_dir = Path(tempfile.mkdtemp(prefix="butlers-qa-git-askpass-"))
    script_path = script_dir / "git-askpass.sh"
    script_path.write_text(
        "#!/bin/sh\n"
        'prompt="${1:-}"\n'
        'case "$prompt" in\n'
        "  *Username*|*username*) printf '%s\\n' \"x-access-token\" ;;\n"
        f"  *) printf '%s\\n' \"${{{_GIT_AUTH_TOKEN_ENV_VAR}:-}}\" ;;\n"
        "esac\n",
        encoding="ascii",
    )
    script_path.chmod(0o700)
    return script_path


def build_git_auth_env(
    gh_token: str | None, *, base_env: dict[str, str] | None = None
) -> dict[str, str]:
    """Build subprocess env for non-interactive git-over-HTTPS commands."""
    env = dict(base_env) if base_env is not None else build_sandbox_env(gh_token)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if gh_token:
        env["GH_TOKEN"] = gh_token
        env[_GIT_AUTH_TOKEN_ENV_VAR] = gh_token
        env["GIT_ASKPASS"] = str(_git_askpass_script())
    else:
        env.pop("GIT_ASKPASS", None)
        env.pop(_GIT_AUTH_TOKEN_ENV_VAR, None)
    return env


_AUTH_ERROR_MARKERS: tuple[str, ...] = (
    "could not read username",
    "authentication failed",
    "repository not found",
    "access denied",
)
"""Lowercase substrings that identify authentication-related git/gh errors.

Shared by :func:`_classify_git_push_error` and the ``gh pr create`` error
classifier so that both code paths remain consistent without duplication.
"""


def _classify_git_push_error(push_err: str) -> str:
    """Return a stable error code prefix for git push failures."""
    lowered = push_err.lower()
    if any(marker in lowered for marker in _AUTH_ERROR_MARKERS):
        return f"git_auth_failed: {push_err}"
    return f"git push failed: {push_err}"


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


async def _run_subprocess(
    *args: str,
    cwd: Path,
    env: dict[str, str],
) -> tuple[int, str, str]:
    """Run a subprocess and return ``(returncode, stdout, stderr)``."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


async def _push_branch_with_gh_auth(
    repo_root: Path,
    branch_name: str,
    owner_repo: str,
    env: dict[str, str],
) -> str | None:
    """Push a branch over HTTPS using ``gh`` as the Git credential helper."""
    rc, _, stderr = await _run_subprocess("gh", "auth", "setup-git", cwd=repo_root, env=env)
    if rc != 0:
        return f"gh auth setup-git failed: {stderr}"

    push_target = f"https://github.com/{owner_repo}.git"
    rc, _, stderr = await _run_subprocess(
        "git",
        "push",
        push_target,
        branch_name,
        cwd=repo_root,
        env=env,
    )
    if rc != 0:
        return _classify_git_push_error(stderr)
    return None


async def _delete_remote_branch_with_gh_auth(
    repo_root: Path,
    branch_name: str,
    owner_repo: str,
    env: dict[str, str],
) -> str | None:
    """Delete a remote branch over HTTPS using ``gh`` as the credential helper."""
    rc, _, stderr = await _run_subprocess("gh", "auth", "setup-git", cwd=repo_root, env=env)
    if rc != 0:
        return f"gh auth setup-git failed: {stderr}"

    push_target = f"https://github.com/{owner_repo}.git"
    rc, _, stderr = await _run_subprocess(
        "git",
        "push",
        push_target,
        "--delete",
        branch_name,
        cwd=repo_root,
        env=env,
    )
    if rc != 0:
        return f"git push --delete failed: {stderr}"
    return None


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
            f"{base}..{branch_name}",
            "--oneline",
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
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
) -> tuple[str | None, int | None]:
    """Look up an open PR by head branch using ``gh pr list``.

    Returns ``(pr_url, pr_number)`` if exactly one open PR is found for the
    given head branch, ``(None, None)`` otherwise.
    """
    import json as _json

    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "pr",
            "list",
            "--head",
            branch_name,
            "--state",
            "open",
            "--json",
            "number,url",
            cwd=str(repo_root),
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


def _parse_github_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


async def _fetch_pr_created_at(
    repo_root: Path,
    *,
    pr_number: int | None,
    pr_url: str | None,
    env: dict[str, str],
) -> datetime | None:
    """Fetch GitHub's PR creation timestamp for journal backdating.

    Missing metadata is non-fatal: callers should pass ``None`` through so
    ``record_event`` retains its explicit current-time fallback.
    """
    import json as _json

    target = str(pr_number) if pr_number is not None else pr_url
    if not target:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "pr",
            "view",
            target,
            "--json",
            "createdAt",
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        data = _json.loads(stdout.decode("utf-8", errors="replace"))
        if not isinstance(data, dict):
            return None
        return _parse_github_datetime(data.get("createdAt"))
    except Exception:  # noqa: BLE001
        logger.debug(
            "_fetch_pr_created_at: lookup failed for PR %r",
            target,
            exc_info=True,
        )
        return None


def _load_investigation_notes(worktree_path: Path | None) -> dict[str, str]:
    """Read and parse the agent's ``INVESTIGATION_NOTES.md`` file.

    The agent's CWD is ``{worktree}/.tmp/qa-agent``; the prompt instructs it
    to write ``INVESTIGATION_NOTES.md`` there with three H2 sections:
    "## Root Cause", "## Fix Summary", "## Test Coverage".

    Returns a dict keyed by section (``root_cause``, ``fix_summary``,
    ``test_coverage``) with the section body (whitespace-stripped) as the
    value.  Sections the agent omitted are simply absent from the dict.
    Returns an empty dict if ``worktree_path`` is ``None``, the file does
    not exist, or the file is unreadable / not UTF-8.

    The parser is intentionally lenient: header matching is case-insensitive,
    extra H2 sections are ignored, and the three expected sections may appear
    in any order.  Empty sections (header present but no body) are dropped so
    the caller falls back to placeholder text.
    """
    if worktree_path is None:
        return {}
    notes_path = worktree_path / _QA_AGENT_SUBDIR / _INVESTIGATION_NOTES_FILE
    try:
        raw = notes_path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(
            "_load_investigation_notes: failed to read %s (%s); falling back to placeholders",
            notes_path,
            exc,
        )
        return {}

    sections: dict[str, str] = {}
    current_key: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        if current_key is None:
            return
        body = "\n".join(buf).strip()
        if body:
            sections[current_key] = body

    for line in raw.splitlines():
        if line.startswith("## "):
            _flush()
            header = line[3:].strip().lower()
            current_key = _NOTES_HEADER_TO_KEY.get(header)
            buf = []
            continue
        if current_key is not None:
            buf.append(line)
    _flush()
    return sections


def _qa_pr_body_contains_raw_evidence_marker(pr_body: str) -> bool:
    return any(marker in pr_body for marker in _FORBIDDEN_QA_PR_BODY_MARKERS)


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
        detail=f"confidence n/a: {why_this_fix}",
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
            "HEAD~1..HEAD",
            "--unified=3",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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


def _diff_snapshot_stats(diff_snapshot: list[dict[str, str]]) -> tuple[int, int, int]:
    additions = 0
    deletions = 0
    file_count = 0
    for line in diff_snapshot:
        kind = line.get("kind")
        if kind == "+":
            additions += 1
        elif kind == "-":
            deletions += 1
        elif kind == "meta" and line.get("text", "").startswith("diff --git "):
            file_count += 1
    return additions, deletions, file_count


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


async def _transition_anonymization_failed_with_journal(
    pool: asyncpg.Pool,
    *,
    attempt_id: uuid.UUID,
    pr_error: str | None,
    healing_session_id: uuid.UUID | None,
) -> None:
    async def _record(session: asyncpg.Pool | asyncpg.Connection) -> None:
        transitioned = await update_attempt_status(
            session,
            attempt_id,
            "anonymization_failed",
            error_detail="PR blocked: residual PII or credentials detected after anonymization",
            healing_session_id=healing_session_id,
        )
        if not transitioned:
            return
        try:
            await record_event(
                session,
                attempt_id=attempt_id,
                step="escalated",
                text=_ANONYMIZATION_FAILURE_EVENT_TEXT,
                detail=_anonymization_failure_detail(pr_error),
            )
        except Exception as _journal_exc:  # noqa: BLE001
            logger.debug(
                "QA dispatch: failed to record anonymization failure journal event "
                "(attempt=%s): %s",
                attempt_id,
                _journal_exc,
            )

    if isinstance(pool, asyncpg.Pool):
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _record(conn)
        return

    await _record(pool)


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


async def _create_qa_pr(
    repo_root: Path,
    branch_name: str,
    finding: QaFinding,
    attempt_id: uuid.UUID,
    labels: list[str],
    gh_token: str | None,
    dashboard_base_url: str | None = None,
    whitelist: RepoWhitelist | None = None,
    worktree_path: Path | None = None,
) -> tuple[str | None, int | None, datetime | None, str | None]:
    """Push branch and create a QA investigation GitHub PR.

    When ``worktree_path`` is provided, the dispatcher reads
    ``{worktree_path}/.tmp/qa-agent/INVESTIGATION_NOTES.md`` (written by the
    investigation agent) and substitutes its Root Cause / Fix Summary / Test
    Coverage sections into the PR body.  Missing file or missing sections fall
    back to a placeholder string.

    Returns
    -------
    tuple[str | None, int | None, datetime | None, str | None]
        ``(pr_url, pr_number, pr_created_at, error_message)``  — *error_message* is:
        - ``None`` on success,
        - ``"anonymization_failed"`` when PII validation blocks the PR; may
          include a sanitized validator reason after ``": "``.
        - ``"no_gh_token"`` when no GitHub token is available,
        - ``"repo_not_whitelisted:remote_unavailable"`` when the origin
          remote URL cannot be resolved; PR creation is blocked fail-closed,
        - ``"repo_not_whitelisted:{reason}:{owner/repo}"`` when whitelist
          enforcement blocks PR creation for a resolved repository; ``reason``
          is the whitelist failure code (e.g. ``whitelist_empty`` or
          ``not_in_whitelist``),
        - Any other string for push/gh failures.
    """
    env: dict[str, str] = build_git_auth_env(gh_token)

    # Step 0: No-op detection — abort before pushing an empty branch.
    # An investigation that produced no commits should not create a remote branch
    # or open a PR; classify explicitly so the caller can transition to unfixable
    # rather than leaving a leaked remote branch.
    if await _detect_no_op_branch(repo_root, branch_name, env):
        logger.info(
            "_create_qa_pr: branch %r has no commits ahead of main — no-op investigation "
            "(attempt=%s); skipping push and PR creation",
            branch_name,
            attempt_id,
        )
        return None, None, None, "no_op_branch"

    if not gh_token:
        return None, None, None, "no_gh_token"

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
        return None, None, None, "repo_not_whitelisted:remote_unavailable"

    allowed, wl_reason = effective_whitelist.is_allowed(owner_repo)
    if not allowed:
        logger.info(
            "_create_qa_pr: PR blocked for repo %r — whitelist reason: %s",
            owner_repo,
            wl_reason,
        )
        # Return a reason code that the caller can use for owner notification.
        return None, None, None, f"repo_not_whitelisted:{wl_reason}:{owner_repo}"

    # Step 1: git push over HTTPS using GH_TOKEN-backed gh auth.
    push_error = await _push_branch_with_gh_auth(repo_root, branch_name, owner_repo, env)
    if push_error is not None:
        return None, None, None, push_error

    # Step 2: Build PR content
    fp_short = finding.fingerprint[:12]

    # Build dashboard link for PR body
    dashboard_link = ""
    if dashboard_base_url:
        dashboard_url = f"{dashboard_base_url.rstrip('/')}/qa/investigations/{attempt_id}"
        dashboard_link = f"\n\n[View investigation details]({dashboard_url})"

    raw_title = f"fix(qa): {finding.exception_type} in {finding.call_site} [{fp_short}]"

    notes = _load_investigation_notes(worktree_path)
    root_cause = notes.get("root_cause", _NOTES_PLACEHOLDER)
    fix_summary = notes.get("fix_summary", _NOTES_PLACEHOLDER)
    test_coverage = notes.get("test_coverage", _NOTES_PLACEHOLDER)

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
{root_cause}

### Fix Summary
{fix_summary}

### Test Coverage
{test_coverage}

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
        validator_detail = _format_anonymization_validator_detail(violations)
        _record_qa_anonymization_failed()
        logger.warning(
            "Anonymization validation failed for QA investigation PR (attempt=%s): "
            "%d violation(s): %s",
            attempt_id,
            len(violations),
            violations[:3],
        )
        # Delete the remote branch (await to avoid leaking the child process)
        delete_error = await _delete_remote_branch_with_gh_auth(
            repo_root, branch_name, owner_repo, env
        )
        if delete_error is not None:
            logger.warning(
                "Failed to delete remote branch %s after anonymization failure: %s",
                branch_name,
                delete_error,
            )
        return None, None, None, _anonymization_failure_error(validator_detail)

    if _qa_pr_body_contains_raw_evidence_marker(pr_body):
        logger.warning(
            "QA investigation PR body contained a raw-evidence marker after anonymization "
            "(attempt=%s); blocking GitHub PR creation",
            attempt_id,
        )
        delete_error = await _delete_remote_branch_with_gh_auth(
            repo_root, branch_name, owner_repo, env
        )
        if delete_error is not None:
            logger.warning(
                "Failed to delete remote branch %s after raw-evidence guard failure: %s",
                branch_name,
                delete_error,
            )
        return (
            None,
            None,
            None,
            _anonymization_failure_error("raw evidence marker detected in PR body"),
        )

    # Step 4c: Sanitize + validate labels — labels are externally visible on the
    # public destination and must clear the same gate as the title/body.
    sanitized_labels, label_violations = sanitize_labels(labels, repo_root)
    if label_violations:
        validator_detail = _format_anonymization_validator_detail(label_violations)
        _record_qa_anonymization_failed()
        logger.warning(
            "Anonymization validation failed for QA investigation PR labels (attempt=%s): "
            "%d violation(s): %s",
            attempt_id,
            len(label_violations),
            label_violations[:3],
        )
        delete_error = await _delete_remote_branch_with_gh_auth(
            repo_root, branch_name, owner_repo, env
        )
        if delete_error is not None:
            logger.warning(
                "Failed to delete remote branch %s after label anonymization failure: %s",
                branch_name,
                delete_error,
            )
        return None, None, None, _anonymization_failure_error(validator_detail)

    # Gate passed across every externally-visible field. Record that the gate ran
    # (pass) for audit without persisting any sanitized/raw field content.
    logger.info(
        "QA publication sanitization gate PASSED (attempt=%s): title, body, and %d label(s) clean",
        attempt_id,
        len(sanitized_labels),
    )

    # Step 5: gh pr create
    label_args: list[str] = []
    for label in sanitized_labels:
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
        raw_err = gh_stderr.decode("utf-8", errors="replace").strip()
        # Classify the error to a stable prefix rather than exposing raw stderr.
        # Auth errors get the git_auth_failed prefix (shared with push errors);
        # other failures use a stable "gh_pr_create_failed" prefix.
        lowered = raw_err.lower()
        if any(marker in lowered for marker in _AUTH_ERROR_MARKERS):
            classified_err = f"git_auth_failed: {raw_err}"
        else:
            classified_err = f"gh_pr_create_failed: {raw_err}"
        # Best-effort remote branch cleanup: push succeeded but PR creation failed,
        # so we now own a leaked remote branch.  Clean it up before returning.
        try:
            delete_error = await _delete_remote_branch_with_gh_auth(
                repo_root, branch_name, owner_repo, env
            )
            if delete_error is not None:
                logger.warning(
                    "_create_qa_pr: best-effort branch cleanup after PR creation failure "
                    "could not delete remote branch %r: %s",
                    branch_name,
                    delete_error,
                )
            else:
                logger.debug(
                    "_create_qa_pr: cleaned up remote branch %r after PR creation failure",
                    branch_name,
                )
        except Exception:  # noqa: BLE001
            logger.debug(
                "_create_qa_pr: branch cleanup subprocess raised (attempt=%s)",
                attempt_id,
                exc_info=True,
            )
        return None, None, None, classified_err

    pr_url = gh_stdout.decode("utf-8", errors="replace").strip()
    pr_number: int | None = None
    try:
        pr_number = int(pr_url.rstrip("/").split("/")[-1])
    except (ValueError, IndexError):
        pass

    # Fallback: if URL parsing did not yield a valid pr_number, query gh directly
    # using the head branch so that non-canonical stdout (e.g. extra lines from
    # hooks or warnings) does not strand the attempt with pr_number=NULL.
    if pr_number is None:
        logger.debug(
            "_create_qa_pr: pr_number parse failed from stdout %r; "
            "falling back to head-branch lookup (attempt=%s)",
            pr_url,
            attempt_id,
        )
        fallback_url, fallback_number = await _resolve_pr_by_head(repo_root, branch_name, env)
        if fallback_number is not None:
            pr_url = fallback_url or pr_url
            pr_number = fallback_number
            logger.info(
                "_create_qa_pr: resolved PR identity via head-branch fallback "
                "(attempt=%s pr_number=%s)",
                attempt_id,
                pr_number,
            )
        else:
            logger.warning(
                "_create_qa_pr: could not resolve pr_number for attempt=%s "
                "(stdout=%r); persisting pr_url without pr_number",
                attempt_id,
                pr_url,
            )

    pr_created_at = await _fetch_pr_created_at(
        repo_root,
        pr_number=pr_number,
        pr_url=pr_url,
        env=env,
    )
    return pr_url, pr_number, pr_created_at, None


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
) -> None:
    """Run the QA investigation agent and handle PR creation.

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
        sandbox_env = build_sandbox_env(
            gh_token,
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
            # will be attached on the next valid status transition (pr_open / failed / timeout).
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
            error_detail = result.error or "Investigation agent returned non-success result"
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
                    error_class="agent_failure",
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

        # Agent succeeded — create PR
        pr_url, pr_number, pr_created_at, pr_error = await _create_qa_pr(
            repo_root=repo_root,
            branch_name=branch_name,
            finding=finding,
            attempt_id=attempt_id,
            labels=config.pr_labels,
            gh_token=gh_token,
            dashboard_base_url=config.dashboard_base_url,
            whitelist=config.repo_whitelist,
            worktree_path=worktree_path,
        )
        diff_snapshot = (
            [] if pr_error == "no_op_branch" else await _capture_commit_diff_snapshot(worktree_path)
        )

        if _is_anonymization_failure_error(pr_error):
            if metrics is not None:
                _elapsed = (_time.monotonic() - _phase_start) * 1000
                metrics.record_recovery_phase_duration(
                    workflow="qa",
                    phase="investigate",
                    outcome="anonymization_failed",
                    duration_ms=_elapsed,
                )
                metrics.record_recovery_execution_failure(
                    workflow="qa",
                    phase="investigate",
                    error_class="anonymization_failed",
                )
            if phase_session_id is not None:
                try:
                    await update_phase_session_status(
                        pool,
                        phase_session_id,
                        "failed",
                        error_detail="PR blocked: anonymization_failed",
                    )
                except Exception as _pss_exc:
                    logger.debug("Failed to mark phase session failed: %s", _pss_exc)
            await _transition_anonymization_failed_with_journal(
                pool,
                attempt_id=attempt_id,
                pr_error=pr_error,
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
                diff_snapshot=diff_snapshot,
                otel_span=_inv_span,
            )
            return

        if pr_error == "no_gh_token":
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
                    error_class="no_gh_token",
                )
            if phase_session_id is not None:
                try:
                    await update_phase_session_status(
                        pool, phase_session_id, "failed", error_detail="no_gh_token"
                    )
                except Exception as _pss_exc:
                    logger.debug("Failed to mark phase session failed: %s", _pss_exc)
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail="no_gh_token: BUTLERS_QA_GH_TOKEN not found in CredentialStore",
                healing_session_id=investigation_session_id,
            )
            await _record_escalation_for_terminal(
                pool,
                attempt_id=attempt_id,
                status="failed",
                error_detail="no_gh_token: BUTLERS_QA_GH_TOKEN not found in CredentialStore",
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
            return

        if pr_error is not None and pr_error.startswith("git_auth_failed:"):
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
                    error_class="git_auth_failed",
                )
            if phase_session_id is not None:
                try:
                    await update_phase_session_status(
                        pool,
                        phase_session_id,
                        "failed",
                        error_detail=pr_error,
                    )
                except Exception as _pss_exc:
                    logger.debug("Failed to mark phase session failed: %s", _pss_exc)
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail=(
                    "git_auth_failed: QA GitHub token is present but git-over-HTTPS "
                    "authentication is not configured for subprocess pushes"
                ),
                healing_session_id=investigation_session_id,
            )
            await _record_escalation_for_terminal(
                pool,
                attempt_id=attempt_id,
                status="failed",
                error_detail=(
                    "git_auth_failed: QA GitHub token is present but git-over-HTTPS "
                    "authentication is not configured for subprocess pushes"
                ),
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
                    error_class="repo_not_whitelisted",
                )
            if phase_session_id is not None:
                try:
                    await update_phase_session_status(
                        pool, phase_session_id, "failed", error_detail=f"PR blocked: {wl_detail}"
                    )
                except Exception as _pss_exc:
                    logger.debug("Failed to mark phase session failed: %s", _pss_exc)
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail=f"PR blocked: {wl_detail}",
                healing_session_id=investigation_session_id,
            )
            await _record_escalation_for_terminal(
                pool,
                attempt_id=attempt_id,
                status="failed",
                error_detail=f"PR blocked: {wl_detail}",
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
            return

        if pr_error == "no_op_branch":
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

        if pr_error is not None:
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
                    error_class="pr_error",
                )
            if phase_session_id is not None:
                try:
                    await update_phase_session_status(
                        pool, phase_session_id, "failed", error_detail=pr_error
                    )
                except Exception as _pss_exc:
                    logger.debug("Failed to mark phase session failed: %s", _pss_exc)
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail=pr_error,
                healing_session_id=investigation_session_id,
            )
            await _record_escalation_for_terminal(
                pool,
                attempt_id=attempt_id,
                status="failed",
                error_detail=pr_error,
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
            return

        # PR created successfully
        if phase_session_id is not None:
            try:
                await update_phase_session_status(pool, phase_session_id, "completed")
            except Exception as _pss_exc:
                logger.debug("Failed to mark phase session completed: %s", _pss_exc)
        await update_attempt_status(
            pool,
            attempt_id,
            "pr_open",
            pr_url=pr_url,
            pr_number=pr_number,
            healing_session_id=investigation_session_id,
        )
        additions, deletions, file_count = _diff_snapshot_stats(diff_snapshot)
        try:
            await record_pr_drafted_event(
                pool,
                attempt_id=attempt_id,
                pr_number=pr_number,
                branch_name=branch_name,
                additions=additions,
                deletions=deletions,
                file_count=file_count,
                ts=pr_created_at,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "QA dispatch: failed to record drafted journal event (attempt=%s): %s",
                attempt_id,
                exc,
            )
        # Note: finding was already linked to attempt in dispatch_qa_investigation
        # (via update_finding_attempt at gate 6). No redundant call needed here.

        logger.info("QA investigation PR created: attempt=%s pr_url=%s", attempt_id, pr_url)
        # Remove worktree; keep branch (backs the open PR)
        await _persist_notes_and_remove_worktree(
            pool,
            attempt_id,
            repo_root,
            branch_name,
            worktree_path,
            delete_branch=False,
            delete_remote=False,
            diff_snapshot=diff_snapshot,
            otel_span=_inv_span,
        )
        # Record success only after DB status update and worktree cleanup succeed,
        # so that no conflicting failure metric is emitted for the same attempt.
        if metrics is not None:
            _elapsed = (_time.monotonic() - _phase_start) * 1000
            metrics.record_recovery_phase_duration(
                workflow="qa",
                phase="investigate",
                outcome="success",
                duration_ms=_elapsed,
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

    env = build_sandbox_env(gh_token)

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
                repaired_url, repaired_number = await _resolve_pr_by_head(
                    repo_root, pr_branch_name, env
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
                    str(pr_number),
                    "--json",
                    json_fields,
                    cwd=str(repo_root),
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

        # Build authenticated git env for all branch-prep subprocesses so that
        # private HTTPS remotes don't reject fetch/worktree commands before the
        # agent even runs.
        git_prep_env = build_git_auth_env(gh_token)

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

        sandbox_env = build_sandbox_env(
            gh_token,
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

        owner_repo = await _get_remote_owner_repo(repo_root, sandbox_env)
        if owner_repo is None:
            push_error = "remote_unavailable"
        else:
            push_error = await _push_branch_with_gh_auth(
                repo_root, followup_branch, owner_repo, sandbox_env
            )
        if push_error is not None:
            logger.warning(
                "QA review follow-up: %s (attempt=%s)",
                push_error,
                attempt_id,
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
                push_error[:_MAX_FOLLOWUP_ERROR_LEN],
            )
        else:
            logger.info(
                "QA review follow-up: pushed to origin/%s (attempt=%s pr_number=%s)",
                followup_branch,
                attempt_id,
                pr_number,
            )
            await pool.execute(
                """
                UPDATE public.healing_attempts
                SET last_follow_up_status     = 'succeeded',
                    last_follow_up_session_id = $2,
                    last_follow_up_error      = NULL,
                    updated_at                = now()
                WHERE id = $1
                """,
                attempt_id,
                followup_session_id,
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

    Only launched executions (``healing_session_id IS NOT NULL``) are counted,
    plus synthetic QA dashboard reset rows (``status = 'manual_reset'``).
    Gate rejections that were cleaned up as dispatch events do not have a
    ``healing_session_id`` and remain excluded from the circuit-breaker signal.
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
          AND (
                healing_session_id IS NOT NULL
                OR status = 'manual_reset'
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

        # Fetch latest main before creating the worktree branch.
        # On success, branch from origin/main so long-lived daemon worktrees
        # always start from the freshest available remote ref.
        _fetch_ok = False
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
                    "git fetch origin main failed (non-fatal): %s — falling back to local main",
                    fetch_stderr.decode("utf-8", errors="replace").strip(),
                )
            else:
                _fetch_ok = True
        except Exception as fetch_exc:
            logger.warning(
                "git fetch origin main failed (non-fatal): %s — falling back to local main",
                fetch_exc,
            )

        _base_ref = "origin/main" if _fetch_ok else "main"

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
