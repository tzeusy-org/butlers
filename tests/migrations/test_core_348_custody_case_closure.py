# ruff: noqa: F811
"""core_348 custody case closure on migrated PostgreSQL (bu-s11n0s.1 S5a, REQ-010).

A release or replace closes the episode's fleet case only when every generation held in that
episode is released or replaced. Real migrated roles through ``custody_dashboard``; the helpers
are core_347's.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.migrations.test_core_347_custody_command_engine import (  # noqa: F401
    APPLY_FN,
    dashboard,
    dbs,
    fact,
    hold_payload,
    host,
    scalar,
    session,
    unique,
)

pytestmark = [pytest.mark.integration, pytest.mark.db]


def _hold_two(dbs) -> tuple[dict, list[dict]]:
    """One command, two owner email facts: one episode, one case, two held generations."""
    facts = [fact(dbs, "has-email", unique("close-") + "@example.test") for _ in range(2)]
    held = dashboard(dbs, "hold", hold_payload(*facts), session(dbs))
    assert held["outcome"] == "held"
    assert len(held["holds"]) == 2
    return held, facts


def _resolve(dbs, action: str, hold: dict) -> dict:
    return dashboard(
        dbs,
        action,
        {
            "command_id": str(uuid.uuid4()),
            "hold_id": hold["hold_id"],
            "expected_generation": hold["generation"],
        },
        session(dbs),
    )


def _case(dbs, case_id: str) -> tuple[str, str | None]:
    with dbs["admin"].connect() as c:
        row = c.execute(
            text("SELECT state, outcome FROM public.fleet_cases WHERE id = CAST(:c AS uuid)"),
            {"c": case_id},
        ).one()
    return row[0], row[1]


def _active(dbs, episode_id: str) -> int:
    return scalar(
        dbs,
        "SELECT count(*) FROM public.custody_holds "
        "WHERE episode_id = CAST(:e AS uuid) AND released_at IS NULL",
        e=episode_id,
    )


def test_a_single_generation_release_closes_the_case(dbs) -> None:
    """REQ-010: the only held generation released closes the episode's case."""
    target = fact(dbs, "has-email", unique("single-") + "@example.test")
    held = dashboard(dbs, "hold", hold_payload(target), session(dbs))
    assert _case(dbs, held["case_id"]) == ("open", None)
    released = _resolve(dbs, "release", held["holds"][0])
    assert released["outcome"] == "released"
    assert released["case_id"] == held["case_id"]
    assert released["case_closed"] is True
    assert released["remaining_held"] == 0
    assert _case(dbs, held["case_id"]) == ("closed", "custody_resolved")


def test_a_partial_release_leaves_the_case_active_until_the_last(dbs) -> None:
    """REQ-010: a partial recovery stays active; the second release closes the same case."""
    held, _ = _hold_two(dbs)
    first, second = held["holds"]
    partial = _resolve(dbs, "release", first)
    assert partial["case_closed"] is False
    assert partial["remaining_held"] == 1
    assert _case(dbs, held["case_id"]) == ("open", None)
    assert _active(dbs, held["episode_id"]) == 1
    final = _resolve(dbs, "release", second)
    assert final["case_id"] == held["case_id"]
    assert final["case_closed"] is True
    assert final["remaining_held"] == 0
    assert _case(dbs, held["case_id"]) == ("closed", "custody_resolved")


def test_a_replace_counts_as_resolved_for_the_closure(dbs) -> None:
    """REQ-010: a replaced generation is resolved exactly like a released one."""
    held, facts = _hold_two(dbs)
    first, second = held["holds"]
    with dbs["admin"].begin() as c:
        c.execute(
            text("UPDATE relationship.entity_facts SET validity = 'superseded' WHERE id = ANY(:i)"),
            {"i": facts},
        )
    partial = _resolve(dbs, "replace", first)
    assert partial["outcome"] == "replaced"
    assert partial["case_closed"] is False
    assert _case(dbs, held["case_id"]) == ("open", None)
    final = _resolve(dbs, "release", second)
    assert final["case_closed"] is True
    assert _case(dbs, held["case_id"]) == ("closed", "custody_resolved")


def test_a_replace_of_the_last_generation_closes_the_case(dbs) -> None:
    target = fact(dbs, "has-email", unique("replaced-") + "@example.test")
    held = dashboard(dbs, "hold", hold_payload(target), session(dbs))
    with dbs["admin"].begin() as c:
        c.execute(
            text("UPDATE relationship.entity_facts SET validity = 'superseded' WHERE id = :i"),
            {"i": target},
        )
    replaced = _resolve(dbs, "replace", held["holds"][0])
    assert replaced["case_closed"] is True
    assert _case(dbs, held["case_id"]) == ("closed", "custody_resolved")


def test_an_ordinary_case_close_never_releases_a_hold(dbs) -> None:
    """REQ-010: closing the case by any other route leaves the hold held."""
    target = fact(dbs, "has-email", unique("ordinary-") + "@example.test")
    held = dashboard(dbs, "hold", hold_payload(target), session(dbs))
    with dbs["admin"].begin() as c:
        c.execute(
            text(
                "UPDATE public.fleet_cases SET state = 'closed', outcome = 'owner closed', "
                "closed_at = now() WHERE id = CAST(:c AS uuid)"
            ),
            {"c": held["case_id"]},
        )
    assert _active(dbs, held["episode_id"]) == 1
    listed = dashboard(dbs, "list", {}, session(dbs))
    assert held["holds"][0]["hold_id"] in {h["hold_id"] for h in listed["holds"]}
    # The hold is still releasable on its own, and the release does not need the closed case.
    released = _resolve(dbs, "release", held["holds"][0])
    assert released["outcome"] == "released"
    assert _active(dbs, held["episode_id"]) == 0


def test_a_release_in_one_episode_does_not_close_another_episode(dbs) -> None:
    held_a, _ = _hold_two(dbs)
    held_b, _ = _hold_two(dbs)
    assert held_a["case_id"] != held_b["case_id"]
    _resolve(dbs, "release", held_a["holds"][0])
    last = _resolve(dbs, "release", held_a["holds"][1])
    assert last["case_closed"] is True
    assert _case(dbs, held_b["case_id"]) == ("open", None)
    assert _active(dbs, held_b["episode_id"]) == 2


def test_concurrent_last_releases_close_the_case_exactly_once(dbs) -> None:
    """Locks cover every active member, so two racing last releases cannot both miss closure."""
    for _ in range(3):
        held, _ = _hold_two(dbs)
        barrier = Barrier(2)

        def release(hold: dict, barrier: Barrier = barrier) -> dict:
            proof = session(dbs)
            barrier.wait()
            return dashboard(
                dbs,
                "release",
                {
                    "command_id": str(uuid.uuid4()),
                    "hold_id": hold["hold_id"],
                    "expected_generation": hold["generation"],
                },
                proof,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(release, held["holds"]))
        assert [r["outcome"] for r in results] == ["released", "released"]
        assert sorted(r["case_closed"] for r in results) == [False, True]
        assert _case(dbs, held["case_id"]) == ("closed", "custody_resolved")
        assert _active(dbs, held["episode_id"]) == 0


def _race_release_and_rehold(dbs, held: dict, target, extra) -> tuple[dict, dict]:
    barrier = Barrier(2)
    first = held["holds"][0]

    def release() -> dict:
        proof = session(dbs)
        barrier.wait()
        return dashboard(
            dbs,
            "release",
            {
                "command_id": str(uuid.uuid4()),
                "hold_id": first["hold_id"],
                "expected_generation": first["generation"],
            },
            proof,
        )

    def rehold() -> dict:
        proof = session(dbs)
        barrier.wait()
        return dashboard(dbs, "hold", hold_payload(target, extra), proof)

    with ThreadPoolExecutor(max_workers=2) as pool:
        released, rehold_future = pool.submit(release), pool.submit(rehold)
        return released.result(), rehold_future.result()


def test_a_hold_racing_the_last_release_never_strands_or_wrongly_closes_the_case(dbs) -> None:
    """Whichever order wins, the case is closed exactly when its episode holds nothing."""
    for _ in range(3):
        target = fact(dbs, "has-email", unique("lastrace-") + "@example.test")
        extra = fact(dbs, "has-email", unique("lastrace-x-") + "@example.test")
        held = dashboard(dbs, "hold", hold_payload(target), session(dbs))
        released, rehold = _race_release_and_rehold(dbs, held, target, extra)
        assert released["outcome"] == "released"
        assert rehold["outcome"] in {"held", "already_held"}
        state, _ = _case(dbs, held["case_id"])
        assert (state == "closed") == (_active(dbs, held["episode_id"]) == 0)
        assert released["case_closed"] == (state == "closed")


def test_downgrade_restores_the_previous_body_and_reapplies(postgres_container) -> None:
    """core_348 swaps only the function body and one policy; core_347's body returns on rollback."""
    from alembic import command
    from butlers.migrations import _build_alembic_config

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "relationship"],
        schemas={"relationship": "relationship"},
        revisions={"core": "core_348"},
    )
    config = _build_alembic_config(url, chains=["core"])
    engine = create_engine(url)

    def marker() -> bool:
        with engine.connect() as c:
            return bool(
                c.execute(
                    text(
                        "SELECT position('core_348' IN pg_get_functiondef(to_regprocedure(:f))) > 0"
                    ),
                    {"f": APPLY_FN},
                ).scalar()
            )

    def close_policies() -> int:
        with engine.connect() as c:
            return c.execute(
                text(
                    "SELECT count(*) FROM pg_policies "
                    "WHERE policyname = 'fleet_cases_custody_engine_close'"
                )
            ).scalar_one()

    try:
        assert marker() and close_policies() == 1
        command.downgrade(config, "core_347")
        assert not marker() and close_policies() == 0
        with engine.connect() as c:
            assert c.execute(text("SELECT to_regclass('public.custody_commands')")).scalar()
        command.upgrade(config, "core_348")
        assert marker() and close_policies() == 1
    finally:
        engine.dispose()
