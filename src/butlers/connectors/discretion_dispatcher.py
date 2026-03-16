"""DiscretionDispatcher — semaphore-gated adapter dispatcher for discretion LLM calls.

Provides a lightweight, concurrent-limited wrapper around the RuntimeAdapter
registry specifically for single-turn discretion inference.  Callers supply a
prompt and optional system prompt; the dispatcher resolves the appropriate
model from ``shared.model_catalog`` at the ``Complexity.DISCRETION`` tier,
lazily instantiates the matching adapter, and invokes it with no tools and
a strict timeout.

Usage::

    dispatcher = DiscretionDispatcher(pool=db_pool)
    response = await dispatcher.call("Is this spam?", system_prompt="Reply YES or NO.")

Design notes
------------
- Adapter instances are cached per ``runtime_type`` (same pattern as
  ``Spawner._get_or_create_adapter``).
- Model resolution is performed on every call so catalog updates take effect
  without restarting the dispatcher.
- ``asyncio.wait_for`` enforces the per-call wall-clock timeout; the inner
  adapter invocation may also have its own timeout, but the outer guard is
  the authoritative limit.
- ``mcp_servers={}``, ``env={}``, and ``max_turns=1`` are always passed to
  the adapter — discretion calls are single-turn with no tool access.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from butlers.core.model_routing import Complexity, resolve_model
from butlers.core.runtimes.base import RuntimeAdapter, get_adapter

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT: int = 4
_DEFAULT_TIMEOUT_S: float = 5.0


class DiscretionDispatcher:
    """Semaphore-gated adapter dispatcher for discretion-tier LLM calls.

    Parameters
    ----------
    pool:
        An asyncpg connection pool used to resolve the discretion model from
        ``shared.model_catalog``.
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
        # Adapter instance cache keyed by runtime_type string.
        self._adapter_cache: dict[str, RuntimeAdapter] = {}

    def _get_or_create_adapter(self, runtime_type: str) -> RuntimeAdapter:
        """Return a cached adapter instance for *runtime_type*, creating lazily.

        Follows the same best-effort constructor pattern as
        ``Spawner._get_or_create_adapter``: tries with ``butler_name`` kwarg
        first, falls back to bare instantiation for adapters that don't
        accept it.

        Raises
        ------
        ValueError
            If no adapter is registered for the given runtime type string.
        """
        if runtime_type in self._adapter_cache:
            return self._adapter_cache[runtime_type]

        adapter_cls = get_adapter(runtime_type)
        try:
            adapter: RuntimeAdapter = adapter_cls(butler_name=self._butler_name)  # type: ignore[call-arg]
        except TypeError:
            adapter = adapter_cls()

        self._adapter_cache[runtime_type] = adapter
        logger.debug(
            "DiscretionDispatcher: lazily instantiated adapter runtime_type=%s", runtime_type
        )
        return adapter

    async def call(
        self,
        prompt: str,
        system_prompt: str = "",
    ) -> str:
        """Invoke the discretion-tier model with *prompt* and return the response text.

        Resolution order:
        1. Query ``shared.model_catalog`` for ``Complexity.DISCRETION``.
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
            If ``shared.model_catalog`` contains no enabled entry for the
            ``discretion`` complexity tier.
        asyncio.TimeoutError
            If the adapter invocation exceeds ``timeout_s``.
        """
        catalog_result = await resolve_model(self._pool, self._butler_name, Complexity.DISCRETION)
        if catalog_result is None:
            raise RuntimeError(
                "No discretion model configured in shared.model_catalog. "
                "Add an enabled entry with complexity_tier='discretion'."
            )

        runtime_type, model_id, extra_args = catalog_result
        adapter = self._get_or_create_adapter(runtime_type)

        async def _invoke() -> str:
            result_text, _tool_calls, _usage = await adapter.invoke(
                prompt=prompt,
                system_prompt=system_prompt,
                mcp_servers={},
                env={},
                max_turns=1,
                model=model_id,
                runtime_args=extra_args or None,
            )
            return result_text or ""

        async with self._semaphore:
            return await asyncio.wait_for(_invoke(), timeout=self._timeout_s)
