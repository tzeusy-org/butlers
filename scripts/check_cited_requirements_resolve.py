#!/usr/bin/env python3
"""check_cited_requirements_resolve.py -- not implemented yet (bu-lpwjc).

Placeholder so the guard's tests fail on their own assertions rather than on a
missing file. The predicate lands in the following commit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Not implemented yet.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--strict", action="store_true")
    parser.parse_args()
    return 0


if __name__ == "__main__":
    sys.exit(main())
