"""DiscretionDispatcher — semaphore-gated adapter dispatcher for discretion LLM calls.

Provides a lightweight, concurrent-limited wrapper around the RuntimeAdapter
registry specifically for single-turn discretion inference.  Callers supply a
prompt and optional system prompt; the dispatcher resolves the appropriate
model from ``public.model_catalog`` at the ``Complexity.DISCRETION`` tier,
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
"""

from __future__ import annotations

import asyncio
import logging
import os

import asyncpg

from butlers.core.model_routing import (
    Complexity,
    check_token_quota,
    record_token_usage,
    resolve_model,
)
from butlers.core.runtimes.base import RuntimeAdapter, create_adapter

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT: int = 4
_DEFAULT_TIMEOUT_S: float = 30.0

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
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        butler_name: str = "__discretion__",
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._pool = pool
        self._butler_name = butler_name
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._timeout_s = timeout_s
        self._adapter_cache: dict[str, RuntimeAdapter] = {}
        self._adapter_cache_key: dict[str, str] = {}

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
    ) -> str:
        """Invoke the discretion-tier model with *prompt* and return the response text.

        Resolution order:
        1. Query ``public.model_catalog`` for ``Complexity.DISCRETION``.
        2. Raise ``RuntimeError`` if no enabled catalog entry matches.
        3. Acquire the concurrency semaphore.
        4. Invoke the adapter with ``asyncio.wait_for`` enforcing ``timeout_s``.

        Parameters
        ----------
        prompt:
            The user-facing prompt to send.
        system_prompt:
            Optional system-level instructions for the model.

        Returns
        -------
        str
            The model's response text.  Returns an empty string if the adapter
            returns ``None`` as its result.

        Raises
        ------
        RuntimeError
            If ``public.model_catalog`` contains no enabled entry for the
            ``discretion`` complexity tier.
        asyncio.TimeoutError
            If the adapter invocation exceeds ``timeout_s``.
        """
        catalog_result = await resolve_model(self._pool, self._butler_name, Complexity.DISCRETION)
        if catalog_result is None:
            raise RuntimeError(
                "No discretion model configured in public.model_catalog. "
                "Add an enabled entry with complexity_tier='discretion'."
            )

        runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s = catalog_result

        # Pre-call quota check: block if catalog entry token budget is exhausted.
        quota = await check_token_quota(self._pool, catalog_entry_id)
        if not quota.allowed:
            windows_exceeded = []
            if quota.limit_24h is not None and quota.usage_24h >= quota.limit_24h:
                windows_exceeded.append(f"24h (used={quota.usage_24h}, limit={quota.limit_24h})")
            if quota.limit_30d is not None and quota.usage_30d >= quota.limit_30d:
                windows_exceeded.append(f"30d (used={quota.usage_30d}, limit={quota.limit_30d})")
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

        async with self._semaphore:
            try:
                result = await asyncio.wait_for(_invoke(), timeout=session_timeout_s)
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
                            butler_name=self._butler_name,
                            session_id=None,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens or 0,
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
            return result
