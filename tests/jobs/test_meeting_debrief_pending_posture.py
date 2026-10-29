"""``meeting_debrief_pending`` re-checks posture for attendees unresolved at snapshot time.

The snapshot keeps an unresolved attendee by raw name and email. If that email later resolves
to an entity that is not ``active`` (or to the owner), the debrief must not name them
(bu-yqxbc8). An email that still resolves to nothing is shown, as before.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from butlers.tools.relationship.meeting_debrief import meeting_debrief_pending

pytestmark = pytest.mark.unit

NOW = datetime(2031, 3, 1, 12, 0, tzinfo=UTC)


class _Pool:
    """Routes the three reads the tool makes: debrief rows, email resolution, posture."""

    def __init__(
        self,
        attendees: list[dict[str, Any]],
        people: list[dict[str, Any]],
        active_ids: set[str] | None = None,
    ) -> None:
        self._attendees = attendees
        self._people = people
        self._active_ids = active_ids or set()
        self.resolve_calls: list[list[str]] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM meeting_debriefs" in sql:
            return [
                {
                    "id": uuid.uuid4(),
                    "event_id": uuid.uuid4(),
                    "occurrence_start": NOW,
                    "event_title": "Planning",
                    "attendees": json.dumps(self._attendees),
                    "prompted_at": NOW,
                    "batch_position": 1,
                }
            ]
        if "relationship.entity_facts" in sql:
            wanted = list(args[0])
            self.resolve_calls.append(wanted)
            return [p for p in self._people if p["email"] in wanted]
        if "FROM public.entities" in sql:
            ids = {str(i) for i in args[0]}
            return [{"id": uuid.UUID(i)} for i in ids if i in self._active_ids]
        raise AssertionError(sql)


def _person(email: str, posture: str, *, owner: bool = False) -> dict[str, Any]:
    return {
        "entity_id": uuid.uuid4(),
        "email": email,
        "name": "Resolved Later",
        "posture": posture,
        "roles": ["owner"] if owner else [],
        "tombstoned": False,
    }


def _stranger(email: str = "new@example.test", name: str = "New Person") -> dict[str, Any]:
    return {"entity_id": None, "name": name, "email": email}


async def _shown(attendees, people) -> list[str]:
    result = await meeting_debrief_pending(_Pool(attendees, people))
    return [a["email"] for d in result["debriefs"] for a in d["attendees"]]


@pytest.mark.parametrize("posture", ["memorial", "quiet", "no_contact"])
async def test_unresolved_attendee_that_later_resolves_to_an_inactive_entity_is_not_named(
    posture,
) -> None:
    people = [_person("new@example.test", posture)]
    keeper = _stranger("other@example.test", "Other")
    result = await meeting_debrief_pending(_Pool([_stranger(), keeper], people))

    assert [a["email"] for a in result["debriefs"][0]["attendees"]] == ["other@example.test"]


async def test_debrief_with_only_a_withheld_attendee_is_not_listed() -> None:
    result = await meeting_debrief_pending(
        _Pool([_stranger()], [_person("new@example.test", "no_contact")])
    )

    assert result == {"debriefs": []}


async def test_unresolved_attendee_that_resolves_to_the_owner_is_not_named() -> None:
    people = [_person("new@example.test", "active", owner=True)]

    assert await _shown([_stranger()], people) == []


async def test_attendee_that_still_resolves_to_nothing_is_still_shown() -> None:
    assert await _shown([_stranger()], []) == ["new@example.test"]


async def test_unresolved_attendee_that_now_resolves_to_an_active_entity_is_shown() -> None:
    assert await _shown([_stranger()], [_person("new@example.test", "active")]) == [
        "new@example.test"
    ]


async def test_email_match_ignores_case_and_surrounding_space() -> None:
    people = [_person("new@example.test", "memorial")]

    assert await _shown([_stranger("  New@Example.TEST ")], people) == []


async def test_resolution_is_one_batched_lookup_of_only_the_unresolved_emails() -> None:
    resolved = {"entity_id": str(uuid.uuid4()), "name": "Known", "email": "known@example.test"}
    pool = _Pool([resolved, _stranger(), _stranger("b@example.test")], [], {resolved["entity_id"]})

    await meeting_debrief_pending(pool)

    assert len(pool.resolve_calls) == 1
    assert pool.resolve_calls[0] == ["b@example.test", "new@example.test"]


async def test_withholding_logs_nothing(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        await meeting_debrief_pending(
            _Pool([_stranger(name="Hidden Name")], [_person("new@example.test", "memorial")])
        )

    assert "Hidden Name" not in caplog.text and "new@example.test" not in caplog.text


@pytest.mark.parametrize("reverse", [False, True], ids=["row-order-a", "row-order-b"])
@pytest.mark.parametrize(
    "blocker",
    [
        _person("shared@example.test", "no_contact"),
        _person("shared@example.test", "memorial"),
        _person("shared@example.test", "quiet"),
        _person("shared@example.test", "active", owner=True),
    ],
    ids=["no_contact", "memorial", "quiet", "owner"],
)
async def test_shared_email_is_withheld_if_any_claimant_is_inactive_or_owner(
    blocker, reverse
) -> None:
    active_duplicate = _person("shared@example.test", "active")
    people = [blocker, active_duplicate]
    if reverse:
        people.reverse()

    assert await _shown([_stranger("shared@example.test")], people) == []


@pytest.mark.parametrize("reverse", [False, True], ids=["row-order-a", "row-order-b"])
async def test_shared_email_with_only_active_claimants_is_shown(reverse) -> None:
    people = [_person("shared@example.test", "active"), _person("shared@example.test", "active")]
    if reverse:
        people.reverse()

    assert await _shown([_stranger("shared@example.test")], people) == ["shared@example.test"]


async def test_resolver_merges_shared_email_claimants_fail_closed() -> None:
    from butlers.jobs.meeting_debrief import _merge_claimants

    def claimant(
        entity_id: str, posture: str, *, owner: bool = False, tombstoned: bool = False
    ) -> dict[str, Any]:
        return {
            "entity_id": entity_id,
            "name": "N",
            "posture": posture,
            "is_owner": owner,
            "tombstoned": tombstoned,
        }

    quiet = claimant("b", "quiet")
    owner = claimant("a", "active", owner=True)
    active = claimant("c", "active")

    assert _merge_claimants([active, quiet, owner]) == owner  # the owner outranks other blockers
    assert _merge_claimants([quiet, active]) == quiet
    assert _merge_claimants([active, {**active, "entity_id": "d"}]) is None
    assert _merge_claimants([active]) == active

    # Tombstoned (merged-away or soft-deleted) claimants: never resolve, never make it ambiguous,
    # but a non-active posture still withholds.
    gone_active = claimant("e", "active", tombstoned=True)
    gone_no_contact = claimant("f", "no_contact", tombstoned=True)
    gone_owner = claimant("g", "active", owner=True, tombstoned=True)
    assert _merge_claimants([gone_active, active]) == active
    assert _merge_claimants([gone_no_contact, active]) == gone_no_contact
    assert _merge_claimants([claimant("h", "memorial", tombstoned=True)])["posture"] == "memorial"
    assert _merge_claimants([gone_active]) is None
    assert _merge_claimants([gone_owner, active]) == active


class _ResolverPool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.sql = ""

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.sql = sql
        return self.rows


def _fact(entity_id: uuid.UUID, email: str, posture: str = "active") -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "email": email.lower(),
        "name": "Dana",
        "posture": posture,
        "roles": [],
        "tombstoned": False,
    }


async def test_one_entity_holding_an_email_in_two_casings_is_one_claimant() -> None:
    from butlers.jobs.meeting_debrief import _resolve_people

    dana = uuid.uuid4()
    pool = _ResolverPool([_fact(dana, "Dana@Example.com"), _fact(dana, "dana@example.com")])

    people = await _resolve_people(pool, {"dana@example.com"})

    assert people["dana@example.com"]["entity_id"] == str(dana)
    assert people["dana@example.com"]["name"] == "Dana"


async def test_email_ambiguous_between_distinct_active_entities_is_left_unresolved() -> None:
    from butlers.jobs.meeting_debrief import _resolve_people

    pool = _ResolverPool(
        [_fact(uuid.uuid4(), "shared@example.test"), _fact(uuid.uuid4(), "shared@example.test")]
    )

    assert await _resolve_people(pool, {"shared@example.test"}) == {}


async def test_ambiguous_attendee_keeps_the_calendar_display_name_in_the_snapshot() -> None:
    from butlers.jobs import meeting_debrief as job

    inserted: list[str] = []
    rows = [_fact(uuid.uuid4(), "shared@example.test"), _fact(uuid.uuid4(), "shared@example.test")]
    occurrence = {
        "event_id": uuid.uuid4(),
        "starts_at": NOW,
        "ends_at": NOW,
        "title": "Sync",
        "metadata": json.dumps(
            {
                "attendees": [
                    {"email": "owner@example.test", "self": True, "response_status": "accepted"},
                    {
                        "email": "shared@example.test",
                        "display_name": "Calendar Name",
                        "response_status": "accepted",
                    },
                ]
            }
        ),
    }

    class _WriterPool:
        async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
            return rows if "relationship.entity_facts" in sql else [occurrence]

        async def fetchval(self, sql: str, *args: Any) -> uuid.UUID:
            inserted.append(args[-1])
            return uuid.uuid4()

    await job._record_new_debriefs(_WriterPool(), NOW)

    assert json.loads(inserted[0]) == [
        {"entity_id": None, "name": "Calendar Name", "email": "shared@example.test"}
    ]


async def test_resolver_sql_ignores_merged_and_soft_deleted_entities() -> None:
    from butlers.jobs.meeting_debrief import _resolve_people

    pool = _ResolverPool([])
    await _resolve_people(pool, {"x@example.test"})

    assert "merged_into" in pool.sql and "deleted_at" in pool.sql


def _tombstoned(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "tombstoned": True}


async def test_deleted_no_contact_holder_still_withholds_beside_a_live_active_entity() -> None:
    people = [
        _tombstoned(_person("dana@example.test", "no_contact")),
        _person("dana@example.test", "active"),
    ]

    assert await _shown([_stranger("dana@example.test")], people) == []


async def test_deleted_memorial_holder_alone_still_withholds() -> None:
    people = [_tombstoned(_person("dana@example.test", "memorial"))]

    assert await _shown([_stranger("dana@example.test")], people) == []


async def test_deleted_active_holder_beside_a_live_active_entity_is_shown() -> None:
    people = [
        _tombstoned(_person("dana@example.test", "active")),
        _person("dana@example.test", "active"),
    ]

    assert await _shown([_stranger("dana@example.test")], people) == ["dana@example.test"]


async def test_deleted_active_holder_alone_leaves_the_attendee_shown_as_unresolved() -> None:
    people = [_tombstoned(_person("dana@example.test", "active"))]

    assert await _shown([_stranger("dana@example.test")], people) == ["dana@example.test"]
