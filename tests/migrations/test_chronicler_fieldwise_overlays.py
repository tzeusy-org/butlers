"""Correction overlay precedence on a migrated PostgreSQL database."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = pytest.mark.integration


def test_fieldwise_overlays_and_reversible_migration(postgres_container):
    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "chronicler"],
        schemas={"core": "chronicler_mem", "memory": "chronicler_mem", "chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_042"},
    )
    engine = create_engine(url)
    config = _build_alembic_config(url, chains=["chronicler"], target_schema="chronicler")
    try:
        with engine.begin() as c:
            c.execute(
                text("""INSERT INTO chronicler.source_adapter_state
                (source_name, chronicler_compatibility) VALUES ('synthetic', 'supported')""")
            )
            episode = c.execute(
                text("""INSERT INTO chronicler.episodes
                (source_name, source_ref, episode_type, start_at, end_at, title, layer)
                VALUES ('synthetic', 'episode', 'test', '2026-01-01', '2026-01-03', 'canonical', 'activity')
                RETURNING id""")
            ).scalar_one()
            event = c.execute(
                text("""INSERT INTO chronicler.point_events
                (source_name, source_ref, event_type, occurred_at, title)
                VALUES ('synthetic', 'event', 'test', '2026-01-01', 'canonical') RETURNING id""")
            ).scalar_one()
            for kind, target in (("episode", episode), ("point_event", event)):
                c.execute(
                    text("""INSERT INTO chronicler.overrides
                    (target_kind, target_id, corrected_start_at, corrected_end_at, corrected_title,
                     corrected_privacy, corrected_tombstone_at, created_at)
                    VALUES (:kind, :target, '2026-01-02', '2026-01-04', 'first', 'restricted',
                            '2026-01-05', '2026-02-01')"""),
                    {"kind": kind, "target": target},
                )
                c.execute(
                    text("""INSERT INTO chronicler.overrides
                    (target_kind, target_id, note, created_at)
                    VALUES (:kind, :target, 'note only', '2026-02-02')"""),
                    {"kind": kind, "target": target},
                )

        with engine.begin() as c:
            for table, type_column, time_column in (
                ("episodes", "episode_type", "start_at"),
                ("point_events", "event_type", "occurred_at"),
            ):
                c.execute(
                    text(f"""INSERT INTO chronicler.{table}
                    (source_name, source_ref, {type_column}, {time_column}, title)
                    VALUES ('synthetic', 'visible', 'test', '2026-01-01', 'visible')""")
                )
            canonical_before = [
                c.execute(
                    text(f"SELECT row_to_json(t)::text FROM chronicler.{table} t WHERE id=:id"),
                    {"id": target},
                ).scalar_one()
                for table, target in (("episodes", episode), ("point_events", event))
            ]

        def rows():
            with engine.connect() as c:
                return [
                    c.execute(
                        text(f"SELECT * FROM chronicler.{view} WHERE id = :id"), {"id": target}
                    )
                    .mappings()
                    .one()
                    for view, target in (
                        ("v_episodes_corrected", episode),
                        ("v_point_events_corrected", event),
                    )
                ]

        # The predecessor demonstrably loses privacy and tombstone for both kinds.
        for row in rows():
            assert row["privacy"] == "normal" and row["tombstone_at"] is None
        command.upgrade(config, "chronicler@chronicler_043")
        with engine.connect() as c:
            for view in ("v_episodes_corrected", "v_point_events_corrected"):
                visible = (
                    c.execute(
                        text(
                            f"SELECT title FROM chronicler.{view} WHERE tombstone_at IS NULL AND source_name='synthetic'"
                        )
                    )
                    .scalars()
                    .all()
                )
                assert visible == ["visible"]
        for row in rows():
            assert row["privacy"] == "restricted" and row["tombstone_at"] is not None
            assert row["title"] == "first" and row["correction_note"] == "note only"
        with engine.begin() as c:
            assert c.execute(
                text("SELECT tombstoned FROM chronicler.v_episodes_public_source WHERE id=:id"),
                {"id": episode},
            ).scalar_one()
            for kind, target in (("episode", episode), ("point_event", event)):
                c.execute(
                    text("""INSERT INTO chronicler.overrides
                    (target_kind, target_id, corrected_title, corrected_privacy, created_at)
                    VALUES (:kind, :target, 'later', 'sensitive', '2026-02-03')"""),
                    {"kind": kind, "target": target},
                )
            assert (
                c.execute(
                    text(
                        "SELECT count(*) FROM chronicler.overrides WHERE target_kind='episode' AND target_id=:id"
                    ),
                    {"id": episode},
                ).scalar_one()
                == 3
            )
            for table, target in (("episodes", episode), ("point_events", event)):
                canonical = c.execute(
                    text(
                        f"SELECT title, privacy, tombstone_at FROM chronicler.{table} WHERE id=:id"
                    ),
                    {"id": target},
                ).one()
                assert tuple(canonical) == ("canonical", "normal", None)
            public = c.execute(
                text(
                    "SELECT effective_privacy, tombstoned FROM chronicler.v_episodes_public_source WHERE id=:id"
                ),
                {"id": episode},
            ).one()
            assert tuple(public) == ("restricted", True)
        for row in rows():
            assert row["title"] == "later" and row["privacy"] == "sensitive"
            assert row["tombstone_at"] is not None
            assert row["start_at" if "start_at" in row else "occurred_at"].day == 2
        assert rows()[0]["end_at"].day == 4
        with engine.connect() as c:
            canonical_after = [
                c.execute(
                    text(f"SELECT row_to_json(t)::text FROM chronicler.{table} t WHERE id=:id"),
                    {"id": target},
                ).scalar_one()
                for table, target in (("episodes", episode), ("point_events", event))
            ]
            assert canonical_before == canonical_after
        command.downgrade(config, "chronicler@chronicler_042")
        for row in rows():
            assert row["title"] == "later" and row["tombstone_at"] is None
        command.upgrade(config, "chronicler@chronicler_043")
        assert all(row["tombstone_at"] is not None for row in rows())
    finally:
        engine.dispose()
