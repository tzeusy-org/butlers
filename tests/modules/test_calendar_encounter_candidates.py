"""bu-5ii4gj.3 E1: who may count as met at one occurrence, from its participant rows (pure)."""

from __future__ import annotations

import uuid

import pytest

from butlers.modules.calendar.participants import encounter_candidates

pytestmark = pytest.mark.unit

ALICE, BOB = uuid.UUID(int=1), uuid.UUID(int=2)


def _row(resolution, status="accepted", role="required", entity_id=None):
    return {
        "resolution": resolution,
        "response_status": status,
        "role": role,
        "entity_id": entity_id,
    }


def test_an_accepted_owner_and_an_exact_guest_count():
    found = encounter_candidates([_row("owner"), _row("exact", entity_id=ALICE)])

    assert found.counted and found.entity_ids == (ALICE,) and found.owner_rows == 1


@pytest.mark.parametrize("status", ["declined", "tentative", "needsAction"])
def test_an_owner_who_did_not_accept_counts_nothing(status):
    found = encounter_candidates([_row("owner", status), _row("exact", entity_id=ALICE)])

    assert not found.counted and found.entity_ids == ()


def test_no_owner_row_counts_nothing():
    assert not encounter_candidates([_row("exact", entity_id=ALICE)]).counted


def test_a_declined_guest_a_resource_and_the_unknown_are_not_met():
    found = encounter_candidates(
        [
            _row("owner"),
            _row("exact", "declined", entity_id=ALICE),
            _row("resource", role="resource"),
            _row("exact", role="resource", entity_id=BOB),
            _row("unresolved"),
            _row("ambiguous"),
        ]
    )

    assert found.counted and found.entity_ids == () and found.unresolved == 2


def test_a_manual_link_counts_and_a_repeated_guest_counts_once():
    found = encounter_candidates(
        [
            _row("owner"),
            _row("manual", "needsAction", entity_id=ALICE),
            _row("exact", entity_id=ALICE),
        ]
    )

    assert found.entity_ids == (ALICE,)


def test_an_owner_organised_event_with_no_owner_row_counts():
    rows = [_row("exact", entity_id=ALICE)]

    assert encounter_candidates(rows, owner_organized=True).entity_ids == (ALICE,)
    assert not encounter_candidates(rows).counted


def test_an_owner_rows_own_answer_beats_the_organizer_fallback():
    rows = [_row("owner", "declined"), _row("exact", entity_id=ALICE)]

    assert not encounter_candidates(rows, owner_organized=True).counted
