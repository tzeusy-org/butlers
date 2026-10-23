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
- Adapter instances are cached per ``runtime_type`` and built by the shared
  :class:`~butlers.core.runtimes.registry.AdapterRegistry`.
- Model resolution is performed on every call so catalog updates take effect
  without restarting the dispatcher.
- ``asyncio.wait_for`` enforces the per-call wall-clock timeout. The model
  execution timeout is passed unchanged to the adapter; an adapter may declare
  a bounded setup/finalizer allowance that is added only to this outer guard.
- ``mcp_servers={}``, ``max_turns=1``, and a minimal env (PATH, HOME) are
  always passed to the adapter — discretion calls are single-turn with no
  tool access.
- Every attempt loop concern runs in the model-call kernel,
  :func:`~butlers.core.model_call.call_model` (bu-m0vjeb.1): tier resolution,
  spend routing rules (``trigger_source="discretion"``, bu-m95jq), the monthly
  spend ceiling (bu-svqpzo.14), per-entry quota skips (bu-x82cy), same-tier
  failover (bu-8fves), attempt rows and token usage. The dispatcher supplies
  only the attempt itself and its auth-health bookkeeping. Discretion calls
  never carry MCP tool calls (``mcp_servers={}``), so the classifier's
  captured-tool-call gate never fires here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import asyncpg
from prometheus_client import Counter

from butlers.cli_auth.registry import providers_for_runtime
from butlers.core.dispatch_intent import discretion_dispatch_intent
from butlers.core.failover_classifier import FailoverDecision
from butlers.core.metrics import ButlerMetrics
from butlers.core.model_call import AttemptOutcome, AttemptSink, CallSpec, Candidate, call_model
from butlers.core.model_routing import Complexity
from butlers.core.purpose_lane import (
    PURPOSE_LANE_PRIVATE_CONTENT,
    PURPOSE_LANE_STANDARD,
)
from butlers.core.runtimes.base import (
    RuntimeAdapter,
    validated_session_timeout_overhead_s,
)
from butlers.core.runtimes.registry import AdapterRegistry
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT: int = 4
_DEFAULT_TIMEOUT_S: float = 30.0

_PURPOSE_LANES = frozenset({PURPOSE_LANE_STANDARD, PURPOSE_LANE_PRIVATE_CONTENT})

# bu-svqpzo.14 [decision]: the owner's monthly spend ceiling binds API fast lanes too, so a
# discretion call is refused (and a fleet-halt quota_skip row written) once month-to-date
# spend reaches it, exactly as a spawned session is.
_ENFORCE_MONTHLY_CEILING: bool = True


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


class _AuthHealthSink(AttemptSink):
    """Kernel sink that also keeps the dispatcher's reactive auth-health bookkeeping."""

    def __init__(self, dispatcher: DiscretionDispatcher) -> None:
        super().__init__(dispatcher.pool, metrics=dispatcher._metrics)
        self._dispatcher = dispatcher

    def on_attempt_start(self, candidate: Candidate) -> None:
        self._dispatcher._last_runtime_type = candidate.runtime_type

    def on_runtime_failure(self, candidate: Candidate, decision: FailoverDecision) -> None:
        # bu-ur7go: a genuine provider/auth-classified failure (e.g. a missing or revoked
        # ~/.codex/auth.json — see bu-ofo3i) is recorded regardless of failover eligibility so
        # connector /status and discretion_auth_failures_total surface it. bu-ujm9d gave
        # connection refused / service unavailable / bad gateway the distinct
        # "provider_unavailable" prefix, so a network blip does not match here.
        if decision.reason.startswith("provider_auth_error"):
            self._dispatcher._last_auth_failure_at = time.time()
            self._dispatcher._last_auth_failure_reason = decision.reason
            discretion_auth_failures_total.labels(runtime_type=candidate.runtime_type).inc()


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
        purpose_lane: str = PURPOSE_LANE_STANDARD,
        credential_store: CredentialStore | None = None,
        codex_auth_authority: CredentialStore | None = None,
    ) -> None:
        if purpose_lane not in _PURPOSE_LANES:
            raise ValueError(f"Unsupported purpose_lane: {purpose_lane!r}")
        self._pool = pool
        self._credential_store = credential_store
        self._codex_auth_authority = codex_auth_authority
        self._butler_name = butler_name
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._timeout_s = timeout_s
        self._complexity_tier = complexity_tier
        self._purpose_lane = purpose_lane
        self._adapters = AdapterRegistry(
            codex_authority=codex_auth_authority,
            butler_name=butler_name,
            **({"credential_store": credential_store} if credential_store is not None else {}),
        )
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
        """Return the cached adapter for *runtime_type* from the shared adapter registry."""
        return self._adapters.for_runtime(runtime_type, provider_config)

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

        Resolution, the spend gate, failover and accounting run in
        :func:`~butlers.core.model_call.call_model` at ``complexity_tier``; this method
        supplies the attempt: acquire the concurrency semaphore and invoke the adapter
        single-turn, with no tools, under ``asyncio.wait_for``.

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
            If ``public.model_catalog`` contains no enabled entry for the tier
            (:class:`~butlers.core.model_call.NoModelConfigured`), if the monthly spend
            ceiling refuses the call (:class:`~butlers.core.model_call.SpendCeilingReached`),
            or if every same-tier candidate fails or is quota-skipped; that message starts
            with ``same_tier_failover_exhausted`` so callers and logs can tell "one attempt
            failed" from "every same-tier candidate failed".
        asyncio.TimeoutError
            If the adapter invocation exceeds ``timeout_s`` and no further same-tier
            candidate is eligible or available. Any other ineligible failure re-raises its
            original exception.
        """
        private = self._purpose_lane == PURPOSE_LANE_PRIVATE_CONTENT
        spec = CallSpec(
            butler_name=self._butler_name,
            tier=self._complexity_tier,
            receipt_intent=discretion_dispatch_intent(
                self._complexity_tier,
                purpose_lane=self._purpose_lane,
            ),
            # bu-m95jq: the same literal ``purpose`` the ledger row carries, so a spend rule
            # scoped to condition={"purpose": "discretion"} matches.
            spend_rule_trigger=self._purpose_lane if private else "discretion",
            purpose_lane=self._purpose_lane,
            usage_butler_name=self._butler_name if private else identity or self._butler_name,
            usage_purpose=PURPOSE_LANE_PRIVATE_CONTENT if private else "discretion",
            redact_failures=private,
            enforce_ceiling=_ENFORCE_MONTHLY_CEILING,
            caller="DiscretionDispatcher",
        )

        async def attempt(adapter: RuntimeAdapter, candidate: Candidate) -> AttemptOutcome:
            # Thinking models (qwen3 family) default to chain-of-thought mode which produces
            # <think> tokens that get stripped, leaving empty output. Prepend /no_think to
            # disable thinking for single-turn classification tasks like discretion.
            effective_prompt = (
                f"/no_think\n{prompt}" if _needs_no_think(candidate.model_id) else prompt
            )
            async with self._semaphore:
                outer_timeout_s = candidate.session_timeout_s + (
                    validated_session_timeout_overhead_s(adapter)
                )
                result_text, _tool_calls, usage = await asyncio.wait_for(
                    adapter.invoke(
                        prompt=effective_prompt,
                        system_prompt=system_prompt,
                        mcp_servers={},
                        env=_minimal_env(),
                        max_turns=1,
                        model=candidate.model_id,
                        runtime_args=candidate.extra_args or None,
                        timeout=candidate.session_timeout_s,
                    ),
                    timeout=outer_timeout_s,
                )
            return AttemptOutcome(result_text or "", usage)

        result = await call_model(
            self._pool,
            spec,
            attempt,
            adapter_for=self._adapter_for,
            sink=_AuthHealthSink(self),
        )
        self._last_success_at = time.time()
        return result.text

    async def _adapter_for(self, candidate: Candidate) -> RuntimeAdapter:
        provider_config = await self._resolve_provider_config(candidate.model_id)
        return self._get_or_create_adapter(candidate.runtime_type, provider_config)

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
