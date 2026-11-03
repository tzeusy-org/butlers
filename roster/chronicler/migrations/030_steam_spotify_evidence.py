"""Route Steam and Spotify source history through situational fusion (bu-r3fmja.7).

Revision ID: chronicler_030
Revises: chronicler_029

Preserve row identities, provenance, corrections and tombstones; change only the
base layer. Daily Steam totals remain fallback evidence. Spotify stays useful as
a quiet-run breaker but never counts as Play. Downgrade preserves evidence:
restoring automatic counted rows would double-count existing derived blocks.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_030"
down_revision = "chronicler_029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Runtime and migration share this same-database, transaction-scoped key.
    op.execute("SELECT pg_advisory_xact_lock(1128813128, 5471)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS daily_repair_control (
            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            replay_epoch BIGINT NOT NULL DEFAULT 1,
            replay_ack BIGINT NOT NULL DEFAULT 0,
            timezone TEXT NOT NULL,
            closed_through DATE NOT NULL,
            blocked_reason TEXT
        );
        INSERT INTO daily_repair_control (timezone, closed_through, blocked_reason)
        SELECT coalesce(min(timezone), 'Asia/Singapore'),
               (now() AT TIME ZONE coalesce(min(timezone), 'Asia/Singapore'))::date - 1,
               CASE WHEN count(DISTINCT timezone) > 1 THEN 'mixed rollup timezones' END
        FROM daily_rollups
        ON CONFLICT (singleton) DO NOTHING;
        CREATE TABLE IF NOT EXISTS daily_repair_days (
            local_date DATE PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            timezone TEXT NOT NULL,
            dirty_generation BIGINT NOT NULL DEFAULT 1,
            built_generation BIGINT NOT NULL DEFAULT 0,
            totals_pending BOOLEAN NOT NULL DEFAULT TRUE,
            outlier_pending BOOLEAN NOT NULL DEFAULT TRUE,
            facts_pending BOOLEAN NOT NULL DEFAULT TRUE,
            published_revision BIGINT NOT NULL DEFAULT 0,
            historical_flags_status TEXT NOT NULL DEFAULT 'unknown',
            prose_status TEXT NOT NULL DEFAULT 'pending',
            availability_snapshot JSONB,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS daily_repair_archive (
            local_date DATE NOT NULL,
            generation BIGINT NOT NULL,
            row_kind TEXT NOT NULL,
            row_key TEXT NOT NULL,
            audit_hash TEXT,
            content JSONB,
            dependency_ids UUID[] NOT NULL DEFAULT '{}',
            text_access TEXT NOT NULL DEFAULT 'audit_only'
                CHECK (text_access IN ('audit_only', 'redacted')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (local_date, generation, row_kind, row_key)
        );
        REVOKE ALL ON daily_repair_archive FROM PUBLIC;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION enqueue_daily_repair(span_start TIMESTAMPTZ,
                                                        span_end TIMESTAMPTZ,
                                                        force_history BOOLEAN DEFAULT FALSE)
        RETURNS VOID LANGUAGE plpgsql SET search_path = chronicler, public AS $$
        DECLARE ctl daily_repair_control%ROWTYPE; d DATE; last_day DATE;
        BEGIN
            IF span_start IS NULL THEN RETURN; END IF;
            IF NOT pg_try_advisory_xact_lock(1128813128, 5471) THEN
                RAISE EXCEPTION 'daily repair busy' USING ERRCODE = '55P03';
            END IF;
            SELECT * INTO STRICT ctl FROM daily_repair_control WHERE singleton;
            ctl.closed_through := greatest(ctl.closed_through,
                (now() AT TIME ZONE ctl.timezone)::date - 1);
            last_day := least(ctl.closed_through,
                ((coalesce(span_end, (ctl.closed_through + 1)::timestamp
                    AT TIME ZONE ctl.timezone) - interval '1 microsecond')
                    AT TIME ZONE ctl.timezone)::date);
            FOR d IN SELECT generate_series((span_start AT TIME ZONE ctl.timezone)::date,
                                            last_day, interval '1 day')::date LOOP
                -- Only a new uncached yesterday belongs to the ordinary first
                -- day-close (every allowed lookback includes it). Older uncached
                -- changes still queue durably; upgrade forces every affected date.
                IF NOT force_history AND d = ctl.closed_through
                    AND NOT EXISTS (SELECT 1 FROM daily_rollups WHERE local_date = d)
                    AND NOT EXISTS (SELECT 1 FROM daily_repair_days WHERE local_date = d
                                    AND (totals_pending OR built_generation > 0)) THEN
                    CONTINUE;
                END IF;
                INSERT INTO daily_repair_days (local_date, timezone)
                VALUES (d, ctl.timezone)
                ON CONFLICT (local_date) DO UPDATE SET
                    dirty_generation = daily_repair_days.dirty_generation + 1,
                    totals_pending = TRUE, outlier_pending = TRUE, facts_pending = TRUE,
                    prose_status = 'pending', updated_at = now();
                -- Only materialized dependent dates need flag reconstruction.
                INSERT INTO daily_repair_days
                    (local_date, timezone, totals_pending, outlier_pending)
                SELECT DISTINCT r.local_date, r.timezone, FALSE, TRUE FROM daily_rollups r
                WHERE r.local_date > d AND r.local_date <= d + 14
                ON CONFLICT (local_date) DO UPDATE SET
                    dirty_generation = daily_repair_days.dirty_generation + 1,
                    outlier_pending = TRUE, facts_pending = TRUE, updated_at = now();
                -- The 28-day memory horizon is distinct from the 14-day flags horizon.
                INSERT INTO daily_repair_days
                    (local_date, timezone, totals_pending, outlier_pending)
                SELECT x::date, ctl.timezone, FALSE, FALSE
                FROM generate_series(d + 1, least(d + 28, ctl.closed_through),
                                     interval '1 day') x
                ON CONFLICT (local_date) DO UPDATE SET
                    dirty_generation = daily_repair_days.dirty_generation + 1,
                    facts_pending = TRUE, updated_at = now();
            END LOOP;
        END $$;
    """)
    # Capture old/current intervals and derived provenance before re-layering.
    op.execute("""
        SELECT enqueue_daily_repair(start_at, end_at, TRUE) FROM v_episodes_corrected
        WHERE source_name IN ('steam.play_history', 'spotify.session_summary',
                              'steam.status_history');
        SELECT enqueue_daily_repair(canonical_start_at, canonical_end_at, TRUE)
        FROM v_episodes_corrected
        WHERE source_name IN ('steam.play_history', 'spotify.session_summary',
                              'steam.status_history');
        SELECT enqueue_daily_repair(start_at, end_at, TRUE) FROM episodes derived
        WHERE source_name = 'chronicler.situation_inferred'
          AND EXISTS (SELECT 1 FROM episodes evidence
                      WHERE evidence.source_name IN ('steam.play_history',
                          'spotify.session_summary', 'steam.status_history')
                        AND derived.evidence_refs ? evidence.id::text);
    """)
    op.execute("""
        UPDATE episodes SET layer = 'evidence', updated_at = now()
        WHERE (source_name = 'steam.play_history' AND episode_type = 'play_episode')
           OR (source_name = 'spotify.session_summary' AND episode_type = 'listening_episode')
    """)

    # One initial replay rebuilds historical fallback Play from evidence, not
    # just the adapter's normal three-day trailing window.
    op.execute("""
        UPDATE projection_checkpoints SET watermark = NULL, watermark_id = NULL
        WHERE source_name = 'chronicler.situation_inferred'
    """)
    # The replay acknowledgement is published atomically with its checkpoint.
    # Any real old-date correction queues work in its own source transaction.
    op.execute("""
        CREATE OR REPLACE FUNCTION invalidate_daily_repair_episode()
        RETURNS TRIGGER LANGUAGE plpgsql SET search_path = chronicler, public AS $$
        DECLARE item RECORD; target UUID;
                override_start TIMESTAMPTZ; override_end TIMESTAMPTZ;
        BEGIN
            IF TG_TABLE_NAME = 'episodes' THEN
                IF TG_OP = 'UPDATE' AND
                    (to_jsonb(OLD) - ARRAY['updated_at', 'created_at']) =
                    (to_jsonb(NEW) - ARRAY['updated_at', 'created_at']) THEN
                    RETURN NULL;
                END IF;
                IF NOT pg_try_advisory_xact_lock(1128813128, 5471) THEN
                    RAISE EXCEPTION 'daily repair busy' USING ERRCODE = '55P03';
                END IF;
                -- Use the same winning row as v_episodes_corrected. In particular,
                -- NULL timing fields fall back to THIS base row, never to a stale
                -- override. After DELETE the override still exists, unlike the view.
                SELECT corrected_start_at, corrected_end_at
                INTO override_start, override_end FROM v_latest_overrides
                WHERE target_kind = 'episode' AND target_id = coalesce(NEW.id, OLD.id);
                IF TG_OP <> 'INSERT' THEN
                    PERFORM enqueue_daily_repair(OLD.start_at, OLD.end_at);
                    PERFORM enqueue_daily_repair(coalesce(override_start, OLD.start_at),
                                                 coalesce(override_end, OLD.end_at));
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    PERFORM enqueue_daily_repair(NEW.start_at, NEW.end_at);
                    PERFORM enqueue_daily_repair(coalesce(override_start, NEW.start_at),
                                                 coalesce(override_end, NEW.end_at));
                END IF;
                IF TG_OP = 'DELETE' OR NEW.privacy = 'restricted'
                    OR NEW.tombstone_at IS NOT NULL THEN
                    UPDATE daily_repair_archive SET content = NULL, audit_hash = NULL, text_access = 'redacted'
                    WHERE coalesce(NEW.id, OLD.id) = ANY(dependency_ids);
                END IF;
                IF coalesce(NEW.source_name, OLD.source_name)
                    <> 'chronicler.situation_inferred' THEN
                    UPDATE daily_repair_control SET replay_epoch = replay_epoch + 1
                    WHERE singleton;
                END IF;
            ELSE
                target := coalesce(NEW.target_id, OLD.target_id);
                IF coalesce(NEW.target_kind, OLD.target_kind) <> 'episode' THEN
                    RETURN NULL;
                END IF;
                FOR item IN SELECT start_at, end_at FROM episodes WHERE id = target
                    UNION ALL SELECT start_at, end_at FROM v_episodes_corrected WHERE id = target
                    UNION ALL SELECT corrected_start_at, corrected_end_at FROM overrides
                        WHERE target_kind = 'episode' AND target_id = target
                LOOP
                    PERFORM enqueue_daily_repair(item.start_at, item.end_at);
                END LOOP;
                UPDATE daily_repair_control SET replay_epoch = replay_epoch + 1
                WHERE singleton;
                -- Timing/note corrections preserve historical text. Only privacy
                -- or tombstone corrections require removal of copied content.
                IF coalesce(NEW.corrected_privacy, OLD.corrected_privacy) = 'restricted'
                    OR coalesce(NEW.corrected_tombstone_at,
                                OLD.corrected_tombstone_at) IS NOT NULL THEN
                    UPDATE daily_repair_archive
                    SET content = NULL, audit_hash = NULL, text_access = 'redacted'
                    WHERE target = ANY(dependency_ids);
                END IF;
            END IF;
            RETURN NULL;
        END $$;
        DROP TRIGGER IF EXISTS episodes_daily_repair ON episodes;
        CREATE TRIGGER episodes_daily_repair AFTER INSERT OR UPDATE OR DELETE ON episodes
            FOR EACH ROW EXECUTE FUNCTION invalidate_daily_repair_episode();
        DROP TRIGGER IF EXISTS overrides_daily_repair ON overrides;
        CREATE TRIGGER overrides_daily_repair AFTER INSERT OR UPDATE OR DELETE ON overrides
            FOR EACH ROW EXECUTE FUNCTION invalidate_daily_repair_episode();
    """)


def downgrade() -> None:
    pass
