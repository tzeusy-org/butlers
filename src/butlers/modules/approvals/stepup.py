"""Action-bound standing-rule admission on the owning database transaction.

Only creation, approve-with-rule and promotion use this interface. Tightening
and ordinary decisions do not. The private auth routine is the authority; an
actor id, callback, ContextVar or caller boolean cannot manufacture a factor.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.core.dashboard_stepup import dashboard_stepup_proof
from butlers.modules.approvals.executor import _approval_write_transaction

REQUIRED = {"error": "dashboard_step_up_required", "open_dashboard": "/approvals"}
KINDS = frozenset({"create", "from_action", "approve_with_rule", "promotion"})


class _Refused(Exception):
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result


@dataclass(frozen=True, repr=False)
class RuleBinding:
    kind: str
    target: str
    request_digest: str
    action_digest: str
    namespace: str


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError("Unsupported action binding value")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_json_value
    ).encode()
    if len(encoded) > 65536:
        raise ValueError("Action binding too large")
    return hashlib.sha256(encoded).hexdigest()


def _policy_version() -> str:
    package = Path(__file__).resolve().parent
    root = package.parents[3]
    # A butler may have its own database with public as its schema. Never
    # interpret a submitted label or current_schema() as a roster identity.
    # Bind the finite trusted policy inventory conservatively: any configured
    # policy/code change requires fresh confirmation, including cross-pool use.
    policies = sorted((root / "roster").glob("*/butler.toml"))
    if not policies:
        raise ValueError("Action policy unavailable")
    paths = [*policies, *sorted(package.glob("*.py")), root / "src/butlers/config.py"]
    return hashlib.sha256(
        b"\0".join(
            str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() for path in paths
        )
    ).hexdigest()


async def bind_rule_action(
    connection: Any, kind: str, parameters: dict[str, Any], *, lock: bool = False
) -> RuleBinding:
    """Bind final submitted fields and the actual receiver-derived source/version.

    Issuance reads a snapshot without retaining a domain lock while accessing
    the auth pool. Consumption re-derives it with the source locked. Namespace
    comes from this connection, never a submitted butler/actor label.
    """
    if kind not in KINDS:
        raise ValueError("Unsupported step-up consumer")
    namespace = await connection.fetchval("SELECT current_database() || ':' || current_schema()")
    if not isinstance(namespace, str) or not namespace:
        raise ValueError("Action namespace unavailable")
    if kind == "approve_with_rule":
        parameters = {key: value for key, value in parameters.items() if key != "create_rule"}
    request = {
        "kind": kind,
        "parameters": parameters,
        "namespace": namespace,
        "version": 1,
        "policy_version": _policy_version(),
    }
    source = None
    target = "new"
    if kind != "create":
        key = "suggestion_id" if kind == "promotion" else "action_id"
        target = str(UUID(str(parameters[key])))
        # Keep both relation names literal; only the closed consumer and lock
        # mode select them, never an input SQL identifier or an allowlist waiver.
        query = (
            "SELECT * FROM autonomy_suggestions WHERE id = $1"
            if kind == "promotion"
            else "SELECT * FROM pending_actions WHERE id = $1"
        )
        if lock:
            query += " FOR UPDATE"
        row = await connection.fetchrow(query, UUID(target))
        if row is None:
            raise ValueError("Step-up source unavailable")
        source = dict(row)
    return RuleBinding(kind, target, _digest(request), _digest([request, source]), namespace)


class _WriteResult(dict):
    def __init__(self, value: dict, *, deferred: bool = False):
        super().__init__(value)
        self.deferred = deferred


def is_deferred(result: dict) -> bool:
    return isinstance(result, _WriteResult) and result.deferred


class _CommittedResult(_WriteResult):
    """A receipt read is an outcome lookup, never another effect request."""


def is_receipt(result: dict) -> bool:
    return isinstance(result, _CommittedResult)


def _result_reference(binding: RuleBinding, result: dict) -> dict:
    """Keep only closed durable references in the dump-readable proof store.

    Approval rows retain their existing privacy and retention policies. No
    tool arguments, descriptions, evidence, challenges or credentials are
    duplicated into authorization storage.
    """
    identity = str(UUID(str(result["id"])))
    if binding.kind == "approve_with_rule":
        rule_id = str(UUID(str(result["created_rule"]["id"])))
    elif binding.kind == "promotion":
        rule_id = str(UUID(str(result["resulting_rule_id"])))
    else:
        rule_id = identity
    if binding.kind not in {"create", "from_action"} and identity != binding.target:
        raise RuntimeError("Step-up result identity mismatch")
    return {"version": 1, "kind": binding.kind, "id": identity, "rule_id": rule_id}


async def _read_result(connection: Any, binding: RuleBinding, reference: Any) -> dict:
    from butlers.modules.approvals.models import ApprovalRule, PendingAction

    if not isinstance(reference, dict) or set(reference) != {"version", "kind", "id", "rule_id"}:
        raise RuntimeError("Step-up receipt unavailable")
    if type(reference["version"]) is not int or reference["version"] != 1:
        raise RuntimeError("Step-up receipt unavailable")
    if reference["kind"] != binding.kind:
        raise RuntimeError("Step-up receipt unavailable")
    identity, rule_id = UUID(reference["id"]), UUID(reference["rule_id"])
    if binding.kind in {"create", "from_action"}:
        if identity != rule_id:
            raise RuntimeError("Step-up receipt unavailable")
        row = await connection.fetchrow("SELECT * FROM approval_rules WHERE id=$1", rule_id)
        if row is None:
            raise RuntimeError("Step-up receipt unavailable")
        return _CommittedResult(ApprovalRule.from_row(row).to_dict())
    if str(identity) != binding.target:
        raise RuntimeError("Step-up receipt unavailable")
    if binding.kind == "promotion":
        from butlers.modules.approvals.autonomy_suggestions import _row_to_dict

        row = await connection.fetchrow("SELECT * FROM autonomy_suggestions WHERE id=$1", identity)
        if row is None or row["resulting_rule_id"] != rule_id:
            raise RuntimeError("Step-up receipt unavailable")
        return _CommittedResult(_row_to_dict(row))
    row = await connection.fetchrow("SELECT * FROM pending_actions WHERE id=$1", identity)
    rule = await connection.fetchrow("SELECT * FROM approval_rules WHERE id=$1", rule_id)
    if row is None or rule is None:
        raise RuntimeError("Step-up receipt unavailable")
    result = _CommittedResult(PendingAction.from_row(row).to_dict())
    result["created_rule"] = ApprovalRule.from_row(rule).to_dict()
    return result


async def _store(connection: Any, phase: str, binding: RuleBinding, result=None) -> dict:
    proof = dashboard_stepup_proof.get()
    if proof is None:
        return {"state": "refused"}
    # No SQL spans/parameters or auth exceptions may export nonce/session data.
    with suppress_instrumentation():
        raw = await connection.fetchval(
            "SELECT dashboard_stepup.consume($1,$2,$3,$4,$5,$6,$7,$8::jsonb)",
            phase,
            proof.nonce,
            proof.session_digest,
            binding.kind,
            binding.target,
            binding.request_digest,
            binding.action_digest,
            json.loads(json.dumps(result, default=_json_value, allow_nan=False))
            if result is not None
            else None,
        )
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict):
        raise RuntimeError("Step-up storage unavailable")
    return value


@dataclass
class _EnlistedWrite:
    connection: Any
    kind: str
    child_used: bool = False


_enlisted_write: ContextVar[_EnlistedWrite | None] = ContextVar("enlisted_rule_write", default=None)


async def run_rule_write(
    pool: Any,
    kind: str,
    parameters: dict[str, Any],
    writer: Callable[[Any], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Consume once, write and retain the exact result in one owning scope.

    A borrowed connection gets a savepoint, never a new pool checkout/commit.
    Exceptions and refused writers roll back both nonce and authority changes.
    The outer API still owns its audit/commit and post-commit side effects.
    """
    parent = _enlisted_write.get()
    if parent is not None and parent.connection is pool:
        expected = {"approve_with_rule": "approve_with_rule", "promotion": "create"}.get(
            parent.kind
        )
        if expected != kind or parent.child_used:
            raise RuntimeError("Step-up child write mismatch")
        parent.child_used = True
        return await writer(pool)
    if dashboard_stepup_proof.get() is None:
        return dict(REQUIRED)
    state = getattr(pool, "is_in_transaction", None)
    borrowed = callable(state) and state()
    try:
        async with _approval_write_transaction(pool) as connection:
            in_transaction = getattr(connection, "is_in_transaction", None)
            if not callable(in_transaction) or not in_transaction():
                raise RuntimeError("Step-up requires an owning transaction")
            binding = await bind_rule_action(connection, kind, parameters, lock=True)
            admission = await _store(connection, "begin", binding)
            if admission.get("state") == "completed":
                return await _read_result(connection, binding, admission.get("result"))
            if admission.get("state") != "admitted":
                raise _Refused(dict(REQUIRED))
            token = _enlisted_write.set(_EnlistedWrite(connection, kind))
            try:
                result = await writer(connection)
            finally:
                _enlisted_write.reset(token)
            if not isinstance(result, dict) or "error" in result:
                raise _Refused(result if isinstance(result, dict) else dict(REQUIRED))
            retained = await _store(
                connection, "finish", binding, _result_reference(binding, result)
            )
            if retained.get("state") != "completed":
                raise RuntimeError("Step-up receipt unavailable")
            return _WriteResult(result, deferred=borrowed)
    except _Refused as exc:
        return exc.result
    except (
        asyncpg.InvalidSchemaNameError,
        asyncpg.UndefinedTableError,
        asyncpg.UndefinedFunctionError,
        asyncpg.InsufficientPrivilegeError,
    ):
        # A different database or unavailable trusted interface never falls
        # back to a separately committed auth-pool transaction.
        return dict(REQUIRED)


async def remember_rule(writer: Any, result: dict) -> None:
    """Best-effort descriptive memory, only after a new committed decision."""
    if writer is None or "error" in result or is_receipt(result) or is_deferred(result):
        return
    rule = result.get("created_rule", result)
    if "arg_constraints" not in rule:
        return
    from butlers.modules.approvals.models import ApprovalRule

    try:
        await writer.record_standing_rule(ApprovalRule.from_dict(rule), active=True)
    except Exception:
        logging.getLogger(__name__).exception("Post-commit standing-rule memory writeback failed")


@asynccontextmanager
async def descriptive_savepoint(connection: Any):
    """Retain fail-open tracker semantics inside an enlisted SQL transaction."""
    enlisted = _enlisted_write.get()
    if enlisted is not None and enlisted.connection is connection:
        async with connection.transaction():
            yield connection
    else:
        yield connection
