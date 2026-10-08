"""Preflight protected core downgrade boundaries before newer revisions mutate."""

from __future__ import annotations

import importlib.util
import re
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

from butlers.migrations import _chain_script_directory, get_all_chains, get_chain_revision_ids

_RUNTIME_ATTENTION_REVISION = "core_198"
_RUNTIME_ATTENTION_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_198_runtime_attention_outbox.py"
)


def _destination_revisions(context: Any) -> tuple[str | None, ...]:
    raw = context.get_revision_argument()
    if isinstance(raw, tuple):
        return raw
    return (raw,)


def _downgrade_crosses_runtime_attention(context: Any) -> bool:
    """Use the checked-in revision graph to determine whether core_198 is crossed."""
    # A per-schema version table legitimately contains independent core,
    # module and roster heads. The singular Alembic API rejects that supported
    # topology before the protected boundary can decide anything.
    current_heads = tuple(context.get_context().get_current_heads())
    known = {revision for chain in get_all_chains() for revision in get_chain_revision_ids(chain)}
    if len(set(current_heads)) != len(current_heads) or any(
        not isinstance(head, str) or head not in known for head in current_heads
    ):
        raise RuntimeError("Protected downgrade has unknown or duplicate current revisions")
    core_ids = get_chain_revision_ids("core")
    core_heads = [head for head in current_heads if head in core_ids]
    if len(core_heads) > 1:
        raise RuntimeError("Protected downgrade has ambiguous current core revisions")
    if not core_heads:
        return False
    current = core_heads[0]
    script = _chain_script_directory("core")
    for destination in _destination_revisions(context):
        if isinstance(destination, str):
            relative = re.fullmatch(r"-(\d+)", destination)
            if relative is not None:
                steps = list(script.iterate_revisions(current, None))
                count = int(relative.group(1))
                if count > len(steps):
                    raise RuntimeError("Protected downgrade relative target exceeds core history")
                if any(step.revision == _RUNTIME_ATTENTION_REVISION for step in steps[:count]):
                    return True
                continue
            if destination in known and destination not in core_ids:
                # Preserve foreign branch version rows; their own downgrade
                # semantics cannot remove the protected core interface.
                continue
        revisions = script.iterate_revisions(current, destination)
        if any(revision.revision == _RUNTIME_ATTENTION_REVISION for revision in revisions):
            return True
    return False


@lru_cache(maxsize=1)
def _runtime_attention_migration() -> ModuleType:
    """Load the protected revision as the single authority for its catalog proof."""
    spec = importlib.util.spec_from_file_location(
        "_butlers_core_198_runtime_attention_preflight",
        _RUNTIME_ATTENTION_MIGRATION,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("core_198 protected migration could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preflight_runtime_attention_downgrade(op: Any, context: Any) -> None:
    """Refuse a protected downgrade before any newer revision changes state.

    ``core_231`` contains an autocommit boundary. Waiting for ``core_198`` to
    reject an unauthorized rollback would therefore commit intervening
    downgrades and leave both the version stamp and newer evidence partially
    removed. Reuse core_198's exact lock, absence, bootstrap-authority, and
    finalized-interface proofs while the schema is still at its original head.
    """
    if not _downgrade_crosses_runtime_attention(context):
        return

    protected = _runtime_attention_migration()
    bind = op.get_bind()
    if protected.protected_rollback_preflight_passes(bind):
        return
    raise RuntimeError(
        "newer core revision cannot begin downgrade work because the protected "
        "core_198 rollback preflight failed"
    )
