#!/usr/bin/env python3
"""Check TOML-declared retirements against tracked source; see scripts/README.md.

Only --tighten writes, and only after a successful check. It lowers deprecated
ceilings to observed counts, preserving comments and all other registry bytes.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Surface:
    id: str
    kind: str
    state: str
    pattern: str
    scope: list[str]
    bead: str
    survivor: str
    allow: list[dict[str, str]]
    exclude_parts: list[str]
    suffixes: list[str]
    ceiling: int
    expires: date | None


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(s, str) and s for s in value)


def _relative(value: str) -> bool:
    return not value.startswith(("/", ":")) and ".." not in PurePosixPath(value).parts


def load_registry(text: str) -> list[Surface]:
    raw = tomllib.loads(text)
    if set(raw) != {"surface"} or not isinstance(raw["surface"], list) or not raw["surface"]:
        raise ValueError("expected a nonempty [[surface]] registry")
    surfaces = []
    ids = set()
    required = {"id", "kind", "state", "pattern", "scope", "bead", "survivor"}
    optional = {"allow", "exclude_parts", "suffixes", "ceiling", "expires"}
    for row in raw["surface"]:
        if not isinstance(row, dict) or not required <= row.keys() <= required | optional:
            raise ValueError("missing or unknown surface fields")
        if not all(isinstance(row[key], str) and row[key] for key in required - {"scope"}):
            raise ValueError("surface identity, pattern and provenance must be nonempty strings")
        if row["id"] in ids or row["kind"] not in {"path", "pattern"}:
            raise ValueError("duplicate id or unsupported kind")
        ids.add(row["id"])
        if row["state"] not in {"retired", "deprecated"}:
            raise ValueError(f"{row['id']}: unsupported state")
        for key in ("scope", "exclude_parts", "suffixes"):
            if not _strings(row.get(key, [])) or (key == "scope" and not row[key]):
                raise ValueError(f"{row['id']}: invalid {key}")
        if not all(_relative(path) for path in row["scope"]):
            raise ValueError(f"{row['id']}: scope must use relative Git paths or globs")
        if row["kind"] == "pattern":
            re.compile(row["pattern"])
        elif not _relative(row["pattern"]):
            raise ValueError(f"{row['id']}: path pattern must be relative")
        allow = row.get("allow", [])
        if not isinstance(allow, list) or any(
            not isinstance(item, dict)
            or set(item) != {"path", "line_contains"}
            or not all(isinstance(value, str) and value for value in item.values())
            or not _relative(item["path"])
            for item in allow
        ):
            raise ValueError(f"{row['id']}: allow requires exact path and line_contains")
        ceiling = row.get("ceiling", 0)
        if type(ceiling) is not int or ceiling < 0:
            raise ValueError(f"{row['id']}: ceiling must be a nonnegative integer")
        expires = row.get("expires")
        if row["state"] == "deprecated":
            if "ceiling" not in row or type(expires) is not date:
                raise ValueError(f"{row['id']}: deprecation needs ceiling and expires (TOML date)")
        elif ceiling != 0 or expires is not None:
            raise ValueError(f"{row['id']}: retired entries have no expiry or positive ceiling")
        surfaces.append(
            Surface(
                **{key: row[key] for key in required},
                allow=allow,
                exclude_parts=row.get("exclude_parts", []),
                suffixes=row.get("suffixes", []),
                ceiling=ceiling,
                expires=expires,
            )
        )
    return surfaces


def scan(root: Path, surface: Surface) -> tuple[int, list[tuple[str, int]], int]:
    result = subprocess.run(
        ["git", "ls-files", "--full-name", "-z", "--", *surface.scope],
        cwd=root,
        check=True,
        capture_output=True,
    )
    files = [
        name
        for name in result.stdout.decode().split("\0")
        if name
        and not set(PurePosixPath(name).parts) & set(surface.exclude_parts)
        and (not surface.suffixes or PurePosixPath(name).suffix in surface.suffixes)
    ]
    if not files:
        raise ValueError("scope matched zero tracked files")
    if surface.kind == "path":
        # Include untracked reappearances and directories, matching the old exists() guard.
        return (
            len(files),
            [(p.relative_to(root).as_posix(), 1) for p in sorted(root.glob(surface.pattern))],
            0,
        )
    pattern = re.compile(surface.pattern)
    hits = []
    allowed = 0
    for name in files:
        text = (root / name).read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            start = match.start()
            lineno = text.count("\n", 0, start) + 1
            end = text.find("\n", start)
            line = text[text.rfind("\n", 0, start) + 1 : end if end >= 0 else len(text)]
            if any(
                item["path"] == name and item["line_contains"] in line for item in surface.allow
            ):
                allowed += 1
            else:
                hits.append((name, lineno))
    return len(files), hits, allowed


def tighten(text: str, surfaces: list[Surface], counts: list[int]) -> str:
    # Entries remain in order; edit only their own explicit ceiling assignment.
    blocks = re.split(r"(?m)(?=^\[\[surface\]\]\s*(?:#.*)?$)", text)
    if len(blocks) != len(surfaces) + 1:
        raise ValueError("--tighten needs each [[surface]] header on its own line")
    for index, (surface, count) in enumerate(zip(surfaces, counts, strict=True), 1):
        if surface.state == "deprecated" and count < surface.ceiling:
            blocks[index], changed = re.subn(
                r"(?m)^(\s*ceiling\s*=\s*)\d[\d_]*(?=\s*(?:#.*)?$)",
                lambda match, count=count: match[1] + str(count),
                blocks[index],
            )
            if changed != 1:
                raise ValueError(f"{surface.id}: --tighten needs a standalone ceiling assignment")
    return "".join(blocks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--registry", type=Path, help="Default: <root>/scripts/retired_surfaces.toml"
    )
    parser.add_argument("--tighten", action="store_true", help="Lower ceilings; never raise them")
    args = parser.parse_args()
    root = args.root.resolve()
    registry = args.registry or root / "scripts/retired_surfaces.toml"
    try:
        text = registry.read_bytes().decode("utf-8")
        surfaces = load_registry(text)
    except (OSError, ValueError, re.error) as exc:
        print(f"{registry}:1: invalid registry: {exc}", file=sys.stderr)
        return 1
    failed = False
    counts = []
    for surface in surfaces:
        context = f"{surface.id} bead={surface.bead} survivor={surface.survivor}"
        try:
            scanned, hits, allowed = scan(root, surface)
        except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as exc:
            print(f"{registry}:1: {context}: scan failed ({exc})", file=sys.stderr)
            failed = True
            continue
        counts.append(len(hits))
        print(f"{surface.id}: scanned={scanned} matched={len(hits) + allowed} allowed={allowed}")
        if surface.expires is not None and date.today() > surface.expires:
            print(f"{registry}:1: {context}: expired {surface.expires}", file=sys.stderr)
            failed = True
        if len(hits) > surface.ceiling:
            failed = True
            for path, line in hits:
                print(
                    f"{path}:{line}: {context}: {len(hits)} hits > ceiling {surface.ceiling}",
                    file=sys.stderr,
                )
    if failed:
        return 1
    if args.tighten:
        try:
            updated = tighten(text, surfaces, counts)
            if updated != text:
                registry.write_bytes(updated.encode("utf-8"))
        except (OSError, ValueError) as exc:
            print(f"{registry}:1: cannot tighten ({exc})", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
