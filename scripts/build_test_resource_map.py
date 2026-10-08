"""Regenerate/check deterministic public-resource reader candidates; no imports of tests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from butlers.testing.resource_readers import DECLARATIONS, REGISTRY, discover  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = discover(args.root, json.loads((args.root / DECLARATIONS).read_text()))
        output = json.dumps(result, sort_keys=True, indent=2) + "\n"
        path = args.root / REGISTRY
        if args.check:
            if path.read_text() != output:
                raise ValueError("READER_REGISTRY_STALE")
        else:
            path.write_text(output)
        print("Public resource reader declaration/consumer check passed")
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print("Public resource reader declaration/consumer check failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
