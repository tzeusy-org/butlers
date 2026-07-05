"""DiscretionDispatcher — semaphore-gated adapter dispatcher for discretion LLM calls.

Provides a lightweight, concurrent-limited wrapper around the RuntimeAdapter
registry specifically for single-turn discretion inference.  Callers supply a
prompt and optional system prompt; the dispatcher resolves the appropriate
model from ``public.model_catalog`` at the ``Complexity.SPECIALTY`` tier,
lazily instantiates the matching adapter, and invokes it with no tools and
a strict timeout.

Usage::

    dispatcher = DiscretionDispatcher(pool=db_pool)
    response = await dispatcher.call("Is this spam?", system_prompt="Reply YES or NO.")

Design notes
------------
- Adapter instances are cached per ``runtime_type``; instantiation is
  handled by :func:`~butlers.core.runtimes.base.create_adapter`.
- Model resolution is performed on every call so catalog updates take effect
  without restarting the dispatcher.
- ``asyncio.wait_for`` enforces the per-call wall-clock timeout; the inner
  adapter invocation may also have its own timeout, but the outer guard is
  the authoritative limit.
- ``mcp_servers={}``, ``max_turns=1``, and a minimal env (PATH, HOME) are
  always passed to the adapter — discretion calls are single-turn with no
  tool access.
- Same-tier failover (bu-8fves): a failed ``adapter.invoke()`` is classified
  via :func:`~butlers.core.failover_classifier.classify_failover_eligibility`
  (the same default-closed classifier ``Spawner._run()`` uses) and, when
  eligible, retried against the next same-tier candidate from
  ``public.model_catalog`` via
  :func:`~butlers.core.model_routing.next_same_tier_candidate`. Discretion
  calls never carry MCP tool calls (``mcp_servers={}``), so Gate 1 of the
  classifier (captured tool calls suppress failover) never fires here.
  ``adapter.last_process_info`` is still passed through, since some gates
  (e.g. OpenCode's pre-tool-call ``APIError`` envelope) key off it rather
  than ``tool_calls``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

import asyncpg

from butlers.core.failover_classifier import FailoverContext, classify_failover_eligibility
from butlers.core.metrics import ButlerMetrics
from butlers.core.model_routing import (
    Complexity,
    check_token_quota,
    next_same_tier_candidate,
    record_token_usage,
    resolve_model_with_effective_tier,
)
from butlers.core.runtimes.base import RuntimeAdapter, create_adapter

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT: int = 4
_DEFAULT_TIMEOUT_S: float = 30.0

# Hard cap on same-tier failover attempts per call() — a defensive backstop
# against unbounded looping, mirroring Spawner._run()'s _MAX_FAILOVER_ATTEMPTS.
# Discretion calls are cheap single-turn screens, but the cap still guards
# against a pathological catalog (many same-tier entries all failing).
_MAX_FAILOVER_ATTEMPTS: int = 5

# Ollama model families that default to thinking mode and need /no_think
# prepended to the prompt for single-turn classification tasks.
_THINKING_MODEL_PREFIXES: tuple[str, ...] = ("qwen3",)


def _needs_no_think(model_id: str) -> bool:
    """Return True if *model_id* is a thinking model that needs /no_think."""
    # model_id format: "ollama/qwen3.5:9b", "ollama/qwen3:4b", etc.
    bare = model_id.split("/", 1)[-1] if "/" in model_id else model_id
    return any(bare.startswith(prefix) for prefix in _THINKING_MODEL_PREFIXES)


def _minimal_env() -> dict[str, str]:
    """Build a minimal env dict for the runtime subprocess.

    The adapter needs at least PATH (for shebang resolution) and HOME
    (for OpenCode's internal SQLite model registry).  Without these the
    child process cannot discover provider models and all calls fail with
    "Model not found".
    """
    env: dict[str, str] = {}
    for var in ("PATH", "HOME", "USER"):
        value = os.environ.get(var)
        if value:
            env[var] = value
    return env


class DiscretionDispatcher:
    """Semaphore-gated adapter dispatcher for discretion-tier LLM calls.

    Parameters
    ----------
    pool:
        An asyncpg connection pool used to resolve the discretion model from
        ``public.model_catalog``.
    butler_name:
        The butler identity name forwarded to ``resolve_model`` for
        per-butler overrides.  Defaults to ``"__discretion__"`` which
        effectively means no per-butler override (global catalog only).
    max_concurrent:
        Maximum number of concurrent adapter invocations.  Enforced via an
        ``asyncio.Semaphore``.
    timeout_s:
        Per-call wall-clock timeout in seconds.  Passed to
        ``asyncio.wait_for``.
    complexity_tier:
        Catalog complexity tier used for model resolution. Defaults to the
        discretion tier for existing connector discretion callers.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        butler_name: str = "__discretion__",
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        complexity_tier: Complexity = Complexity.SPECIALTY,
    ) -> None:
        self._pool = pool
        self._butler_name = butler_name
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._timeout_s = timeout_s
        self._complexity_tier = complexity_tier
        self._adapter_cache: dict[str, RuntimeAdapter] = {}
        self._adapter_cache_key: dict[str, str] = {}
        # Reuses the same OTel instruments Spawner._run() records same-tier
        # failover to, keyed by the constructor's butler_name (not the
        # per-call identity= label) so per-connector identities (one per
        # Telegram chat, etc.) never blow up metric cardinality.
        self._metrics = ButlerMetrics(butler_name=butler_name)

    def _get_or_create_adapter(
        self,
        runtime_type: str,
        provider_config: dict[str, dict] | None = None,
    ) -> RuntimeAdapter:
        """Return a cached adapter for *runtime_type*, creating via
        :func:`~butlers.core.runtimes.base.create_adapter` on cache miss.
        """
        cfg_str = str(provider_config) if provider_config else ""
        if runtime_type in self._adapter_cache:
            if self._adapter_cache_key.get(runtime_type, "") == cfg_str:
                return self._adapter_cache[runtime_type]

        adapter = create_adapter(
            runtime_type,
            provider_config=provider_config,
            butler_name=self._butler_name,
        )
        self._adapter_cache[runtime_type] = adapter
        self._adapter_cache_key[runtime_type] = cfg_str
        logger.debug(
            "DiscretionDispatcher: lazily instantiated adapter runtime_type=%s", runtime_type
        )
        return adapter

    async def _resolve_provider_config(self, model_id: str) -> dict[str, dict] | None:
        """Look up provider base URL from ``public.provider_config``.

        When *model_id* starts with ``ollama/``, queries the DB for the
        Ollama provider's base URL and returns an OpenCode-compatible
        provider config dict including ``npm`` adapter, ``/v1``-suffixed
        base URL, and explicit model registration.

        Delegates to :func:`butlers.core.spawner.resolve_provider_config`.
        """
        from butlers.core.spawner import resolve_provider_config

        return await resolve_provider_config(self._pool, model_id)

    async def call(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        identity: str | None = None,
    ) -> str:
        """Invoke the discretion-tier model with *prompt* and return the response text.

        Resolution order:
        1. Query ``public.model_catalog`` for ``Complexity.SPECIALTY``.
        2. Raise ``RuntimeError`` if no enabled catalog entry matches.
        3. Acquire the concurrency semaphore.
        4. Invoke the adapter with ``asyncio.wait_for`` enforcing ``timeout_s``.

        Parameters
        ----------
        prompt:
            The user-facing prompt to send.
        system_prompt:
            Optional system-level instructions for the model.
        identity:
            Per-connector identity for spend attribution (e.g. ``"tg:<chat_id>"``,
            ``"home_assistant:ha.local:8123"``) forwarded by
            :class:`~butlers.connectors.discretion.DiscretionEvaluator` as its
            ``source_name``. Recorded as the ledger's ``butler_name`` in place
            of the constructor's ``butler_name`` default (``"__discretion__"``)
            so ``public.token_usage_ledger`` can distinguish which connector
            source triggered the call instead of every discretion call sharing
            one opaque identity. Does not affect model resolution (which still
            uses the constructor's ``butler_name`` for per-butler catalog
            overrides) — this is spend attribution only. ``None`` (the
            default) preserves the prior constructor-default behavior.

        Returns
        -------
        str
            The model's response text.  Returns an empty string if the adapter
            returns ``None`` as its result.

        Raises
        ------
        RuntimeError
            If ``public.model_catalog`` contains no enabled entry for the
            ``discretion`` complexity tier, or if every same-tier candidate
            fails and same-tier failover is exhausted (see "Same-tier
            failover" below). The final ``RuntimeError``'s message includes
            ``same_tier_failover_exhausted`` so callers/logs can distinguish
            "one attempt failed" from "every same-tier candidate failed".
        asyncio.TimeoutError
            If the adapter invocation exceeds ``timeout_s`` and no further
            same-tier candidate is eligible or available.

        Same-tier failover
        -------------------
        Mirrors ``Spawner._run()``'s same-tier failover loop (bu-8fves): a
        failed ``adapter.invoke()`` is classified via
        :func:`~butlers.core.failover_classifier.classify_failover_eligibility`.
        When the classifier judges the failure systemic and pre-invocation
        (e.g. provider/auth errors, timeouts, connection errors) — the same
        default-closed allow-list the spawner uses — the call retries against
        the next same-tier candidate from ``public.model_catalog``
        (:func:`~butlers.core.model_routing.next_same_tier_candidate`), up to
        ``_MAX_FAILOVER_ATTEMPTS`` attempts. Non-eligible failures (business
        errors, or the candidate pool exhausted) re-raise the original
        exception immediately — same as a single-attempt call previously did.
        Every attempt/suppression/exhaustion is recorded via the same
        ``ButlerMetrics`` failover instruments ``Spawner._run()`` uses
        (``butlers.spawner.failover_*``), keyed by the constructor's
        ``butler_name`` so per-connector ``identity=`` values never inflate
        metric cardinality.
        """
        catalog_result = await resolve_model_with_effective_tier(
            self._pool, self._butler_name, self._complexity_tier
        )
        if catalog_result is None:
            raise RuntimeError(
                f"No {self._complexity_tier} model configured in public.model_catalog. "
                f"Add an enabled entry with complexity_tier={self._complexity_tier.value!r}."
            )

        (
            runtime_type,
            model_id,
            extra_args,
            catalog_entry_id,
            session_timeout_s,
            effective_tier,
        ) = catalog_result

        attempted_ids: list[uuid.UUID] = []
        attempt_count = 0

        while True:
            attempt_count += 1

            # Pre-call quota check: block if catalog entry token budget is exhausted.
            # Quota exhaustion is not retried across same-tier candidates here — it
            # is a hard per-entry block, matching the prior single-attempt behavior.
            quota = await check_token_quota(self._pool, catalog_entry_id)
            if not quota.allowed:
                windows_exceeded = []
                if quota.limit_24h is not None and quota.usage_24h >= quota.limit_24h:
                    windows_exceeded.append(
                        f"24h (used={quota.usage_24h}, limit={quota.limit_24h})"
                    )
                if quota.limit_30d is not None and quota.usage_30d >= quota.limit_30d:
                    windows_exceeded.append(
                        f"30d (used={quota.usage_30d}, limit={quota.limit_30d})"
                    )
                raise RuntimeError(
                    f"Token quota exhausted for catalog entry '{model_id}': "
                    + "; ".join(windows_exceeded)
                )

            # Resolve provider config for models using external providers
            # (e.g. ollama/ prefix needs the base URL from public.provider_config)
            provider_config = await self._resolve_provider_config(model_id)
            adapter = self._get_or_create_adapter(runtime_type, provider_config)

            # Thinking models (qwen3 family) default to chain-of-thought mode
            # which produces <think> tokens that get stripped, leaving empty
            # output.  Prepend /no_think to disable thinking for single-turn
            # classification tasks like discretion.
            effective_prompt = f"/no_think\n{prompt}" if _needs_no_think(model_id) else prompt

            _usage_dict: dict | None = None

            async def _invoke() -> str:
                nonlocal _usage_dict
                result_text, _tool_calls, _usage_dict = await adapter.invoke(
                    prompt=effective_prompt,
                    system_prompt=system_prompt,
                    mcp_servers={},
                    env=_minimal_env(),
                    max_turns=1,
                    model=model_id,
                    runtime_args=extra_args or None,
                    timeout=session_timeout_s,
                )
                return result_text or ""

            attempt_exc: Exception | None = None
            result: str = ""

            async with self._semaphore:
                try:
                    result = await asyncio.wait_for(_invoke(), timeout=session_timeout_s)
                except Exception as exc:  # noqa: BLE001 — classified below
                    attempt_exc = exc
                finally:
                    # Record token usage best-effort (success and failure).
                    # Tokens are consumed by the provider on invocation regardless of outcome.
                    if _usage_dict:
                        input_tokens = _usage_dict.get("input_tokens")
                        output_tokens = _usage_dict.get("output_tokens")
                        if input_tokens is not None:
                            await record_token_usage(
                                self._pool,
                                catalog_entry_id=catalog_entry_id,
                                butler_name=identity or self._butler_name,
                                session_id=None,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens or 0,
                                purpose="discretion",
                            )
                            logger.debug(
                                "Discretion token usage recorded: in=%d out=%d model=%s",
                                input_tokens,
                                output_tokens or 0,
                                model_id,
                            )
                        else:
                            logger.debug(
                                "Discretion adapter returned usage without input_tokens: %s",
                                _usage_dict,
                            )
                    else:
                        logger.debug(
                            "Discretion adapter returned no usage data for model=%s",
                            model_id,
                        )

            if attempt_exc is None:
                return result

            # Invocation failed: classify for same-tier failover eligibility.
            # tool_calls is always [] — discretion calls pass mcp_servers={}, so
            # Gate 1 (captured tool calls suppress failover) never fires here.
            # process_info is still passed through: some gates (e.g. OpenCode's
            # pre-tool-call APIError envelope) key off it, not just tool_calls.
            decision = classify_failover_eligibility(
                FailoverContext(exception=attempt_exc, process_info=adapter.last_process_info)
            )

            if not decision.eligible:
                logger.debug(
                    "DiscretionDispatcher failover suppressed for model=%s: %s",
                    model_id,
                    decision.reason,
                )
                self._metrics.record_failover_suppressed(reason=decision.reason)
                raise attempt_exc

            attempted_ids.append(catalog_entry_id)

            if attempt_count >= _MAX_FAILOVER_ATTEMPTS:
                logger.warning(
                    "DiscretionDispatcher same-tier failover: safety cap "
                    "(_MAX_FAILOVER_ATTEMPTS=%d) reached for tier=%s after attempt on model=%s",
                    _MAX_FAILOVER_ATTEMPTS,
                    effective_tier,
                    model_id,
                )
                self._metrics.record_failover_exhausted(tier=effective_tier)
                raise RuntimeError(
                    f"same_tier_failover_exhausted: tier={effective_tier} after "
                    f"{attempt_count} attempt(s) (safety cap); last error: "
                    f"{type(attempt_exc).__name__}: {attempt_exc}"
                ) from attempt_exc

            next_candidate = await next_same_tier_candidate(
                self._pool, self._butler_name, effective_tier, attempted_ids
            )
            if next_candidate is None:
                logger.warning(
                    "DiscretionDispatcher same-tier failover exhausted for tier=%s "
                    "after %d attempt(s); last error on model=%s: %s",
                    effective_tier,
                    attempt_count,
                    model_id,
                    decision.reason,
                )
                self._metrics.record_failover_exhausted(tier=effective_tier)
                raise RuntimeError(
                    f"same_tier_failover_exhausted: tier={effective_tier} after "
                    f"{attempt_count} attempt(s); last error: "
                    f"{type(attempt_exc).__name__}: {attempt_exc}"
                ) from attempt_exc

            (
                next_runtime_type,
                next_model_id,
                next_extra_args,
                next_catalog_entry_id,
                next_session_timeout_s,
            ) = next_candidate
            logger.info(
                "DiscretionDispatcher same-tier failover: %s -> %s (tier=%s, reason=%s)",
                model_id,
                next_model_id,
                effective_tier,
                decision.reason,
            )
            self._metrics.record_failover_attempt(
                from_model=model_id,
                to_model=next_model_id,
                reason=decision.reason.split(":")[0],
            )

            runtime_type = next_runtime_type
            model_id = next_model_id
            extra_args = next_extra_args
            catalog_entry_id = next_catalog_entry_id
            session_timeout_s = next_session_timeout_s
            # Loop again with the updated candidate.
