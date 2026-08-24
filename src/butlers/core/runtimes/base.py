"""RuntimeAdapter ABC and adapter registry.

Defines the abstract interface that all runtime adapters must implement,
plus a registry/factory function that maps runtime type strings
(e.g. 'claude', 'codex', 'gemini') to adapter classes.
"""

from __future__ import annotations

import abc
import logging
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

# Default runtime adapter type used to seed every Spawner's adapter pool.
# There is no roster-level or per-butler differentiation: every live butler
# boots into the same default adapter. Per-session overrides come from
# ``public.model_catalog`` at spawn time — not from ``butler.toml``.
DEFAULT_RUNTIME_TYPE = "codex"

logger = logging.getLogger(__name__)


class RuntimeAdapter(abc.ABC):
    """Abstract base class for runtime adapters.

    A runtime adapter encapsulates how a specific AI runtime (Claude Code,
    Codex, Gemini CLI, etc.) is invoked. The spawner delegates to the
    adapter so that butler core stays runtime-agnostic.
    """

    #: Whether this adapter's ``invoke()`` accepts a ``resume_session_id``
    #: kwarg to continue a prior provider-native session (bu-ep4ks.8) instead
    #: of cold-starting with a freshly composed system prompt. False for
    #: every adapter by default; callers MUST check this flag before passing
    #: ``resume_session_id`` -- adapters that don't support it do not accept
    #: the parameter at all, rather than silently ignoring it.
    supports_resume: bool = False

    #: Capability declarations that hold for every model reachable through this
    #: adapter -- the baseline layer a ``public.model_catalog`` row's own
    #: ``capabilities`` envelope is merged over
    #: (``butlers.core.model_capabilities.effective_capabilities``). Keys are
    #: ``ModelFeature`` values, kept as plain strings here so this module stays
    #: importable from ``model_capabilities`` without a cycle; a key outside the
    #: vocabulary is dropped with a warning rather than trusted.
    #:
    #: An adapter declares only what its integration decides. ``session_resume``
    #: is read from ``supports_resume`` above and needs no entry.  A feature left
    #: undeclared reads as UNKNOWN, which a dispatch that requires it treats as
    #: unproven -- so declare a capability the adapter genuinely cannot provide
    #: as ``False`` rather than omitting it.
    declared_capabilities: ClassVar[Mapping[str, bool]] = {}

    @property
    def session_timeout_overhead_s(self) -> float:
        """Return bounded setup/teardown time outside the execution budget.

        ``timeout`` passed to :meth:`invoke` remains the maximum time for the
        provider runtime itself.  Adapters with bounded best-effort work that
        must happen immediately before or after that runtime can declare its
        worst-case allowance here, so the Spawner's outer cancellation guard
        does not turn a successful provider execution into a session timeout.
        """
        return 0.0

    @property
    @abc.abstractmethod
    def binary_name(self) -> str:
        """Return the name of the CLI binary this adapter requires.

        Used at startup to verify the binary is on PATH via ``shutil.which``.
        """
        ...

    @abc.abstractmethod
    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        max_turns: int = 20,
        model: str | None = None,
        runtime_args: list[str] | None = None,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        """Invoke the runtime with the given prompt and configuration.

        Parameters
        ----------
        prompt:
            The user prompt to send to the runtime.
        system_prompt:
            System-level instructions for the runtime.
        mcp_servers:
            MCP server configurations the runtime should connect to.
        env:
            Environment variables to pass to the runtime process. Adapters
            MUST treat this mapping as caller-owned and derive a private copy
            before adding runtime-specific subprocess variables.
        runtime_args:
            Optional additional CLI arguments configured for the runtime.
        cwd:
            Working directory for the runtime process.
        timeout:
            Maximum execution time in seconds.

        Returns
        -------
        tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]
            A tuple of (result_text, tool_calls, usage). Usage is a dict
            with keys ``input_tokens``, ``output_tokens``, and optionally
            ``cache_read_input_tokens`` / ``cache_creation_input_tokens``,
            or None if the runtime does not report token usage.

            Token-bucket contract (all adapters MUST follow it — cost
            estimation prices each bucket at a different rate):

            - ``input_tokens``: UNCACHED input tokens only. Cache reads and
              cache writes are excluded, even when the vendor's native
              counter folds them in (e.g. OpenAI ``prompt_tokens`` includes
              cached tokens — adapters must subtract them out).
            - ``cache_read_input_tokens``: input tokens served from a
              prompt cache.
            - ``cache_creation_input_tokens``: input tokens written to a
              prompt cache.
        """
        ...

    @abc.abstractmethod
    def build_config_file(
        self,
        mcp_servers: dict[str, Any],
        tmp_dir: Path,
    ) -> Path:
        """Write a runtime-specific config file into tmp_dir.

        Different runtimes may require different config file formats
        (e.g. mcp.json for Claude Code, a YAML file for others).

        Parameters
        ----------
        mcp_servers:
            MCP server configurations to include in the config file.
        tmp_dir:
            Temporary directory to write the config file into.

        Returns
        -------
        Path
            Path to the generated config file.
        """
        ...

    @abc.abstractmethod
    def parse_system_prompt_file(self, config_dir: Path) -> str:
        """Read the system prompt from the butler's config directory.

        Different runtimes may use different filenames or formats
        (e.g. CLAUDE.md for Claude Code, AGENTS.md or equivalent for others).

        Parameters
        ----------
        config_dir:
            Path to the butler's config directory.

        Returns
        -------
        str
            The parsed system prompt text.
        """
        ...

    @property
    def last_process_info(self) -> dict[str, Any] | None:
        """Process-level metadata from the most recent invoke() call.

        Returns a dict with some or all of the following keys:

        Always present (subprocess adapters):
        - ``pid`` (int | None): OS process ID.
        - ``exit_code`` (int): Process exit code; ``-1`` for timeout/kill.
        - ``command`` (str): Sanitized command string for logging.
        - ``stderr`` (str): Captured stderr (may be truncated or placeholder).
        - ``runtime_type`` (str): Adapter type label (e.g. ``"codex"``).

        Failover classification fields (present on failure paths):
        - ``error_detail`` (str): Structured error detail extracted from
          stdout/stderr, preferred over raw ``stderr`` for classifier matching.
          Set on non-zero exit for all subprocess adapters.
        - ``is_pre_tool_call`` (bool): ``True`` when the failure occurred before
          the adapter itself observed any tool calls in its output stream. This
          is a best-effort signal from the adapter's parser; the spawner should
          combine it with daemon-side tool-call capture for the authoritative
          side-effect gate. Set on all failure paths (non-zero exit, timeout).

        Codex-specific:
        - ``retry_attempted`` (bool): Whether adapter-internal retries were made.
        - ``retry_succeeded`` (bool | None): Outcome of internal retries.
        - ``attempt_count`` (int): Total subprocess spawns for this invocation.
        - ``mcp_connection_failed`` (bool): Whether MCP discovery failed.
        - ``result_source`` (str): Which attempt produced the result.
        - ``spawn_latency_ms`` (int): Total spawn-to-completion latency.
        - ``mcp_server_count`` (int): Number of MCP servers configured.

        Available after invoke() completes for subprocess-based adapters.
        SDK-based adapters may return None or partial info.
        """
        return None

    def create_worker(self) -> RuntimeAdapter:
        """Return a worker-scoped adapter instance for pooled spawner workers.

        Default implementation returns ``self`` which is sufficient for
        stateless adapters. Stateful adapters may override to return a fresh
        instance per worker.
        """
        return self

    async def reset(self) -> None:
        """Reset adapter/provider state after a failed invocation.

        The spawner calls this after unsuccessful sessions so adapters that
        cache provider clients can clear stale state before the next request.
        Default implementation is a no-op.
        """
        return None

    async def speculative_prewarm(self) -> None:
        """Optional fire-and-forget warmup hook, called OFF the ``invoke()`` critical path.

        Speculative prewarm (bu-ep4ks.13 follow-up / bu-k9te9, slice 4): as soon as the
        spawner knows which runtime_type it will dispatch to, it fires this speculatively
        (``asyncio.create_task``, never awaited on the dispatch path) so any expensive
        one-time-per-process warmup an adapter needs (e.g. ``CodexAdapter``'s token
        refresh) has already happened by the time ``invoke()`` actually runs -- ``invoke()``
        then finds nothing left to do on its own (already-safe, already-idempotent)
        pre-invocation warmup check.

        Default implementation is a no-op (most adapters have nothing to speculatively
        warm). Overriding adapters MUST keep this best-effort: never raise, never block
        meaningfully, and never write ``public.model_dispatch_attempts`` provenance -- a
        prewarm failure must never fail, delay, or alter a real dispatch.
        """
        return None


def validated_session_timeout_overhead_s(runtime: RuntimeAdapter) -> float:
    """Return a finite non-negative runtime setup/teardown allowance.

    Both normal Spawner sessions and direct discretion calls use this one
    adapter declaration to extend only their outer safety guard.  A malformed
    custom adapter must therefore fail closed identically in both paths.
    """
    try:
        declared = runtime.session_timeout_overhead_s
    except (AttributeError, TypeError, ValueError):
        declared = None

    if isinstance(declared, bool) or not isinstance(declared, (int, float)):
        logger.warning(
            "Runtime adapter supplied an invalid session-timeout overhead; ignoring it "
            "(runtime=%s)",
            type(runtime).__name__,
        )
        return 0.0

    allowance_s = float(declared)
    if not math.isfinite(allowance_s) or allowance_s < 0:
        logger.warning(
            "Runtime adapter supplied an invalid session-timeout overhead; ignoring it "
            "(runtime=%s)",
            type(runtime).__name__,
        )
        return 0.0
    return allowance_s


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

_ADAPTER_REGISTRY: dict[str, type[RuntimeAdapter]] = {}


def register_adapter(type_str: str, adapter_cls: type[RuntimeAdapter]) -> None:
    """Register a runtime adapter class under the given type string."""
    _ADAPTER_REGISTRY[type_str] = adapter_cls


def get_adapter(type_str: str) -> type[RuntimeAdapter]:
    """Look up an adapter class by runtime type string.

    Parameters
    ----------
    type_str:
        One of the registered runtime type strings
        (e.g. 'claude', 'codex', 'gemini').

    Returns
    -------
    type[RuntimeAdapter]
        The adapter class (not an instance).

    Raises
    ------
    ValueError
        If no adapter is registered for the given type string.
    """
    if type_str not in _ADAPTER_REGISTRY:
        available = ", ".join(sorted(_ADAPTER_REGISTRY)) or "(none)"
        raise ValueError(f"Unknown runtime type {type_str!r}. Available adapters: {available}")
    return _ADAPTER_REGISTRY[type_str]


def list_registered_runtime_types() -> tuple[str, ...]:
    """Return the sorted tuple of registered runtime adapter type strings.

    Useful for contract tests that want to project "is this name valid?"
    without having to replicate the registry's lookup logic.
    """
    return tuple(sorted(_ADAPTER_REGISTRY))


def create_adapter(
    runtime_type: str,
    *,
    provider_config: dict[str, dict[str, Any]] | None = None,
    **constructor_kwargs: Any,
) -> RuntimeAdapter:
    """Instantiate a runtime adapter with best-effort constructor kwargs.

    Resolves the adapter class via :func:`get_adapter`, then attempts
    instantiation with progressively fewer keyword arguments:

    1. All *constructor_kwargs* plus *provider_config* (if given).
    2. Only *provider_config* (if given).
    3. Bare instantiation (no arguments).

    This handles the fact that different adapter classes accept different
    constructor signatures (e.g. ``butler_name``, ``log_root``,
    ``credential_store``, ``provider_config``).

    Parameters
    ----------
    runtime_type:
        Registered runtime type string (e.g. ``"claude"``, ``"opencode"``).
    provider_config:
        Optional provider configuration forwarded to adapters that accept
        it (e.g. OpenCodeAdapter uses it to set the Ollama base URL).
    **constructor_kwargs:
        Additional keyword arguments forwarded to the adapter constructor
        (e.g. ``butler_name``, ``log_root``, ``credential_store``).

    Returns
    -------
    RuntimeAdapter
        A newly created adapter instance.

    Raises
    ------
    ValueError
        If no adapter is registered for the given runtime type string.
    """
    adapter_cls = get_adapter(runtime_type)
    full_kwargs: dict[str, Any] = dict(constructor_kwargs)
    if provider_config:
        full_kwargs["provider_config"] = provider_config
    try:
        return adapter_cls(**full_kwargs)  # type: ignore[call-arg]
    except TypeError:
        minimal: dict[str, Any] = {}
        if provider_config:
            minimal["provider_config"] = provider_config
        try:
            return adapter_cls(**minimal)  # type: ignore[call-arg]
        except TypeError:
            return adapter_cls()  # type: ignore[call-arg]
