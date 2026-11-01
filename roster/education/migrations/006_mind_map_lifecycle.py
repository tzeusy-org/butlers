"""Draft creation and serialized active-map content integrity.

Revision ID: education_006
Revises: education_005

Legacy abandonment and its system audit commit together, before enforcement.
Node removal writes the parent row before checking content: concurrent deletes
and activation serialize, including a serialization failure at repeatable read.
Cascade deletion has no surviving parent and is exempt. Downgrade refuses
outstanding drafts rather than silently activating or abandoning them; historic
abandonment, audit and the pre-existing application data columns are retained.
"""

from __future__ import annotations

from alembic import op

revision = "education_006"
down_revision = "education_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE education.mind_maps
            DROP CONSTRAINT IF EXISTS mind_maps_status_check;
        ALTER TABLE education.mind_maps
            ADD CONSTRAINT mind_maps_status_check
            CHECK (status IN ('draft', 'active', 'completed', 'abandoned'));
        ALTER TABLE education.mind_maps
            ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE education.mind_map_nodes ADD COLUMN IF NOT EXISTS sequence INTEGER;
    """)
    op.execute("""
        WITH abandoned AS (
            UPDATE education.mind_maps m SET status = 'abandoned', updated_at = now()
            WHERE status = 'active'
              AND NOT EXISTS (SELECT 1 FROM education.mind_map_nodes n WHERE n.mind_map_id = m.id)
            RETURNING m.id
        )
        INSERT INTO public.audit_log (actor, action, target, note)
        SELECT 'system:education_006', 'education.mind_map.legacy_empty_abandoned',
               id::text, 'legacy active map contained no concepts'
        FROM abandoned;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION education.require_active_map_content()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF NEW.status = 'active' AND NOT EXISTS (
                SELECT 1 FROM education.mind_map_nodes WHERE mind_map_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'An active curriculum requires at least one concept'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS mind_maps_active_content ON education.mind_maps;
        CREATE TRIGGER mind_maps_active_content BEFORE INSERT OR UPDATE
            ON education.mind_maps FOR EACH ROW
            EXECUTE FUNCTION education.require_active_map_content();

        CREATE OR REPLACE FUNCTION education.serialize_concept_removal()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE parent_id uuid; destination_id uuid;
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF OLD.mind_map_id = NEW.mind_map_id THEN RETURN NEW; END IF;
                destination_id := NEW.mind_map_id;
            END IF;
            FOR parent_id IN
                SELECT id FROM education.mind_maps
                WHERE id = OLD.mind_map_id
                   OR id = destination_id
                ORDER BY id
            LOOP
                UPDATE education.mind_maps SET updated_at = updated_at WHERE id = parent_id;
            END LOOP;
            IF TG_OP = 'UPDATE' THEN RETURN NEW; END IF;
            RETURN OLD;
        END $$;
        DROP TRIGGER IF EXISTS mind_map_nodes_removal_lock ON education.mind_map_nodes;
        CREATE TRIGGER mind_map_nodes_removal_lock BEFORE DELETE OR UPDATE OF mind_map_id
            ON education.mind_map_nodes FOR EACH ROW
            EXECUTE FUNCTION education.serialize_concept_removal();

        CREATE OR REPLACE FUNCTION education.require_content_after_removal()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM education.mind_maps
                       WHERE id = OLD.mind_map_id AND status = 'active')
               AND NOT EXISTS (SELECT 1 FROM education.mind_map_nodes
                               WHERE mind_map_id = OLD.mind_map_id) THEN
                RAISE EXCEPTION 'Cannot remove the final concept of an active curriculum'
                    USING ERRCODE = '23514';
            END IF;
            RETURN OLD;
        END $$;
        DROP TRIGGER IF EXISTS mind_map_nodes_active_content ON education.mind_map_nodes;
        CREATE TRIGGER mind_map_nodes_active_content AFTER DELETE OR UPDATE OF mind_map_id
            ON education.mind_map_nodes FOR EACH ROW
            EXECUTE FUNCTION education.require_content_after_removal();
        ALTER TABLE education.mind_maps ALTER COLUMN status SET DEFAULT 'draft';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM education.mind_maps WHERE status = 'draft') THEN
                RAISE EXCEPTION 'Settle draft curricula before downgrading education_006';
            END IF;
        END $$;
        DROP TRIGGER IF EXISTS mind_map_nodes_active_content ON education.mind_map_nodes;
        DROP TRIGGER IF EXISTS mind_map_nodes_removal_lock ON education.mind_map_nodes;
        DROP TRIGGER IF EXISTS mind_maps_active_content ON education.mind_maps;
        DROP FUNCTION IF EXISTS education.require_content_after_removal();
        DROP FUNCTION IF EXISTS education.serialize_concept_removal();
        DROP FUNCTION IF EXISTS education.require_active_map_content();
        ALTER TABLE education.mind_maps DROP CONSTRAINT IF EXISTS mind_maps_status_check;
        ALTER TABLE education.mind_maps ADD CONSTRAINT mind_maps_status_check
            CHECK (status IN ('active', 'completed', 'abandoned'));
        ALTER TABLE education.mind_maps ALTER COLUMN status SET DEFAULT 'active';
    """)
