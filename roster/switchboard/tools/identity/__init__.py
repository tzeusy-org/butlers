"""Switchboard identity resolution and prompt injection.

Provides ``resolve_and_inject_identity`` — the single entry point used by the
Switchboard's message ingestion path to:

1. Resolve the sender's channel identifier to a known contact.
2. Create a temporary contact (with ``needs_disambiguation=true``) for unknown senders.
3. Build the structured identity preamble injected at the start of every routed prompt.
4. Notify the owner once per new unknown sender.
"""

from butlers.tools.switchboard.identity.inject import (
    IdentityResolutionResult,
    resolve_and_inject_identity,
)

__all__ = [
    "IdentityResolutionResult",
    "resolve_and_inject_identity",
]
