"""A condition_fire inbox row binds only an admitted wake intent.

Revision ID: sw_065
Revises: sw_064

RFC 0038 S2.1 (bu-dhloi1.7, PR B2). sw_063's composite foreign key ties a ``condition_fire``
inbox row to the wake intent of its envelope generation and fire event, but a terminal
``not_admitted`` receipt shares those keys, so the key alone would let a row bind a refused fire.
This revision adds the missing check: the bound intent must carry a successor epoch and not be
``not_admitted`` or ``cancelled``. The wake admission writer is the only one that inserts such a
row, and it inserts the admitted intent first; the trigger makes that a database fact.

It also admits ``admit_error`` as a ``not_admitted`` reason: a fire whose admission keeps raising
is closed with a terminal receipt rather than retried forever.

The downgrade drops the trigger and function and restores the reason list; it refuses while an
``admit_error`` receipt exists.
"""

from alembic import op

revision = "sw_065"
down_revision = "sw_064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("LOCK TABLE switchboard.goal_inbox IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        CREATE FUNCTION switchboard.goal_inbox_fire_admitted()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF NEW.input_kind = 'condition_fire' AND NOT EXISTS (
                   SELECT 1 FROM switchboard.wake_intents w
                   WHERE w.envelope_id = NEW.envelope_id
                     AND w.generation = NEW.envelope_generation
                     AND w.fire_event_id = NEW.event_id AND w.goal_id = NEW.goal_id
                     AND w.epoch IS NOT NULL AND w.state NOT IN ('not_admitted','cancelled')) THEN
                RAISE EXCEPTION 'A condition fire is delivered only for an admitted wake intent';
            END IF;
            RETURN NULL;
        END $$;
        CREATE TRIGGER goal_inbox_fire_admitted AFTER INSERT ON switchboard.goal_inbox
            FOR EACH ROW EXECUTE FUNCTION switchboard.goal_inbox_fire_admitted();
        ALTER TABLE switchboard.wake_intents
            DROP CONSTRAINT IF EXISTS wake_intents_not_admitted_reason_check;
        ALTER TABLE switchboard.wake_intents ADD CONSTRAINT wake_intents_not_admitted_reason_check
            CHECK (not_admitted_reason IN ('stopped','expired','cap_reached','goal_not_waiting',
                'envelope_not_open','stale_generation','admit_error'));
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE switchboard.wake_intents
            DROP CONSTRAINT IF EXISTS wake_intents_not_admitted_reason_check;
        ALTER TABLE switchboard.wake_intents ADD CONSTRAINT wake_intents_not_admitted_reason_check
            CHECK (not_admitted_reason IN ('stopped','expired','cap_reached','goal_not_waiting',
                'envelope_not_open','stale_generation'));
        DROP TRIGGER IF EXISTS goal_inbox_fire_admitted ON switchboard.goal_inbox;
        DROP FUNCTION IF EXISTS switchboard.goal_inbox_fire_admitted();
    """)
