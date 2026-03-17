"""Self-healing dispatch engine.

Shared decision layer that evaluates whether an error warrants spawning a
healing agent.  Called by BOTH:

- The self-healing MCP module (primary path — butler calls ``report_error``)
- The spawner's except block (secondary path — hard crashes)

Both paths converge on the same 10-gate sequence and, if all gates pass,
create a worktree, spawn a healing agent, and start a timeout watchdog.

Gate sequence
-------------
1.  No-recursion guard    — reject if ``trigger_source == "healing"``
2.  Opt-in gate           — reject if healing not enabled
3.  Fingerprint computation — compute or accept pre-computed fingerprint
4.  Fingerprint persistence — update session record (best-effort)
5.  Severity gate         — reject if severity above threshold (lower = worse)
6.  Novelty gate          — reject if active attempt exists (atomic check+insert)
7.  Cooldown gate         — reject if recent terminal attempt within window
8.  Concurrency cap       — reject if too many active investigations
9.  Circuit breaker       — reject if consecutive failures exceed threshold
10. Model resolution      — reject if no ``self_healing`` tier model available

Spec reference
--------------
openspec/changes/butler-self-healing/specs/self-healing-dispatch/spec.md
openspec/changes/butler-self-healing/design.md §2, §5, §6, §8, §12, §14
"""

from __future__ import annotations

import asyncio
import logging
import types
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

from butlers.core.healing.anonymizer import anonymize, validate_anonymized
from butlers.core.healing.fingerprint import (
    FingerprintResult,
    compute_fingerprint,
)
from butlers.core.healing.tracking import (
    count_active_attempts,
    create_or_join_attempt,
    get_attempt,
    get_recent_attempt,
    get_recent_terminal_statuses,
    update_attempt_status,
)
from butlers.core.healing.worktree import (
    WorktreeCreationError,
    create_healing_worktree,
    remove_healing_worktree,
)
from butlers.core.model_routing import Complexity, resolve_model
from butlers.core.sessions import session_set_healing_fingerprint

logger = logging.getLogger(__name__)

try:
    from opentelemetry import context as otel_context
    from opentelemetry import trace

    _tracer = trace.get_tracer("butlers.healing")
    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

#: Fingerprint input: either a pre-computed FingerprintResult (module path) or
#: a raw (exc, tb) tuple (spawner fallback path).
FingerprintInput = FingerprintResult | tuple[BaseException, types.TracebackType | None]

# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


@dataclass
class HealingConfig:
    """Configuration for the self-healing dispatcher.

    Populated from ``[modules.self_healing]`` (or ``[healing]``) in
    ``butler.toml``.  All values have safe defaults.

    Parameters
    ----------
    enabled:
        Master on/off switch.  When ``False``, dispatch always skips.
    severity_threshold:
        Maximum severity score that triggers healing.  Lower numbers are MORE
        severe.  Default 2 (medium).  Set to 1 to only heal high/critical.
    cooldown_minutes:
        Minutes between investigations of the same fingerprint after any
        terminal status.  Default 60.
    max_concurrent:
        Maximum number of simultaneous ``investigating`` rows.  Default 2.
    circuit_breaker_threshold:
        Number of consecutive failure statuses before all dispatch is halted.
        Default 5.  ``unfixable`` does not count as a failure for circuit
        breaker purposes.
    timeout_minutes:
        Maximum wall-clock minutes for a healing agent session before the
        watchdog cancels it.  Default 30.
    gh_token_env_var:
        Environment variable name that holds the GitHub token for PR creation.
        Default ``"GH_TOKEN"``.
    pr_labels:
        Labels to apply to self-healing PRs.  Default ``["self-healing", "automated"]``.
    """

    enabled: bool = False
    severity_threshold: int = 2
    cooldown_minutes: int = 60
    max_concurrent: int = 2
    circuit_breaker_threshold: int = 5
    timeout_minutes: int = 30
    gh_token_env_var: str = "GH_TOKEN"
    pr_labels: list[str] = field(default_factory=lambda: ["self-healing", "automated"])

    @classmethod
    def from_module_config(cls, module_cfg: dict) -> HealingConfig:
        """Build a HealingConfig from a ``[modules.self_healing]`` dict."""
        return cls(
            enabled=bool(module_cfg.get("enabled", False)),
            severity_threshold=int(module_cfg.get("severity_threshold", 2)),
            cooldown_minutes=int(module_cfg.get("cooldown_minutes", 60)),
            max_concurrent=int(module_cfg.get("max_concurrent", 2)),
            circuit_breaker_threshold=int(module_cfg.get("circuit_breaker_threshold", 5)),
            timeout_minutes=int(module_cfg.get("timeout_minutes", 30)),
            gh_token_env_var=str(module_cfg.get("gh_token_env_var", "GH_TOKEN")),
            pr_labels=list(module_cfg.get("pr_labels", ["self-healing", "automated"])),
        )


# ---------------------------------------------------------------------------
# Gate result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchResult:
    """Result returned by ``dispatch_healing()``.

    Attributes
    ----------
    accepted:
        ``True`` if a healing agent was spawned.
    fingerprint:
        The 64-character hex fingerprint, or ``None`` if dispatch was skipped
        before fingerprint computation (e.g. opt-in gate failed).
    reason:
        Short machine-readable reason code for rejection, or ``"dispatched"``
        on success.
    attempt_id:
        UUID of the created ``healing_attempts`` row, or ``None`` if no row
        was created.
    """

    accepted: bool
    fingerprint: str | None
    reason: str
    attempt_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Circuit breaker helper
# ---------------------------------------------------------------------------

#: Statuses that count against the circuit breaker.
_CIRCUIT_BREAKER_FAILURE_STATUSES = frozenset({"failed", "timeout", "anonymization_failed"})


async def _is_circuit_breaker_tripped(
    pool: asyncpg.Pool,
    threshold: int,
) -> bool:
    """Return True if the last *threshold* terminal attempts are all failure statuses.

    Failure statuses for the circuit breaker: ``failed``, ``timeout``,
    ``anonymization_failed``.  ``unfixable``, ``pr_open``, and ``pr_merged``
    do NOT trip the breaker.
    """
    recent_statuses = await get_recent_terminal_statuses(pool, limit=threshold)
    if len(recent_statuses) < threshold:
        return False
    return all(s in _CIRCUIT_BREAKER_FAILURE_STATUSES for s in recent_statuses)


# ---------------------------------------------------------------------------
# Healing agent prompt
# ---------------------------------------------------------------------------

_HEALING_PROMPT_TEMPLATE = """\
You are a self-healing agent for the {butler_name} butler. A session has failed \
and you have been spawned to investigate the root cause and propose a fix.

## Error Summary

**Fingerprint:** {fingerprint}
**Exception type:** {exception_type}
**Call site:** {call_site}
**Severity:** {severity}
**Sanitized message:** {sanitized_message}
**Butler:** {butler_name}
**Trigger source:** {trigger_source}

{agent_context_section}

## Your Task

1. Investigate the root cause of this error in the codebase (your CWD is an \
isolated worktree branched from main).
2. Write a fix with tests.
3. Commit your changes to the current branch (do NOT push — the dispatcher \
handles pushing and PR creation).
4. If the error is not a code bug (external service, data issue, user error), \
write a commit with a file explaining why it is unfixable and what the \
recommendation is.

## Important Rules

- Do NOT create a PR yourself — the dispatcher handles that.
- Do NOT include any PII, user data, credentials, or environment-specific \
information in commit messages or code.
- Run tests after your fix: ``uv run pytest`` and ``uv run ruff check src/ tests/``.
- Stay within the scope of this specific error.
"""

_AGENT_CONTEXT_SECTION_TEMPLATE = """\
## Butler Diagnostic Context

The butler agent that encountered this error provided the following analysis:

{context}

Use this context as a starting point for your investigation, but verify \
independently — the reporting agent may have incomplete information.
"""


def _build_healing_prompt(
    fp: FingerprintResult,
    butler_name: str,
    trigger_source: str,
    agent_context: str | None,
) -> str:
    """Build the system prompt for the healing agent."""
    if agent_context and agent_context.strip():
        agent_context_section = _AGENT_CONTEXT_SECTION_TEMPLATE.format(
            context=agent_context.strip()
        )
    else:
        agent_context_section = (
            "*(No agent diagnostic context available — this error was captured "
            "via the spawner fallback after a hard crash.)*"
        )

    return _HEALING_PROMPT_TEMPLATE.format(
        fingerprint=fp.fingerprint,
        exception_type=fp.exception_type,
        call_site=fp.call_site,
        severity=fp.severity,
        sanitized_message=fp.sanitized_message,
        butler_name=butler_name,
        trigger_source=trigger_source,
        agent_context_section=agent_context_section,
    )


# ---------------------------------------------------------------------------
# PR creation flow
# ---------------------------------------------------------------------------


def _build_pr_body(
    fp: FingerprintResult,
    butler_name: str,
    attempt_id: uuid.UUID,
    repo_root: Path,
    agent_context: str | None,
    first_seen: str | None = None,
    occurrences: int | None = None,
) -> str:
    """Build anonymized PR body from the structured template."""
    context_section = ""
    if agent_context and agent_context.strip():
        anon_context = anonymize(agent_context.strip(), repo_root)
        context_section = f"\n### Butler Diagnostic Context\n{anon_context}\n"

    first_seen_line = f"\n**First seen:** {first_seen}" if first_seen is not None else ""
    occurrences_line = f"\n**Occurrences:** {occurrences}" if occurrences is not None else ""

    raw_body = f"""\
## Self-Healing Fix: {fp.fingerprint[:12]}

**Butler:** {butler_name}
**Error:** {fp.exception_type}
**Call site:** {fp.call_site}
**Fingerprint:** `{fp.fingerprint}`
**Attempt ID:** `{attempt_id}`{first_seen_line}{occurrences_line}

### Root Cause
*(Filled in by the healing agent's commit message and PR description.)*

### Fix Summary
*(Filled in by the healing agent.)*

### Test Coverage
*(Filled in by the healing agent.)*
{context_section}
---
*Automated fix proposed by butler self-healing. Review carefully before merging.*

*Fingerprint: `{fp.fingerprint}`*
"""
    return raw_body


async def _create_pr(
    repo_root: Path,
    branch_name: str,
    fp: FingerprintResult,
    butler_name: str,
    attempt_id: uuid.UUID,
    agent_context: str | None,
    labels: list[str],
    gh_token: str | None,
    first_seen: str = "unknown",
    occurrences: int | str = "unknown",
) -> tuple[str | None, int | None, str | None]:
    """Push branch and create a GitHub PR.

    Returns
    -------
    tuple[str | None, int | None, str | None]
        ``(pr_url, pr_number, error_message)`` — *error_message* is:

        - ``None`` on success,
        - ``"anonymization_failed"`` when PII validation blocks the PR,
        - Any other string for push/gh failures.
    """
    import os

    env: dict[str, str] = dict(os.environ)
    if gh_token:
        env["GH_TOKEN"] = gh_token

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

    # Step 2: Build and anonymize PR content
    pr_title = f"fix(healing): {fp.exception_type} in {fp.call_site} [{fp.fingerprint[:12]}]"
    pr_title = anonymize(pr_title, repo_root)

    pr_body = _build_pr_body(
        fp,
        butler_name,
        attempt_id,
        repo_root,
        agent_context,
        first_seen=first_seen,
        occurrences=occurrences,
    )
    pr_body = anonymize(pr_body, repo_root)

    # Step 3: Validate for residual PII
    title_clean, title_violations = validate_anonymized(pr_title)
    body_clean, body_violations = validate_anonymized(pr_body)
    if not title_clean or not body_clean:
        violations = title_violations + body_violations
        logger.warning(
            "Anonymization validation failed for healing PR (attempt=%s): %d violation(s): %s",
            attempt_id,
            len(violations),
            violations[:3],  # log first 3 for diagnosis without revealing PII
        )
        # Delete the remote branch that was just pushed
        await asyncio.create_subprocess_exec(
            "git",
            "push",
            "origin",
            "--delete",
            branch_name,
            cwd=str(repo_root),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        return None, None, "anonymization_failed"

    # Step 4: gh pr create
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
        return (
            None,
            None,
            (f"gh pr create failed: {gh_stderr.decode('utf-8', errors='replace').strip()}"),
        )

    # Parse PR URL and number from stdout
    pr_url = gh_stdout.decode("utf-8", errors="replace").strip()
    pr_number: int | None = None
    try:
        # URL format: https://github.com/.../pull/N
        pr_number = int(pr_url.rstrip("/").split("/")[-1])
    except (ValueError, IndexError):
        pass

    return pr_url, pr_number, None


# ---------------------------------------------------------------------------
# Timeout watchdog
# ---------------------------------------------------------------------------


async def _timeout_watchdog(
    pool: asyncpg.Pool,
    attempt_id: uuid.UUID,
    repo_root: Path,
    branch_name: str,
    healing_task: asyncio.Task,
    timeout_minutes: int,
) -> None:
    """Sleep for *timeout_minutes* then cancel the healing task if still running."""
    try:
        await asyncio.sleep(timeout_minutes * 60)
        if not healing_task.done():
            logger.warning(
                "Healing session timed out after %d minutes (attempt=%s); cancelling",
                timeout_minutes,
                attempt_id,
            )
            healing_task.cancel()
            try:
                await healing_task
            except (asyncio.CancelledError, Exception):
                pass

            await update_attempt_status(
                pool,
                attempt_id,
                "timeout",
                error_detail=f"Healing session cancelled after {timeout_minutes} minute timeout",
            )
            await remove_healing_worktree(
                repo_root,
                branch_name,
                delete_branch=True,
                delete_remote=False,
            )
    except asyncio.CancelledError:
        # Watchdog was cancelled because the healing task completed normally
        pass


# ---------------------------------------------------------------------------
# Healing session runner
# ---------------------------------------------------------------------------


async def _run_healing_session(
    pool: asyncpg.Pool,
    repo_root: Path,
    attempt_id: uuid.UUID,
    branch_name: str,
    worktree_path: Path,
    fp: FingerprintResult,
    butler_name: str,
    trigger_source: str,
    agent_context: str | None,
    config: HealingConfig,
    spawner,  # Spawner instance — typed as Any to avoid circular import
    gh_token: str | None,
) -> None:
    """Run the healing agent and handle PR creation.

    This coroutine is scheduled as an ``asyncio.Task`` and monitored by a
    separate timeout watchdog task.
    """
    healing_session_id: uuid.UUID | None = None

    try:
        prompt = _build_healing_prompt(fp, butler_name, trigger_source, agent_context)

        # Spawn the healing agent — trigger_source="healing" prevents recursion.
        # The healing agent gets:
        #   - CWD set to the worktree path (via spawner config or passed as cwd)
        #   - complexity=SELF_HEALING for model selection
        #   - trigger_source="healing" to block recursive healing
        result = await spawner.trigger(
            prompt=prompt,
            trigger_source="healing",
            complexity=Complexity.SELF_HEALING,
            cwd=str(worktree_path),
            bypass_butler_semaphore=True,
        )

        if result.session_id is not None:
            healing_session_id = result.session_id
            await update_attempt_status(
                pool,
                attempt_id,
                "investigating",
                healing_session_id=healing_session_id,
            )

        if not result.success:
            error_detail = result.error or "Healing agent returned non-success result"
            logger.warning("Healing agent failed (attempt=%s): %s", attempt_id, error_detail)
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail=error_detail,
                healing_session_id=healing_session_id,
            )
            await remove_healing_worktree(
                repo_root,
                branch_name,
                delete_branch=True,
                delete_remote=False,
            )
            return

        # Agent succeeded — fetch attempt metadata for PR body, then create PR
        first_seen = "unknown"
        occurrences: int | str = "unknown"
        try:
            attempt_row = await get_attempt(pool, attempt_id)
            if attempt_row is not None:
                created_at = attempt_row.get("created_at")
                if created_at is not None:
                    first_seen = str(created_at)
                session_ids = attempt_row.get("session_ids") or []
                occurrences = len(session_ids)
        except Exception as _meta_exc:
            logger.debug(
                "Failed to fetch attempt metadata for PR body (attempt=%s): %s",
                attempt_id,
                _meta_exc,
            )

        pr_url, pr_number, pr_error = await _create_pr(
            repo_root=repo_root,
            branch_name=branch_name,
            fp=fp,
            butler_name=butler_name,
            attempt_id=attempt_id,
            agent_context=agent_context,
            labels=config.pr_labels,
            gh_token=gh_token,
            first_seen=first_seen,
            occurrences=occurrences,
        )

        if pr_error == "anonymization_failed":
            await update_attempt_status(
                pool,
                attempt_id,
                "anonymization_failed",
                error_detail="PR blocked: residual PII or credentials detected after anonymization",
                healing_session_id=healing_session_id,
            )
            # Remote branch already deleted by _create_pr; only remove local + worktree
            await remove_healing_worktree(
                repo_root,
                branch_name,
                delete_branch=True,
                delete_remote=False,
            )
            return

        if pr_error is not None:
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail=pr_error,
                healing_session_id=healing_session_id,
            )
            await remove_healing_worktree(
                repo_root,
                branch_name,
                delete_branch=True,
                delete_remote=False,
            )
            return

        # PR created successfully
        await update_attempt_status(
            pool,
            attempt_id,
            "pr_open",
            pr_url=pr_url,
            pr_number=pr_number,
            healing_session_id=healing_session_id,
        )
        logger.info("Healing PR created: attempt=%s pr_url=%s", attempt_id, pr_url)
        # Worktree removed after PR creation; branch kept (backs the open PR)
        await remove_healing_worktree(
            repo_root,
            branch_name,
            delete_branch=False,
            delete_remote=False,
        )

    except asyncio.CancelledError:
        # Cancelled by watchdog — watchdog sets status to "timeout"
        raise

    except Exception as exc:
        logger.exception("Unexpected error in healing session (attempt=%s): %s", attempt_id, exc)
        await update_attempt_status(
            pool,
            attempt_id,
            "failed",
            error_detail=f"{type(exc).__name__}: {exc}",
            healing_session_id=healing_session_id,
        )
        await remove_healing_worktree(
            repo_root,
            branch_name,
            delete_branch=True,
            delete_remote=False,
        )


# ---------------------------------------------------------------------------
# Public dispatch function
# ---------------------------------------------------------------------------


async def dispatch_healing(
    pool: asyncpg.Pool,
    butler_name: str,
    session_id: uuid.UUID,
    fingerprint_input: FingerprintInput,
    config: HealingConfig,
    repo_root: Path,
    spawner,  # Spawner instance — typed as Any to avoid circular import
    agent_context: str | None = None,
    trigger_source: str = "external",
    gh_token: str | None = None,
) -> DispatchResult:
    """Evaluate gates and, if all pass, spawn a healing agent.

    This function is non-fatal: all internal exceptions are caught and logged.
    The caller (MCP tool handler or spawner fallback) always receives a
    ``DispatchResult``.

    Parameters
    ----------
    pool:
        asyncpg connection pool for ``shared.healing_attempts`` and session
        tracking queries.
    butler_name:
        Name of the butler whose session failed.
    session_id:
        UUID of the failed session (used for fingerprint persistence and to
        seed the attempt's ``session_ids`` array).
    fingerprint_input:
        Either a pre-computed ``FingerprintResult`` (module path, after calling
        ``compute_fingerprint_from_report``) or a ``(exc, tb)`` tuple (spawner
        fallback path — fingerprint computed here).
    config:
        ``HealingConfig`` built from ``[modules.self_healing]`` or ``[healing]``
        in ``butler.toml``.
    repo_root:
        Absolute path to the repository root (for worktree creation).
    spawner:
        The butler's ``Spawner`` instance.  Used to spawn the healing agent
        via ``spawner.trigger()``.
    agent_context:
        Optional diagnostic reasoning from the reporting butler agent
        (module path only).  Anonymized before inclusion in healing prompts and PR.
    trigger_source:
        The ``trigger_source`` of the FAILED session (not the healing session).
        Used for the no-recursion check.
    gh_token:
        GitHub token for ``gh pr create``.  When ``None``, the environment's
        ``GH_TOKEN`` is used.

    Returns
    -------
    DispatchResult
        Always returned — never raises.
    """
    _span = None
    _span_token = None
    if _HAS_OTEL:
        _span = _tracer.start_span(
            "butlers.healing.dispatch",
            attributes={
                "butler.name": butler_name,
                "healing.trigger_source": trigger_source,
            },
        )
        _span_token = otel_context.attach(trace.set_span_in_context(_span))

    try:
        # -------------------------------------------------------------------
        # Gate 1: No-recursion guard (FIRST — before any other work)
        # -------------------------------------------------------------------
        if trigger_source == "healing":
            logger.debug("Healing dispatch skipped: trigger_source=healing (no recursive healing)")
            return DispatchResult(accepted=False, fingerprint=None, reason="no_recursion")

        # -------------------------------------------------------------------
        # Gate 2: Opt-in check (before fingerprint computation)
        # -------------------------------------------------------------------
        if not config.enabled:
            logger.debug("Healing dispatch skipped: healing not enabled for butler=%s", butler_name)
            return DispatchResult(accepted=False, fingerprint=None, reason="disabled")

        # -------------------------------------------------------------------
        # Gate 3: Fingerprint computation
        # -------------------------------------------------------------------
        if isinstance(fingerprint_input, FingerprintResult):
            fp = fingerprint_input
        else:
            exc_obj, tb_obj = fingerprint_input
            fp = compute_fingerprint(exc_obj, tb_obj)

        # -------------------------------------------------------------------
        # Gate 4: Fingerprint persistence (best-effort)
        # -------------------------------------------------------------------
        try:
            await session_set_healing_fingerprint(pool, session_id, fp.fingerprint)
        except Exception as persist_exc:
            logger.warning(
                "Failed to persist healing fingerprint for session %s: %s",
                session_id,
                persist_exc,
            )

        # -------------------------------------------------------------------
        # Gate 5: Severity gate
        # -------------------------------------------------------------------
        if fp.severity > config.severity_threshold:
            logger.debug(
                "Healing dispatch skipped: severity=%d > threshold=%d (butler=%s)",
                fp.severity,
                config.severity_threshold,
                butler_name,
            )
            return DispatchResult(
                accepted=False,
                fingerprint=fp.fingerprint,
                reason="severity_below_threshold",
            )

        # -------------------------------------------------------------------
        # Gate 6: Novelty gate — atomic check+insert via create_or_join_attempt
        # -------------------------------------------------------------------
        # We use create_or_join_attempt which does INSERT ON CONFLICT atomically.
        # If is_new=False, another investigation is already active.
        attempt_id_or_existing, is_new = await create_or_join_attempt(
            pool=pool,
            fingerprint=fp.fingerprint,
            butler_name=butler_name,
            severity=fp.severity,
            exception_type=fp.exception_type,
            call_site=fp.call_site,
            session_id=session_id,
            sanitized_msg=fp.sanitized_message,
        )

        if not is_new:
            logger.debug(
                "Healing dispatch skipped: already investigating fingerprint=%s",
                fp.fingerprint[:12],
            )
            return DispatchResult(
                accepted=False,
                fingerprint=fp.fingerprint,
                reason="already_investigating",
            )

        attempt_id = attempt_id_or_existing

        # From here on, we have an 'investigating' row; any early exit must
        # update it to a terminal state to avoid leaking an orphaned row.

        # -------------------------------------------------------------------
        # Gate 7: Cooldown gate
        # -------------------------------------------------------------------
        recent = await get_recent_attempt(pool, fp.fingerprint, config.cooldown_minutes)
        if recent is not None:
            logger.debug(
                "Healing dispatch skipped: cooldown active for fingerprint=%s",
                fp.fingerprint[:12],
            )
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail="Dispatch rejected by cooldown gate",
            )
            return DispatchResult(
                accepted=False,
                fingerprint=fp.fingerprint,
                reason="cooldown",
            )

        # -------------------------------------------------------------------
        # Gate 8: Concurrency cap
        # -------------------------------------------------------------------
        active_count = await count_active_attempts(pool)
        # Note: active_count includes the row we just inserted, so the
        # threshold comparison uses >.
        if active_count > config.max_concurrent:
            logger.debug(
                "Healing dispatch skipped: concurrency cap reached (active=%d, max=%d, butler=%s)",
                active_count,
                config.max_concurrent,
                butler_name,
            )
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail="Dispatch rejected by concurrency cap",
            )
            return DispatchResult(
                accepted=False,
                fingerprint=fp.fingerprint,
                reason="concurrency_cap",
            )

        # -------------------------------------------------------------------
        # Gate 9: Circuit breaker
        # -------------------------------------------------------------------
        if config.circuit_breaker_threshold > 0:
            tripped = await _is_circuit_breaker_tripped(pool, config.circuit_breaker_threshold)
            if tripped:
                logger.warning(
                    "Healing dispatch skipped: circuit breaker tripped (threshold=%d, butler=%s)",
                    config.circuit_breaker_threshold,
                    butler_name,
                )
                await update_attempt_status(
                    pool,
                    attempt_id,
                    "failed",
                    error_detail="Dispatch rejected by circuit breaker",
                )
                return DispatchResult(
                    accepted=False,
                    fingerprint=fp.fingerprint,
                    reason="circuit_breaker",
                )

        # -------------------------------------------------------------------
        # Gate 10: Model resolution
        # -------------------------------------------------------------------
        model_result = None
        try:
            model_result = await resolve_model(pool, butler_name, Complexity.SELF_HEALING)
        except Exception as model_exc:
            logger.warning(
                "Model resolution failed for self_healing tier (butler=%s): %s",
                butler_name,
                model_exc,
            )

        if model_result is None:
            logger.warning(
                "Healing dispatch skipped: no self_healing tier model available for butler=%s",
                butler_name,
            )
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail="No self_healing tier model available",
            )
            return DispatchResult(
                accepted=False,
                fingerprint=fp.fingerprint,
                reason="no_model",
            )

        # -------------------------------------------------------------------
        # All gates passed — create worktree
        # -------------------------------------------------------------------
        try:
            worktree_path, branch_name = await create_healing_worktree(
                repo_root, butler_name, fp.fingerprint
            )
        except WorktreeCreationError as wt_exc:
            logger.warning(
                "Healing dispatch failed: worktree creation error (butler=%s): %s",
                butler_name,
                wt_exc,
            )
            await update_attempt_status(
                pool,
                attempt_id,
                "failed",
                error_detail=f"Worktree creation failed: {wt_exc.git_output or str(wt_exc)}",
            )
            return DispatchResult(
                accepted=False,
                fingerprint=fp.fingerprint,
                reason="worktree_creation_failed",
            )

        # Store the worktree path and branch on the attempt row
        await update_attempt_status(
            pool,
            attempt_id,
            "investigating",
            branch_name=branch_name,
            worktree_path=str(worktree_path),
        )

        logger.info(
            "Healing dispatch accepted: butler=%s fingerprint=%s attempt=%s branch=%s",
            butler_name,
            fp.fingerprint[:12],
            attempt_id,
            branch_name,
        )

        # -------------------------------------------------------------------
        # Spawn healing agent + timeout watchdog as background asyncio tasks
        # -------------------------------------------------------------------
        healing_task = asyncio.create_task(
            _run_healing_session(
                pool=pool,
                repo_root=repo_root,
                attempt_id=attempt_id,
                branch_name=branch_name,
                worktree_path=worktree_path,
                fp=fp,
                butler_name=butler_name,
                trigger_source=trigger_source,
                agent_context=agent_context,
                config=config,
                spawner=spawner,
                gh_token=gh_token,
            ),
            name=f"healing-{attempt_id}",
        )

        asyncio.create_task(
            _timeout_watchdog(
                pool=pool,
                attempt_id=attempt_id,
                repo_root=repo_root,
                branch_name=branch_name,
                healing_task=healing_task,
                timeout_minutes=config.timeout_minutes,
            ),
            name=f"healing-watchdog-{attempt_id}",
        )

        return DispatchResult(
            accepted=True,
            fingerprint=fp.fingerprint,
            reason="dispatched",
            attempt_id=attempt_id,
        )

    except Exception as dispatch_exc:
        logger.warning(
            "Unexpected error in healing dispatcher (butler=%s): %s",
            butler_name,
            dispatch_exc,
            exc_info=True,
        )
        # Return what we know
        _fp_str: str | None = None
        if isinstance(fingerprint_input, FingerprintResult):
            _fp_str = fingerprint_input.fingerprint
        return DispatchResult(
            accepted=False,
            fingerprint=_fp_str,
            reason="internal_error",
        )
    finally:
        if _HAS_OTEL and _span is not None:
            _span.end()
            if _span_token is not None:
                otel_context.detach(_span_token)
