"""Runtime adapter abstraction layer.

Provides the RuntimeAdapter ABC and a registry/factory for looking up
adapter classes by runtime type string.
"""

from butlers.core.runtimes.base import (
    RuntimeAdapter,
    create_adapter,
    get_adapter,
    list_registered_runtime_types,
    register_adapter,
)
from butlers.core.runtimes.claude_code import ClaudeCodeAdapter
from butlers.core.runtimes.codex import CodexAdapter
from butlers.core.runtimes.gemini import GeminiAdapter
from butlers.core.runtimes.opencode import OpenCodeAdapter

__all__ = [
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "GeminiAdapter",
    "OpenCodeAdapter",
    "RuntimeAdapter",
    "create_adapter",
    "get_adapter",
    "list_registered_runtime_types",
    "register_adapter",
]
