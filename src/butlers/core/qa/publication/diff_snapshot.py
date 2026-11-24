"""Derive a QA case's diff snapshot from a sealed export, with no git over pod-written state.

An isolated attempt runs in a pod that writes its own clone, so the dispatcher never runs git
there. The sealed export already holds everything the pod added (every new tree and blob); the
only inputs it lacks are the *base* blobs and trees, which live in the dispatcher's own trusted
repository. This module reads those in-process through the same confined :class:`ObjectStore`
the sealer uses (loose objects and packfiles, no git process), walks the base and head trees
together, and renders a unified diff into the dossier's line model.

Bounds mirror the sealer: a file over ``MAX_BLOB_BYTES`` or that is not UTF-8 text becomes a
one-line placeholder rather than content, the file count and rendered lines are capped, and
the store's work budget bounds the total bytes read. Any refusal gives ``[]`` so the caller
records an empty snapshot exactly as it does today; a diff snapshot is display, never a gate.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
from dataclasses import dataclass
from pathlib import Path

from butlers.core.qa.diff import parse_unified_diff
from butlers.core.qa.publication.export import (
    MAX_BLOB_BYTES,
    MAX_EXPORT_WORK_BYTES,
    ExportRefused,
    SealedExport,
    _parse_commit,
    _parse_tree,
    _TreeEntry,
)
from butlers.core.qa.publication.object_store import ObjectStore, ObjectStoreError

logger = logging.getLogger(__name__)

#: Most changed files rendered; the rest are summarised in one trailing line.
MAX_DIFF_FILES = 200
#: Rendered line cap handed to the unified-diff parser.
MAX_DIFF_LINES = 10_000
_TREE_MODE = b"40000"
_SUBMODULE_MODE = b"160000"


@dataclass(frozen=True, slots=True)
class _Change:
    path: str
    old: str | None  # blob oid, None when the file is added
    new: str | None  # blob oid, None when the file is deleted


class _Objects:
    """Object reads: the sealed export first (head side), then the trusted base store."""

    def __init__(self, export: SealedExport, base: ObjectStore) -> None:
        self._exported = {obj.oid: obj for obj in export.objects}
        self._base = base

    def read(self, oid: str, kind: str, *, max_size: int | None = None) -> bytes:
        held = self._exported.get(oid)
        if held is not None:
            if held.kind != kind:
                raise ExportRefused("unexpected_object_type")
            return held.data
        try:
            found_kind, data = self._base.read(oid, max_size=max_size)
        except ObjectStoreError as exc:
            raise ExportRefused(exc.code) from exc
        if found_kind != kind:
            raise ExportRefused("unexpected_object_type")
        return data

    def tree(self, oid: str) -> dict[bytes, _TreeEntry]:
        return _parse_tree(self.read(oid, "tree"))


def _is_blob(entry: _TreeEntry) -> bool:
    return entry.mode != _TREE_MODE and entry.mode != _SUBMODULE_MODE


def _walk(objects: _Objects, old: str | None, new: str | None, prefix: str) -> list[_Change]:
    """Every file that differs between two trees (either may be absent)."""
    if old == new:
        return []
    old_entries = objects.tree(old) if old is not None else {}
    new_entries = objects.tree(new) if new is not None else {}
    changes: list[_Change] = []
    for name in sorted(old_entries.keys() | new_entries.keys()):
        before, after = old_entries.get(name), new_entries.get(name)
        if before is not None and after is not None:
            if before.oid == after.oid and before.mode == after.mode:
                continue
        path = prefix + name.decode("utf-8", errors="replace")
        before_tree = before is not None and before.mode == _TREE_MODE
        after_tree = after is not None and after.mode == _TREE_MODE
        if before_tree or after_tree:
            changes += _walk(
                objects,
                before.oid if before_tree and before is not None else None,
                after.oid if after_tree and after is not None else None,
                path + "/",
            )
        before_blob = before.oid if before is not None and _is_blob(before) else None
        after_blob = after.oid if after is not None and _is_blob(after) else None
        if (before_blob or after_blob) and before_blob != after_blob:
            changes.append(_Change(path, before_blob, after_blob))
    return changes


def _text(objects: _Objects, oid: str | None) -> list[str] | None:
    """The blob's lines, ``[]`` for an absent side, ``None`` when it is not displayable text."""
    if oid is None:
        return []
    try:
        data = objects.read(oid, "blob", max_size=MAX_BLOB_BYTES)
    except ExportRefused:
        return None
    if len(data) > MAX_BLOB_BYTES or b"\0" in data:
        return None
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _render(objects: _Objects, changes: list[_Change]) -> str:
    out: list[str] = []
    for change in changes[:MAX_DIFF_FILES]:
        out.append(f"diff --git a/{change.path} b/{change.path}")
        old_lines, new_lines = _text(objects, change.old), _text(objects, change.new)
        if old_lines is None or new_lines is None:
            out.append(f"Binary or oversize file not shown: {change.path}")
            continue
        old_label = "/dev/null" if change.old is None else f"a/{change.path}"
        new_label = "/dev/null" if change.new is None else f"b/{change.path}"
        out.extend(
            difflib.unified_diff(old_lines, new_lines, old_label, new_label, n=3, lineterm="")
        )
    if len(changes) > MAX_DIFF_FILES:
        out.append(f"... ({len(changes) - MAX_DIFF_FILES} more changed files not shown)")
    return "\n".join(out)


def _derive(export: SealedExport, base_objects_dir: Path) -> str:
    store = ObjectStore(base_objects_dir, work_budget=MAX_EXPORT_WORK_BYTES)
    with store:
        objects = _Objects(export, store)
        base_tree = _parse_commit(objects.read(export.base_sha, "commit")).tree
        head_tree = _parse_commit(objects.read(export.head_sha, "commit")).tree
        return _render(objects, _walk(objects, base_tree, head_tree, ""))


async def derive_diff_snapshot(
    export: SealedExport, base_objects_dir: Path
) -> list[dict[str, str]]:
    """The net ``base..head`` diff of a sealed export, in the dossier's line model.

    ``base_objects_dir`` is the dispatcher's own trusted object directory (it must hold the
    export's base commit and the base blobs); the pod-written clone is never read. Returns
    ``[]`` on any refusal or error.
    """
    try:
        text = await asyncio.to_thread(_derive, export, base_objects_dir)
    except (ExportRefused, ObjectStoreError, ValueError, IndexError, OSError) as exc:
        logger.warning("Could not derive the QA diff snapshot from the sealed export: %s", exc)
        return []
    return [line.model_dump(mode="json") for line in parse_unified_diff(text, MAX_DIFF_LINES)]
