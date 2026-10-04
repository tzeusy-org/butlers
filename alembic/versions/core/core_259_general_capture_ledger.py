"""General-owned durable capture receipts (RFC0037, REQ-general-capture-001/-002).

Replay installs the same public objects without replacing stored evidence. No General
schema is needed to install the ledger. Runtime admission and dispatch start disabled.
"""

from alembic import op

revision = "core_259"
down_revision = "core_258"
branch_labels = None
depends_on = None

TABLES = ("captures", "capture_operations", "capture_service_control")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.captures (
            id uuid PRIMARY KEY,
            principal_id uuid NOT NULL,
            source_occurrence uuid NOT NULL,
            source_occurred_at timestamptz NOT NULL,
            service_epoch uuid NOT NULL,
            mutation_key text CHECK (mutation_key IS NULL OR
                (octet_length(mutation_key) BETWEEN 1 AND 128 AND btrim(mutation_key) <> '')),
            canonical_intake text NOT NULL CHECK (octet_length(canonical_intake) <= 65536),
            payload_digest text NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
            disposition text NOT NULL DEFAULT 'held'
                CHECK (disposition IN ('held', 'routed', 'refused')),
            category text NOT NULL DEFAULT 'pending' CHECK (category IN
                ('pending', 'classification_failed', 'destination_unavailable',
                 'ownership_refused', 'target_outcome_unknown', 'source_unavailable',
                 'recovery_required', 'routed')),
            operation_id uuid,
            claim_version bigint NOT NULL DEFAULT 0 CHECK (claim_version >= 0),
            receipt jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (principal_id, source_occurrence, mutation_key),
            CHECK (encode(sha256(convert_to(canonical_intake, 'UTF8')), 'hex') = payload_digest),
            CHECK ((jsonb_typeof(canonical_intake::jsonb) = 'object'
                AND jsonb_typeof(canonical_intake::jsonb->'text') = 'string'
                AND octet_length(canonical_intake::jsonb->>'text') BETWEEN 1 AND 32768
                AND jsonb_typeof(canonical_intake::jsonb->'references') = 'array'
                AND jsonb_array_length(canonical_intake::jsonb->'references') <= 8) IS TRUE),
            CHECK ((disposition = 'routed') = (receipt IS NOT NULL))
        );
        CREATE TABLE IF NOT EXISTS public.capture_operations (
            id uuid PRIMARY KEY,
            capture_id uuid NOT NULL UNIQUE REFERENCES public.captures(id),
            service_epoch uuid NOT NULL,
            target_owner text NOT NULL CHECK (target_owner = 'general'),
            kind text NOT NULL CHECK (kind IN ('note', 'fact', 'preference')),
            payload_digest text NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
            stage text NOT NULL DEFAULT 'claimed'
                CHECK (stage IN ('claimed', 'in_doubt', 'routed')),
            receipt jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK ((stage = 'routed') = (receipt IS NOT NULL)),
            CHECK (receipt IS NULL OR
                (receipt->>'owner' = 'general' AND receipt->>'operation_id' = id::text
                 AND (receipt->>'item_id')::uuid IS NOT NULL
                 AND (receipt->>'version')::bigint > 0
                 AND (receipt->>'generation')::bigint >= 0
                 AND receipt->>'digest' ~ '^[0-9a-f]{64}$'
                 AND jsonb_typeof(receipt) = 'object'
                 AND receipt - ARRAY['owner','operation_id','item_id','version',
                                     'generation','digest']::text[] = '{}'::jsonb
                 AND jsonb_typeof(receipt->'version') = 'number'
                 AND jsonb_typeof(receipt->'generation') = 'number') IS TRUE)
        );
        CREATE TABLE IF NOT EXISTS public.capture_service_control (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            admitted_epoch uuid,
            admission_enabled boolean NOT NULL DEFAULT false,
            dispatch_enabled boolean NOT NULL DEFAULT false,
            recovery_required boolean NOT NULL DEFAULT true,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (NOT (admission_enabled OR dispatch_enabled) OR
                   (admitted_epoch IS NOT NULL AND NOT recovery_required))
        );
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.capture_preserve_evidence()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'capture evidence removal requires forward remediation';
            END IF;
            IF TG_TABLE_NAME = 'captures' THEN
                IF OLD.disposition <> 'held' OR
                   (to_jsonb(OLD) - ARRAY['disposition','category','operation_id','claim_version',
                                         'receipt','updated_at']) <>
                   (to_jsonb(NEW) - ARRAY['disposition','category','operation_id','claim_version',
                                         'receipt','updated_at']) OR
                   (OLD.operation_id IS NOT NULL AND
                    NEW.operation_id IS DISTINCT FROM OLD.operation_id) OR
                   NEW.claim_version < OLD.claim_version THEN
                    RAISE EXCEPTION 'immutable capture binding or terminal receipt';
                END IF;
                IF NEW.disposition = 'routed' AND NOT EXISTS (
                    SELECT 1 FROM public.capture_operations AS operation
                    WHERE operation.id = NEW.operation_id AND operation.capture_id = NEW.id
                      AND operation.stage = 'routed' AND operation.receipt = NEW.receipt
                      AND operation.service_epoch = NEW.service_epoch
                      AND operation.payload_digest = NEW.payload_digest
                ) THEN
                    RAISE EXCEPTION 'capture requires an exact owned operation receipt';
                END IF;
            ELSIF TG_TABLE_NAME = 'capture_operations' THEN
                IF OLD.stage = 'routed' OR
                   (to_jsonb(OLD) - ARRAY['stage','receipt','updated_at']) <>
                   (to_jsonb(NEW) - ARRAY['stage','receipt','updated_at']) OR
                   (OLD.stage = 'in_doubt' AND NEW.stage = 'claimed') THEN
                    RAISE EXCEPTION 'immutable capture operation lineage';
                END IF;
            END IF;
            RETURN NEW;
        END $$;
    """)
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS capture_general_service ON public.{table}")
        op.execute(f"""CREATE POLICY capture_general_service ON public.{table}
            FOR ALL TO PUBLIC USING (current_user = 'butler_general_rw')
            WITH CHECK (current_user = 'butler_general_rw')""")
        op.execute(f"DROP TRIGGER IF EXISTS capture_preserve_evidence ON public.{table}")
        op.execute(f"""CREATE TRIGGER capture_preserve_evidence
            BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.capture_preserve_evidence()""")
        op.execute(f"DROP TRIGGER IF EXISTS capture_no_truncate ON public.{table}")
        op.execute(f"""CREATE TRIGGER capture_no_truncate BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.capture_preserve_evidence()""")
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'butler_general_rw') THEN
                GRANT SELECT, INSERT, UPDATE ON public.{table} TO butler_general_rw;
            END IF;
        END $$""")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'butler_general_rw') THEN
            SET LOCAL ROLE butler_general_rw;
            INSERT INTO public.capture_service_control DEFAULT VALUES ON CONFLICT DO NOTHING;
            RESET ROLE;
        END IF;
    END $$""")
    # Fixed, invoker-rights importer. It does not bypass General RLS. JSON fields
    # must match the exact row type; conflict rows must match byte-for-byte.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.capture_restore_row(relation_name text, payload jsonb)
        RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE expected text[]; actual text[]; existing jsonb; incoming jsonb;
        BEGIN
            IF current_user <> 'butler_general_rw' OR jsonb_typeof(payload) <> 'object' THEN
                RAISE EXCEPTION 'capture restore authority or shape invalid';
            END IF;
            SELECT array_agg(key ORDER BY key) INTO actual FROM jsonb_object_keys(payload) AS key;
            CASE relation_name
            WHEN 'captures' THEN
                SELECT array_agg(attname::text ORDER BY attname) INTO expected
                FROM pg_attribute WHERE attrelid = 'public.captures'::regclass
                  AND attnum > 0 AND NOT attisdropped;
                IF actual IS DISTINCT FROM expected THEN
                    RAISE EXCEPTION 'capture restore shape invalid';
                END IF;
                SELECT to_jsonb(r) INTO incoming
                    FROM jsonb_populate_record(NULL::public.captures, payload) AS r;
                SELECT to_jsonb(t) INTO existing FROM public.captures AS t
                    WHERE id = (payload->>'id')::uuid;
                IF existing IS NULL THEN
                    INSERT INTO public.captures SELECT *
                        FROM jsonb_populate_record(NULL::public.captures, payload);
                END IF;
            WHEN 'capture_operations' THEN
                SELECT array_agg(attname::text ORDER BY attname) INTO expected
                FROM pg_attribute WHERE attrelid = 'public.capture_operations'::regclass
                  AND attnum > 0 AND NOT attisdropped;
                IF actual IS DISTINCT FROM expected THEN
                    RAISE EXCEPTION 'capture restore shape invalid';
                END IF;
                SELECT to_jsonb(r) INTO incoming
                    FROM jsonb_populate_record(NULL::public.capture_operations, payload) AS r;
                SELECT to_jsonb(t) INTO existing FROM public.capture_operations AS t
                    WHERE id = (payload->>'id')::uuid;
                IF existing IS NULL THEN
                    IF NOT EXISTS (SELECT 1 FROM public.captures AS c
                        WHERE c.id = (payload->>'capture_id')::uuid
                          AND c.operation_id = (payload->>'id')::uuid
                          AND c.service_epoch = (payload->>'service_epoch')::uuid
                          AND c.payload_digest = payload->>'payload_digest'
                          AND (c.receipt IS NULL OR c.receipt = payload->'receipt')) THEN
                        RAISE EXCEPTION 'capture restore operation binding invalid';
                    END IF;
                    INSERT INTO public.capture_operations SELECT *
                        FROM jsonb_populate_record(NULL::public.capture_operations, payload);
                END IF;
            WHEN 'capture_service_control' THEN
                SELECT array_agg(attname::text ORDER BY attname) INTO expected
                FROM pg_attribute WHERE attrelid = 'public.capture_service_control'::regclass
                  AND attnum > 0 AND NOT attisdropped;
                IF actual IS DISTINCT FROM expected OR payload->>'singleton' <> 'true' THEN
                    RAISE EXCEPTION 'capture restore shape invalid';
                END IF;
                -- Never restore enablement from a dump. Epoch provenance stays old.
                incoming := payload || '{"admission_enabled":false,"dispatch_enabled":false,
                                         "recovery_required":true}'::jsonb;
                SELECT to_jsonb(t) INTO existing FROM public.capture_service_control AS t;
                IF existing IS NULL THEN
                    INSERT INTO public.capture_service_control SELECT *
                        FROM jsonb_populate_record(NULL::public.capture_service_control, incoming);
                ELSIF existing->'admitted_epoch' IS DISTINCT FROM incoming->'admitted_epoch' THEN
                    RAISE EXCEPTION 'capture restore control conflict';
                ELSE
                    UPDATE public.capture_service_control
                    SET admission_enabled = false, dispatch_enabled = false,
                        recovery_required = true;
                    RETURN;
                END IF;
            ELSE RAISE EXCEPTION 'capture restore relation invalid';
            END CASE;
            IF existing IS NOT NULL AND existing <> incoming THEN
                RAISE EXCEPTION 'capture restore conflict';
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'capture restore row invalid';
        END $$;
        REVOKE ALL ON FUNCTION public.capture_restore_row(text, jsonb) FROM PUBLIC;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'butler_general_rw') THEN
                GRANT EXECUTE ON FUNCTION public.capture_restore_row(text, jsonb)
                    TO butler_general_rw;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # Inspect as migration owner with RLS disabled: nonempty must ERROR, not look
    # empty through FORCE RLS. A privileged maintenance owner sees the real rows.
    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'butler_general_rw') THEN
            RAISE EXCEPTION 'capture downgrade requires General authority';
        END IF;
        SET LOCAL ROLE butler_general_rw;
        IF EXISTS (SELECT 1 FROM public.captures) OR
           EXISTS (SELECT 1 FROM public.capture_operations) OR
           EXISTS (SELECT 1 FROM public.capture_service_control
                   WHERE admitted_epoch IS NOT NULL OR admission_enabled
                      OR dispatch_enabled OR NOT recovery_required) THEN
            RAISE EXCEPTION 'capture evidence retained; use forward remediation';
        END IF;
        RESET ROLE;
    END $$""")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE public.{table}")
    op.execute("DROP FUNCTION public.capture_restore_row(text, jsonb)")
    op.execute("DROP FUNCTION public.capture_preserve_evidence()")
