"""Trusted, module-owned canonical preparation before approval admission."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from butlers.core.approvals_hooks import DecisionDossier


class CanonicalApprovalPreparer(Protocol):
    """Registered server behavior, never an object supplied by an MCP caller."""

    public_arguments: frozenset[str]

    async def prepare_and_park(
        self,
        *,
        tool_args: dict[str, Any],
        requested_at: datetime,
        expires_at: datetime,
        dossier: DecisionDossier,
    ) -> dict[str, Any]:
        """Atomically bind the owning command and its ordinary pending action."""
        ...
