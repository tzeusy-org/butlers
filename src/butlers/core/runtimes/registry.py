"""The one place a runtime adapter is constructed (bu-m0vjeb.1).

Every daemon-side adapter factory (the Spawner's adapter pool, the connector discretion
dispatcher, the runtime-probe coordinator, and the model-call kernel) builds adapters here, so
the Codex special case lives in exactly one function.

Codex does not take the generic provider or log-root constructor options. Passing them through
:func:`~butlers.core.runtimes.base.create_adapter` would trip its compatibility fallback, which
retries with fewer arguments and so drops the explicit Codex authority with them. Codex is
therefore built from ``butler_name`` and its explicitly supplied authority only; a missing
authority still fails closed inside ``CodexAdapter``. Every other runtime receives the caller's
constructor options and the resolved provider configuration unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from butlers.core.runtimes import base
from butlers.core.runtimes.base import RuntimeAdapter

logger = logging.getLogger(__name__)

CODEX_RUNTIME_TYPE = "codex"


def takes_provider_config(runtime_type: str) -> bool:
    """Whether the runtime's adapter consumes resolved provider configuration (not Codex)."""
    return runtime_type != CODEX_RUNTIME_TYPE


def lacks_required_authority(runtime_type: str, codex_authority: Any) -> bool:
    """True for a Codex runtime whose explicit authority is not system-global.

    Such an adapter would fail closed; callers that must not record that as a model failure
    (for example a probe) check this first.
    """
    return runtime_type == CODEX_RUNTIME_TYPE and not getattr(
        codex_authority, "has_system_global_authority", False
    )


def build_runtime_adapter(
    runtime_type: str,
    *,
    codex_authority: Any = None,
    provider_config: dict[str, dict[str, Any]] | None = None,
    **constructor_kwargs: Any,
) -> RuntimeAdapter:
    """Construct a fresh adapter for ``runtime_type``.

    ``codex_authority`` is the credential store a Codex adapter receives; callers pass the
    authority they hold explicitly (never inferred). ``constructor_kwargs`` (``butler_name``,
    ``log_root``, ``credential_store``, ...) go to every other runtime as given; for Codex only
    ``butler_name`` is kept.
    """
    if runtime_type == CODEX_RUNTIME_TYPE:
        kwargs = {k: constructor_kwargs[k] for k in ("butler_name",) if k in constructor_kwargs}
        return base.create_adapter(runtime_type, credential_store=codex_authority, **kwargs)
    return base.create_adapter(runtime_type, provider_config=provider_config, **constructor_kwargs)


class AdapterRegistry:
    """Adapters for one caller's construction context, cached per runtime and provider config.

    A cached adapter is reused only while the provider configuration it was built with is
    unchanged; a different configuration rebuilds it.
    """

    def __init__(self, *, codex_authority: Any = None, **constructor_kwargs: Any) -> None:
        self._codex_authority = codex_authority
        self._constructor_kwargs = constructor_kwargs
        self._cache: dict[str, RuntimeAdapter] = {}
        self._cache_key: dict[str, str] = {}

    def for_runtime(
        self,
        runtime_type: str,
        provider_config: dict[str, dict[str, Any]] | None = None,
    ) -> RuntimeAdapter:
        """Return the cached adapter for ``runtime_type``, building it on a cache miss."""
        cfg_str = str(provider_config) if provider_config else ""
        if runtime_type in self._cache and self._cache_key.get(runtime_type, "") == cfg_str:
            return self._cache[runtime_type]
        adapter = build_runtime_adapter(
            runtime_type,
            codex_authority=self._codex_authority,
            provider_config=provider_config,
            **self._constructor_kwargs,
        )
        self._cache[runtime_type] = adapter
        self._cache_key[runtime_type] = cfg_str
        logger.debug("AdapterRegistry: instantiated adapter runtime_type=%s", runtime_type)
        return adapter
