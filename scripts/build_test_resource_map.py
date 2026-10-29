#!/usr/bin/env python3
"""Build/check the current reader registry without importing repository tests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from butlers.testing.resource_readers import (  # noqa: E402
    DECLARATIONS,
    REGISTRY,
    digest,
    discover,
    read_json,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        declarations = read_json(args.root / DECLARATIONS)
        current = discover(args.root, declarations)
        target = args.root / REGISTRY
        if args.check:
            if digest(read_json(target)) != digest(current):
                print("reader registry stale: review declarations and regenerate", file=sys.stderr)
                return 1
        else:
            target.write_text(json.dumps(current, sort_keys=True, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "digest": current["digest"],
                    "sources": len(current["source_hashes"]),
                    "patterns": len(current["readers"]),
                    "conservative_unknowns": len(current["conservative_unknowns"]),
                }
            )
        )
        return 0
    except (OSError, ValueError, SyntaxError, TypeError, KeyError):
        print("reader source/declaration evidence unavailable", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
