"""The owner's Vouch and Block verbs, on a real Postgres (REQ-module-calendar-042)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime

import pytest

from butlers.core.temporal.calendar_provenance import is_quarantined_invitation
from butlers.modules.calendar.organizer_decisions import (
    NoCalendarEndpoint,
    apply_decision,
    restamp_organizer,
)
from butlers.modules.calendar.organizer_verdict import VERDICT_VERSION

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_OWN = "me@example.com"
_STRANGER = "who@nowhere.test"
_ENDPOINTS = ("google_calendar:user:me@example.com", "google_calendar:user:me@work.test")
_SCHEMAS = {"relationship": "relationship", "switchboard": "switchboard"}


async def _pool(db):
    return await db("core", "relationship", "switchboard", schemas=_SCHEMAS)


async def _seed(pool, *, endpoints=_ENDPOINTS) -> None:
    for endpoint in endpoints:
        await pool.execute(
            "INSERT INTO switchboard.connector_registry (connector_type, endpoint_identity) "
            "VALUES ('google_calendar', $1)",
            endpoint,
        )
    source = await pool.fetchval(
        "INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id, "
        "display_name, writable, metadata) VALUES ('provider:google:primary', 'provider_event', "
        "'user', 'google', 'primary', 'Primary', true, '{}') RETURNING id"
    )
    for ref, organizer in (("a", _STRANGER.upper()), ("b", f" {_STRANGER} "), ("c", "x@else.test")):
        await pool.execute(
            "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, "
            "ends_at, status, metadata, source_butler) VALUES ($1, $2, 'Invitation', 'UTC', $3, "
            "$4, 'confirmed', $5, 'general')",
            source,
            ref,
            datetime(2026, 12, 1, 10, tzinfo=UTC),
            datetime(2026, 12, 1, 11, tzinfo=UTC),
            {
                "source_type": "provider_event",
                "organizer": organizer,
                "attendees": [{"email": _OWN, "self": True, "response_status": "needsAction"}],
                "organizer_verdict": "first_contact",
                "verdict_version": VERDICT_VERSION,
            },
        )


async def _rules(pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT scope, action, created_by, deleted_at FROM switchboard.ingestion_rules "
        "WHERE condition ->> 'address' = $1 ORDER BY scope, created_by",
        _STRANGER,
    )
    return [dict(r) for r in rows]


async def _state(pool) -> dict[str, tuple[str | None, bool]]:
    rows = await pool.fetch(
        "SELECT origin_ref, metadata ->> 'organizer_verdict' AS verdict, metadata "
        "FROM calendar_events"
    )
    return {r["origin_ref"]: (r["verdict"], is_quarantined_invitation(r["metadata"])) for r in rows}


async def test_vouch_writes_one_calendar_scoped_rule_per_endpoint_and_restamps(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    result = await apply_decision(pool, _STRANGER, "vouch")
    assert (result.endpoints, result.rules_written, result.rules_removed) == (2, 2, 0)
    rules = await _rules(pool)
    assert {r["scope"] for r in rules} == {
        f"connector:{e.split(':', 1)[0]}:{e}" for e in _ENDPOINTS
    }
    assert all(r["action"] == "pass_through" and r["created_by"] == "calendar_vouch" for r in rules)
    assert not any(r["scope"] == "global" for r in rules)  # never global
    assert await restamp_organizer(pool, f"  {_STRANGER.upper()}") == 2
    state = await _state(pool)
    assert state["a"] == ("vouched", False) and state["b"] == ("vouched", False)
    assert state["c"] == ("first_contact", True)  # another organizer is untouched


async def test_block_writes_a_connector_block_and_quarantines(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    await apply_decision(pool, _STRANGER, "block")
    rules = await _rules(pool)
    assert all(r["action"] == "block" and r["created_by"] == "calendar_block" for r in rules)
    await restamp_organizer(pool, _STRANGER)
    assert (await _state(pool))["a"] == ("blocked", True)


async def test_a_repeated_verb_writes_nothing_new(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    await apply_decision(pool, _STRANGER, "vouch")
    again = await apply_decision(pool, _STRANGER, "vouch")
    assert (again.rules_written, again.rules_removed) == (0, 0)
    assert len(await _rules(pool)) == 2


async def test_the_opposite_verb_replaces_the_rules_it_wrote(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    await apply_decision(pool, _STRANGER, "block")
    swapped = await apply_decision(pool, _STRANGER, "vouch")
    assert (swapped.rules_written, swapped.rules_removed) == (2, 2)
    live = [r for r in await _rules(pool) if r["deleted_at"] is None]
    assert {r["created_by"] for r in live} == {"calendar_vouch"}
    await restamp_organizer(pool, _STRANGER)
    assert (await _state(pool))["a"] == ("vouched", False)


async def test_a_vouch_does_not_override_a_global_block(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    await pool.execute(
        "INSERT INTO switchboard.ingestion_rules (scope, rule_type, condition, action, priority, "
        "name) VALUES ('global', 'sender_address', $1, 'skip', 1, 'owner block')",
        {"address": _STRANGER},
    )
    await apply_decision(pool, _STRANGER, "vouch")
    await restamp_organizer(pool, _STRANGER)
    assert (await _state(pool))["a"] == ("blocked", True)


async def test_a_vouch_does_not_remove_a_connector_block_the_owner_wrote_another_way(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    await pool.execute(
        "INSERT INTO switchboard.ingestion_rules (scope, rule_type, condition, action, priority, "
        "name, created_by) VALUES ($1, 'sender_address', $2, 'block', 50, 'manual', 'dashboard')",
        f"connector:google_calendar:{_ENDPOINTS[0]}",
        {"address": _STRANGER},
    )
    await apply_decision(pool, _STRANGER, "vouch")
    await restamp_organizer(pool, _STRANGER)
    manual = [r for r in await _rules(pool) if r["created_by"] == "dashboard"]
    assert manual and manual[0]["deleted_at"] is None
    # The Vouch rule has the lower priority number, so it matches first: the owner's latest
    # decision stands over their older connector block.
    assert (await _state(pool))["a"] == ("vouched", False)


async def test_the_rules_are_scoped_to_the_calendar_connector_only(db) -> None:
    pool = await _pool(db)
    await _seed(pool)
    await pool.execute(
        "INSERT INTO switchboard.connector_registry (connector_type, endpoint_identity) "
        "VALUES ('gmail', 'gmail:user:me@example.com')"
    )
    await apply_decision(pool, _STRANGER, "block")
    scopes = {r["scope"] for r in await _rules(pool)}
    assert all(scope.startswith("connector:google_calendar:") for scope in scopes)
    assert not any("gmail" in scope for scope in scopes)  # the same sender's email is unaffected


async def test_with_no_calendar_endpoint_there_is_no_scope_to_write_in(db) -> None:
    pool = await _pool(db)
    await _seed(pool, endpoints=())
    with pytest.raises(NoCalendarEndpoint):
        await apply_decision(pool, _STRANGER, "vouch")
    assert await _rules(pool) == []


@pytest.mark.parametrize("bad", ["", "   ", "not-an-address"])
async def test_an_organizer_without_an_address_is_refused(db, bad) -> None:
    pool = await _pool(db)
    await _seed(pool)
    with pytest.raises(ValueError):
        await apply_decision(pool, bad, "vouch")
