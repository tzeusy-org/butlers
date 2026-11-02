#!/usr/bin/env python3
"""Operator entry point: reconcile held QA publication operations read-only.

Runs ``sweep_held_operations`` once against the fixed origin named by
``--repository`` and prints one content-free JSON report (identifiers and
categories only).  GitHub is only read; the single write is the ledger's
reconciliation record, which releases a hold only for an exact observation.

Credentials never pass through argv, the report or logs:

- the database connection string is read as one line from stdin;
- the GitHub token is read from ``BUTLERS_QA_GH_TOKEN`` in the environment,
  injected by the operator's secret plumbing (``bws run -- ...`` or a pod
  ``secretKeyRef``).  There is no credential-file discovery.

``pr_labels`` holds are observed against the dispatcher's PR labels: the QA
dispatch defaults unless ``--label`` is repeated to name the configured set.  The
ledger's stored request digest decides, so a wrong set only keeps the hold
(``payload_mismatch``).

Exit status: 0 when nothing stays held, 2 when any operation stays held or the
run was refused, 1 on an unexpected failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import asyncpg

from butlers.core.qa.dispatch import DEFAULT_PR_LABELS, QA_GH_TOKEN_KEY
from butlers.core.qa.publication.github_reader import GithubPublicationReader
from butlers.core.qa.publication.reconcile import sweep_held_operations
from butlers.core.qa.publication.state import PublicationRefused

_QA_ROLE = "butler_qa_rw"


class _Refused(RuntimeError):
    """A closed, locally constructed refusal reason."""


async def _assume_qa_role(connection: asyncpg.Connection) -> None:
    # Forced row security admits only the QA role as a ledger writer.
    await connection.execute(f"SET ROLE {_QA_ROLE}")


async def run(
    *,
    dsn: str,
    reader: GithubPublicationReader,
    limit: int,
    labels: tuple[str, ...] = DEFAULT_PR_LABELS,
) -> int:
    async with asyncpg.create_pool(
        dsn=dsn, min_size=1, max_size=1, command_timeout=30, setup=_assume_qa_role
    ) as pool:
        entries = await sweep_held_operations(pool, reader, limit=limit, labels=labels)
    report = [
        {
            "operation_id": str(e.operation_id),
            "attempt_id": str(e.attempt_id),
            "kind": e.kind,
            "status": e.status,
            "category": e.category,
        }
        for e in entries
    ]
    print(json.dumps({"entries": report}, sort_keys=True))
    return 2 if any(e.status == "held" for e in entries) else 0


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        # argparse's default echoes the caller's raw argument values.
        print('{"error":"invalid_arguments"}')
        raise SystemExit(2)


def main() -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("--repository", required=True, help="fixed origin, owner/name")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--label",
        action="append",
        dest="labels",
        help="a configured QA PR label (repeat for each); defaults to the dispatch labels",
    )
    args = parser.parse_args()
    try:
        token = os.environ.get(QA_GH_TOKEN_KEY, "")
        if not token:
            raise _Refused("token_missing")
        if not 1 <= args.limit <= 1000:
            raise _Refused("invalid_arguments")
        try:
            reader = GithubPublicationReader(args.repository, token=token)
        except ValueError:
            raise _Refused("invalid_repository") from None
        dsn = sys.stdin.readline().strip()
        if not dsn:
            raise _Refused("connection_input_missing")
        labels = tuple(args.labels) if args.labels else DEFAULT_PR_LABELS
        return asyncio.run(run(dsn=dsn, reader=reader, limit=args.limit, labels=labels))
    except (_Refused, PublicationRefused) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    except Exception:  # noqa: BLE001 — never echo driver or provider text
        print('{"error":"operation_failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
