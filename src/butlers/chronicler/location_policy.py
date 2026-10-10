"""Owning policy read contract without projection/runtime dependencies."""

import re
from typing import Any

import asyncpg

from butlers.location_retention import strict_days


class PolicyUnavailableError(RuntimeError):
    """Missing/malformed installed authority is not a default-success policy."""


class PolicyConflictError(RuntimeError):
    """The owner must review the currently committed version before changing it."""


def _policy(row: Any) -> dict[str, Any]:
    if row is None:
        raise PolicyUnavailableError("Location retention policy is unavailable")
    try:
        days = strict_days(row["days"])
        version = row["version"]
        if type(version) is not int or version <= 0 or row["spatial_scheme_version"] != 1:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyUnavailableError("Location retention policy is unavailable") from exc
    return {
        "days": days,
        "version": version,
        "updated_at": row["updated_at"],
        "raw_age_basis": "original effective event time; four-hour server skew fallback",
        "precision_after_forgetting_m": 150,
        "widening_restores_forgotten_points": False,
        "prepared_decisions_may_finish": True,
    }


async def read_policy(pool: asyncpg.Pool) -> dict[str, Any]:
    row = await pool.fetchrow("SELECT * FROM location_retention_policy WHERE singleton")
    return _policy(row)


def closed_failure(exc: Exception) -> tuple[str, str, str]:
    """Fixed class/category and SQLSTATE; never exception text or arguments."""
    classes = (
        (PolicyUnavailableError, "policy_unavailable"),
        (PolicyConflictError, "policy_conflict"),
        (asyncpg.InsufficientPrivilegeError, "insufficient_privilege"),
        (asyncpg.UndefinedTableError, "undefined_table"),
        (asyncpg.UndefinedColumnError, "undefined_column"),
        (asyncpg.CheckViolationError, "check_violation"),
        (asyncpg.ForeignKeyViolationError, "foreign_key_violation"),
        (asyncpg.UniqueViolationError, "unique_violation"),
        (asyncpg.PostgresError, "other_postgres"),
        (AttributeError, "attribute_error"),
        (KeyError, "key_error"),
        (TypeError, "type_error"),
        (ValueError, "value_error"),
        (RuntimeError, "runtime_error"),
    )
    label = next((label for cls, label in classes if isinstance(exc, cls)), "other_native")
    category = "postgres" if isinstance(exc, asyncpg.PostgresError) else "native"
    state = getattr(exc, "sqlstate", None)
    state = state if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state) else "unknown"
    return category, label, state
