"""Let settled signed confirms shed their prompt copy (bu-jv1moa.46).

Revision ID: core_307
Revises: core_306

``public.pending_confirms.prompt`` holds the card's question, quoted facts and option
labels. core_282 made it immutable for the row's whole life, so answered and expired
rows kept that copy forever and every backup carried it. The confirm worker now
replaces the prompt of a settled row (terminal, applied, rendering current) with a
content-free tombstone once the retention horizon passes.

The database owns the tombstone's shape: ``confirm_redacted_prompt`` is the only
replacement the identity guard admits, it keeps a digest of the original prompt so a
repeated producer request still proves it names the same immutable question, and a
redacted row can never re-enter application. The horizon itself is service policy.
"""

from alembic import op

revision = "core_307"
down_revision = "core_306"
branch_labels = None
depends_on = None

_RUNTIME_ROLES = "ARRAY['butler_switchboard_rw','butler_messenger_rw']"

_GUARD_PREFIX = """
CREATE OR REPLACE FUNCTION public.guard_confirm_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$ BEGIN
    IF ROW(NEW.id,NEW.producer,NEW.producer_key,NEW.recipient_entity,
           NEW.recipient_sender,NEW.recipient_chat,NEW.recipient_owner,
           NEW.goal_delivery_ref,NEW.sink,NEW.sink_ref,NEW.created_at,NEW.expires_at)
       IS DISTINCT FROM
       ROW(OLD.id,OLD.producer,OLD.producer_key,OLD.recipient_entity,
           OLD.recipient_sender,OLD.recipient_chat,OLD.recipient_owner,
           OLD.goal_delivery_ref,OLD.sink,OLD.sink_ref,OLD.created_at,OLD.expires_at)
    THEN RAISE EXCEPTION 'confirm prompt identity is immutable'; END IF;
"""

_GUARD_SUFFIX = """
    IF OLD.status <> 'pending' AND
       ROW(NEW.status,NEW.answer_index,NEW.answered_at,NEW.answer_channel) IS DISTINCT FROM
       ROW(OLD.status,OLD.answer_index,OLD.answered_at,OLD.answer_channel)
    THEN RAISE EXCEPTION 'confirm choice is immutable'; END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_confirm_identity() FROM PUBLIC;
"""


def upgrade():
    op.execute(f"""
        CREATE OR REPLACE FUNCTION public.confirm_redacted_prompt(prompt JSONB) RETURNS JSONB
        LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog AS $$
            SELECT jsonb_build_object(
                'redacted', true,
                'digest', encode(sha256(convert_to(prompt::text, 'UTF8')), 'hex'))
        $$;
        REVOKE ALL ON FUNCTION public.confirm_redacted_prompt(JSONB) FROM PUBLIC;
        DO $$ DECLARE role_name TEXT; BEGIN
            FOREACH role_name IN ARRAY {_RUNTIME_ROLES} LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
                    EXECUTE format(
                        'GRANT EXECUTE ON FUNCTION public.confirm_redacted_prompt(JSONB) TO %I',
                        role_name);
                END IF;
            END LOOP;
        END $$;
    """)
    op.execute(
        _GUARD_PREFIX
        + """
    -- The prompt changes once: a settled row becomes its own content-free tombstone.
    IF NEW.prompt IS DISTINCT FROM OLD.prompt AND NOT (
        OLD.status <> 'pending' AND OLD.application_status = 'applied'
        AND NOT OLD.prompt ? 'redacted'
        AND NEW.prompt = public.confirm_redacted_prompt(OLD.prompt))
    THEN RAISE EXCEPTION 'confirm prompt identity is immutable'; END IF;
    -- NEW as well as OLD: one statement must not redact and reopen application together.
    IF (OLD.prompt ? 'redacted' OR NEW.prompt ? 'redacted')
       AND NEW.application_status <> 'applied'
    THEN RAISE EXCEPTION 'redacted confirm cannot re-enter application'; END IF;
"""
        + _GUARD_SUFFIX
    )


def downgrade():
    # Already-redacted rows stay redacted: their content is gone by design.
    op.execute(
        _GUARD_PREFIX
        + """
    IF NEW.prompt IS DISTINCT FROM OLD.prompt
    THEN RAISE EXCEPTION 'confirm prompt identity is immutable'; END IF;
"""
        + _GUARD_SUFFIX
    )
    op.execute("DROP FUNCTION IF EXISTS public.confirm_redacted_prompt(JSONB)")
