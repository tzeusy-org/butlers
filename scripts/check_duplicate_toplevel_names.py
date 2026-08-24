#!/usr/bin/env python3
"""
check_duplicate_toplevel_names.py

Fail when one Python file binds the same module-level name twice.

Why this exists
---------------
Two concurrent branches added a module-level helper named ``_fenced_code_blocks``
to the same test file, with incompatible return types (``list[str]`` and
``str``). The definitions were nowhere near each other, so git auto-merged them
cleanly and the later one silently shadowed the earlier one for every caller --
including the earlier branch's own security guard for
REQ-database-security-006. The guard did not crash: handed a ``str`` it iterated
the *characters* of that string, its membership test was never true, and it
returned ``[]`` for every input. A runnable ``pg_restore.sh`` inside a fenced
code block -- exactly the escape hatch that requirement exists to block -- would
have passed unnoticed (bu-ayrbg, found while merge-testing #3808 against #3839).

``ruff check`` reports "All checks passed!" on that tree. F811 (redefinition of
unused name) does not fire, because the FIRST definition IS used between the two
definitions, so the name is not "unused" at the point of redefinition. That
intervening-use condition is F811's blind spot, and it is precisely the shape a
merge produces: each branch defines the name and uses it, then the union has two
definitions with uses in between. Neither branch's CI could see it either --
each is green in isolation and CI never builds the union of open PRs.

The failure mode is a check that silently stops checking, not an error. This
gate makes the redefinition itself the failure, independent of use.

What counts as a duplicate
--------------------------
Two or more bindings of one name in ``ast.Module.body`` -- the *direct*
children of the module, nothing nested -- via:

* ``def`` / ``async def`` / ``class``;
* ``x = ...`` with a plain ``Name`` target (each target of a chained
  ``a = b = ...`` counts);
* ``x: T = ...`` with a plain ``Name`` target.

Deliberately not counted, each for a reason:

``import`` / ``from ... import``
    Imports are the canonical *legitimate* rebinding site -- optional-dependency
    fallbacks, version shims, re-export surfaces -- and a duplicated import
    shadows a name with the same kind of object rather than a different
    signature, so it cannot produce the silent-behaviour-change this gate exists
    to catch. F811 already fires on the unused-then-reimported case.

Anything inside ``if`` / ``try`` / ``with`` at module level
    ``if TYPE_CHECKING:``, ``try: import x / except ImportError: x = None``, and
    ``if sys.version_info >= (3, 13):`` fallbacks all rebind names on purpose,
    and none of their bodies are in ``ast.Module.body``. Scanning only the
    direct children therefore excludes them for free, and the exclusion is the
    right one on its merits: a rebinding an author guarded with control flow is
    visible to the next reader as a rebinding. The incident class is two
    *unconditional* top-level definitions, which is exactly what remains.

``@overload`` stubs
    ``typing.overload`` requires several same-named ``def``s followed by the
    real implementation; that is the language's own idiom, not a collision. A
    definition decorated with ``overload`` (bare, or attribute-qualified as in
    ``typing.overload``) is skipped, so only the implementation binds.

``@property`` / ``@x.setter`` pairs
    Class-level, so they live in a ``ClassDef`` body and are never in
    ``ast.Module.body``. Nothing special is done for them; a regression test
    pins that they stay clean.

``x += ...``
    ``AugAssign`` mutates an existing binding rather than creating a second one.

``x: T`` with no value
    A bare annotation declares a type without binding, so ``x: int`` followed by
    ``x = 5`` is one binding, not two.

``_``
    By convention a discard, never referenced, and repeated on purpose by the
    ``@fn.register`` singledispatch idiom.

Tuple-unpacking targets (``a, b = ...``) are also skipped: they bind several
names at once, which makes a "duplicate" hard to describe usefully, and no
incident of this class has come from them.

A false positive here gets the gate disabled, which is worse than not shipping
it, so every judgement above errs towards silence -- and every one of them is
pinned by a test in ``tests/scripts/test_check_duplicate_toplevel_names.py``.

Scope
-----
``src/``, ``tests/``, ``roster/`` and ``conftest.py`` -- exactly the repo's lint
gate scope. ``alembic/`` and ``scripts/`` are outside it by standing repo
convention and are not scanned here either.

Scanning nothing is a failure
-----------------------------
If the scan finds zero Python files it exits 1 rather than printing a green "no
duplicates found". A guard whose silence is indistinguishable between "clean"
and "looked at nothing" is the same defect class this file exists to catch, so a
renamed or relocated scope directory has to fail loudly. The success line always
reports the file count for the same reason.

Baseline ratchet
----------------
``duplicate-toplevel-names-baseline.json`` maps ``"<relative path>::<name>"`` to
a one-line reason. Keying on the *pair* is deliberate: keying on the file alone
would swallow the next duplicate added to a file already on the list, and keying
on the name alone would license the same collision anywhere in the tree.

There is deliberately **no** ``--update-baseline`` flag, matching
``check_archived_requirements_landed.py``: a guard that can re-freeze itself is
one command away from meaning nothing. Entries come out by hand; the gate prints
which frozen entries have healed.

The baseline ships empty. All three pre-existing violations were repaired in the
same change (bu-ayrbg) -- see that bead for what each one turned out to be.

Limits
------
This is a single-file, single-name check. It says nothing about a helper
duplicated across two files, about a module-level name shadowing an imported
one, or about two definitions that are genuinely identical (it reports those
too, because a merge cannot tell them apart from the dangerous kind either).
Because it reads ``ast.Module.body`` only, a redefinition an author deliberately
hid inside ``if True:`` is invisible to it.

Usage:
  python3 scripts/check_duplicate_toplevel_names.py             # gate (ratchet)
  python3 scripts/check_duplicate_toplevel_names.py --strict    # ignore ratchet
  python3 scripts/check_duplicate_toplevel_names.py --root DIR  # scan another tree

Exit codes:
  0  Files were scanned and every module-level name in them is bound once.
  1  An unfrozen duplicate was found, a file could not be parsed, or the scan
     found no Python files at all.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "duplicate-toplevel-names-baseline.json"

# The repo's lint gate scope, verbatim. Directories are walked recursively;
# files are taken as-is. An entry that does not exist under --root is skipped,
# so this list can name paths a partial tree does not carry.
SCAN_PATHS = ("src", "tests", "roster", "conftest.py")

# Never reported. See "What counts as a duplicate" in the module docstring.
IGNORED_NAMES = frozenset({"_"})


@dataclass(frozen=True)
class Binding:
    """One module-level binding of one name."""

    name: str
    lineno: int
    kind: str  # "def" | "async def" | "class" | "assignment"


@dataclass(frozen=True)
class Duplicate:
    """A name bound more than once at module level in one file."""

    path: str  # relative to the scanned root
    name: str
    bindings: tuple[Binding, ...]

    def key(self) -> str:
        return f"{self.path}::{self.name}"

    def describe(self) -> str:
        sites = ", ".join(f"{b.kind} at line {b.lineno}" for b in self.bindings)
        return f"{self.path}: `{self.name}` is bound {len(self.bindings)} times ({sites})"


def _is_overload(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when the definition is a ``typing.overload`` stub rather than a binding.

    Matches a bare ``@overload`` and any attribute form (``@typing.overload``,
    ``@t.overload``). Call forms are not matched because ``overload`` takes no
    arguments; anything shaped like ``@overload(...)`` is some other decorator.
    """
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "overload":
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr == "overload":
            return True
    return False


def module_level_bindings(tree: ast.Module) -> list[Binding]:
    """Every binding in ``tree.body``, in source order.

    Only the direct children of the module are considered; see the docstring for
    why nested (``if`` / ``try`` / ``with``) rebinding is out of scope.
    """
    bindings: list[Binding] = []

    def bind_target(target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            bindings.append(Binding(target.id, target.lineno, "assignment"))

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if _is_overload(node):
                continue
            kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            bindings.append(Binding(node.name, node.lineno, kind))
        elif isinstance(node, ast.ClassDef):
            bindings.append(Binding(node.name, node.lineno, "class"))
        elif isinstance(node, ast.Assign):
            # Every target of a chained `a = b = ...` binds.
            for target in node.targets:
                bind_target(target)
        elif isinstance(node, ast.AnnAssign):
            # `x: int` with no value declares a type without binding.
            if node.value is not None:
                bind_target(node.target)

    return [b for b in bindings if b.name not in IGNORED_NAMES]


def duplicates_in_source(source: str, path: str) -> list[Duplicate]:
    """Every module-level name ``source`` binds more than once."""
    tree = ast.parse(source)
    by_name: dict[str, list[Binding]] = {}
    for binding in module_level_bindings(tree):
        by_name.setdefault(binding.name, []).append(binding)

    return [
        Duplicate(path=path, name=name, bindings=tuple(bindings))
        for name, bindings in by_name.items()
        if len(bindings) > 1
    ]


def python_files(root: Path) -> list[Path]:
    """Every ``.py`` file under the scan scope, deduplicated and sorted."""
    found: set[Path] = set()
    for entry in SCAN_PATHS:
        target = root / entry
        if target.is_dir():
            found.update(p for p in target.rglob("*.py") if p.is_file())
        elif target.is_file() and target.suffix == ".py":
            found.add(target)
    return sorted(found)


def collect(root: Path) -> tuple[list[Duplicate], list[str], int]:
    """``(duplicates, parse errors, files scanned)`` over the scan scope under ``root``."""
    duplicates: list[Duplicate] = []
    errors: list[str] = []
    files = python_files(root)

    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError) as exc:  # pragma: no cover - unreadable file
            errors.append(f"{relative}: could not read ({exc})")
            continue
        try:
            duplicates.extend(duplicates_in_source(source, relative))
        except SyntaxError as exc:
            # A file this gate cannot parse is a file it cannot check, and
            # skipping it silently would be the defect this gate exists to
            # catch. Report it instead.
            errors.append(f"{relative}: could not parse ({exc.msg} at line {exc.lineno})")

    duplicates.sort(key=lambda d: (d.path, d.name))
    return duplicates, errors, len(files)


def load_baseline(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text("utf-8"))
    return {str(key): str(reason) for key, reason in raw.items()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when one Python file binds the same module-level name twice."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Tree to scan (default: repo root).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Ratchet file of already-known duplicates (default: %(default)s).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Ignore the ratchet; any duplicate fails.",
    )
    args = parser.parse_args()

    duplicates, errors, scanned = collect(args.root)
    frozen = {} if args.strict else load_baseline(args.baseline)

    if scanned == 0:
        print(
            f"No Python files found under {args.root} in {', '.join(SCAN_PATHS)}. "
            "This gate fails rather than reporting a vacuous pass: a scan that reads "
            "nothing is indistinguishable from a scan that found nothing wrong, which "
            "is the defect it exists to catch. Check --root, or update SCAN_PATHS if "
            "the lint scope moved.",
            file=sys.stderr,
        )
        return 1

    for error in errors:
        print(f"error: {error}", file=sys.stderr)

    unfrozen = [d for d in duplicates if d.key() not in frozen]

    for duplicate in duplicates:
        if duplicate.key() in frozen:
            print(f"note: {duplicate.key()} is frozen -- {frozen[duplicate.key()]}")

    present = {d.key() for d in duplicates}
    healed = sorted(key for key in frozen if key not in present)
    if healed:
        print(
            "\nFrozen duplicates that have since been repaired: "
            + ", ".join(healed)
            + f"\n  Delete these entries from {args.baseline.name} by hand. "
            "There is no re-freeze flag on purpose."
        )

    if unfrozen:
        print(f"\n{len(unfrozen)} duplicate module-level name(s):")
        for duplicate in unfrozen:
            print(f"  {duplicate.describe()}")
        print(
            "\nThe last binding wins for every caller, including callers written against "
            "the first. `ruff` will not catch this: F811 skips a redefinition whose earlier "
            "definition is used in between, which is exactly what a merge of two branches "
            "that each added the name produces. Rename one, delete the dead one, or -- if "
            "the shadowing is genuinely intended -- record it in "
            f"{args.baseline.name} with a reason."
        )

    if unfrozen or errors:
        return 1

    print(
        f"No duplicate module-level names across {scanned} Python file(s)"
        + (f" ({len(frozen)} frozen entr(ies) still outstanding)." if frozen else ".")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
