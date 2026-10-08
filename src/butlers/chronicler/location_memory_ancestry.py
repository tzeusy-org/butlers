"""Complete immutable native parent bundles; no authority from shrinking joins."""

from __future__ import annotations

from typing import Any

from butlers.chronicler.location_policy import PolicyUnavailableError


def require_complete_parents(rows: list[Any]) -> None:
    """Check the original declared count and every captured parent's actual birth.

    Callers retain the frozen header and declared parents with LEFT JOINs. An
    empty result denotes no native record; a native header without parents must
    remain a row and refuse. Multiple output births for one parent are allowed,
    but none may disappear because its immutable digest differs.
    """
    if not rows:
        return
    expected = rows[0]["parent_count"]
    if (
        type(expected) is not int
        or expected < 1
        or len({(row["copy_generation"], row["input_digest"]) for row in rows}) != expected
        or any(
            row["parent_count"] != expected
            or row["copy_generation"] is None
            or row["output_id"] is None
            or row["birth_digest"] != row["input_digest"]
            for row in rows
        )
    ):
        raise PolicyUnavailableError("Native complete input ancestry differs")
