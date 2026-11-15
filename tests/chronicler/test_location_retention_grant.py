"""The prepared retention grant's input contract (bu-s11n0s.7, slice 4a).

The eligibility rule itself is proved against Postgres in
``tests/integration/test_owntracks_retention_grant_integration.py``. These tests pin what the
preparer refuses before it touches the database, and that a missing policy writes nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from butlers.chronicler.location_retention import PolicyUnavailable
from butlers.chronicler.location_retention_grant import StaleRevisions, prepare_retention_grant
from butlers.chronicler.projection_coverage import COVERED_ADAPTERS
from butlers.chronicler.storage import owntracks_projection_lock_key

pytestmark = pytest.mark.unit

_REVISIONS = {adapter: "v1:" + "a" * 64 for adapter in COVERED_ADAPTERS}


class _Untouched:
    """A connection that fails the test if the preparer uses it."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"connection used: {name}")


class _NoPolicy:
    """A connection whose policy row is missing; records every statement."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def transaction(self) -> _NoPolicy:
        return self

    async def __aenter__(self) -> _NoPolicy:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def fetchrow(self, query: str, *args: Any) -> None:
        self.statements.append(query)
        return None

    async def execute(self, query: str, *args: Any) -> str:
        self.statements.append(query)
        return ""

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.statements.append(query)
        return []

    async def executemany(self, query: str, args: Any) -> None:
        self.statements.append(query)


@pytest.mark.parametrize(
    "revisions",
    [
        pytest.param({}, id="none"),
        pytest.param(
            {k: v for k, v in _REVISIONS.items() if k != "owntracks.ssid_presence"},
            id="an-adapter-missing",
        ),
        pytest.param({**_REVISIONS, "owntracks.other": "v1:" + "a" * 64}, id="an-unknown-adapter"),
        pytest.param({**_REVISIONS, "owntracks.points": "legacy"}, id="a-legacy-revision"),
        pytest.param({**_REVISIONS, "owntracks.points": "v1:abc"}, id="a-malformed-revision"),
    ],
)
async def test_revisions_must_name_every_covered_adapter_exactly(revisions: dict) -> None:
    with pytest.raises(ValueError, match="config_revisions"):
        await prepare_retention_grant(_Untouched(), config_revisions=revisions)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
async def test_the_limit_must_be_a_positive_integer(limit: Any) -> None:
    with pytest.raises(ValueError, match="limit"):
        await prepare_retention_grant(_Untouched(), config_revisions=_REVISIONS, limit=limit)


def test_preparation_takes_the_same_locks_the_projections_hold() -> None:
    # The keys the projections have always hashed: changing one would let a run interleave.
    assert [owntracks_projection_lock_key(a) for a in sorted(COVERED_ADAPTERS)] == [
        "chronicler:trail:owntracks.place_cluster",
        "chronicler:trail:owntracks.points",
        "chronicler.projection:owntracks.ssid_presence",
    ]
    with pytest.raises(ValueError):
        owntracks_projection_lock_key("google_calendar.completed")


async def test_a_missing_policy_fails_closed_and_writes_nothing() -> None:
    conn = _NoPolicy()
    with pytest.raises(PolicyUnavailable):
        await prepare_retention_grant(conn, config_revisions=_REVISIONS)
    assert not [s for s in conn.statements if "INSERT" in s.upper()]


class _Attempts(_NoPolicy):
    """A connection with a policy row and the given per-adapter attempt revisions."""

    def __init__(self, attempts: dict[str, str]) -> None:
        super().__init__()
        self.attempts = attempts

    async def fetchrow(self, query: str, *args: Any) -> dict[str, int]:
        self.statements.append(query)
        return {"days": 30, "version": 1}

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.statements.append(query)
        return None

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.statements.append(query)
        if "location_projection_attempts" in query:
            return [{"adapter_name": a, "config_revision": r} for a, r in self.attempts.items()]
        return []


@pytest.mark.parametrize(
    "attempts",
    [
        pytest.param({}, id="no-attempts"),
        pytest.param(
            {k: v for k, v in _REVISIONS.items() if k != "owntracks.points"},
            id="an-adapter-never-ran",
        ),
        pytest.param({**_REVISIONS, "owntracks.points": "v1:" + "b" * 64}, id="a-stale-revision"),
    ],
)
async def test_a_revision_the_adapters_did_not_last_run_with_is_refused(attempts: dict) -> None:
    """S4b (1): the caller's revisions are cross-checked inside the grant transaction."""
    conn = _Attempts(attempts)
    with pytest.raises(StaleRevisions):
        await prepare_retention_grant(conn, config_revisions=_REVISIONS)
    assert not [s for s in conn.statements if "INSERT" in s.upper()]
    # Checked after the locks, so no projection run can change the record in between.
    first_attempts = next(i for i, s in enumerate(conn.statements) if "attempts" in s)
    assert first_attempts > max(i for i, s in enumerate(conn.statements) if "advisory" in s)


async def test_matching_attempt_revisions_let_preparation_proceed() -> None:
    conn = _Attempts(dict(_REVISIONS))
    grant = await prepare_retention_grant(conn, config_revisions=_REVISIONS)
    assert grant.item_count == 0
    assert any("INSERT INTO location_retention_grants" in s for s in conn.statements)
