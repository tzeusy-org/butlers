#!/usr/bin/env python3
"""
check_owner_emails.py

Fail when a tracked file contains one of the deployment owner's personal email
addresses (full address or bare ``local@`` variant).

The repository is meant to be user-agnostic: fixtures, specs and docs use
placeholders such as ``owner@example.com`` / ``owner.secondary@example.com``.
The blocked addresses are identified by the SHA-256 of their lowercased local
part, so this guard does not itself re-introduce them. Every ``<local>@`` token
in every tracked text file is hashed and compared against the blocklist.

Usage: python3 scripts/check_owner_emails.py
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys

#: sha256(lowercased local part) of blocked owner addresses.
_BLOCKED_LOCAL_PART_SHA256 = frozenset(
    {
        "b1a3d5bf80f45d640fda01e8617f464eb47c470e25d524bc7c015170411dd95c",
        "3c9243797426bc50aa00526657dd14a9174c68f2eae44266c09575f15edad986",
    }
)

_LOCAL_PART_RE = re.compile(rb"([A-Za-z0-9._%+-]+)@")


def main() -> int:
    paths = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True).stdout.split(
        b"\0"
    )
    hits: list[str] = []
    for raw in paths:
        if not raw:
            continue
        path = raw.decode()
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except (FileNotFoundError, IsADirectoryError):
            continue
        if b"\0" in data[:8192]:
            continue  # binary
        for lineno, line in enumerate(data.splitlines(), start=1):
            if b"@" not in line:
                continue
            for match in _LOCAL_PART_RE.finditer(line):
                digest = hashlib.sha256(match.group(1).lower()).hexdigest()
                if digest in _BLOCKED_LOCAL_PART_SHA256:
                    hits.append(f"{path}:{lineno}")
    if hits:
        print("check_owner_emails: owner email address found; use a placeholder such as")
        print("owner@example.com / owner.secondary@example.com instead:")
        for hit in hits:
            print(f"  {hit}")
        return 1
    print("check_owner_emails: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
