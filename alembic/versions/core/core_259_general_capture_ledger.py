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
            CHECK ((disposition = 'routed') = (receipt IS NOT NULL)),
            CHECK (disposition <> 'routed' OR operation_id IS NOT NULL)
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
                 AND (receipt->>'item_id')::uuid = id
                 AND (receipt->>'version')::bigint = 1
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
    # A target UUID is the server-generated operation UUID, never an arbitrary
    # locator. Deferred checks allow claim and captures-before-operations import
    # order while requiring both sides and immutable General create proof at
    # transaction end. Historical proof survives later privacy or deletion.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.capture_validate_receipt(
            capture_uuid uuid, require_current boolean DEFAULT false)
        RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog SET row_security = on AS $$
        DECLARE c public.captures; o public.capture_operations; v record; parent record;
                item record;
        BEGIN
            IF current_user <> 'butler_general_rw' THEN
                RAISE EXCEPTION 'capture receipt authority invalid';
            END IF;
            SELECT * INTO c FROM public.captures WHERE id=capture_uuid;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'capture receipt binding invalid';
            END IF;
            IF c.operation_id IS NULL THEN
                IF c.disposition='routed' OR EXISTS (
                    SELECT 1 FROM public.capture_operations WHERE capture_id=c.id) THEN
                    RAISE EXCEPTION 'capture receipt binding invalid';
                END IF;
                RETURN;
            END IF;
            SELECT * INTO o FROM public.capture_operations WHERE id=c.operation_id;
            IF NOT FOUND OR o.capture_id<>c.id OR o.service_epoch<>c.service_epoch
               OR o.payload_digest<>c.payload_digest
               OR (o.stage='routed') <> (c.disposition='routed')
               OR o.receipt IS DISTINCT FROM c.receipt THEN
                RAISE EXCEPTION 'capture receipt binding invalid';
            END IF;
            IF o.stage <> 'routed' THEN RETURN; END IF;
            IF NOT COALESCE((SELECT has_schema_privilege(oid,'USAGE')
                            FROM pg_namespace WHERE nspname='general'), false) THEN
                RAISE EXCEPTION 'capture target proof unavailable';
            END IF;
            IF to_regclass('general.source_versions') IS NULL THEN
                RAISE EXCEPTION 'capture target proof unavailable';
            END IF;
            SELECT * INTO v FROM general.source_versions
            WHERE item_id=o.id AND version=1 AND operation='create';
            IF NOT FOUND OR v.digest<>o.receipt->>'digest'
               OR v.eligibility_generation<>(o.receipt->>'generation')::bigint
               OR v.recorded_at<o.created_at
               OR jsonb_array_length(c.canonical_intake::jsonb->'references')<>0
               OR v.content IS DISTINCT FROM jsonb_build_object(
                    'collection_id', v.collection_id::text,
                    'data', jsonb_build_object('text', c.canonical_intake::jsonb->'text',
                        'references', c.canonical_intake::jsonb->'references'),
                    'tags', '[]'::jsonb)
               -- source_digest's sorted, compact UTF-8 create projection. This
               -- fixed target has only UUID/string values and empty refs/tags.
               OR v.digest<>encode(sha256(convert_to(
                    '{"content":{"collection_id":' || to_json(v.collection_id::text)::text ||
                    ',"data":{"references":[],"text":' ||
                    to_json(c.canonical_intake::jsonb->>'text')::text ||
                    '},"tags":[]},"item_id":' || to_json(o.id::text)::text ||
                    ',"operation":"create"}', 'UTF8')), 'hex') THEN
                RAISE EXCEPTION 'capture target proof invalid';
            END IF;
            IF require_current THEN
                -- Normal finalization rechecks current ordinary eligibility with
                -- the same parent-before-item order as General's writers.
                SELECT * INTO parent FROM general.collections AS p
                WHERE p.id=v.collection_id AND NOT p.custody_private
                  AND p.name=CASE o.kind WHEN 'note' THEN 'notes'
                      WHEN 'fact' THEN 'facts' ELSE 'preferences' END
                  AND p.eligibility_generation=v.eligibility_generation
                  AND EXISTS (SELECT 1 FROM general.collection_vocabulary AS vocabulary
                              WHERE vocabulary.collection_id=p.id)
                  AND NOT EXISTS (SELECT 1 FROM general.collection_items AS reserved
                      WHERE reserved.collection_id=p.id AND reserved.data ? 'possession_profile')
                FOR SHARE OF p;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'capture target proof unavailable';
                END IF;
                SELECT * INTO item FROM general.collection_items
                WHERE id=o.id AND collection_id=parent.id FOR SHARE;
                IF NOT FOUND OR v.content IS DISTINCT FROM jsonb_build_object(
                    'collection_id', item.collection_id::text,
                    'data', item.data, 'tags', item.tags)
                   OR EXISTS (SELECT 1 FROM general.source_versions
                              WHERE item_id=o.id AND version>1) THEN
                    RAISE EXCEPTION 'capture target proof invalid';
                END IF;
            END IF;
        END $$;
        REVOKE ALL ON FUNCTION public.capture_validate_receipt(uuid, boolean) FROM PUBLIC;
        CREATE OR REPLACE FUNCTION public.capture_validate_commit()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE capture_uuid uuid; require_current boolean := false;
        BEGIN
            IF TG_TABLE_NAME='captures' THEN
                capture_uuid := NEW.id;
            ELSE
                capture_uuid := NEW.capture_id;
                IF TG_OP='UPDATE' THEN
                    require_current := NEW.stage='routed' AND OLD.stage<>'routed';
                END IF;
            END IF;
            PERFORM public.capture_validate_receipt(capture_uuid, require_current);
            RETURN NULL;
        END $$;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='butler_general_rw') THEN
                GRANT EXECUTE ON FUNCTION public.capture_validate_receipt(uuid, boolean)
                    TO butler_general_rw;
            END IF;
        END $$;
    """)
    for table in ("captures", "capture_operations"):
        op.execute(f"DROP TRIGGER IF EXISTS capture_validate_commit ON public.{table}")
        op.execute(f"""CREATE CONSTRAINT TRIGGER capture_validate_commit
            AFTER INSERT OR UPDATE ON public.{table}
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION public.capture_validate_commit()""")
    # Fixed, invoker-rights importer. It does not bypass General RLS. JSON fields
    # must match the exact row type; conflict rows must match byte-for-byte.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.capture_restore_row(relation_name text, payload jsonb)
        RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog SET row_security = on AS $$
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
                incoming := payload || jsonb_build_object(
                    'admission_enabled', false, 'dispatch_enabled', false,
                    'recovery_required', true);
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
            -- Fixed diagnostic only: never repeat payloads or raw exceptions.
            RAISE EXCEPTION 'capture restore row invalid (SQLSTATE %)', SQLSTATE;
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
    # Public objects are shared by every schema's core chain. A sibling may
    # already have removed the complete empty ledger; a partial ledger is never
    # evidence of emptiness. Inspect through the exact General RLS policy.
    op.execute("""DO $$ DECLARE table_count integer; policy_count integer; all_policies integer;
    BEGIN
        SELECT count(*) INTO table_count FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname IN
            ('captures','capture_operations','capture_service_control');
        IF table_count=0 THEN RETURN; END IF;
        IF table_count<>3 THEN
            RAISE EXCEPTION 'capture evidence retained; partial ledger requires remediation';
        END IF;
        LOCK TABLE public.captures, public.capture_operations,
            public.capture_service_control IN ACCESS EXCLUSIVE MODE;
        SELECT count(*) INTO all_policies FROM pg_policy AS p
        WHERE p.polrelid IN ('public.captures'::regclass,
            'public.capture_operations'::regclass,'public.capture_service_control'::regclass);
        SELECT count(*) INTO policy_count FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid=c.relnamespace
        JOIN pg_policy AS p ON p.polrelid=c.oid
        WHERE n.nspname='public' AND c.relname IN
            ('captures','capture_operations','capture_service_control')
          AND c.relrowsecurity AND c.relforcerowsecurity
          AND p.polname='capture_general_service' AND p.polcmd='*'
          AND p.polpermissive AND p.polroles=ARRAY[0::oid]
          AND pg_get_expr(p.polqual,p.polrelid)=
              '(CURRENT_USER = ''butler_general_rw''::name)'
          AND pg_get_expr(p.polwithcheck,p.polrelid)=
              '(CURRENT_USER = ''butler_general_rw''::name)';
        IF policy_count<>3 OR all_policies<>3 THEN
            RAISE EXCEPTION 'capture evidence retained; General policy proof invalid';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'butler_general_rw') THEN
            RAISE EXCEPTION 'capture downgrade requires General authority';
        END IF;
        SET LOCAL row_security=on;
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
        op.execute(f"DROP TABLE IF EXISTS public.{table}")
    op.execute("DROP FUNCTION IF EXISTS public.capture_restore_row(text, jsonb)")
    op.execute("DROP FUNCTION IF EXISTS public.capture_preserve_evidence()")
    op.execute("DROP FUNCTION IF EXISTS public.capture_validate_commit()")
    op.execute("DROP FUNCTION IF EXISTS public.capture_validate_receipt(uuid, boolean)")
