#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["asyncpg>=0.29"]
# ///
"""Run content-blind WhatsApp entity reconciliation in dry-run or apply mode."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

_REPO_ROOT = Path(__file__).resolve().parents[1]
# PEP 723 scripts run in an isolated environment.  Add only this checkout's
# source tree so the operator command executes the exact reviewed repository
# implementation without packaging the full application dependency set.
sys.path.insert(0, str(_REPO_ROOT / "src"))

from butlers.tools.relationship.entity_merge import LockedGuardRejected  # noqa: E402
from butlers.tools.relationship.whatsapp_reconciliation import (  # noqa: E402
    ContentBlindReconciliationReport,
    PlanDigestMismatch,
    ReconciliationCategory,
    apply_whatsapp_reconciliation,
    build_whatsapp_reconciliation_plan,
)

_DATABASE_ENV = "BUTLERS_DATABASE_URL"
_RELATIONSHIP_SEARCH_PATH = "relationship,public"


class OperatorError(RuntimeError):
    """Stable operator-command failure with no database or entity content."""

    def __init__(self, classification: str) -> None:
        self.classification = classification
        super().__init__(classification)


class OperatorUsageError(OperatorError):
    def __init__(self) -> None:
        super().__init__("authorization_arguments")


class OperatorConfigurationError(OperatorError):
    def __init__(self) -> None:
        super().__init__("missing_database_url")


class _ContentBlindArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise OperatorUsageError


async def _configure_json(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "json",
        schema="pg_catalog",
        encoder=json.dumps,
        decoder=json.loads,
    )
    await conn.set_type_codec(
        "jsonb",
        schema="pg_catalog",
        encoder=json.dumps,
        decoder=json.loads,
    )


def _dry_run_report(plan) -> ContentBlindReconciliationReport:
    return ContentBlindReconciliationReport(
        mode="dry_run",
        counts={category.value: int(plan.counts[category]) for category in ReconciliationCategory},
        planned=len(plan.pairs),
        applied=0,
        plan_digest=plan.digest,
    )


async def run(
    *,
    apply: bool,
    plan_digest: str | None,
) -> ContentBlindReconciliationReport:
    """Run one explicit operator invocation using only the environment DSN."""
    if (apply and not plan_digest) or (not apply and plan_digest is not None):
        raise OperatorUsageError

    database_url = os.environ.get(_DATABASE_ENV)
    if not database_url:
        raise OperatorConfigurationError

    pool = await asyncpg.create_pool(
        database_url,
        server_settings={"search_path": _RELATIONSHIP_SEARCH_PATH},
        init=_configure_json,
    )
    try:
        if apply:
            return await apply_whatsapp_reconciliation(
                pool,
                authorized_digest=plan_digest,
            )
        plan = await build_whatsapp_reconciliation_plan(pool)
        return _dry_run_report(plan)
    finally:
        await pool.close()


def _report_payload(report: ContentBlindReconciliationReport) -> dict[str, object]:
    return {
        "mode": report.mode,
        "counts": dict(report.counts),
        "planned": report.planned,
        "applied": report.applied,
        "plan_digest": report.plan_digest,
    }


def _emit_json(payload: dict[str, object], *, error: bool = False) -> None:
    print(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        file=sys.stderr if error else sys.stdout,
    )


async def main(argv: list[str] | None = None) -> int:
    parser = _ContentBlindArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--plan-digest")
    try:
        args = parser.parse_args(argv)
        if bool(args.apply) != (args.plan_digest is not None):
            raise OperatorUsageError
        report = await run(apply=bool(args.apply), plan_digest=args.plan_digest)
    except OperatorUsageError:
        _emit_json({"error": "authorization_arguments"}, error=True)
        return 2
    except OperatorConfigurationError:
        _emit_json({"error": "missing_database_url"}, error=True)
        return 1
    except PlanDigestMismatch:
        _emit_json({"error": "plan_digest_mismatch"}, error=True)
        return 1
    except LockedGuardRejected:
        _emit_json({"error": "plan_drift"}, error=True)
        return 1
    except Exception:  # noqa: BLE001
        _emit_json({"error": "reconciliation_failed"}, error=True)
        return 1

    _emit_json(_report_payload(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
