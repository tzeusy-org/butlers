#!/usr/bin/env python3
"""Backfill and parity tooling for one-DB multi-schema migration.

This script migrates data from legacy per-butler databases into a consolidated
database with per-butler schemas, then verifies deterministic parity.

Supported flows:
- `plan`: Validate connectivity/table presence and emit row-count snapshots.
- `migrate`: Copy/upsert source rows into target schema tables.
- `verify`: Enforce row-count and checksum parity (fails loudly on mismatch).
- `run`: Execute migrate then verify.
- `rollback`: Truncate migrated target tables after a failed attempt.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

DEFAULT_CORE_TABLES = ["state", "scheduled_tasks", "sessions", "route_inbox"]
DEFAULT_SHARED_TABLES = ["butler_secrets"]
DEFAULT_TARGET_ENV = "BUTLERS_DATABASE_URL"
DEFAULT_SHARED_SOURCE_ENV = "BUTLER_SHARED_DATABASE_URL"
IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


class MigrationConfigError(Exception):
    """Raised when input configuration is invalid."""


class ParityMismatchError(Exception):
    """Raised when parity checks fail."""

    def __init__(self, mismatches: list[dict[str, Any]]) -> None:
        super().__init__("parity checks failed")
        self.mismatches = mismatches


@dataclass(frozen=True)
class TableJob:
    """Defines a source table -> target table copy/verify unit."""

    source_env_var: str
    source_schema: str
    source_table: str
    target_schema: str
    target_table: str
    scope: str  # "core" or "shared"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _quote_ident(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _qualified_table(schema: str, table: str) -> str:
    return f"{_quote_ident(schema)}.{_quote_ident(table)}"


def _validate_identifier(identifier: str, label: str) -> str:
    if not IDENTIFIER_RE.fullmatch(identifier):
        raise MigrationConfigError(
            f"Invalid {label} {identifier!r}. Only lowercase letters, digits, and '_' are allowed."
        )
    return identifier


def _require_env(env_var: str) -> str:
    value = os.environ.get(env_var)
    if not value:
        raise MigrationConfigError(
            f"Environment variable {env_var!r} is not set; cannot resolve database DSN."
        )
    return value


def _normalize_table_list(values: list[str] | None, default: list[str], label: str) -> list[str]:
    table_names = values if values else list(default)
    normalized = []
    seen: set[str] = set()
    for table in table_names:
        table_name = _validate_identifier(table.strip(), label)
        if table_name not in seen:
            seen.add(table_name)
            normalized.append(table_name)
    return normalized


def _parse_source_env_mappings(raw_mappings: list[str]) -> dict[str, str]:
    if not raw_mappings:
        raise MigrationConfigError("At least one --source-env mapping is required.")

    mappings: dict[str, str] = {}
    for raw in raw_mappings:
        if "=" not in raw:
            raise MigrationConfigError(
                f"Invalid --source-env mapping {raw!r}. Expected format: <schema>=<ENV_VAR>."
            )
        schema_raw, env_raw = raw.split("=", 1)
        schema = _validate_identifier(schema_raw.strip(), "schema name")
        env_var = env_raw.strip()
        if not env_var:
            raise MigrationConfigError(f"Invalid --source-env mapping {raw!r}; missing env var.")
        if schema == "shared":
            raise MigrationConfigError(
                "Schema name 'shared' is reserved; use --shared-source-env for shared schema data."
            )
        mappings[schema] = env_var
    return mappings


def _selected_schemas(
    source_mappings: dict[str, str],
    requested: list[str] | None,
) -> list[str]:
    if not requested:
        return list(source_mappings.keys())

    selected = [_validate_identifier(schema, "schema filter") for schema in requested]
    unknown = [schema for schema in selected if schema not in source_mappings]
    if unknown:
        raise MigrationConfigError(
            f"--schema contains unknown mappings: {', '.join(sorted(set(unknown)))}"
        )
    return selected


def _build_jobs(args: argparse.Namespace) -> tuple[list[TableJob], dict[str, str]]:
    source_mappings = _parse_source_env_mappings(args.source_env)
    selected = _selected_schemas(source_mappings, args.schema)
    source_schema = _validate_identifier(args.source_schema, "source schema")

    core_tables = _normalize_table_list(args.core_table, DEFAULT_CORE_TABLES, "core table")
    shared_tables = _normalize_table_list(args.shared_table, DEFAULT_SHARED_TABLES, "shared table")

    jobs: list[TableJob] = []
    for target_schema in selected:
        for table in core_tables:
            jobs.append(
                TableJob(
                    source_env_var=source_mappings[target_schema],
                    source_schema=source_schema,
                    source_table=table,
                    target_schema=target_schema,
                    target_table=table,
                    scope="core",
                )
            )

    if args.include_shared:
        shared_source_schema = _validate_identifier(
            args.shared_source_schema, "shared source schema"
        )
        for table in shared_tables:
            jobs.append(
                TableJob(
                    source_env_var=args.shared_source_env,
                    source_schema=shared_source_schema,
                    source_table=table,
                    target_schema="shared",
                    target_table=table,
                    scope="shared",
                )
            )

    if not jobs:
        raise MigrationConfigError("No table jobs were generated from the provided arguments.")
    return jobs, source_mappings


async def _table_exists(conn: asyncpg.Connection, schema: str, table: str) -> bool:
    result = await conn.fetchval(
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
    return bool(result)


async def _table_columns(conn: asyncpg.Connection, schema: str, table: str) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = $1
          AND table_name = $2
        ORDER BY ordinal_position
        """,
        schema,
        table,
    )
    return [row["column_name"] for row in rows]


async def _primary_key_columns(conn: asyncpg.Connection, schema: str, table: str) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT a.attname AS column_name
        FROM pg_index i
        JOIN pg_class c
          ON c.oid = i.indrelid
        JOIN pg_namespace n
          ON n.oid = c.relnamespace
        JOIN unnest(i.indkey) WITH ORDINALITY AS key_cols(attnum, ordinality)
          ON TRUE
        JOIN pg_attribute a
          ON a.attrelid = c.oid
         AND a.attnum = key_cols.attnum
        WHERE n.nspname = $1
          AND c.relname = $2
          AND i.indisprimary
        ORDER BY key_cols.ordinality
        """,
        schema,
        table,
    )
    return [row["column_name"] for row in rows]


async def _row_count(conn: asyncpg.Connection, schema: str, table: str) -> int:
    query = f"SELECT COUNT(*)::BIGINT FROM {_qualified_table(schema, table)}"
    value = await conn.fetchval(query)
    return int(value or 0)


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, dict):
        return {
            str(k): _normalize_value(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, set):
        normalized_items = [_normalize_value(item) for item in value]
        return sorted(normalized_items, key=lambda item: json.dumps(item, sort_keys=True))
    return str(value)


def _normalized_record_payload(row: asyncpg.Record, ordered_columns: list[str]) -> str:
    payload = {column: _normalize_value(row[column]) for column in ordered_columns}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


async def _table_fingerprint(
    conn: asyncpg.Connection,
    schema: str,
    table: str,
    columns: list[str],
    pk_columns: list[str],
    batch_size: int,
) -> tuple[int, str]:
    if not pk_columns:
        raise MigrationConfigError(
            f"Table {schema}.{table} does not define a primary key; parity cannot be deterministic."
        )

    column_sql = ", ".join(_quote_ident(column) for column in columns)
    order_sql = ", ".join(_quote_ident(column) for column in pk_columns)
    query = f"SELECT {column_sql} FROM {_qualified_table(schema, table)} ORDER BY {order_sql}"

    row_count = 0
    digest = hashlib.sha256()
    async with conn.transaction():
        statement = await conn.prepare(query)
        async for row in statement.cursor(prefetch=batch_size):
            digest.update(_normalized_record_payload(row, columns).encode("utf-8"))
            digest.update(b"\n")
            row_count += 1
    return row_count, digest.hexdigest()


async def _pk_diff_samples(
    source_conn: asyncpg.Connection,
    target_conn: asyncpg.Connection,
    source_schema: str,
    source_table: str,
    target_schema: str,
    target_table: str,
    pk_columns: list[str],
    sample_limit: int,
    max_rows: int,
) -> dict[str, Any]:
    source_count = await _row_count(source_conn, source_schema, source_table)
    target_count = await _row_count(target_conn, target_schema, target_table)
    if max(source_count, target_count) > max_rows:
        return {
            "skipped": True,
            "reason": (
                f"PK diff skipped because table size exceeds --pk-diff-max-rows ({max_rows})."
            ),
            "source_rows": source_count,
            "target_rows": target_count,
        }

    pk_sql = ", ".join(_quote_ident(column) for column in pk_columns)
    source_query = f"SELECT {pk_sql} FROM {_qualified_table(source_schema, source_table)}"
    target_query = f"SELECT {pk_sql} FROM {_qualified_table(target_schema, target_table)}"
    source_rows = await source_conn.fetch(source_query)
    target_rows = await target_conn.fetch(target_query)

    source_set = {tuple(row[column] for column in pk_columns) for row in source_rows}
    target_set = {tuple(row[column] for column in pk_columns) for row in target_rows}

    missing = sorted(source_set - target_set)[:sample_limit]
    extra = sorted(target_set - source_set)[:sample_limit]

    def _format(items: list[tuple[Any, ...]]) -> list[list[Any]]:
        return [[_normalize_value(value) for value in row] for row in items]

    return {
        "skipped": False,
        "missing_sample": _format(missing),
        "extra_sample": _format(extra),
        "missing_count": max(0, len(source_set - target_set)),
        "extra_count": max(0, len(target_set - source_set)),
    }


async def _open_connections(
    jobs: list[TableJob], target_env_var: str
) -> tuple[asyncpg.Connection, dict[str, asyncpg.Connection]]:
    target_conn = await asyncpg.connect(_require_env(target_env_var))
    source_connections: dict[str, asyncpg.Connection] = {}
    for env_var in sorted({job.source_env_var for job in jobs}):
        source_connections[env_var] = await asyncpg.connect(_require_env(env_var))
    return target_conn, source_connections


async def _close_connections(
    target_conn: asyncpg.Connection,
    source_connections: dict[str, asyncpg.Connection],
) -> None:
    await target_conn.close()
    for conn in source_connections.values():
        await conn.close()


async def _plan_job(
    source_conn: asyncpg.Connection,
    target_conn: asyncpg.Connection,
    job: TableJob,
) -> dict[str, Any]:
    source_exists = await _table_exists(source_conn, job.source_schema, job.source_table)
    target_exists = await _table_exists(target_conn, job.target_schema, job.target_table)

    result = {
        "scope": job.scope,
        "source_table": f"{job.source_schema}.{job.source_table}",
        "target_table": f"{job.target_schema}.{job.target_table}",
        "source_env_var": job.source_env_var,
        "status": "planned",
        "source_exists": source_exists,
        "target_exists": target_exists,
    }

    if source_exists:
        result["source_count"] = await _row_count(source_conn, job.source_schema, job.source_table)
    if target_exists:
        result["target_count"] = await _row_count(target_conn, job.target_schema, job.target_table)

    if not source_exists or not target_exists:
        result["status"] = "error"
        missing = []
        if not source_exists:
            missing.append("source")
        if not target_exists:
            missing.append("target")
        result["error"] = f"Missing table(s): {', '.join(missing)}"

    return result


def _insert_statement(
    schema: str,
    table: str,
    columns: list[str],
    pk_columns: list[str],
) -> str:
    column_sql = ", ".join(_quote_ident(column) for column in columns)
    placeholders = ", ".join(f"${idx}" for idx in range(1, len(columns) + 1))
    pk_sql = ", ".join(_quote_ident(column) for column in pk_columns)
    non_pk_columns = [column for column in columns if column not in set(pk_columns)]

    if non_pk_columns:
        updates = ", ".join(
            f"{_quote_ident(column)} = EXCLUDED.{_quote_ident(column)}" for column in non_pk_columns
        )
        conflict = f"ON CONFLICT ({pk_sql}) DO UPDATE SET {updates}"
    else:
        conflict = f"ON CONFLICT ({pk_sql}) DO NOTHING"

    return (
        f"INSERT INTO {_qualified_table(schema, table)} ({column_sql}) "
        f"VALUES ({placeholders}) {conflict}"
    )


async def _migrate_job(
    source_conn: asyncpg.Connection,
    target_conn: asyncpg.Connection,
    job: TableJob,
    batch_size: int,
    dry_run: bool,
    replace_target: bool,
) -> dict[str, Any]:
    source_exists = await _table_exists(source_conn, job.source_schema, job.source_table)
    target_exists = await _table_exists(target_conn, job.target_schema, job.target_table)
    if not source_exists or not target_exists:
        raise MigrationConfigError(
            f"Cannot migrate {job.source_schema}.{job.source_table} -> "
            f"{job.target_schema}.{job.target_table}: missing source or target table."
        )

    source_columns = await _table_columns(source_conn, job.source_schema, job.source_table)
    target_columns = await _table_columns(target_conn, job.target_schema, job.target_table)
    if source_columns != target_columns:
        raise MigrationConfigError(
            f"Column mismatch for {job.target_schema}.{job.target_table}: "
            f"source columns {source_columns}, target columns {target_columns}."
        )

    source_pk = await _primary_key_columns(source_conn, job.source_schema, job.source_table)
    target_pk = await _primary_key_columns(target_conn, job.target_schema, job.target_table)
    if not source_pk:
        raise MigrationConfigError(
            f"Source table {job.source_schema}.{job.source_table} has no primary key."
        )
    if source_pk != target_pk:
        raise MigrationConfigError(
            f"Primary key mismatch for {job.target_schema}.{job.target_table}: "
            f"source {source_pk}, target {target_pk}."
        )

    source_count = await _row_count(source_conn, job.source_schema, job.source_table)
    target_count_before = await _row_count(target_conn, job.target_schema, job.target_table)

    result = {
        "scope": job.scope,
        "source_table": f"{job.source_schema}.{job.source_table}",
        "target_table": f"{job.target_schema}.{job.target_table}",
        "source_env_var": job.source_env_var,
        "source_count": source_count,
        "target_count_before": target_count_before,
        "status": "planned" if dry_run else "migrated",
        "replace_target": replace_target,
    }

    if dry_run:
        result["planned_copy_rows"] = source_count
        result["target_count_after"] = target_count_before
        return result

    if replace_target:
        await target_conn.execute(
            f"TRUNCATE TABLE {_qualified_table(job.target_schema, job.target_table)} "
            "RESTART IDENTITY CASCADE"
        )

    insert_sql = _insert_statement(job.target_schema, job.target_table, source_columns, source_pk)
    select_columns_sql = ", ".join(_quote_ident(column) for column in source_columns)
    order_sql = ", ".join(_quote_ident(column) for column in source_pk)
    select_sql = (
        f"SELECT {select_columns_sql} FROM {_qualified_table(job.source_schema, job.source_table)} "
        f"ORDER BY {order_sql}"
    )

    copied_rows = 0
    batch: list[tuple[Any, ...]] = []
    async with source_conn.transaction():
        statement = await source_conn.prepare(select_sql)
        async for row in statement.cursor(prefetch=batch_size):
            batch.append(tuple(row[column] for column in source_columns))
            if len(batch) >= batch_size:
                await target_conn.executemany(insert_sql, batch)
                copied_rows += len(batch)
                batch.clear()
    if batch:
        await target_conn.executemany(insert_sql, batch)
        copied_rows += len(batch)

    target_count_after = await _row_count(target_conn, job.target_schema, job.target_table)
    result["copied_rows"] = copied_rows
    result["target_count_after"] = target_count_after
    return result


async def _verify_job(
    source_conn: asyncpg.Connection,
    target_conn: asyncpg.Connection,
    job: TableJob,
    batch_size: int,
    pk_diff_limit: int,
    pk_diff_max_rows: int,
) -> dict[str, Any]:
    source_exists = await _table_exists(source_conn, job.source_schema, job.source_table)
    target_exists = await _table_exists(target_conn, job.target_schema, job.target_table)
    if not source_exists or not target_exists:
        raise MigrationConfigError(
            f"Cannot verify {job.source_schema}.{job.source_table} -> "
            f"{job.target_schema}.{job.target_table}: missing source or target table."
        )

    source_columns = await _table_columns(source_conn, job.source_schema, job.source_table)
    target_columns = await _table_columns(target_conn, job.target_schema, job.target_table)
    if source_columns != target_columns:
        raise MigrationConfigError(
            f"Column mismatch for {job.target_schema}.{job.target_table}: "
            f"source columns {source_columns}, target columns {target_columns}."
        )

    source_pk = await _primary_key_columns(source_conn, job.source_schema, job.source_table)
    target_pk = await _primary_key_columns(target_conn, job.target_schema, job.target_table)
    if source_pk != target_pk:
        raise MigrationConfigError(
            f"Primary key mismatch for {job.target_schema}.{job.target_table}: "
            f"source {source_pk}, target {target_pk}."
        )

    source_count, source_checksum = await _table_fingerprint(
        source_conn,
        job.source_schema,
        job.source_table,
        source_columns,
        source_pk,
        batch_size,
    )
    target_count, target_checksum = await _table_fingerprint(
        target_conn,
        job.target_schema,
        job.target_table,
        target_columns,
        target_pk,
        batch_size,
    )

    result = {
        "scope": job.scope,
        "source_table": f"{job.source_schema}.{job.source_table}",
        "target_table": f"{job.target_schema}.{job.target_table}",
        "source_env_var": job.source_env_var,
        "status": "ok",
        "primary_key_columns": source_pk,
        "source_count": source_count,
        "target_count": target_count,
        "source_checksum": source_checksum,
        "target_checksum": target_checksum,
    }

    if source_count != target_count or source_checksum != target_checksum:
        diff = await _pk_diff_samples(
            source_conn,
            target_conn,
            job.source_schema,
            job.source_table,
            job.target_schema,
            job.target_table,
            source_pk,
            sample_limit=pk_diff_limit,
            max_rows=pk_diff_max_rows,
        )
        result["status"] = "mismatch"
        result["pk_diff"] = diff
        result["error"] = (
            f"Parity mismatch: counts {source_count} != {target_count} or "
            f"checksums {source_checksum} != {target_checksum}"
        )
    return result


async def _rollback_jobs(
    target_conn: asyncpg.Connection,
    jobs: list[TableJob],
    dry_run: bool,
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []
    for job in jobs:
        target = (job.target_schema, job.target_table)
        if target in seen:
            continue
        seen.add(target)
        before = await _row_count(target_conn, job.target_schema, job.target_table)
        result = {
            "target_table": f"{job.target_schema}.{job.target_table}",
            "status": "planned" if dry_run else "rolled_back",
            "target_count_before": before,
        }
        if not dry_run:
            await target_conn.execute(
                f"TRUNCATE TABLE {_qualified_table(job.target_schema, job.target_table)} "
                "RESTART IDENTITY CASCADE"
            )
        result["target_count_after"] = await _row_count(
            target_conn, job.target_schema, job.target_table
        )
        results.append(result)
    return results


def _build_report(
    args: argparse.Namespace,
    jobs: list[TableJob],
    results: list[dict[str, Any]],
    status: str,
    error: str | None = None,
) -> dict[str, Any]:
    table_total = len(results)
    ok_statuses = {"planned", "migrated", "rolled_back", "ok"}
    summary = {
        "tables_total": table_total,
        "tables_ok": sum(1 for result in results if result.get("status") in ok_statuses),
        "tables_failed": sum(
            1 for result in results if result.get("status") in {"error", "mismatch"}
        ),
    }
    return {
        "generated_at": _utc_now(),
        "action": args.command,
        "status": status,
        "error": error,
        "target_env_var": args.target_env,
        "job_count": len(jobs),
        "jobs": [
            {
                "scope": job.scope,
                "source_env_var": job.source_env_var,
                "source_table": f"{job.source_schema}.{job.source_table}",
                "target_table": f"{job.target_schema}.{job.target_table}",
            }
            for job in jobs
        ],
        "results": results,
        "summary": summary,
    }


def _write_report(report_path: Path | None, report: dict[str, Any]) -> None:
    if not report_path:
        return
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Report written to {report_path}")


def _print_results(action: str, results: list[dict[str, Any]], dry_run: bool = False) -> None:
    if action == "migrate":
        mode = "DRY-RUN" if dry_run else "MIGRATE"
        print(f"{mode} summary:")
        for result in results:
            print(
                f"  - {result['target_table']}: status={result['status']} "
                f"source_count={result.get('source_count', '?')} "
                f"target_before={result.get('target_count_before', '?')} "
                f"target_after={result.get('target_count_after', '?')}"
            )
        return

    if action == "verify":
        print("VERIFY summary:")
        for result in results:
            line = (
                f"  - {result['target_table']}: status={result['status']} "
                f"source_count={result.get('source_count', '?')} "
                f"target_count={result.get('target_count', '?')}"
            )
            if result.get("status") == "mismatch":
                line += " [MISMATCH]"
            print(line)
        return

    if action == "plan":
        print("PLAN summary:")
        for result in results:
            print(
                f"  - {result['target_table']}: status={result['status']} "
                f"source_exists={result.get('source_exists')} "
                f"target_exists={result.get('target_exists')} "
                f"source_count={result.get('source_count', '?')} "
                f"target_count={result.get('target_count', '?')}"
            )
        return

    if action == "rollback":
        mode = "ROLLBACK DRY-RUN" if dry_run else "ROLLBACK"
        print(f"{mode} summary:")
        for result in results:
            print(
                f"  - {result['target_table']}: status={result['status']} "
                f"target_before={result.get('target_count_before', '?')} "
                f"target_after={result.get('target_count_after', '?')}"
            )


def _common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target-env",
        default=DEFAULT_TARGET_ENV,
        help=f"Env var containing consolidated target DB DSN (default: {DEFAULT_TARGET_ENV}).",
    )
    parser.add_argument(
        "--source-env",
        action="append",
        default=[],
        help=(
            "Mapping of target schema to source DSN env var, "
            "e.g. general=BUTLER_GENERAL_DATABASE_URL. Repeat per schema."
        ),
    )
    parser.add_argument(
        "--schema",
        action="append",
        default=[],
        help="Schema(s) from --source-env to include. Default: all provided mappings.",
    )
    parser.add_argument(
        "--source-schema",
        default="public",
        help="Source schema containing core tables in each legacy DB (default: public).",
    )
    parser.add_argument(
        "--core-table",
        action="append",
        default=[],
        help=(
            "Core table name to migrate/verify. Repeatable. "
            "Default: state, scheduled_tasks, sessions, route_inbox."
        ),
    )
    parser.add_argument(
        "--include-shared",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include shared-table migration/verification (default: true).",
    )
    parser.add_argument(
        "--shared-source-env",
        default=DEFAULT_SHARED_SOURCE_ENV,
        help=f"Env var containing shared source DB DSN (default: {DEFAULT_SHARED_SOURCE_ENV}).",
    )
    parser.add_argument(
        "--shared-source-schema",
        default="public",
        help="Source schema containing shared tables (default: public).",
    )
    parser.add_argument(
        "--shared-table",
        action="append",
        default=[],
        help="Shared table name to migrate/verify. Repeatable. Default: butler_secrets.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Batch size for reads/writes and checksum streaming (default: 500).",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Optional JSON report output path.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-DB multi-schema migration utility (backfill + parity)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Validate table availability and row-count snapshot.")
    _common_args(plan)

    migrate = subparsers.add_parser("migrate", help="Copy/upsert rows into target schemas.")
    _common_args(migrate)
    migrate.add_argument(
        "--replace-target",
        action="store_true",
        help="Truncate target table before copying source data.",
    )
    migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write target data; emit planned migration counts only.",
    )

    verify = subparsers.add_parser("verify", help="Run deterministic parity checks.")
    _common_args(verify)
    verify.add_argument(
        "--pk-diff-limit",
        type=int,
        default=20,
        help="Max sample rows for missing/extra PK output on mismatch (default: 20).",
    )
    verify.add_argument(
        "--pk-diff-max-rows",
        type=int,
        default=100_000,
        help="Skip PK diff when either side exceeds this row count (default: 100000).",
    )

    run = subparsers.add_parser("run", help="Run migrate then verify in one command.")
    _common_args(run)
    run.add_argument(
        "--replace-target",
        action="store_true",
        help="Truncate target table before copying source data.",
    )
    run.add_argument(
        "--pk-diff-limit",
        type=int,
        default=20,
        help="Max sample rows for missing/extra PK output on mismatch (default: 20).",
    )
    run.add_argument(
        "--pk-diff-max-rows",
        type=int,
        default=100_000,
        help="Skip PK diff when either side exceeds this row count (default: 100000).",
    )

    rollback = subparsers.add_parser("rollback", help="Truncate migrated target tables.")
    _common_args(rollback)
    rollback.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what rollback would truncate without modifying target tables.",
    )
    rollback.add_argument(
        "--confirm-rollback",
        required=True,
        help="Safety guard. Must equal ROLLBACK to execute non-dry-run rollback.",
    )
    return parser


async def _run_command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    jobs, _ = _build_jobs(args)

    target_conn, source_connections = await _open_connections(jobs, args.target_env)
    results: list[dict[str, Any]] = []
    try:
        if args.command == "plan":
            for job in jobs:
                source_conn = source_connections[job.source_env_var]
                results.append(await _plan_job(source_conn, target_conn, job))
            if any(result["status"] == "error" for result in results):
                raise MigrationConfigError("PLAN found missing source/target tables.")
            _print_results("plan", results)
            return 0, _build_report(args, jobs, results, status="ok")

        if args.command == "migrate":
            for job in jobs:
                source_conn = source_connections[job.source_env_var]
                results.append(
                    await _migrate_job(
                        source_conn,
                        target_conn,
                        job,
                        batch_size=args.batch_size,
                        dry_run=args.dry_run,
                        replace_target=args.replace_target,
                    )
                )
            _print_results("migrate", results, dry_run=args.dry_run)
            return 0, _build_report(args, jobs, results, status="ok")

        if args.command == "verify":
            for job in jobs:
                source_conn = source_connections[job.source_env_var]
                results.append(
                    await _verify_job(
                        source_conn,
                        target_conn,
                        job,
                        batch_size=args.batch_size,
                        pk_diff_limit=args.pk_diff_limit,
                        pk_diff_max_rows=args.pk_diff_max_rows,
                    )
                )
            _print_results("verify", results)
            mismatches = [result for result in results if result.get("status") == "mismatch"]
            if mismatches:
                raise ParityMismatchError(mismatches)
            return 0, _build_report(args, jobs, results, status="ok")

        if args.command == "run":
            for job in jobs:
                source_conn = source_connections[job.source_env_var]
                results.append(
                    await _migrate_job(
                        source_conn,
                        target_conn,
                        job,
                        batch_size=args.batch_size,
                        dry_run=False,
                        replace_target=args.replace_target,
                    )
                )
            _print_results("migrate", results, dry_run=False)
            verify_results: list[dict[str, Any]] = []
            for job in jobs:
                source_conn = source_connections[job.source_env_var]
                verify_results.append(
                    await _verify_job(
                        source_conn,
                        target_conn,
                        job,
                        batch_size=args.batch_size,
                        pk_diff_limit=args.pk_diff_limit,
                        pk_diff_max_rows=args.pk_diff_max_rows,
                    )
                )
            _print_results("verify", verify_results)
            mismatches = [result for result in verify_results if result.get("status") == "mismatch"]
            if mismatches:
                combined = results + verify_results
                raise ParityMismatchError(mismatches=combined)
            return 0, _build_report(args, jobs, results + verify_results, status="ok")

        if args.command == "rollback":
            if not args.dry_run and args.confirm_rollback != "ROLLBACK":
                raise MigrationConfigError(
                    "--confirm-rollback must be exactly 'ROLLBACK' for non-dry-run rollback."
                )
            results = await _rollback_jobs(target_conn, jobs, dry_run=args.dry_run)
            _print_results("rollback", results, dry_run=args.dry_run)
            return 0, _build_report(args, jobs, results, status="ok")

        raise MigrationConfigError(f"Unsupported command {args.command!r}")
    finally:
        await _close_connections(target_conn, source_connections)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.batch_size <= 0:
        parser.error("--batch-size must be greater than 0")
    if hasattr(args, "pk_diff_limit") and args.pk_diff_limit <= 0:
        parser.error("--pk-diff-limit must be greater than 0")
    if hasattr(args, "pk_diff_max_rows") and args.pk_diff_max_rows <= 0:
        parser.error("--pk-diff-max-rows must be greater than 0")

    report: dict[str, Any] | None = None
    try:
        exit_code, report = asyncio.run(_run_command(args))
    except MigrationConfigError as exc:
        error = f"CONFIG ERROR: {exc}"
        print(error, file=sys.stderr)
        report = _build_report(args, [], [], status="error", error=str(exc))
        _write_report(args.report_path, report)
        return 2
    except ParityMismatchError as exc:
        print("PARITY CHECK FAILED:", file=sys.stderr)
        for mismatch in exc.mismatches:
            if mismatch.get("status") == "mismatch":
                print(
                    f"  - {mismatch.get('target_table')}: {mismatch.get('error')}", file=sys.stderr
                )
        report = _build_report(args, [], exc.mismatches, status="error", error="parity mismatch")
        _write_report(args.report_path, report)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI should fail loudly with context.
        print(f"UNEXPECTED ERROR: {exc}", file=sys.stderr)
        report = _build_report(args, [], [], status="error", error=str(exc))
        _write_report(args.report_path, report)
        return 3

    _write_report(args.report_path, report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
