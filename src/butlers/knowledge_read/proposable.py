"""The ``propose_fact`` predicate allowlist, loaded from ``proposable.toml`` (RFC 0039 D13)."""

from __future__ import annotations

import tomllib
from functools import cache
from importlib.resources import files


class ProposableError(ValueError):
    """The packaged allowlist is malformed; the server must not start with it."""


def load_proposable(data: bytes | None = None) -> tuple[str, ...]:
    """The allowed predicates in file order, from the packaged file or explicit bytes."""
    try:
        raw = tomllib.loads(
            (
                data
                if data is not None
                else files(__package__).joinpath("proposable.toml").read_bytes()
            ).decode()
        )
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ProposableError("proposable: invalid TOML/encoding") from exc
    predicates = raw.get("predicates")
    if set(raw) != {"predicates"} or not isinstance(predicates, list) or not predicates:
        raise ProposableError("proposable: expected exactly a non-empty 'predicates' list")
    if not all(isinstance(p, str) and p and p == p.strip() for p in predicates):
        raise ProposableError("proposable: every predicate must be a non-empty trimmed string")
    if len(set(predicates)) != len(predicates):
        raise ProposableError("proposable: duplicate predicate")
    return tuple(predicates)


@cache
def proposable_predicates() -> tuple[str, ...]:
    return load_proposable()
