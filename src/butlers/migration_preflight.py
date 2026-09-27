"""Preflight protected core downgrade boundaries before newer revisions mutate."""

from __future__ import annotations

import importlib.util
import re
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

from butlers.migrations import _chain_script_directory

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
