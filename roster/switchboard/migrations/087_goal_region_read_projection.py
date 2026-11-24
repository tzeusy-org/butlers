"""Distinct region participation for bounded dashboard reads (bu-85bqeq.5).

Revision ID: sw_087
Revises: sw_086

sw_057 forbids deletion/truncation of effects and changes to their goal/capability;
goal created_at is immutable too. An insert-only projection therefore needs only
one synchronous INSERT trigger, including for old goals receiving new effects.
It holds no authority or content and is disposable on downgrade: effects remain
the source of truth. Backfill and trigger installation share the effects write
lock, so concurrent inserts cannot fall between them. Both directions take the
goals lock first, matching writers that lock a goal before inserting effects.
"""

from alembic import op

revision = "sw_087"
down_revision = "sw_086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Block goal row lockers while allowing ordinary goal reads.
    op.execute("LOCK TABLE switchboard.goals IN EXCLUSIVE MODE")
    op.execute("LOCK TABLE switchboard.effects IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""
        CREATE TABLE IF NOT EXISTS switchboard.goal_region_reads (
            region TEXT NOT NULL,
            goal_id UUID NOT NULL REFERENCES switchboard.goals(id),
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (region, goal_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_goal_region_reads_recent
        ON switchboard.goal_region_reads (region, created_at DESC, goal_id DESC)
    """)
    op.execute("ALTER TABLE switchboard.goal_region_reads ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON switchboard.goal_region_reads FROM PUBLIC")
    op.execute("""
        GRANT SELECT, INSERT ON switchboard.goal_region_reads TO butler_switchboard_rw
    """)
    for command in ("SELECT", "INSERT"):
        name = "goal_region_reads_" + command.lower()
        op.execute(f"DROP POLICY IF EXISTS {name} ON switchboard.goal_region_reads")
        predicate = "USING" if command == "SELECT" else "WITH CHECK"
        op.execute(
            f"CREATE POLICY {name} ON switchboard.goal_region_reads FOR {command} "
            f"TO butler_switchboard_rw {predicate} (current_user='butler_switchboard_rw')"
        )
    op.execute("""
        INSERT INTO switchboard.goal_region_reads (region, goal_id, created_at)
        SELECT DISTINCT split_part(e.capability, '__', 1), g.id, g.created_at
        FROM switchboard.effects e JOIN switchboard.goals g ON g.id = e.goal_id
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION switchboard.project_goal_region_read()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            INSERT INTO switchboard.goal_region_reads (region, goal_id, created_at)
            SELECT split_part(NEW.capability, '__', 1), g.id, g.created_at
            FROM switchboard.goals g WHERE g.id = NEW.goal_id
            ON CONFLICT DO NOTHING;
            RETURN NULL;
        END $$
    """)
    op.execute("DROP TRIGGER IF EXISTS project_goal_region_read ON switchboard.effects")
    op.execute("""
        CREATE TRIGGER project_goal_region_read AFTER INSERT ON switchboard.effects
        FOR EACH ROW EXECUTE FUNCTION switchboard.project_goal_region_read()
    """)


def downgrade() -> None:
    # Dropping the projection's foreign key takes this lock on its parent.
    op.execute("LOCK TABLE switchboard.goals IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE switchboard.effects IN SHARE ROW EXCLUSIVE MODE")
    op.execute("DROP TRIGGER IF EXISTS project_goal_region_read ON switchboard.effects")
    op.execute("DROP FUNCTION IF EXISTS switchboard.project_goal_region_read()")
    op.execute("DROP TABLE IF EXISTS switchboard.goal_region_reads")
