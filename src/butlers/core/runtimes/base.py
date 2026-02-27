"""RuntimeAdapter ABC and adapter registry.

Defines the abstract interface that all runtime adapters must implement,
plus a registry/factory function that maps runtime type strings
(e.g. 'claude-code', 'codex', 'gemini') to adapter classes.
"""

from __future__ import annotations

import abc
from pathlib import Path
from typing import Any


class RuntimeAdapter(abc.ABC):
    """Abstract base class for runtime adapters.

    A runtime adapter encapsulates how a specific AI runtime (Claude Code,
    Codex, Gemini CLI, etc.) is invoked. The spawner delegates to the
    adapter so that butler core stays runtime-agnostic.
    """

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
            Environment variables to pass to the runtime process.
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
            with keys like ``input_tokens``, ``output_tokens``, etc.,
            or None if the runtime does not report token usage.
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
        (e.g. 'claude-code', 'codex', 'gemini').

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
