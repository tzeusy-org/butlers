#!/usr/bin/env python3
"""Exact-selection metadata repair. Live plan/apply require separate operator allocation.

Credentials enter on stdin only. Public output contains closed statuses and hashes;
the mandatory beforeimage is private, outside the checkout, and never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import select
import stat
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import UUID

from butlers.db import Database

ROOT = Path(__file__).resolve().parents[1]
ROLE = "butler_switchboard_rw"
MAX_ROWS = 128
MAX_TEXT = 16384
MAX_FILE = 262144
SQL_TIMEOUT = 30
OPERATION_TIMEOUT = 120
CLEANUP_TIMEOUT = 5
STATUSES = {"repair", "object", "sql_null", "refused"}
HASH = re.compile(r"[0-9a-f]{64}\Z")
ROW_KEYS = {"id", "status", "before", "after", "other"}
PLAN_KEYS = {"version", "source", "target", "rows"}

# Only text values cross the JSONB codec boundary. All nonmetadata columns are
# fingerprinted by PostgreSQL, including future columns, without exposing them.
READ_ROWS = """
SELECT id, jsonb_typeof(metadata) AS kind,
       CASE WHEN octet_length(metadata::text) <= 16384 THEN metadata::text END AS text,
       encode(sha256(convert_to(COALESCE(metadata::text, 'SQLNULL'), 'UTF8')), 'hex')
           AS fingerprint,
       encode(sha256(convert_to((to_jsonb(l) - 'metadata')::text, 'UTF8')), 'hex') AS other
FROM public.attention_ledger AS l WHERE id = ANY($1::uuid[]) ORDER BY id
"""
JSON_HASH = "SELECT encode(sha256(convert_to($1::text::jsonb::text, 'UTF8')), 'hex')"
UPDATE = "UPDATE public.attention_ledger SET metadata = $2::text::jsonb WHERE id = $1"


class Refused(Exception):
    """A closed operator refusal; never constructed from private driver text."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused("invalid_json")
        result[key] = value
    return result


def _constant(_value):
    raise Refused("invalid_json")


def strict_json(text: str):
    """Validate bounded strict JSON without rounding any number for later writes."""
    if len(text.encode("utf-8")) > MAX_TEXT:
        raise Refused("invalid_json")
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 32:
                raise Refused("invalid_json")
        elif char in "]}":
            depth -= 1
    try:
        return json.loads(
            text, object_pairs_hook=_pairs, parse_float=Decimal, parse_constant=_constant
        )
    except (ValueError, RecursionError, UnicodeError):
        raise Refused("invalid_json") from None


def classify(kind: str | None, text: str | None) -> tuple[str, str | None]:
    if kind is None:
        return "sql_null", None
    if text is None:
        return "refused", None
    if kind == "object":
        return "object", None
    if kind != "string":
        return "refused", None
    try:
        inner = strict_json(text)
        if isinstance(inner, str) and isinstance(strict_json(inner), dict):
            return "repair", inner
    except (Refused, UnicodeError):
        pass
    return "refused", None


def source_digest() -> str:
    digest = hashlib.sha256()
    for path in (
        Path(__file__).resolve(),
        ROOT / "src/butlers/db.py",
        ROOT / "src/butlers/core/attention_ledger.py",
        ROOT / "alembic/versions/core/core_160_attention_ledger.py",
    ):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def selection(values) -> list[UUID]:
    try:
        ids = [UUID(str(value)) for value in values]
    except (ValueError, TypeError, AttributeError):
        raise Refused("invalid_selection") from None
    if not 1 <= len(ids) <= MAX_ROWS or len(set(ids)) != len(ids):
        raise Refused("invalid_selection")
    return sorted(ids)


def validate_plan(value) -> dict:
    if not isinstance(value, dict) or set(value) != PLAN_KEYS or value["version"] != 1:
        raise Refused("invalid_plan")
    if type(value["version"]) is not int:
        raise Refused("invalid_plan")
    for key in ("source", "target"):
        if not isinstance(value[key], str) or not HASH.fullmatch(value[key]):
            raise Refused("invalid_plan")
    rows = value["rows"]
    if not isinstance(rows, list):
        raise Refused("invalid_plan")
    ids = selection([r.get("id") if isinstance(r, dict) else None for r in rows])
    if [str(i) for i in ids] != [r["id"] for r in rows]:
        raise Refused("invalid_plan")
    for row in rows:
        if (
            set(row) != ROW_KEYS
            or not isinstance(row["status"], str)
            or row["status"] not in STATUSES
        ):
            raise Refused("invalid_plan")
        for key in ("before", "other"):
            if not isinstance(row[key], str) or not HASH.fullmatch(row[key]):
                raise Refused("invalid_plan")
        after = row["after"]
        if row["status"] == "refused":
            if after is not None:
                raise Refused("invalid_plan")
        elif not isinstance(after, str) or not HASH.fullmatch(after):
            raise Refused("invalid_plan")
        elif row["status"] != "repair" and after != row["before"]:
            raise Refused("invalid_plan")
    return value


def read_plan(path: Path, *, expected_sha256: str | None = None) -> dict:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise Refused("invalid_plan")
            raw = stream.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE or b"\0" in raw:
            raise Refused("invalid_plan")
        if expected_sha256 is not None and (
            not isinstance(expected_sha256, str)
            or not HASH.fullmatch(expected_sha256)
            or hashlib.sha256(raw).hexdigest() != expected_sha256
        ):
            raise Refused("plan_hash_mismatch")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        return validate_plan(value)
    except (OSError, ValueError, UnicodeError, RecursionError, TypeError):
        raise Refused("invalid_plan") from None


def write_private(path: Path, value: dict) -> None:
    """Publish a complete 0600 file without replacing any existing destination."""
    path = path.absolute()
    parent = path.parent.resolve(strict=True)
    repository_roots = [ROOT, *(p for p in ROOT.parents if (p / ".git").is_dir())]
    if any(parent == root or root in parent.parents for root in repository_roots):
        raise Refused("private_path_required")
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=True) + "\n").encode()
    if len(raw) > MAX_ROWS * (MAX_TEXT + 1024):
        raise Refused("private_file_overbound")
    fd, temporary = tempfile.mkstemp(prefix=".attention-repair-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, parent / path.name, follow_symlinks=False)
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        raise Refused("private_file_failed") from None
    finally:
        os.unlink(temporary)


async def _identity(conn, target: str) -> str:
    row = await conn.fetchrow(
        """SELECT current_database() AS db, session_user AS login, current_user AS actor,
        inet_server_addr()::text AS address, inet_server_port() AS port,
        d.oid::text AS db_oid, c.oid::text AS table_oid, c.relkind::text AS kind,
        r.rolsuper OR r.rolbypassrls OR r.rolcreaterole AS elevated
        FROM pg_database d, pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
             pg_roles r
        WHERE d.datname=current_database() AND n.nspname='public'
          AND c.relname='attention_ledger' AND r.rolname=current_user""",
        timeout=SQL_TIMEOUT,
    )
    if row is None or row["actor"] != ROLE or row["elevated"] or row["kind"] != "r":
        raise Refused("wrong_database_identity")
    return hashlib.sha256(json.dumps([target, dict(row)], sort_keys=True).encode()).hexdigest()


async def _rows(conn, ids, *, lock=False):
    rows = await conn.fetch(READ_ROWS + (" FOR UPDATE" if lock else ""), ids, timeout=SQL_TIMEOUT)
    if [r["id"] for r in rows] != ids:
        raise Refused("missing_or_unreadable_row")
    return rows


async def _entry(conn, row):
    status, inner = classify(row["kind"], row["text"])
    after = None if status == "refused" else row["fingerprint"]
    if status == "repair":
        after = await conn.fetchval(JSON_HASH, inner, timeout=SQL_TIMEOUT)
    return {
        "id": str(row["id"]),
        "status": status,
        "before": row["fingerprint"],
        "after": after,
        "other": row["other"],
    }, inner


async def plan_repair(pool, ids, *, target: str) -> dict:
    ids = selection(ids)
    async with pool.acquire(timeout=SQL_TIMEOUT) as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            identity = await _identity(conn, target)
            rows = await _rows(conn, ids)
            entries = [(await _entry(conn, row))[0] for row in rows]
    return validate_plan(
        {"version": 1, "source": source_digest(), "target": identity, "rows": entries}
    )


async def apply_repair(pool, plan: dict, *, target: str, beforeimages: Path) -> tuple[dict, dict]:
    plan = validate_plan(plan)
    if plan["source"] != source_digest():
        raise Refused("source_drift")
    ids = selection(r["id"] for r in plan["rows"])
    result = {"updated": 0, "already_applied": 0, "unchanged": 0}
    async with pool.acquire(timeout=SQL_TIMEOUT) as conn:
        async with conn.transaction(isolation="serializable"):
            if await _identity(conn, target) != plan["target"]:
                raise Refused("target_drift")
            rows = await _rows(conn, ids, lock=True)
            changes = []
            for expected, row in zip(plan["rows"], rows, strict=True):
                entry, inner = await _entry(conn, row)
                if row["other"] != expected["other"] or expected["status"] == "refused":
                    raise Refused("row_drift_or_refused")
                if (
                    expected["status"] == "repair"
                    and row["kind"] == "object"
                    and row["fingerprint"] == expected["after"]
                ):
                    result["already_applied"] += 1
                elif entry != expected:
                    raise Refused("row_drift_or_refused")
                elif inner is not None:
                    changes.append((row["id"], inner))
                else:
                    result["unchanged"] += 1
            # Exact original string representations are retained before any
            # write/commit. This private artifact is not itself an apply receipt.
            write_private(
                beforeimages,
                {
                    "plan": plan,
                    "beforeimages": [{"id": str(r["id"]), "text": r["text"]} for r in rows],
                },
            )
            for row_id, inner in changes:
                if await conn.execute(UPDATE, row_id, inner, timeout=SQL_TIMEOUT) != "UPDATE 1":
                    raise Refused("update_failed")
                result["updated"] += 1
    expected_texts = {r["id"]: classify(r["kind"], r["text"])[1] or r["text"] for r in rows}
    return result, expected_texts


async def verify_repair(pool, plan: dict, *, target: str, expected_texts: dict) -> None:
    """Caller supplies a separately opened pool after the apply pool is closed."""
    validate_plan(plan)
    async with pool.acquire(timeout=SQL_TIMEOUT) as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            if await _identity(conn, target) != plan["target"]:
                raise Refused("readback_failed_after_commit")
            rows = await _rows(conn, selection(r["id"] for r in plan["rows"]))
            for expected, row in zip(plan["rows"], rows, strict=True):
                if row["other"] != expected["other"] or row["fingerprint"] != expected["after"]:
                    raise Refused("readback_failed_after_commit")
                if expected["status"] == "repair" and row["kind"] != "object":
                    raise Refused("readback_failed_after_commit")
                # Compare with the privately held intended text, including the
                # actual session reader expression, on an independent lease.
                if (
                    await conn.fetchval(
                        """SELECT metadata IS NOT DISTINCT FROM $2::text::jsonb
                    AND (metadata->>'session_id') IS NOT DISTINCT FROM
                    ($2::text::jsonb->>'session_id')
                    FROM public.attention_ledger WHERE id=$1""",
                        row["id"],
                        expected_texts[row["id"]],
                        timeout=SQL_TIMEOUT,
                    )
                    is not True
                ):
                    raise Refused("readback_failed_after_commit")


def connection_options(dsn: str) -> tuple[dict, str]:
    try:
        if (
            len(dsn.encode()) > MAX_TEXT
            or any(ord(c) < 32 for c in dsn)
            or re.search(r"%(?![0-9A-Fa-f]{2})", dsn)
        ):
            raise ValueError
        url = urlsplit(dsn)
        query = parse_qsl(url.query, strict_parsing=True)
        if (
            url.scheme not in {"postgres", "postgresql"}
            or not url.hostname
            or not url.port
            or not url.username
            or not url.password
            or url.fragment
            or len(query) != 1
            or query[0][0] != "sslmode"
            or query[0][1] not in {"disable", "require", "verify-ca", "verify-full"}
            or not url.path.startswith("/")
            or len(url.path) < 2
        ):
            raise ValueError
        options = {
            "host": url.hostname,
            "port": url.port,
            "user": unquote(url.username, errors="strict"),
            "password": unquote(url.password, errors="strict"),
            "db_name": unquote(url.path[1:], errors="strict"),
            "ssl": query[0][1],
        }
        if any(not str(v) or any(ord(c) < 32 for c in str(v)) for v in options.values()):
            raise ValueError
    except (ValueError, UnicodeError):
        raise Refused("connection_input_invalid") from None
    public_target = {k: v for k, v in options.items() if k != "password"}
    return options, hashlib.sha256(json.dumps(public_target, sort_keys=True).encode()).hexdigest()


@asynccontextmanager
async def open_database(options):
    database = Database(**options, role=ROLE, min_pool_size=1, max_pool_size=1)
    primary = None
    try:
        pool = await asyncio.wait_for(database.connect(), SQL_TIMEOUT)
        if database.role_enforcement_disabled:
            raise Refused("wrong_database_identity")
        yield pool
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if database.pool is not None:
            try:
                await asyncio.wait_for(database.close(), CLEANUP_TIMEOUT)
            except BaseException as cleanup:
                try:
                    database.pool.terminate()
                except Exception:
                    pass
                if primary is None or (
                    isinstance(cleanup, asyncio.CancelledError) and isinstance(primary, Exception)
                ):
                    raise


def read_connection(deadline: float) -> str:
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([sys.stdin.fileno()], [], [], remaining)[0]:
            raise Refused("connection_input_timeout")
        chunk = os.read(sys.stdin.fileno(), min(4096, MAX_TEXT + 1 - len(data)))
        data.extend(chunk)
        newline = data.find(b"\n")
        if len(data) > MAX_TEXT:
            raise Refused("connection_input_invalid")
        if newline >= 0 or not chunk:
            try:
                return bytes(data[:newline] if newline >= 0 else data).decode("utf-8")
            except UnicodeError:
                raise Refused("connection_input_invalid") from None


async def run(args, dsn: str) -> dict:
    options, target = connection_options(dsn)
    if args.apply_plan:
        if args.row_id or not args.beforeimages or not args.plan_sha256:
            raise Refused("invalid_arguments")
        plan = read_plan(args.apply_plan, expected_sha256=args.plan_sha256)
        async with open_database(options) as pool:
            result, expected_texts = await apply_repair(
                pool, plan, target=target, beforeimages=args.beforeimages
            )
        async with open_database(options) as independent_pool:
            await verify_repair(
                independent_pool, plan, target=target, expected_texts=expected_texts
            )
        return {"status": "committed_readback_verified", **result}
    if args.beforeimages or args.plan_sha256:
        raise Refused("invalid_arguments")
    ids = selection(args.row_id)
    async with open_database(options) as pool:
        return await plan_repair(pool, ids, target=target)


def _hard_timeout() -> None:
    # wait_for waits for cancellation to settle. A stuck BEGIN/COMMIT/ROLLBACK,
    # pool reset or driver finalizer must not extend this standalone process
    # indefinitely. Process exit closes its sockets; a commit race is UNKNOWN.
    try:
        os.set_blocking(2, False)
        os.write(2, b'{"status":"timeout_outcome_unknown"}\n')
    except OSError:
        pass
    finally:
        os._exit(124)


def main() -> int:
    class ClosedParser(argparse.ArgumentParser):
        def error(self, message):
            raise Refused("invalid_arguments")

    parser = ClosedParser(description=__doc__)
    parser.add_argument("--row-id", action="append", default=[])
    parser.add_argument("--apply-plan", type=Path)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--beforeimages", type=Path)
    # This command owns its process. Suppress dependency log payloads at the
    # boundary and emit only closed errors; credentials never enter argv.
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    watchdog = threading.Timer(OPERATION_TIMEOUT + CLEANUP_TIMEOUT, _hard_timeout)
    watchdog.name = "attention-metadata-deadline"
    watchdog.daemon = True
    watchdog.start()
    deadline = time.monotonic() + OPERATION_TIMEOUT
    try:
        args = parser.parse_args()
        dsn = read_connection(deadline)
        result = asyncio.run(asyncio.wait_for(run(args, dsn), deadline - time.monotonic()))
        print(json.dumps(result, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        print('{"status":"cancelled"}', file=sys.stderr)
        return 130
    except Exception:
        print('{"status":"refused_or_failed"}', file=sys.stderr)
        return 2
    finally:
        watchdog.cancel()
        watchdog.join(timeout=CLEANUP_TIMEOUT)
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())
