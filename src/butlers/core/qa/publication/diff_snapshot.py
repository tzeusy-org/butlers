"""Derive a QA case's diff snapshot from a sealed export, with no git over pod-written state.

An isolated attempt runs in a pod that writes its own clone, so the dispatcher never runs git
there. The sealed export already holds everything the pod added (every new tree and blob); the
only inputs it lacks are the *base* blobs and trees, which live in the dispatcher's own trusted
repository. This module reads those in-process through the same confined :class:`ObjectStore`
the sealer uses (loose objects and packfiles, no git process), walks the base and head trees
together, and renders a unified diff into the dossier's line model.

Bounds mirror the sealer: a file over ``MAX_DISPLAY_BYTES`` or that is not UTF-8 text becomes a
one-line placeholder rather than content, the file count, tree depth and rendered lines are
capped, and the store's work budget bounds the total bytes read. Every base object is re-hashed
against its id, because the trusted repository is also writable through non-isolated worktrees.
Any refusal or error gives ``[]`` so the caller records an empty snapshot exactly as it does
today; a diff snapshot is display, never a gate.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
from dataclasses import dataclass
from pathlib import Path

from opentelemetry.metrics import Counter

from butlers.core.metrics import get_meter
from butlers.core.qa.diff import parse_unified_diff
from butlers.core.qa.publication.export import (
    MAX_EXPORT_WORK_BYTES,
    MAX_TREE_DEPTH,
    ExportRefused,
    SealedExport,
    _object_id,
    _parse_commit,
    _parse_tree,
    _TreeEntry,
)
from butlers.core.qa.publication.object_store import ObjectStore, ObjectStoreError

logger = logging.getLogger(__name__)

#: Most changed files rendered; the rest are summarised in one trailing line.
MAX_DIFF_FILES = 200
#: Rendered line cap; rendering stops once it is reached, so work never scales with the export.
MAX_DIFF_LINES = 10_000
#: Largest file side that is diffed for display. difflib's matcher is quadratic in the worst
#: case, so this stays well below the sealer's 1 MiB blob bound: 256 KiB is a few thousand lines,
#: far more than a reviewable QA fix, and bounds the CPU one hostile file can cost.
MAX_DISPLAY_BYTES = 256 << 10
_TREE_MODE = b"40000"
_SUBMODULE_MODE = b"160000"


_failures: Counter | None = None


def record_snapshot_failure(stage: str, exc: BaseException) -> None:
    """Log (class name or refusal code only) and count one snapshot failure.

    The message is never logged: object-store and parser errors can echo hostile content.
    """
    global _failures
    reason = exc.code if isinstance(exc, ExportRefused | ObjectStoreError) else type(exc).__name__
    logger.warning("Could not derive the QA diff snapshot (%s): %s", stage, reason)
    try:
        if _failures is None:
            _failures = get_meter().create_counter(
                name="butlers.qa.diff_snapshot_failures_total",
                description="Isolated-attempt diff snapshots that could not be derived",
                unit="failures",
            )
        _failures.add(1, {"stage": stage})
    except Exception:  # metrics must never break dispatch
        logger.debug("diff snapshot failure counter unavailable", exc_info=True)


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
        # The store's contract is that callers re-hash: this repository is agent-writable
        # through the non-isolated worktrees, so a base object is trusted only by its id.
        if _object_id(found_kind, data) != oid:
            raise ExportRefused("object_hash_mismatch")
        return data

    def tree(self, oid: str) -> dict[bytes, _TreeEntry]:
        return _parse_tree(self.read(oid, "tree"))


def _is_blob(entry: _TreeEntry) -> bool:
    return entry.mode != _TREE_MODE and entry.mode != _SUBMODULE_MODE


def _walk(objects: _Objects, old_root: str, new_root: str) -> list[_Change]:
    """Every file that differs between two trees, iteratively and with a depth cap."""
    changes: list[_Change] = []
    stack: list[tuple[str | None, str | None, str, int]] = [(old_root, new_root, "", 0)]
    while stack:
        old, new, prefix, depth = stack.pop()
        if old == new:
            continue
        if depth > MAX_TREE_DEPTH:
            raise ExportRefused("tree_too_deep")
        old_entries = objects.tree(old) if old is not None else {}
        new_entries = objects.tree(new) if new is not None else {}
        subtrees: list[tuple[str | None, str | None, str, int]] = []
        for name in sorted(old_entries.keys() | new_entries.keys()):
            before, after = old_entries.get(name), new_entries.get(name)
            if before is not None and after is not None:
                if before.oid == after.oid and before.mode == after.mode:
                    continue
            path = prefix + name.decode("utf-8", errors="replace")
            before_tree = before is not None and before.mode == _TREE_MODE
            after_tree = after is not None and after.mode == _TREE_MODE
            if before is not None and after is not None and before_tree and after_tree:
                subtrees.append((before.oid, after.oid, path + "/", depth + 1))
            elif before_tree or after_tree:
                subtrees.append(
                    (
                        before.oid if before is not None and before_tree else None,
                        after.oid if after is not None and after_tree else None,
                        path + "/",
                        depth + 1,
                    )
                )
            before_blob = before.oid if before is not None and _is_blob(before) else None
            after_blob = after.oid if after is not None and _is_blob(after) else None
            if (before_blob or after_blob) and before_blob != after_blob:
                changes.append(_Change(path, before_blob, after_blob))
        stack.extend(reversed(subtrees))
    changes.sort(key=lambda change: change.path)
    return changes


_BINARY = "Binary file not shown"
_TOO_LARGE = "File too large to show"
_UNAVAILABLE = "Base version unavailable"


def _text(objects: _Objects, oid: str | None) -> list[str] | str:
    """The blob's lines (``[]`` for an absent side), or the placeholder reason to show."""
    if oid is None:
        return []
    try:
        data = objects.read(oid, "blob", max_size=MAX_DISPLAY_BYTES)
    except ExportRefused as exc:
        if exc.code == "object_too_large":
            return _TOO_LARGE
        if exc.code == "object_missing":
            return _UNAVAILABLE
        raise  # tampering or an unsafe store aborts the whole derivation
    if len(data) > MAX_DISPLAY_BYTES:
        return _TOO_LARGE
    if b"\0" in data:
        return _BINARY
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return _BINARY


def _render(objects: _Objects, changes: list[_Change]) -> str:
    out: list[str] = []
    for change in changes[:MAX_DIFF_FILES]:
        if len(out) >= MAX_DIFF_LINES:
            out.append("... (diff truncated)")
            break
        out.append(f"diff --git a/{change.path} b/{change.path}")
        old_lines, new_lines = _text(objects, change.old), _text(objects, change.new)
        shown = [side for side in (old_lines, new_lines) if isinstance(side, str)]
        if shown:
            out.append(f"{shown[0]}: {change.path}")
            continue
        assert not isinstance(old_lines, str) and not isinstance(new_lines, str)
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
        return _render(objects, _walk(objects, base_tree, head_tree))


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
    except Exception as exc:  # noqa: BLE001 — display only: a hostile export must not fail dispatch
        record_snapshot_failure("derive", exc)
        return []
    return [line.model_dump(mode="json") for line in parse_unified_diff(text, MAX_DIFF_LINES)]
