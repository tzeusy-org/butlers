"""DiscretionDispatcher — semaphore-gated adapter dispatcher for discretion LLM calls.

Provides a lightweight, concurrent-limited wrapper around the RuntimeAdapter
registry specifically for single-turn discretion inference.  Callers supply a
prompt and optional system prompt; the dispatcher resolves the appropriate
model from ``public.model_catalog`` at the ``Complexity.SPECIALTY`` tier,
lazily instantiates the matching adapter, and invokes it with no tools and
a strict timeout.

Usage::

    dispatcher = DiscretionDispatcher(
        pool=db_pool,
        codex_auth_authority=system_global_codex_authority,
    )
    response = await dispatcher.call("Is this spam?", system_prompt="Reply YES or NO.")

Design notes
------------
- Adapter instances are cached per ``runtime_type``; instantiation is
  handled by :func:`~butlers.core.runtimes.base.create_adapter`.
- Model resolution is performed on every call so catalog updates take effect
  without restarting the dispatcher.
- ``asyncio.wait_for`` enforces the per-call wall-clock timeout. The model
  execution timeout is passed unchanged to the adapter; an adapter may declare
  a bounded setup/finalizer allowance that is added only to this outer guard.
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
- Per-catalog-entry quota skips (bu-x82cy): a quota-denied candidate is
  skipped before adapter construction/invocation and advances through the
  same effective-tier candidate loop. The skip consumes the existing bounded
  failover-attempt budget and emits only bounded dispatcher log/metric
  provenance; this session-less path does not import Spawner's session
  provenance or other pre-spawn gates.
- Spend routing rules (bu-m95jq): after tier resolution, the resolved model
  is run through :func:`~butlers.core.model_routing.apply_spend_routing_rules`
  with ``trigger_source="discretion"``, the same integration shape
  ``Spawner._run()`` uses, so an operator-configured rule scoped to
  ``condition={"purpose": "discretion"}`` (or ``"trigger"``) can re-route the
  model actually dispatched. A matching rule's ``action.max_cost_per_call``
  cap is logged for visibility but not enforced as a pre-call DENY here,
  since discretion calls have no ``max_token_budget`` to bound a worst-case
  cost estimate against.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from prometheus_client import Counter

from butlers.cli_auth.registry import providers_for_runtime
from butlers.core.failover_classifier import FailoverContext, classify_failover_eligibility
from butlers.core.metrics import ButlerMetrics
from butlers.core.model_routing import (
    Complexity,
    apply_spend_routing_rules,
    check_token_quota,
    next_same_tier_candidate,
    record_token_usage,
    resolve_model_with_effective_tier,
)
from butlers.core.runtimes.base import (
    RuntimeAdapter,
    create_adapter,
    validated_session_timeout_overhead_s,
)
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT: int = 4
_DEFAULT_TIMEOUT_S: float = 30.0

# bu-ur7go: discretion calls that fail with a provider/auth error (e.g. a
# never-provisioned or revoked ~/.codex/auth.json — see bu-ofo3i) previously
# vanished into the generic fail-open/fail-closed path with zero operator
# visibility. This counter is exported on the same /metrics surface as
# discretion_evaluations_total so a sustained run of auth failures is
# observable without grepping connector logs.
discretion_auth_failures_total = Counter(
    "discretion_auth_failures_total",
    "Total discretion LLM calls that failed with a provider/auth error, by runtime_type",
    labelnames=["runtime_type"],
)

# Hard cap on same-tier failover slots per call() — each runtime invocation or
# pre-invocation quota skip consumes one slot. It is a defensive backstop against
# unbounded looping, mirroring Spawner._run()'s _MAX_FAILOVER_ATTEMPTS. Discretion
# calls are cheap single-turn screens, but the cap still guards against a
# pathological catalog (many same-tier entries all failing or quota-denied).
_MAX_FAILOVER_ATTEMPTS: int = 5

# Quota-skip logs are operational provenance, not a message/session transcript.
# Bound the catalog-derived fields so a malformed catalog row cannot create an
# unbounded log event; prompt, system prompt, and caller identity are never logged.
_QUOTA_SKIP_PROVENANCE_FIELD_MAX_CHARS: int = 256

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
        Per-call provider execution timeout in seconds. It is passed unchanged
        to the adapter; the outer guard also includes a bounded,
        adapter-declared setup/finalizer allowance when applicable.
    complexity_tier:
        Catalog complexity tier used for model resolution. Defaults to the
        discretion tier for existing connector discretion callers.
    credential_store:
        Optional generic credential store for non-Codex runtime adapters.
    codex_auth_authority:
        Explicit system-global authority for Codex runtime adapters.  This is
        intentionally distinct from the model-resolution pool and from the
        generic non-Codex store: the dispatcher never infers that either is
        suitable for ``cli-auth/codex``.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        butler_name: str = "__discretion__",
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        complexity_tier: Complexity = Complexity.SPECIALTY,
        credential_store: CredentialStore | None = None,
        codex_auth_authority: CredentialStore | None = None,
    ) -> None:
        self._pool = pool
        self._credential_store = credential_store
        self._codex_auth_authority = codex_auth_authority
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

        # bu-ur7go: reactive auth-health bookkeeping, updated from real call()
        # outcomes so connector /status can report it without an extra DB
        # round-trip or subprocess/network probe (see get_auth_health()).
        self._last_runtime_type: str | None = None
        self._last_success_at: float | None = None
        self._last_auth_failure_at: float | None = None
        self._last_auth_failure_reason: str | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        """The asyncpg pool this dispatcher resolves models against.

        Exposed read-only so a :class:`~butlers.connectors.discretion.DiscretionEvaluator`
        wired with this dispatcher can reach the same DB pool for its best-effort
        attention-ledger write (the failover-exhausted suppression record,
        bu-5go3y) without reaching into a private attribute.
        """
        return self._pool

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

        constructor_kwargs: dict[str, Any] = {"butler_name": self._butler_name}
        if runtime_type == "codex":
            if self._codex_auth_authority is not None:
                constructor_kwargs["credential_store"] = self._codex_auth_authority
        elif self._credential_store is not None:
            constructor_kwargs["credential_store"] = self._credential_store
        if runtime_type == "codex" and provider_config:
            # ``provider_config`` is a generic adapter-factory argument, not
            # a Codex constructor option. Avoid its fallback path, which
            # would otherwise discard the explicit Codex authority together
            # with that unsupported option.
            adapter = create_adapter(runtime_type, **constructor_kwargs)
        else:
            adapter = create_adapter(
                runtime_type,
                provider_config=provider_config,
                **constructor_kwargs,
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
        3. Apply operator spend routing rules (``purpose="discretion"``) via
           :func:`~butlers.core.model_routing.apply_spend_routing_rules`; a
           matching rule may re-route the resolved model.
        4. Acquire the concurrency semaphore.
        5. Invoke the adapter with ``asyncio.wait_for`` enforcing ``timeout_s``.

        Parameters
        ----------
        prompt:
            The user-facing prompt to send.
        system_prompt:
            Optional system-level instructions for the model.
        identity:
            Per-connector identity for spend attribution (e.g. ``"tg:<chat_id>"``,
            ``"home_assistant:ha.example.invalid:8123"``) forwarded by
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
        quota-denied catalog entry is first treated as a pre-invocation skip,
        then a failed ``adapter.invoke()`` is classified via
        :func:`~butlers.core.failover_classifier.classify_failover_eligibility`.
        Both kinds of transition retry against the next same-tier candidate
        from ``public.model_catalog``
        (:func:`~butlers.core.model_routing.next_same_tier_candidate`), up to
        ``_MAX_FAILOVER_ATTEMPTS`` counted slots. Non-eligible runtime failures
        (business errors) re-raise the original exception immediately — same as
        a single-attempt call previously did. Every transition/suppression/
        exhaustion is recorded via the same ``ButlerMetrics`` failover instruments
        ``Spawner._run()`` uses
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

        # bu-m95jq: spend routing rules (public.spend_rules), model SELECTION
        # override. Mirrors Spawner._run()'s integration (butlers/core/spawner.py)
        # exactly: applied once, right after tier resolution and before the
        # same-tier failover loop below, with trigger_source="discretion" (the
        # same literal purpose= value record_token_usage stamps onto
        # public.token_usage_ledger further down) so a rule scoped to
        # condition={"purpose": "discretion"} matches. A matching rule can
        # re-route the resolved model (action.model) and/or surface a per-call
        # USD cap (action.max_cost_per_call); apply_spend_routing_rules fails
        # open on any DB/lookup error, so a rules problem never blocks a
        # discretion call. The per-call cap is not enforced as a pre-call DENY
        # here (unlike the spawner path) because discretion calls have no
        # analogous max_token_budget to bound a worst-case cost estimate
        # against; it is logged for operator visibility instead.
        if catalog_entry_id is not None:
            try:
                _routing_result = await apply_spend_routing_rules(
                    self._pool,
                    self._butler_name,
                    effective_tier,
                    (runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s),
                    trigger_source="discretion",
                )
                (
                    runtime_type,
                    model_id,
                    extra_args,
                    catalog_entry_id,
                    session_timeout_s,
                ) = _routing_result.resolved
                if _routing_result.max_cost_per_call is not None:
                    logger.info(
                        "DiscretionDispatcher: spend rule set per-call cap $%.4f for "
                        "model=%s (not enforced pre-call: no fixed token budget to "
                        "estimate worst-case cost against)",
                        _routing_result.max_cost_per_call,
                        model_id,
                    )
            except Exception:
                logger.warning(
                    "DiscretionDispatcher: spend routing-rule evaluation failed for "
                    "butler=%s; keeping tier-resolved model=%s",
                    self._butler_name,
                    model_id,
                    exc_info=True,
                )

        attempted_ids: list[uuid.UUID] = []
        attempt_count = 0

        while True:
            attempt_count += 1
            self._last_runtime_type = runtime_type

            # Pre-call quota check: each catalog entry has its own availability
            # cap. A denied entry is a pre-invocation same-tier skip, not a
            # hard block for the logical discretion call.
            quota = await check_token_quota(self._pool, catalog_entry_id)
            if not quota.allowed:
                windows_exceeded: list[str] = []
                if quota.limit_24h is not None and quota.usage_24h >= quota.limit_24h:
                    windows_exceeded.append(
                        f"24h (used={quota.usage_24h}, limit={quota.limit_24h})"
                    )
                if quota.limit_30d is not None and quota.usage_30d >= quota.limit_30d:
                    windows_exceeded.append(
                        f"30d (used={quota.usage_30d}, limit={quota.limit_30d})"
                    )
                bounded_model_id = model_id[:_QUOTA_SKIP_PROVENANCE_FIELD_MAX_CHARS]
                bounded_effective_tier = effective_tier[:_QUOTA_SKIP_PROVENANCE_FIELD_MAX_CHARS]
                bounded_windows = "; ".join(windows_exceeded)[
                    :_QUOTA_SKIP_PROVENANCE_FIELD_MAX_CHARS
                ]
                quota_msg = (
                    f"Token quota exhausted for catalog entry '{bounded_model_id}': "
                    f"{bounded_windows}"
                )
                # Discretion calls do not own a Spawner session/logical-session
                # record. Keep skip provenance bounded and operational: catalog
                # data + stable reason only, never prompt/system/identity content.
                logger.info(
                    "DiscretionDispatcher quota skip: model=%s tier=%s attempt=%d "
                    "reason=quota_exhausted windows=%s",
                    bounded_model_id,
                    bounded_effective_tier,
                    attempt_count,
                    bounded_windows,
                )
                attempted_ids.append(catalog_entry_id)

                if attempt_count >= _MAX_FAILOVER_ATTEMPTS:
                    logger.warning(
                        "DiscretionDispatcher same-tier failover: safety cap "
                        "(_MAX_FAILOVER_ATTEMPTS=%d) reached for tier=%s after quota "
                        "skip on model=%s",
                        _MAX_FAILOVER_ATTEMPTS,
                        bounded_effective_tier,
                        bounded_model_id,
                    )
                    self._metrics.record_failover_exhausted(tier=bounded_effective_tier)
                    raise RuntimeError(
                        f"same_tier_failover_exhausted: tier={bounded_effective_tier} after "
                        f"{attempt_count} attempt(s) (safety cap); last quota skip: {quota_msg}"
                    )

                next_candidate = await next_same_tier_candidate(
                    self._pool, self._butler_name, effective_tier, attempted_ids
                )
                if next_candidate is None:
                    logger.warning(
                        "DiscretionDispatcher same-tier failover exhausted for tier=%s "
                        "after %d attempt(s); last quota skip on model=%s",
                        bounded_effective_tier,
                        attempt_count,
                        bounded_model_id,
                    )
                    self._metrics.record_failover_exhausted(tier=bounded_effective_tier)
                    raise RuntimeError(
                        f"same_tier_failover_exhausted: tier={bounded_effective_tier} after "
                        f"{attempt_count} attempt(s); last quota skip: {quota_msg}"
                    )

                (
                    next_runtime_type,
                    next_model_id,
                    next_extra_args,
                    next_catalog_entry_id,
                    next_session_timeout_s,
                ) = next_candidate
                logger.info(
                    "DiscretionDispatcher same-tier quota failover: %s -> %s "
                    "(tier=%s, reason=quota_exhausted)",
                    bounded_model_id,
                    next_model_id[:_QUOTA_SKIP_PROVENANCE_FIELD_MAX_CHARS],
                    bounded_effective_tier,
                )
                self._metrics.record_failover_attempt(
                    from_model=bounded_model_id,
                    to_model=next_model_id[:_QUOTA_SKIP_PROVENANCE_FIELD_MAX_CHARS],
                    reason="quota_exhausted",
                )
                runtime_type = next_runtime_type
                model_id = next_model_id
                extra_args = next_extra_args
                catalog_entry_id = next_catalog_entry_id
                session_timeout_s = next_session_timeout_s
                continue

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
                    outer_timeout_s = session_timeout_s + validated_session_timeout_overhead_s(
                        adapter
                    )
                    result = await asyncio.wait_for(_invoke(), timeout=outer_timeout_s)
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
                self._last_success_at = time.time()
                return result

            # Invocation failed: classify for same-tier failover eligibility.
            # tool_calls is always [] — discretion calls pass mcp_servers={}, so
            # Gate 1 (captured tool calls suppress failover) never fires here.
            # process_info is still passed through: some gates (e.g. OpenCode's
            # pre-tool-call APIError envelope) key off it, not just tool_calls.
            decision = classify_failover_eligibility(
                FailoverContext(exception=attempt_exc, process_info=adapter.last_process_info)
            )

            # bu-ur7go: a genuine provider/auth-classified failure (e.g. a
            # missing or revoked ~/.codex/auth.json — see bu-ofo3i) is
            # recorded here regardless of failover eligibility so connector
            # /status and discretion_auth_failures_total surface it instead of
            # it disappearing into the generic fail-open/fail-closed path.
            # bu-ujm9d split the classifier's marker buckets so a connection
            # refused / service unavailable / bad gateway failure now carries
            # the distinct "provider_unavailable" reason prefix and does NOT
            # match this startswith check — a network blip must not flip the
            # auth-health surface to "degraded" or increment this counter.
            if decision.reason.startswith("provider_auth_error"):
                self._last_auth_failure_at = time.time()
                self._last_auth_failure_reason = decision.reason
                discretion_auth_failures_total.labels(runtime_type=runtime_type).inc()

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

    def get_auth_health(self) -> dict[str, Any]:
        """Lightweight, synchronous auth-health snapshot for connector /status.

        Deliberately cheap: no DB round-trip, no subprocess, no network call
        (contrast with :func:`butlers.cli_auth.health.probe_provider`, which
        does all three and is meant for the dashboard's on-demand CLI auth
        probe, not a per-/status-poll check). Instead this reflects two
        signals that cost nothing beyond an in-memory read and an
        ``os.path.exists()`` stat:

        - ``auth_file_present``: whether a device-code CLI auth provider is
          registered for the runtime last resolved by :meth:`call` and its
          on-disk token file exists. ``None`` when no such on-disk artifact
          applies (runtime unresolved yet, or an api_key-mode provider with
          no token file — e.g. Anthropic's env-var API key).
        - ``last_discretion_success_at`` / ``last_auth_failure_at``: the most
          recent real outcomes recorded by :meth:`call`, so a stale/revoked
          token that still passes the file-presence check (see bu-ofo3i —
          ``codex login status`` only inspects the file, not backend
          validity) still surfaces once the next real call 401s.

        Returns a dict with keys ``runtime_type``, ``auth_file_present``,
        ``last_discretion_success_at``, ``last_auth_failure_at``, and
        ``status`` (one of ``"ok"``, ``"degraded"``, ``"unknown"``).
        ``"unknown"`` means no discretion call has been attempted yet and no
        auth-file check applies — an idle connector must not be reported as
        ``"ok"`` just because nothing has happened yet.
        """
        runtime_type = self._last_runtime_type
        auth_file_present: bool | None = None

        if runtime_type is not None:
            candidates = [
                provider
                for provider in providers_for_runtime(runtime_type)
                if provider.token_path is not None
            ]
            if candidates:
                auth_file_present = any(
                    provider.token_path.exists()  # type: ignore[union-attr]
                    for provider in candidates
                )

        last_success_iso = (
            datetime.fromtimestamp(self._last_success_at, UTC).isoformat()
            if self._last_success_at is not None
            else None
        )
        last_auth_failure_iso = (
            datetime.fromtimestamp(self._last_auth_failure_at, UTC).isoformat()
            if self._last_auth_failure_at is not None
            else None
        )

        failure_is_current = self._last_auth_failure_at is not None and (
            self._last_success_at is None or self._last_auth_failure_at > self._last_success_at
        )

        if auth_file_present is False or failure_is_current:
            status = "degraded"
        elif runtime_type is None and self._last_auth_failure_at is None:
            status = "unknown"
        else:
            status = "ok"

        return {
            "runtime_type": runtime_type,
            "auth_file_present": auth_file_present,
            "last_discretion_success_at": last_success_iso,
            "last_auth_failure_at": last_auth_failure_iso,
            "status": status,
        }
