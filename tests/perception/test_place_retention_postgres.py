"""bu-dhloi1.350: owner.place history past the 400-day horizon is purged, and nothing else is.

Real PostgreSQL through the fixed installer. The purge is the one sanctioned delete of perception
history: ``public.perception_purge_place_history`` (EXECUTE for the ``perception_retention``
identity only) running as ``perception_owner``, with the immutable trigger re-checking kind and age
on every row. Rows are written through the real writer function with historical ``at`` values.
"""

from __future__ import annotations

from datetime import UTC, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.perception.factories import request, wire_time
from tests.perception.test_perception_postgres import call, prepared_database, write

pytestmark = [pytest.mark.integration, pytest.mark.db]


@pytest.fixture(scope="module")
def database(postgres_container):
    with prepared_database(postgres_container) as data:
        yield data


@pytest.fixture
def db(database):
    with database[1].connect() as c:
        tx = c.begin()
        try:
            yield c
        finally:
            tx.rollback()


PURGE = "SELECT public.perception_purge_place_history(:batch, :dry)"


def _now(c):
    return c.execute(text("SELECT clock_timestamp()")).scalar_one().astimezone(UTC)


def _old(c, key, *, days, generation, kind="owner.place", retracts=None, minutes=0, outside=False):
    """One event whose ``at`` is ``days`` before the database clock (a row older than the cutoff
    when days > 400)."""
    at = _now(c) - timedelta(days=days) + timedelta(minutes=minutes)
    body = request(key=key, kind=kind, generation=generation)
    if outside:
        body["typed_value_or_ref"] = {
            "presence": "outside",
            "place_ref": None,
            "locality_ref": None,
        }
    for field in ("observed_at", "at", "confirmed_at"):
        body[field] = wire_time(at)
    body["effective_span"] = {"start": wire_time(at), "end": wire_time(at + timedelta(hours=1))}
    body["evidence_refs"][0]["expires_at"] = wire_time(at + timedelta(hours=1))
    body["retracts_event_id"] = retracts
    role = "perception_dots_writer" if kind == "owner.place" else "perception_context_writer"
    return write(c, body, role)


def _purge(c, *, batch=100, dry=False, role="perception_retention"):
    return call(c, PURGE, {"batch": batch, "dry": dry}, role)


def _exists(c, event_id) -> bool:
    return bool(
        c.execute(
            text("SELECT count(*) FROM public.timeline WHERE id = :id"), {"id": event_id}
        ).scalar_one()
    )


def _receipt(c, event_id) -> bool:
    return bool(
        c.execute(
            text("SELECT count(*) FROM perception_private.receipts WHERE event_id = :id"),
            {"id": event_id},
        ).scalar_one()
    )


def test_old_transitions_their_retraction_and_receipts_are_purged(db):
    first = _old(db, "first", days=450, generation=0)
    leave = _old(db, "leave", days=450, generation=1, minutes=1, outside=True)
    enter = _old(db, "enter", days=450, generation=2, minutes=9, retracts=leave["event_id"])
    ids = [first["event_id"], leave["event_id"], enter["event_id"], enter["retraction_event_id"]]
    assert all(_exists(db, i) and _receipt(db, i) for i in ids)

    result = _purge(db)

    assert result["dry_run"] is False and result["events"] == 3 and result["retractions"] == 1
    assert not any(_exists(db, i) or _receipt(db, i) for i in ids)
    assert db.execute(text("SELECT count(*) FROM public.timeline")).scalar_one() == 0


def test_a_row_inside_the_horizon_stays_and_one_past_it_goes(db):
    outside = _old(db, "outside", days=401, generation=0)
    inside = _old(db, "inside", days=399, generation=1)

    result = _purge(db)

    assert result["events"] == 1
    assert _exists(db, inside["event_id"]) and _receipt(db, inside["event_id"])
    assert not _exists(db, outside["event_id"])


def test_an_old_row_of_another_kind_stays(db):
    activity = _old(db, "act", days=500, generation=0, kind="owner.activity")

    assert _purge(db)["events"] == 0
    assert _exists(db, activity["event_id"]) and _receipt(db, activity["event_id"])


def test_a_retraction_inside_the_horizon_keeps_its_target(db):
    first = _old(db, "first", days=400, generation=0, minutes=-3)
    target = _old(db, "leave", days=400, generation=1, minutes=-2, outside=True)
    enter = _old(db, "enter", days=400, generation=2, minutes=6, retracts=target["event_id"])

    # Only the unretracted old row goes; the retracted target and its retraction stay together.
    assert _purge(db)["events"] == 1
    assert not _exists(db, first["event_id"])
    assert _exists(db, target["event_id"]) and _exists(db, enter["retraction_event_id"])


def test_the_batch_limit_is_respected_oldest_first(db):
    rows = [_old(db, f"k{i}", days=460 - i, generation=i) for i in range(3)]

    assert _purge(db, batch=2)["events"] == 2
    assert [_exists(db, r["event_id"]) for r in rows] == [False, False, True]
    assert _purge(db, batch=2)["events"] == 1
    assert not _exists(db, rows[2]["event_id"])


def test_a_dry_run_reports_exactly_what_the_real_run_deletes(db):
    first = _old(db, "first", days=450, generation=0)
    leave = _old(db, "leave", days=450, generation=1, minutes=1, outside=True)
    _old(db, "enter", days=450, generation=2, minutes=9, retracts=leave["event_id"])
    _old(db, "young", days=10, generation=3)
    counts = db.execute(text("SELECT count(*) FROM perception_private.receipts")).scalar_one()

    dry = _purge(db, dry=True)
    assert (
        db.execute(text("SELECT count(*) FROM perception_private.receipts")).scalar_one() == counts
    )
    real = _purge(db)

    assert dry["receipts"] > 0
    assert {**dry, "dry_run": False} == real
    assert not _exists(db, first["event_id"])


def test_a_dry_run_counts_and_changes_nothing(db):
    old = _old(db, "dry", days=450, generation=0)

    result = _purge(db, dry=True)

    assert result["dry_run"] is True and result["events"] == 1
    assert _exists(db, old["event_id"]) and _receipt(db, old["event_id"])


def test_only_the_retention_identity_may_run_it(db):
    _old(db, "x", days=450, generation=0)
    for role in ("perception_dots_writer", "perception_owner_reader", "perception_owner"):
        with pytest.raises(DBAPIError):
            _purge(db, role=role)


def _refused(c, role, delete_sql, *, marker=False) -> bool:
    with pytest.raises(DBAPIError) as refused:
        with c.begin_nested():
            c.execute(text(f'SET LOCAL ROLE "{role}"'))
            if marker:
                c.execute(text("SELECT set_config('perception.purge', 'on', true)"))
            c.execute(text(delete_sql))
    return "immutable" in str(refused.value) or "permission denied" in str(refused.value)


def test_a_direct_delete_still_raises_for_every_role_marker_or_not(db):
    old = _old(db, "d", days=450, generation=0)
    delete = f"DELETE FROM public.timeline WHERE id = '{old['event_id']}'"
    receipt = f"DELETE FROM perception_private.receipts WHERE event_id = '{old['event_id']}'"
    for role in (
        "perception_retention",
        "perception_dots_writer",
        "perception_owner_reader",
        "butler_general_rw",
    ):
        for marker in (False, True):
            assert _refused(db, role, delete, marker=marker), (role, marker)
    # The owner itself is refused without the function's marker; a session that sets the marker
    # as another role (superuser included) is not perception_owner, so it is refused too.
    assert _refused(db, "perception_owner", delete)
    assert _refused(db, "perception_owner", receipt)
    db.execute(text("SELECT set_config('perception.purge', 'on', true)"))
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text(delete))
    assert _exists(db, old["event_id"]) and _receipt(db, old["event_id"])


def test_even_the_owner_with_the_marker_cannot_delete_a_fresh_or_foreign_row(db):
    fresh = _old(db, "fresh", days=10, generation=0)
    activity = _old(db, "act", days=500, generation=0, kind="owner.activity")
    for event in (fresh, activity):
        delete = f"DELETE FROM public.timeline WHERE id = '{event['event_id']}'"
        assert _refused(db, "perception_owner", delete, marker=True)
        receipt = f"DELETE FROM perception_private.receipts WHERE event_id = '{event['event_id']}'"
        assert _refused(db, "perception_owner", receipt, marker=True)
    assert _exists(db, fresh["event_id"]) and _exists(db, activity["event_id"])


def test_updates_are_still_refused_for_every_role(db):
    old = _old(db, "u", days=450, generation=0)
    update = f"UPDATE public.timeline SET zone = 'x' WHERE id = '{old['event_id']}'"
    for role in ("perception_owner", "perception_retention"):
        for marker in (False, True):
            assert _refused(db, role, update, marker=marker), (role, marker)
