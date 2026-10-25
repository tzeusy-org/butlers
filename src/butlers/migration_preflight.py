"""Preflight protected core downgrade boundaries before newer revisions mutate."""

from __future__ import annotations

import re
from typing import Any

from butlers.db_authority.interfaces import require_boundary_absence
from butlers.migrations import _chain_script_directory

_RUNTIME_ATTENTION_REVISION = "core_198"


def _destination_revisions(context: Any) -> tuple[str | None, ...]:
    raw = context.get_revision_argument()
    if isinstance(raw, tuple):
        return raw
    return (raw,)


def _downgrade_crosses_runtime_attention(context: Any) -> bool:
    """Use the checked-in revision graph to determine whether core_198 is crossed."""
    current = context.get_context().get_current_revision()
    if current is None:
        return False
    script = _chain_script_directory("core")
    for destination in _destination_revisions(context):
        if isinstance(destination, str):
            relative = re.fullmatch(r"-(\d+)", destination)
            if relative is not None:
                revisions_above_core_198 = sum(
                    1 for _step in script.iterate_revisions(current, _RUNTIME_ATTENTION_REVISION)
                )
                if int(relative.group(1)) > revisions_above_core_198:
                    return True
                continue
        revisions = script.iterate_revisions(current, destination)
        if any(revision.revision == _RUNTIME_ATTENTION_REVISION for revision in revisions):
            return True
    return False


def preflight_runtime_attention_downgrade(op: Any, context: Any) -> None:
    """Refuse a protected downgrade before any newer revision changes state.

    ``core_231`` contains an autocommit boundary. The current source-owned
    protected guard must run while the original version/data are still intact.
    A pinned operator rollback may establish verified absence first; an ordinary
    version downgrade never substitutes for that evidence-bearing operation.
    No dynamic historical loader or retired bootstrap singleton is authority.
    """
    if not _downgrade_crosses_runtime_attention(context):
        return

    require_boundary_absence(op.get_bind(), "attention")
