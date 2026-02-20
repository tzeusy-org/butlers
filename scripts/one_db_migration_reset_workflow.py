#!/usr/bin/env python3
"""Destructive reset workflow for one-db migration rewrite rollout.

Implements a repeatable operator workflow for local/dev/staging:
- reset: destructive database or managed-schema reset
- migrate: replay rewritten core + memory migrations per schema
- validate: run SQL schema/table/revision matrix checks
- run: reset + migrate + validate in one command
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from butlers.migrations import run_migrations

DEFAULT_TARGET_ENV = "BUTLERS_DATABASE_URL"
DEFAULT_BUTLER_SCHEMAS = ("general", "health", "messenger", "relationship", "switchboard")
DEFAULT_MEMORY_SCHEMAS = ("general", "health", "relationship", "switchboard")
DEFAULT_MANAGED_SCHEMAS = ("shared", *DEFAULT_BUTLER_SCHEMAS)
DEFAULT_CORE_TABLES = ("state", "scheduled_tasks", "sessions", "route_inbox")
DEFAULT_MEMORY_TABLES = ("episodes", "facts", "rules", "memory_links")
EXPECTED_CORE_REVISION = "core_001"
EXPECTED_MEMORY_REVISION = "mem_001"
IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
BLOCKED_DROP_DB_NAMES = {"postgres", "template0", "template1"}


class WorkflowConfigError(Exception):
    """Raised when workflow input is invalid."""


@dataclass(frozen=True)
class ParsedTarget:
    """Parsed target DB metadata."""

    db_url: str
    db_name: str
    admin_db_url: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _qualified_table(schema: str, table: str) -> str:
    return f"{_quote_ident(schema)}.{_quote_ident(table)}"


def _require_env(env_var: str) -> str:
    value = os.environ.get(env_var)
    if not value:
        raise WorkflowConfigError(
            f"Environment variable {env_var!r} is not set; cannot resolve target DB."
        )
    return value


def _validate_identifier(identifier: str, label: str) -> str:
    if not IDENTIFIER_RE.fullmatch(identifier):
        raise WorkflowConfigError(
            f"Invalid {label} {identifier!r}. Use lowercase letters, digits, and underscores."
        )
    return identifier


def _normalize_identifiers(
    values: list[str] | None, default: tuple[str, ...], label: str
) -> list[str]:
    raw_values = values if values else list(default)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = _validate_identifier(raw.strip(), label)
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    if not normalized:
        raise WorkflowConfigError(f"No {label}s were provided.")
    return normalized


def _parse_target(db_url: str) -> ParsedTarget:
    parsed = urlsplit(db_url)
    db_name = parsed.path.lstrip("/")
    if not db_name:
        raise WorkflowConfigError("Target DB URL must include a database name.")
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    return ParsedTarget(db_url=db_url, db_name=db_name, admin_db_url=admin_url)


def _check_reset_safety(db_name: str, allow_production_name: bool) -> None:
    lowered = db_name.lower()
    if lowered in BLOCKED_DROP_DB_NAMES:
        raise WorkflowConfigError(
            f"Refusing destructive database reset for protected DB name {db_name!r}."
        )
    if not allow_production_name and ("prod" in lowered or "production" in lowered):
        raise WorkflowConfigError(
            "Target DB name looks production-like. Re-run with --allow-production-db-name "
            "only after manual verification."
        )


async def _schema_exists(conn: asyncpg.Connection, schema: str) -> bool:
    exists = await conn.fetchval(
        """
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.schemata
          WHERE schema_name = $1
        )
        """,
        schema,
    )
    return bool(exists)


async def _table_exists(conn: asyncpg.Connection, schema: str, table: str) -> bool:
    exists = await conn.fetchval(
        """
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.tables
          WHERE table_schema = $1
            AND table_name = $2
        )
        """,
        schema,
        table,
    )
    return bool(exists)


async def _table_count(conn: asyncpg.Connection, schema: str) -> int:
    count = await conn.fetchval(
        """
        SELECT COUNT(*)::BIGINT
        FROM information_schema.tables
        WHERE table_schema = $1
        """,
        schema,
    )
    return int(count or 0)


async def _schema_versions(conn: asyncpg.Connection, schema: str) -> list[str]:
    if not await _table_exists(conn, schema, "alembic_version"):
        return []
    rows = await conn.fetch(
        f"SELECT version_num FROM {_qualified_table(schema, 'alembic_version')}"
    )
    versions = sorted({str(row["version_num"]) for row in rows})
    return versions


async def _reset_database(
    target: ParsedTarget,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    conn = await asyncpg.connect(target.admin_db_url)
    try:
        db_exists = bool(
            await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)", target.db_name
            )
        )
        active_connections = int(
            await conn.fetchval(
                "SELECT COUNT(*)::INT FROM pg_stat_activity WHERE datname = $1",
                target.db_name,
            )
            or 0
        )
        result: dict[str, Any] = {
            "scope": "database",
            "database": target.db_name,
            "db_exists_before": db_exists,
            "active_connections_before": active_connections,
            "status": "planned" if dry_run else "reset",
        }
        if not dry_run:
            await conn.execute(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                target.db_name,
            )
            await conn.execute(f"DROP DATABASE IF EXISTS {_quote_ident(target.db_name)}")
            await conn.execute(f"CREATE DATABASE {_quote_ident(target.db_name)}")
        db_exists_after = bool(
            await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)", target.db_name
            )
        )
        result["db_exists_after"] = db_exists_after
        return result
    finally:
        await conn.close()


async def _reset_managed_schemas(
    target: ParsedTarget,
    *,
    schemas: list[str],
    dry_run: bool,
) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(target.db_url)
    results: list[dict[str, Any]] = []
    try:
        for schema in schemas:
            exists_before = await _schema_exists(conn, schema)
            table_count_before = await _table_count(conn, schema) if exists_before else 0
            result: dict[str, Any] = {
                "scope": "managed-schema",
                "schema": schema,
                "schema_exists_before": exists_before,
                "table_count_before": table_count_before,
                "status": "planned" if dry_run else "reset",
            }
            if not dry_run:
                await conn.execute(f"DROP SCHEMA IF EXISTS {_quote_ident(schema)} CASCADE")
                await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_ident(schema)}")
            exists_after = await _schema_exists(conn, schema)
            table_count_after = await _table_count(conn, schema) if exists_after else 0
            result["schema_exists_after"] = exists_after
            result["table_count_after"] = table_count_after
            results.append(result)
        return results
    finally:
        await conn.close()


async def _migrate_rewritten_chains(
    target: ParsedTarget,
    *,
    butler_schemas: list[str],
    memory_schemas: list[str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for schema in butler_schemas:
        await run_migrations(target.db_url, chain="core", schema=schema)
        results.append(
            {
                "schema": schema,
                "chain": "core",
                "expected_revision": EXPECTED_CORE_REVISION,
                "status": "ok",
            }
        )
    for schema in memory_schemas:
        await run_migrations(target.db_url, chain="memory", schema=schema)
        results.append(
            {
                "schema": schema,
                "chain": "memory",
                "expected_revision": EXPECTED_MEMORY_REVISION,
                "status": "ok",
            }
        )
    return results


async def _validate_schema_matrix(
    target: ParsedTarget,
    *,
    managed_schemas: list[str],
    butler_schemas: list[str],
    memory_schemas: list[str],
    core_tables: list[str],
    memory_tables: list[str],
) -> dict[str, Any]:
    conn = await asyncpg.connect(target.db_url)
    try:
        schema_results: list[dict[str, Any]] = []
        for schema in managed_schemas:
            schema_results.append(
                {
                    "schema": schema,
                    "exists": await _schema_exists(conn, schema),
                }
            )

        table_checks: list[dict[str, Any]] = []
        for schema in butler_schemas:
            for table in core_tables:
                table_checks.append(
                    {
                        "schema": schema,
                        "table": table,
                        "group": "core",
                        "exists": await _table_exists(conn, schema, table),
                    }
                )
        for schema in memory_schemas:
            for table in memory_tables:
                table_checks.append(
                    {
                        "schema": schema,
                        "table": table,
                        "group": "memory",
                        "exists": await _table_exists(conn, schema, table),
                    }
                )

        revision_checks: list[dict[str, Any]] = []
        for schema in butler_schemas:
            versions = await _schema_versions(conn, schema)
            revision_checks.append(
                {
                    "schema": schema,
                    "expected_revision": EXPECTED_CORE_REVISION,
                    "versions": versions,
                    "present": EXPECTED_CORE_REVISION in versions,
                }
            )
        for schema in memory_schemas:
            versions = await _schema_versions(conn, schema)
            revision_checks.append(
                {
                    "schema": schema,
                    "expected_revision": EXPECTED_MEMORY_REVISION,
                    "versions": versions,
                    "present": EXPECTED_MEMORY_REVISION in versions,
                }
            )

        missing_schemas = [item for item in schema_results if not item["exists"]]
        missing_tables = [item for item in table_checks if not item["exists"]]
        missing_revisions = [item for item in revision_checks if not item["present"]]
        status = "ok" if not (missing_schemas or missing_tables or missing_revisions) else "failed"

        return {
            "status": status,
            "schema_results": schema_results,
            "table_checks": table_checks,
            "revision_checks": revision_checks,
            "summary": {
                "schemas_checked": len(schema_results),
                "tables_checked": len(table_checks),
                "revisions_checked": len(revision_checks),
                "missing_schemas": len(missing_schemas),
                "missing_tables": len(missing_tables),
                "missing_revisions": len(missing_revisions),
            },
            "missing": {
                "schemas": missing_schemas,
                "tables": missing_tables,
                "revisions": missing_revisions,
            },
        }
    finally:
        await conn.close()


def _build_report(
    *,
    command: str,
    target_env: str,
    target_db: str,
    status: str,
    details: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "generated_at": _utc_now(),
        "command": command,
        "target_env_var": target_env,
        "target_db": target_db,
        "status": status,
        "error": error,
        "details": details,
    }


def _write_report(path: Path | None, report: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Report written to {path}")


def _add_target_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target-env",
        default=DEFAULT_TARGET_ENV,
        help=f"Env var containing target DB URL (default: {DEFAULT_TARGET_ENV}).",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Optional JSON report output path.",
    )


def _add_schema_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--butler-schema",
        action="append",
        default=[],
        help=(
            "Schema that should contain core tables. Repeatable. "
            "Default: general, health, messenger, relationship, switchboard."
        ),
    )
    parser.add_argument(
        "--memory-schema",
        action="append",
        default=[],
        help=(
            "Schema that should contain memory tables. Repeatable. "
            "Default: general, health, relationship, switchboard."
        ),
    )


def _add_table_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--core-table",
        action="append",
        default=[],
        help=(
            "Expected core table in each --butler-schema. Repeatable. "
            "Default: state, scheduled_tasks, sessions, route_inbox."
        ),
    )
    parser.add_argument(
        "--memory-table",
        action="append",
        default=[],
        help=(
            "Expected memory table in each --memory-schema. Repeatable. "
            "Default: episodes, facts, rules, memory_links."
        ),
    )


def _add_reset_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scope",
        choices=("database", "managed-schemas"),
        default="managed-schemas",
        help=(
            "Reset scope. 'database' drops/recreates the DB; "
            "'managed-schemas' drops/recreates schemas."
        ),
    )
    parser.add_argument(
        "--managed-schema",
        action="append",
        default=[],
        help=(
            "Managed schema to reset when --scope=managed-schemas. Repeatable. "
            "Default: shared + all butler schemas."
        ),
    )
    parser.add_argument(
        "--confirm-destructive-reset",
        default=None,
        help="Safety guard. Must equal RESET for non-dry-run reset operations.",
    )
    parser.add_argument(
        "--allow-production-db-name",
        action="store_true",
        help="Allow target DB names that look production-like (contain 'prod').",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview reset actions without executing destructive statements.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Destructive reset workflow for one-db migration rewrite rollout."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    reset = subparsers.add_parser("reset", help="Run destructive reset only.")
    _add_target_arg(reset)
    _add_reset_args(reset)

    migrate = subparsers.add_parser("migrate", help="Replay rewritten migrations only.")
    _add_target_arg(migrate)
    _add_schema_args(migrate)

    validate = subparsers.add_parser(
        "validate", help="Run SQL schema/table/revision validation only."
    )
    _add_target_arg(validate)
    _add_schema_args(validate)
    _add_table_args(validate)

    run = subparsers.add_parser("run", help="Run reset + migrate + validate in sequence.")
    _add_target_arg(run)
    _add_reset_args(run)
    _add_schema_args(run)
    _add_table_args(run)

    return parser


def _normalized_workflow_args(args: argparse.Namespace) -> dict[str, list[str]]:
    butler_schemas = _normalize_identifiers(
        getattr(args, "butler_schema", []),
        DEFAULT_BUTLER_SCHEMAS,
        "butler schema",
    )
    memory_schemas = _normalize_identifiers(
        getattr(args, "memory_schema", []),
        DEFAULT_MEMORY_SCHEMAS,
        "memory schema",
    )
    managed_schemas = _normalize_identifiers(
        getattr(args, "managed_schema", []),
        DEFAULT_MANAGED_SCHEMAS,
        "managed schema",
    )
    core_tables = _normalize_identifiers(
        getattr(args, "core_table", []),
        DEFAULT_CORE_TABLES,
        "core table",
    )
    memory_tables = _normalize_identifiers(
        getattr(args, "memory_table", []),
        DEFAULT_MEMORY_TABLES,
        "memory table",
    )

    missing_from_butlers = [schema for schema in memory_schemas if schema not in butler_schemas]
    if missing_from_butlers:
        raise WorkflowConfigError(
            "--memory-schema values must also be listed in --butler-schema. "
            f"Missing: {', '.join(missing_from_butlers)}"
        )

    return {
        "butler_schemas": butler_schemas,
        "memory_schemas": memory_schemas,
        "managed_schemas": managed_schemas,
        "core_tables": core_tables,
        "memory_tables": memory_tables,
    }


def _require_confirm_reset(args: argparse.Namespace) -> None:
    if args.dry_run:
        return
    if args.confirm_destructive_reset != "RESET":
        raise WorkflowConfigError(
            "--confirm-destructive-reset must be exactly RESET for non-dry-run reset."
        )


async def _run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    target = _parse_target(_require_env(args.target_env))
    normalized = _normalized_workflow_args(args)

    if args.command == "reset":
        _check_reset_safety(target.db_name, args.allow_production_db_name)
        _require_confirm_reset(args)
        if args.scope == "database":
            reset_result = await _reset_database(target, dry_run=args.dry_run)
        else:
            reset_result = await _reset_managed_schemas(
                target,
                schemas=normalized["managed_schemas"],
                dry_run=args.dry_run,
            )
        details = {"scope": args.scope, "dry_run": args.dry_run, "reset": reset_result}
        return 0, _build_report(
            command=args.command,
            target_env=args.target_env,
            target_db=target.db_name,
            status="ok",
            details=details,
        )

    if args.command == "migrate":
        migrate_result = await _migrate_rewritten_chains(
            target,
            butler_schemas=normalized["butler_schemas"],
            memory_schemas=normalized["memory_schemas"],
        )
        details = {"migrations": migrate_result}
        return 0, _build_report(
            command=args.command,
            target_env=args.target_env,
            target_db=target.db_name,
            status="ok",
            details=details,
        )

    if args.command == "validate":
        validation = await _validate_schema_matrix(
            target,
            managed_schemas=normalized["managed_schemas"],
            butler_schemas=normalized["butler_schemas"],
            memory_schemas=normalized["memory_schemas"],
            core_tables=normalized["core_tables"],
            memory_tables=normalized["memory_tables"],
        )
        exit_code = 0 if validation["status"] == "ok" else 2
        return exit_code, _build_report(
            command=args.command,
            target_env=args.target_env,
            target_db=target.db_name,
            status=validation["status"],
            details={"validation": validation},
        )

    if args.command == "run":
        _check_reset_safety(target.db_name, args.allow_production_db_name)
        _require_confirm_reset(args)
        if args.scope == "database":
            reset_result = await _reset_database(target, dry_run=args.dry_run)
        else:
            reset_result = await _reset_managed_schemas(
                target,
                schemas=normalized["managed_schemas"],
                dry_run=args.dry_run,
            )
        details: dict[str, Any] = {
            "scope": args.scope,
            "dry_run": args.dry_run,
            "reset": reset_result,
        }
        if args.dry_run:
            details["note"] = "Dry-run stops after reset planning; migrate/validate not executed."
            return 0, _build_report(
                command=args.command,
                target_env=args.target_env,
                target_db=target.db_name,
                status="ok",
                details=details,
            )
        migrate_result = await _migrate_rewritten_chains(
            target,
            butler_schemas=normalized["butler_schemas"],
            memory_schemas=normalized["memory_schemas"],
        )
        validation = await _validate_schema_matrix(
            target,
            managed_schemas=normalized["managed_schemas"],
            butler_schemas=normalized["butler_schemas"],
            memory_schemas=normalized["memory_schemas"],
            core_tables=normalized["core_tables"],
            memory_tables=normalized["memory_tables"],
        )
        details["migrations"] = migrate_result
        details["validation"] = validation
        exit_code = 0 if validation["status"] == "ok" else 2
        return exit_code, _build_report(
            command=args.command,
            target_env=args.target_env,
            target_db=target.db_name,
            status=validation["status"],
            details=details,
        )

    raise WorkflowConfigError(f"Unsupported command {args.command!r}")


def _print_summary(report: dict[str, Any]) -> None:
    command = report["command"]
    status = report["status"]
    print(f"{command.upper()} status={status} target_db={report['target_db']}")
    details = report["details"]
    if command in {"validate", "run"} and "validation" in details:
        summary = details["validation"]["summary"]
        print(
            "  validation: "
            f"schemas={summary['schemas_checked']} "
            f"tables={summary['tables_checked']} "
            f"revisions={summary['revisions_checked']} "
            f"missing_schemas={summary['missing_schemas']} "
            f"missing_tables={summary['missing_tables']} "
            f"missing_revisions={summary['missing_revisions']}"
        )


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    report: dict[str, Any] | None = None
    try:
        exit_code, report = asyncio.run(_run(args))
        _print_summary(report)
        _write_report(args.report_path, report)
        return exit_code
    except WorkflowConfigError as exc:
        message = f"CONFIG ERROR: {exc}"
        print(message, file=sys.stderr)
        target_db = "<unknown>"
        try:
            target_db = _parse_target(_require_env(args.target_env)).db_name
        except Exception:  # noqa: BLE001 - best effort for error reporting.
            pass
        report = _build_report(
            command=args.command,
            target_env=args.target_env,
            target_db=target_db,
            status="error",
            details={},
            error=str(exc),
        )
        _write_report(getattr(args, "report_path", None), report)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI should fail loudly.
        print(f"UNEXPECTED ERROR: {exc}", file=sys.stderr)
        target_db = "<unknown>"
        try:
            target_db = _parse_target(_require_env(args.target_env)).db_name
        except Exception:  # noqa: BLE001 - best effort for error reporting.
            pass
        report = _build_report(
            command=args.command,
            target_env=args.target_env,
            target_db=target_db,
            status="error",
            details={},
            error=str(exc),
        )
        _write_report(getattr(args, "report_path", None), report)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
