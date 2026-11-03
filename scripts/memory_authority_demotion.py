#!/usr/bin/env python3
"""Operator-only demotion of laundered owner-authority memory, one schema at a time.

Default mode is a read-only, content-blind dry run. ``--apply`` additionally needs
``--approval`` naming the owner's approval of that exact dry run. Never scheduled,
never wired to a daemon, tool or API. The DSN is read from stdin (one line) and is
never printed. See docs/operations/memory-authority-demotion.md before running it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from butlers.modules.memory import authority_demotion as demotion

EXIT_OK = 0
EXIT_REFUSED = 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Demote laundered owner-authority memory (default: dry run)."
    )
    parser.add_argument("--manifest", type=Path, required=True, help="evidence manifest JSON")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the approved dry run (default: read-only dry run)",
    )
    parser.add_argument("--approval", type=Path, help="owner approval JSON (required by --apply)")
    args = parser.parse_args(argv)
    if args.apply and args.approval is None:
        parser.error("--apply requires --approval")
    if not args.apply and args.approval is not None:
        parser.error("--approval is only meaningful with --apply")
    return args


async def run(args: argparse.Namespace, dsn: str) -> dict:
    import asyncpg

    from butlers.db import register_jsonb_codec

    manifest = demotion.EvidenceManifest.parse(demotion.load_json(args.manifest.read_text()))
    approval = (
        demotion.Approval.parse(demotion.load_json(args.approval.read_text()))
        if args.apply
        else None
    )
    conn = await asyncpg.connect(dsn)
    try:
        await register_jsonb_codec(conn)
        if approval is None:
            return {"mode": "dry_run", **(await demotion.dry_run(conn, manifest)).to_json()}
        return {"mode": "apply", **(await demotion.apply(conn, manifest, approval)).to_json()}
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dsn = sys.stdin.readline().strip()
    if not dsn:
        print(json.dumps({"refused": "missing_dsn_on_stdin"}))
        return EXIT_REFUSED
    try:
        result = asyncio.run(run(args, dsn))
    except demotion.ManifestError as exc:
        print(json.dumps({"refused": "invalid_input", "detail": str(exc)}))
        return EXIT_REFUSED
    except demotion.DemotionRefused as exc:
        # Nothing was committed; the reason is a closed code, never driver text.
        print(json.dumps({"refused": exc.reason}))
        return EXIT_REFUSED
    print(json.dumps(result, indent=2, sort_keys=True))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
