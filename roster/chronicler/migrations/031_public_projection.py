"""Source-owned tables the public MCP projection publishes for Chronicler (RFC 0039 D2).

Revision ID: chronicler_031
Revises: chronicler_030

REQ-public-mcp-projection-006 (bu-y66o00.3, bu-y66o00.14). Raw ``episodes``, their payloads and
``overrides`` are never published, because a row filter on ``episodes.privacy`` would publish rows
the owner later restricted. This adds:

* ``episodes_public``: one row per non-evidence episode, with no payload. ``effective_privacy`` and
  ``tombstoned`` are in the replica identity, so a later restriction reaches the subscriber as a
  DELETE.
* ``rollups_public``: minutes per local day and per ``category`` or ``kind``, the union of the
  activity-layer episodes that may be published.
* ``v_episodes_public_source``: the one definition both are kept equal to.
* A trigger on ``overrides`` that rewrites both in the override's transaction, so no writer of
  ``overrides`` can skip it. ``butlers.chronicler.public_projection`` sweeps every 15 minutes,
  adding new episodes and repairing drift. ``category`` comes from Python
  (``aggregations.category_for``), so the sweep fills the tables; this migration leaves them empty.

Sweep and trigger share one advisory lock. Without it a sweep that read an episode before an
override committed could write its stale values after the trigger's, publishing a row the owner
had just restricted.

Where the view differs from ``v_episodes_corrected``, it only withholds more:

* ``effective_privacy`` folds privacy overrides oldest first. An owner override sets the value and
  any other override can only raise it, so a model can never republish an episode.
* ``tombstoned`` holds once any override tombstones the episode. ``v_latest_overrides`` reads only
  the latest row, so a later note-only override would otherwise resurrect it.
* An episode ``daily_repair_source_redactions`` names counts as tombstoned, as
  ``materialize_daily_rollups`` excludes it. The exception is reason ``source_privacy``: chronicler_030
  redacts on any restriction and never removes the row, so honoring it would stop the owner lifting one.
  A later correction can overwrite the reason, which is why the tombstone rule above reads overrides.
* A calendar title that fell back to the event's location or description is replaced, because
  people who invite the owner write that text (bu-y66o00.14). A row without ``title_source`` falls
  back to whether its payload holds an event title. A corrected title is kept.

The evidence layer is raw signal, which RFC 0039 D4 withholds, so it has no row here.
"""

from __future__ import annotations

from alembic import op
from butlers.migrations.projection import drop_replica_identity, ensure_replica_identity

revision = "chronicler_031"
down_revision = "chronicler_030"
branch_labels = None
depends_on = None

_ROLE = "butler_chronicler_rw"
_CALENDAR_SOURCE = "google_calendar.completed"
_LOCK = "hashtextextended('butlers:chronicler:public-projection', 0)"
_EPISODES = "chronicler.episodes_public"
_ROLLUPS = "chronicler.rollups_public"
_REFRESH = "chronicler.refresh_rollups_public(timestamptz,timestamptz)"
_SYNC = "chronicler.sync_episode_public_from_override()"
_PRIVACY = "chronicler.episode_effective_privacy(uuid,text)"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE {_EPISODES} (
            id UUID PRIMARY KEY REFERENCES chronicler.episodes(id) ON DELETE CASCADE,
            started_at TIMESTAMPTZ NOT NULL,
            ended_at TIMESTAMPTZ,
            kind TEXT NOT NULL,
            title TEXT,
            category TEXT NOT NULL,
            effective_privacy TEXT NOT NULL
                CHECK (effective_privacy IN ('normal', 'sensitive', 'restricted')),
            tombstoned BOOLEAN NOT NULL
        )
    """)
    op.execute(f"""
        CREATE TABLE {_ROLLUPS} (
            local_date DATE NOT NULL,
            group_kind TEXT NOT NULL CHECK (group_kind IN ('category', 'kind')),
            group_key TEXT NOT NULL,
            minutes INTEGER NOT NULL CHECK (minutes > 0),
            PRIMARY KEY (local_date, group_kind, group_key)
        )
    """)
    ensure_replica_identity(_EPISODES, ("id",), ("effective_privacy", "tombstoned"))
    op.execute(f"""
        CREATE FUNCTION {_PRIVACY.replace("(uuid,text)", "(p_episode uuid, p_base text)")}
        RETURNS text LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp
        AS $body$
        DECLARE
            label text := p_base;
            item record;
            rank_of constant jsonb := '{{"normal": 0, "sensitive": 1, "restricted": 2}}';
        BEGIN
            FOR item IN
                SELECT corrected_privacy, submitted_by
                FROM chronicler.overrides
                WHERE target_kind = 'episode' AND target_id = p_episode
                  AND corrected_privacy IS NOT NULL
                ORDER BY created_at, id
            LOOP
                IF item.submitted_by = 'owner'
                   OR (rank_of ->> item.corrected_privacy)::int > (rank_of ->> label)::int THEN
                    label := item.corrected_privacy;
                END IF;
            END LOOP;
            RETURN label;
        END
        $body$
    """)
    op.execute(f"""
        CREATE VIEW chronicler.v_episodes_public_source AS
        SELECT
            e.id,
            COALESCE(o.corrected_start_at, e.start_at) AS started_at,
            COALESCE(o.corrected_end_at, e.end_at) AS ended_at,
            e.episode_type AS kind,
            CASE
                WHEN o.corrected_title IS NOT NULL THEN o.corrected_title
                WHEN e.source_name = '{_CALENDAR_SOURCE}'
                     AND COALESCE(
                         e.payload ->> 'title_source',
                         CASE WHEN NULLIF(btrim(e.payload ->> 'title'), '') IS NOT NULL
                              THEN 'summary' END,
                         'unknown'
                     ) <> 'summary'
                THEN 'Calendar event'
                ELSE e.title
            END AS title,
            CASE WHEN o.target_id IS NULL THEN e.privacy
                 ELSE chronicler.episode_effective_privacy(e.id, e.privacy)
            END AS effective_privacy,
            (
                e.tombstone_at IS NOT NULL
                OR (o.target_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM chronicler.overrides AS t
                    WHERE t.target_kind = 'episode' AND t.target_id = e.id
                      AND t.corrected_tombstone_at IS NOT NULL
                ))
                OR EXISTS (
                    SELECT 1 FROM chronicler.daily_repair_source_redactions AS r
                    WHERE r.source_id = e.id AND r.reason_code <> 'source_privacy'
                )
            ) AS tombstoned,
            e.source_name,
            e.layer,
            e.payload ->> 'trigger_source' AS trigger_source
        FROM chronicler.episodes AS e
        LEFT JOIN chronicler.v_latest_overrides AS o
            ON o.target_kind = 'episode' AND o.target_id = e.id
        WHERE e.layer <> 'evidence'
    """)
    # Rebuilds the local days a span touches, or every day when both bounds are NULL. An open-ended
    # episode counts to the end of its first local day.
    op.execute(f"""
        CREATE FUNCTION chronicler.refresh_rollups_public(p_from timestamptz, p_to timestamptz)
        RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $body$
        DECLARE
            zone text;
            first_day date;
            last_day date;
        BEGIN
            SELECT timezone INTO zone FROM chronicler.daily_repair_control WHERE singleton;
            zone := COALESCE(zone, 'Asia/Singapore');
            first_day := (p_from AT TIME ZONE zone)::date;
            last_day := (p_to AT TIME ZONE zone)::date;
            WITH spans AS (
                SELECT p.started_at,
                       COALESCE(
                           p.ended_at,
                           (((p.started_at AT TIME ZONE zone)::date + 1)::timestamp)
                               AT TIME ZONE zone
                       ) AS ended_at,
                       p.kind, p.category
                FROM {_EPISODES} AS p
                JOIN chronicler.episodes AS e ON e.id = p.id
                WHERE e.layer = 'activity'
                  AND p.effective_privacy IN ('normal', 'sensitive')
                  AND NOT p.tombstoned
            ),
            clipped AS (
                SELECT day::date AS local_date, g.group_kind, g.group_key,
                       GREATEST(s.started_at, (day::timestamp) AT TIME ZONE zone) AS from_at,
                       LEAST(s.ended_at, ((day + interval '1 day')::timestamp) AT TIME ZONE zone)
                           AS to_at
                FROM spans AS s
                CROSS JOIN LATERAL generate_series(
                    ((s.started_at AT TIME ZONE zone)::date)::timestamp,
                    ((s.ended_at AT TIME ZONE zone)::date)::timestamp,
                    interval '1 day'
                ) AS day
                CROSS JOIN LATERAL (VALUES ('category', s.category), ('kind', s.kind))
                    AS g(group_kind, group_key)
                WHERE (first_day IS NULL OR day::date >= first_day)
                  AND (last_day IS NULL OR day::date <= last_day)
            ),
            islands AS (
                SELECT *, count(*) FILTER (WHERE starts_island) OVER (
                    PARTITION BY local_date, group_kind, group_key ORDER BY from_at, to_at
                ) AS island
                FROM (
                    SELECT *, from_at > max(to_at) OVER (
                        PARTITION BY local_date, group_kind, group_key
                        ORDER BY from_at, to_at
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                    ) IS NOT FALSE AS starts_island
                    FROM clipped
                    WHERE to_at > from_at
                ) AS marked
            ),
            merged AS (
                SELECT local_date, group_kind, group_key, max(to_at) - min(from_at) AS span
                FROM islands
                GROUP BY local_date, group_kind, group_key, island
            ),
            desired AS (
                SELECT local_date, group_kind, group_key,
                       round(extract(epoch FROM sum(span)) / 60)::integer AS minutes
                FROM merged
                GROUP BY local_date, group_kind, group_key
                HAVING round(extract(epoch FROM sum(span)) / 60) > 0
            ),
            removed AS (
                DELETE FROM {_ROLLUPS} AS r
                WHERE (first_day IS NULL OR r.local_date >= first_day)
                  AND (last_day IS NULL OR r.local_date <= last_day)
                  AND NOT EXISTS (
                      SELECT 1 FROM desired AS d
                      WHERE d.local_date = r.local_date AND d.group_kind = r.group_kind
                        AND d.group_key = r.group_key
                  )
            )
            INSERT INTO {_ROLLUPS} AS r (local_date, group_kind, group_key, minutes)
            SELECT local_date, group_kind, group_key, minutes FROM desired
            ON CONFLICT (local_date, group_kind, group_key) DO UPDATE
            SET minutes = EXCLUDED.minutes
            WHERE r.minutes IS DISTINCT FROM EXCLUDED.minutes;
        END
        $body$
    """)
    op.execute(f"""
        CREATE FUNCTION {_SYNC}
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp
        AS $body$
        DECLARE
            before_row {_EPISODES}%ROWTYPE;
            after_row {_EPISODES}%ROWTYPE;
        BEGIN
            PERFORM pg_advisory_xact_lock({_LOCK});
            SELECT * INTO before_row FROM {_EPISODES} WHERE id = NEW.target_id;
            IF NOT FOUND THEN
                -- Not swept yet, so nothing is published; the sweep will read this override.
                RETURN NULL;
            END IF;
            UPDATE {_EPISODES} AS p
            SET started_at = s.started_at,
                ended_at = s.ended_at,
                title = s.title,
                effective_privacy = s.effective_privacy,
                tombstoned = s.tombstoned
            FROM chronicler.v_episodes_public_source AS s
            WHERE s.id = p.id AND p.id = NEW.target_id
            RETURNING p.* INTO after_row;
            PERFORM chronicler.refresh_rollups_public(
                LEAST(before_row.started_at, after_row.started_at),
                GREATEST(
                    COALESCE(before_row.ended_at, before_row.started_at),
                    COALESCE(after_row.ended_at, after_row.started_at)
                )
            );
            RETURN NULL;
        END
        $body$
    """)
    op.execute(f"""
        CREATE TRIGGER trg_overrides_sync_episode_public
        AFTER INSERT ON chronicler.overrides
        FOR EACH ROW WHEN (NEW.target_kind = 'episode')
        EXECUTE FUNCTION {_SYNC}
    """)
    for table in (_EPISODES, _ROLLUPS):
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_ROLE}")
    op.execute("REVOKE ALL ON chronicler.v_episodes_public_source FROM PUBLIC")
    op.execute(f"GRANT SELECT ON chronicler.v_episodes_public_source TO {_ROLE}")
    for function in (_PRIVACY, _REFRESH, _SYNC):
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {_ROLE}")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_overrides_sync_episode_public ON chronicler.overrides")
    op.execute(f"DROP FUNCTION IF EXISTS {_SYNC}")
    op.execute(f"DROP FUNCTION IF EXISTS {_REFRESH}")
    op.execute("DROP VIEW IF EXISTS chronicler.v_episodes_public_source")
    op.execute(f"DROP FUNCTION IF EXISTS {_PRIVACY}")
    drop_replica_identity(_EPISODES)
    op.execute(f"DROP TABLE IF EXISTS {_ROLLUPS}")
    op.execute(f"DROP TABLE IF EXISTS {_EPISODES}")
