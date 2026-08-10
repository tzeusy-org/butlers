"""Hourly automated model-catalog verification sweep (bu-hmdqz.2).

``last_verified_ok`` and ``last_verified_at`` on ``public.model_catalog``
were manual-only: ``POST /api/settings/models/verify-all`` (rate-limited to
once per minute, but otherwise never called except by an owner clicking
"Verify all" on the Models tab). The settings-secrets auditor's evidence for
this move found every one of 31 catalog entries ``last_verified_at`` 23 days
stale — the routing layer's own health signal was silently governing model
exclusion behind an ageless green badge on the dashboard.

This module runs the exact same verification core
(``butlers.api.routers.model_settings.run_verify_all_models``) on an hourly
cadence so ``last_verified_ok`` is never more than roughly one interval
stale, closing the loop the manual button alone left open.

Design
------
- Reuses ``run_verify_all_models`` directly — no parallel verification
  implementation to drift from the manual endpoint's behavior. The manual
  endpoint's once-per-minute rate limit is an HTTP-surface concern specific
  to that route; this job calls the shared core function directly on its own
  (much coarser) hourly cadence, so the two never fight over the same
  in-process rate-limit sentinel.
- ``audit_actor="model_verify_sweep"`` distinguishes an automated run from an
  owner-initiated one (``audit_actor="owner"``) in ``public.audit_log`` —
  same honesty concern as the rest of the degraded-source-flagging
  convention: a fully automated action should not be attributed to a human
  who did not act.
- Sleeps first, mirroring ``butlers.jobs.secrets_lifecycle.
  run_secrets_lifecycle_loop`` — avoids a burst of real LLM-CLI verification
  calls at every process boot (dev reloads, and any test that exercises the
  full API lifespan via ``with TestClient(app) as client:``).
"""

from __future__ import annotations

import asyncio
import logging

from butlers.api.db import DatabaseManager
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

# One hour by default — the bead's explicit target ("last_verified_ok is
# never >1h stale"). Overridable via MODEL_VERIFY_INTERVAL_S for tests/tuning.
DEFAULT_MODEL_VERIFY_INTERVAL_S = 3600.0

_AUDIT_ACTOR = "model_verify_sweep"


async def run_model_verify_sweep(db: DatabaseManager) -> dict[str, int] | None:
    """Run one verify-all sweep against the shared credential pool.

    Returns a summary dict ``{total, ok, failed, skipped}``, or ``None`` if no
    shared pool is configured (mirrors ``run_secrets_lifecycle_check``'s
    no-pool short-circuit). Never raises — a failure inside the verification
    core is logged and swallowed so the loop is never killed by one bad tick.
    """
    try:
        pool = db.credential_shared_pool()
    except KeyError:
        logger.warning("model_verify_sweep: no shared credential pool configured; skipping")
        return None

    # Local import: avoids a module-import-time cycle between the jobs
    # package and the API router package (mirrors the local `deliver` import
    # pattern used throughout butlers.core.*_attention modules).
    from butlers.api.routers.model_settings import run_verify_all_models

    codex_auth_authority = CredentialStore(pool, system_global_pool=pool)
    result = await run_verify_all_models(
        pool,
        audit_actor=_AUDIT_ACTOR,
        codex_auth_authority=codex_auth_authority,
    )
    return {
        "total": result.total,
        "ok": result.ok,
        "failed": result.failed,
        "skipped": result.skipped,
    }


async def run_model_verify_loop(
    db: DatabaseManager,
    *,
    interval_s: float = DEFAULT_MODEL_VERIFY_INTERVAL_S,
) -> None:
    """Run ``run_model_verify_sweep`` every ``interval_s`` until cancelled.

    Sleeps first — see module docstring. A single sweep's failure is logged
    and swallowed (mirrors ``run_secrets_lifecycle_loop``) so one bad tick
    never kills the loop.

    Intended to be wrapped in ``asyncio.create_task()`` from the API lifespan
    and cancelled on shutdown — see ``butlers.api.app.lifespan``.

    Raises ``ValueError`` immediately for a non-positive ``interval_s``
    rather than spinning a tight zero-sleep loop that would hammer every
    configured model with real LLM-CLI verification calls — the caller
    (``butlers.api.app.lifespan``) already validates and falls back to
    ``DEFAULT_MODEL_VERIFY_INTERVAL_S`` before calling this, so this is
    defense-in-depth for any other caller.
    """
    if interval_s <= 0:
        raise ValueError(f"interval_s must be a positive number, got {interval_s!r}")
    while True:
        await asyncio.sleep(interval_s)
        try:
            summary = await run_model_verify_sweep(db)
            logger.info("model_verify_sweep: %s", summary)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("model_verify_sweep: sweep failed")
