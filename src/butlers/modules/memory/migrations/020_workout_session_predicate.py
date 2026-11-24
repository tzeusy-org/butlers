"""Curate the workout_session predicate without rewriting stored facts.

Revision ID: mem_020
Revises: mem_019

The local snapshot distinguishes an inserted seed from a promoted proposal
and preserves the previous definition for an exact downgrade.
"""

from __future__ import annotations

from alembic import op

revision = "mem_020"
down_revision = "mem_019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the prior definition and the mutation paired within the revision.
    op.execute("""
        CREATE TABLE IF NOT EXISTS _mem_020_workout_predicate_snapshot (
            name text PRIMARY KEY,
            previous_definition jsonb,
            installed_definition jsonb NOT NULL
        );
        LOCK TABLE predicate_registry IN SHARE ROW EXCLUSIVE MODE;

        WITH previous AS MATERIALIZED (
            SELECT to_jsonb(reg) - ARRAY['search_vector', 'usage_count', 'last_used_at']
                AS definition
            FROM predicate_registry AS reg
            WHERE name = 'workout_session' AND status = 'proposed'
        ),
        curated AS (
            INSERT INTO predicate_registry (
                name, expected_subject_type, expected_object_type, is_edge,
                description, is_temporal, scope, status, example_json
            )
            SELECT
                'workout_session', 'person', NULL, false,
                'Workout session event. valid_at is the session start. '
                'Metadata includes activity_type, duration_ms, end_time, '
                'session_id, and detection.',
                true, 'health', 'active',
                '{"content": "Recorded running session", "metadata": {
                    "activity_type": "running", "duration_ms": 1800000,
                    "end_time": "2026-04-24T09:30:00Z",
                    "session_id": "example-workout", "detection": "manual"
                }}'::jsonb
            WHERE NOT EXISTS (
                SELECT 1 FROM _mem_020_workout_predicate_snapshot
                WHERE name = 'workout_session'
            )
            ON CONFLICT (name) DO UPDATE
            SET expected_subject_type = EXCLUDED.expected_subject_type,
                expected_object_type = EXCLUDED.expected_object_type,
                is_edge = EXCLUDED.is_edge,
                description = EXCLUDED.description,
                description_embedding = NULL,
                is_temporal = EXCLUDED.is_temporal,
                scope = EXCLUDED.scope,
                status = EXCLUDED.status,
                example_json = EXCLUDED.example_json
            WHERE predicate_registry.status = 'proposed'
            RETURNING *
        )
        INSERT INTO _mem_020_workout_predicate_snapshot
            (name, previous_definition, installed_definition)
        SELECT
            curated.name,
            (SELECT definition FROM previous),
            to_jsonb(curated) - ARRAY[
                'search_vector', 'description_embedding', 'usage_count', 'last_used_at'
            ]
        FROM curated;
    """)


def downgrade() -> None:
    # A later definition edit belongs to the registry user. Leave it and the
    # snapshot intact so the operator can resolve the drift before retrying.
    op.execute("""
        LOCK TABLE predicate_registry IN SHARE ROW EXCLUSIVE MODE;
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM _mem_020_workout_predicate_snapshot AS snapshot
                LEFT JOIN predicate_registry AS reg USING (name)
                WHERE (to_jsonb(reg) - ARRAY[
                    'search_vector', 'description_embedding', 'usage_count', 'last_used_at'
                ]) IS DISTINCT FROM snapshot.installed_definition
            ) THEN
                RAISE EXCEPTION 'workout_session predicate definition changed after mem_020';
            END IF;
        END
        $$;

        DELETE FROM predicate_registry AS reg
        USING _mem_020_workout_predicate_snapshot AS snapshot
        WHERE reg.name = snapshot.name AND snapshot.previous_definition IS NULL;

        UPDATE predicate_registry AS reg
        SET expected_subject_type = previous.expected_subject_type,
            expected_object_type = previous.expected_object_type,
            is_edge = previous.is_edge,
            description = previous.description,
            description_embedding = previous.description_embedding,
            is_temporal = previous.is_temporal,
            scope = previous.scope,
            status = previous.status,
            example_json = previous.example_json
        FROM _mem_020_workout_predicate_snapshot AS snapshot
        CROSS JOIN LATERAL jsonb_populate_record(
            NULL::predicate_registry, snapshot.previous_definition
        ) AS previous
        WHERE reg.name = snapshot.name AND snapshot.previous_definition IS NOT NULL;

        DROP TABLE _mem_020_workout_predicate_snapshot;
    """)
