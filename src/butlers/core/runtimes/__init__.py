"""Runtime adapter abstraction layer.

Provides the RuntimeAdapter ABC and a registry/factory for looking up
adapter classes by runtime type string.
"""

from butlers.core.model_capabilities import set_adapter_lookup
from butlers.core.runtimes.api import ApiAdapter
from butlers.core.runtimes.base import (
    DEFAULT_RUNTIME_TYPE,
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
    "DEFAULT_RUNTIME_TYPE",
    "ApiAdapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "GeminiAdapter",
    "OpenCodeAdapter",
    "RuntimeAdapter",
    "create_adapter",
    "get_adapter",
    "list_registered_runtime_types",
    "register_adapter",
    "set_adapter_lookup",
]

# Hand the registry lookup to ``model_capabilities`` rather than letting it reach
# back in here for one. The edge has to point this way: that module sits in the
# import graph of the Finder endpoint (``/entities/search``), which Brief 6b
# Amendment 15 requires to be provably free of LLM SDKs, and this package imports
# ``anthropic`` via ``ApiAdapter``. Installing from the package __init__ rather
# than from ``base`` keeps the old guarantee that a non-None lookup means every
# in-tree adapter above is already registered.
set_adapter_lookup(get_adapter)
