"""memory schema: episodes, facts, rules, memory_links, memory_events,
predicate_registry, memory_policies, rule_applications, embedding_versions.

Revision ID: mem_001
Revises:
Create Date: 2026-03-26 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "mem_001"
down_revision = None
branch_labels = ("memory",)
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Extensions — the DB init script pre-installs these in public; this is
    # a safety net for fresh environments.
    # -------------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # =========================================================================
    # 1. episodes
    # =========================================================================
    # Final state after: 001, 014 (tenant/request lineage), 015 (consolidation
    # state machine — renames retry_count -> consolidation_attempts,
    # last_error -> last_consolidation_error, adds lease/dead-letter columns).
    op.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler TEXT NOT NULL,
            session_id UUID,
            content TEXT NOT NULL,
            embedding vector(384),
            search_vector tsvector,
            importance FLOAT NOT NULL DEFAULT 5.0,
            reference_count INTEGER NOT NULL DEFAULT 0,
            consolidated BOOLEAN NOT NULL DEFAULT false,
            consolidation_status VARCHAR(20) NOT NULL DEFAULT 'pending',
            consolidation_attempts INTEGER NOT NULL DEFAULT 0,
            last_consolidation_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_referenced_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ DEFAULT now() + interval '7 days',
            metadata JSONB DEFAULT '{}'::jsonb,
            tenant_id TEXT NOT NULL DEFAULT 'owner',
            request_id TEXT,
            retention_class TEXT NOT NULL DEFAULT 'transient',
            sensitivity TEXT NOT NULL DEFAULT 'normal',
            leased_until TIMESTAMPTZ,
            leased_by TEXT,
            dead_letter_reason TEXT,
            next_consolidation_retry_at TIMESTAMPTZ,
            CONSTRAINT chk_episodes_consolidation_status
                CHECK (consolidation_status IN (
                    'pending', 'consolidated', 'failed', 'dead_letter'
                ))
        )
    """)

    # -- episodes indexes --
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_butler_created
        ON episodes (butler, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_expires
        ON episodes (expires_at) WHERE expires_at IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_unconsolidated
        ON episodes (butler, created_at) WHERE consolidation_status = 'pending'
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_search
        ON episodes USING gin(search_vector)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_embedding
        ON episodes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_tenant_butler_status_created
        ON episodes (tenant_id, butler, consolidation_status, created_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_lease_claim
        ON episodes (tenant_id, butler, created_at, id)
        WHERE consolidation_status = 'pending'
    """)

    # =========================================================================
    # 2. facts
    # =========================================================================
    # Final state after: 001, 002 (entity_id), 004 (object_entity_id),
    # 007 (valid_at), 008 (valid_at nullable), 014 (tenant/request lineage),
    # 016 (idempotency_key, observed_at, invalid_at).
    #
    # FKs to public.entities are added conditionally (mem_006 dropped shadow
    # entities and re-pointed FKs to public.entities).
    op.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding vector(384),
            search_vector tsvector,
            importance FLOAT NOT NULL DEFAULT 5.0,
            confidence FLOAT NOT NULL DEFAULT 1.0,
            decay_rate FLOAT NOT NULL DEFAULT 0.008,
            permanence TEXT NOT NULL DEFAULT 'standard',
            source_butler TEXT,
            source_episode_id UUID REFERENCES episodes(id) ON DELETE SET NULL,
            supersedes_id UUID REFERENCES facts(id) ON DELETE SET NULL,
            validity TEXT NOT NULL DEFAULT 'active',
            scope TEXT NOT NULL DEFAULT 'global',
            reference_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_referenced_at TIMESTAMPTZ,
            last_confirmed_at TIMESTAMPTZ,
            tags JSONB DEFAULT '[]'::jsonb,
            metadata JSONB DEFAULT '{}'::jsonb,
            entity_id UUID,
            object_entity_id UUID,
            valid_at TIMESTAMPTZ,
            tenant_id TEXT NOT NULL DEFAULT 'owner',
            request_id TEXT,
            retention_class TEXT NOT NULL DEFAULT 'operational',
            sensitivity TEXT NOT NULL DEFAULT 'normal',
            idempotency_key TEXT,
            observed_at TIMESTAMPTZ DEFAULT now(),
            invalid_at TIMESTAMPTZ
        )
    """)

    # Conditional FKs to public.entities (from mem_006 and mem_004).
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.entities') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   WHERE c.conname = 'facts_entity_id_shared_fkey'
                     AND t.relname = 'facts'
               )
            THEN
                ALTER TABLE facts
                    ADD CONSTRAINT facts_entity_id_shared_fkey
                    FOREIGN KEY (entity_id)
                    REFERENCES public.entities(id)
                    ON DELETE RESTRICT;
            END IF;
        END
        $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.entities') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   WHERE c.conname = 'facts_object_entity_id_shared_fkey'
                     AND t.relname = 'facts'
               )
            THEN
                ALTER TABLE facts
                    ADD CONSTRAINT facts_object_entity_id_shared_fkey
                    FOREIGN KEY (object_entity_id)
                    REFERENCES public.entities(id)
                    ON DELETE RESTRICT;
            END IF;
        END
        $$;
    """)

    # -- facts indexes --
    # Basic indexes from 001
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_scope_validity
        ON facts (scope, validity) WHERE validity = 'active'
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate
        ON facts (subject, predicate)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_search
        ON facts USING gin(search_vector)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_tags
        ON facts USING gin(tags)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_embedding
        ON facts USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20)
    """)

    # Partial unique indexes (final state from mem_008: valid_at IS NULL scoped)
    # Property-fact uniqueness: (entity_id, scope, predicate) for property facts.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_entity_scope_predicate_active
        ON facts (entity_id, scope, predicate)
        WHERE entity_id IS NOT NULL
          AND object_entity_id IS NULL
          AND validity = 'active'
          AND valid_at IS NULL
    """)
    # Edge-fact uniqueness: (entity_id, object_entity_id, scope, predicate).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_edge_scope_predicate_active
        ON facts (entity_id, object_entity_id, scope, predicate)
        WHERE object_entity_id IS NOT NULL
          AND validity = 'active'
          AND valid_at IS NULL
    """)
    # Subject-keyed uniqueness (no entity_id).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_no_entity_subject_predicate_active
        ON facts (scope, subject, predicate)
        WHERE entity_id IS NULL
          AND validity = 'active'
          AND valid_at IS NULL
    """)

    # object_entity_id partial index (from mem_004)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_object_entity_id
        ON facts (object_entity_id)
        WHERE object_entity_id IS NOT NULL
    """)

    # Tenant-scoped partial index on active facts (from mem_014)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_tenant_scope_validity
        ON facts (tenant_id, scope, validity)
        WHERE validity = 'active'
    """)

    # Temporal idempotency index (from mem_016)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_temporal_idempotency
        ON facts (tenant_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
    """)

    # GIN index on facts.metadata (from mem_012)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_metadata_gin
        ON facts USING gin(metadata)
    """)

    # Partial B-tree indexes for aggregation queries (from mem_012)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_meal_predicate_valid
        ON facts (predicate, valid_at)
        WHERE predicate >= 'meal_'
          AND predicate < 'meal`'
          AND validity = 'active'
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_transaction_predicate_valid
        ON facts (predicate, valid_at)
        WHERE predicate >= 'transaction_'
          AND predicate < 'transaction`'
          AND validity = 'active'
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_measurement_predicate_valid
        ON facts (predicate, valid_at)
        WHERE predicate >= 'measurement_'
          AND predicate < 'measurement`'
          AND validity = 'active'
    """)

    # =========================================================================
    # 3. rules
    # =========================================================================
    # Final state after: 001, 014 (tenant/request lineage).
    op.execute("""
        CREATE TABLE IF NOT EXISTS rules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            content TEXT NOT NULL,
            embedding vector(384),
            search_vector tsvector,
            scope TEXT NOT NULL DEFAULT 'global',
            maturity TEXT NOT NULL DEFAULT 'candidate',
            confidence FLOAT NOT NULL DEFAULT 0.5,
            decay_rate FLOAT NOT NULL DEFAULT 0.008,
            permanence TEXT NOT NULL DEFAULT 'standard',
            effectiveness_score FLOAT NOT NULL DEFAULT 0.0,
            applied_count INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            harmful_count INTEGER NOT NULL DEFAULT 0,
            source_episode_id UUID REFERENCES episodes(id) ON DELETE SET NULL,
            source_butler TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_applied_at TIMESTAMPTZ,
            last_evaluated_at TIMESTAMPTZ,
            last_confirmed_at TIMESTAMPTZ,
            reference_count INTEGER NOT NULL DEFAULT 0,
            last_referenced_at TIMESTAMPTZ,
            tags JSONB DEFAULT '[]'::jsonb,
            metadata JSONB DEFAULT '{}'::jsonb,
            tenant_id TEXT NOT NULL DEFAULT 'owner',
            request_id TEXT,
            retention_class TEXT NOT NULL DEFAULT 'rule',
            sensitivity TEXT NOT NULL DEFAULT 'normal'
        )
    """)

    # -- rules indexes --
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rules_scope_maturity
        ON rules (scope, maturity)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rules_search
        ON rules USING gin(search_vector)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rules_embedding
        ON rules USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rules_tenant_scope_maturity
        ON rules (tenant_id, scope, maturity)
    """)

    # =========================================================================
    # 4. memory_links
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_links (
            source_type TEXT NOT NULL,
            source_id UUID NOT NULL,
            target_type TEXT NOT NULL,
            target_id UUID NOT NULL,
            relation TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_type, source_id, target_type, target_id),
            CONSTRAINT chk_memory_links_relation CHECK (
                relation IN (
                    'derived_from',
                    'supports',
                    'contradicts',
                    'supersedes',
                    'related_to'
                )
            )
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_links_target
        ON memory_links (target_type, target_id)
    """)

    # =========================================================================
    # 5. memory_events
    # =========================================================================
    # Final state after: 003, 022 (enrichment columns).
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_type TEXT NOT NULL,
            actor TEXT,
            tenant_id TEXT,
            payload JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            request_id TEXT,
            memory_type TEXT,
            memory_id UUID,
            actor_butler TEXT
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_events_event_type_created
        ON memory_events (event_type, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_events_tenant_created
        ON memory_events (tenant_id, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_events_actor_butler_type
        ON memory_events (actor_butler, event_type, created_at DESC)
        WHERE actor_butler IS NOT NULL
    """)

    # =========================================================================
    # 6. predicate_registry
    # =========================================================================
    # Final state after: 005, 007 (is_temporal), 023 (hybrid search),
    # 023b (lifecycle), 023c (scope), 025 (aliases), 025b (example_json),
    # 025c (inverse_of, is_symmetric).
    op.execute("""
        CREATE TABLE IF NOT EXISTS predicate_registry (
            name TEXT PRIMARY KEY,
            expected_subject_type TEXT,
            expected_object_type TEXT,
            is_edge BOOLEAN NOT NULL DEFAULT false,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            is_temporal BOOLEAN NOT NULL DEFAULT false,
            search_vector tsvector,
            description_embedding vector(384),
            usage_count INTEGER NOT NULL DEFAULT 0,
            last_used_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'active',
            superseded_by TEXT,
            deprecated_at TIMESTAMPTZ,
            scope TEXT NOT NULL DEFAULT 'global',
            aliases TEXT[] NOT NULL DEFAULT '{}',
            inverse_of TEXT,
            is_symmetric BOOLEAN NOT NULL DEFAULT false,
            example_json JSONB,
            CONSTRAINT predicate_registry_status_check
                CHECK (status IN ('active', 'deprecated', 'proposed')),
            CONSTRAINT predicate_registry_scope_check
                CHECK (scope IN ('global', 'health', 'relationship', 'finance', 'home', 'travel'))
        )
    """)

    # -- predicate_registry trigger for search_vector (final from mem_025) --
    op.execute("""
        CREATE OR REPLACE FUNCTION predicate_registry_search_vector_trigger()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', coalesce(NEW.name, '')), 'A') ||
                setweight(to_tsvector('english',
                    coalesce(array_to_string(NEW.aliases, ' '), '')), 'A') ||
                setweight(to_tsvector('english', coalesce(NEW.description, '')), 'B');
            RETURN NEW;
        END;
        $$
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS trg_predicate_registry_search_vector
            ON predicate_registry
    """)

    op.execute("""
        CREATE TRIGGER trg_predicate_registry_search_vector
        BEFORE INSERT OR UPDATE OF name, description, aliases
        ON predicate_registry
        FOR EACH ROW
        EXECUTE FUNCTION predicate_registry_search_vector_trigger()
    """)

    # -- predicate_registry indexes --
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_predicate_registry_name_trgm
        ON predicate_registry
        USING GIN (name gin_trgm_ops)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_predicate_registry_search_vector
        ON predicate_registry
        USING GIN (search_vector)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_predicate_registry_status
        ON predicate_registry (status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_predicate_registry_scope
        ON predicate_registry (scope)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_predicate_registry_aliases
        ON predicate_registry
        USING GIN (aliases)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_predicate_registry_is_symmetric
        ON predicate_registry (is_symmetric)
        WHERE is_symmetric = true
    """)

    # =========================================================================
    # 7. memory_policies
    # =========================================================================
    # Final state after: 017 (create) + 020 (fix schema and re-seed).
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_policies (
            retention_class TEXT PRIMARY KEY,
            ttl_days INT,
            decay_rate DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            min_retrieval_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.2,
            archive_before_delete BOOLEAN NOT NULL DEFAULT FALSE,
            allow_summarization BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)

    # Seed with the 8 corrected retention classes (from mem_020).
    op.execute("""
        INSERT INTO memory_policies
            (retention_class, ttl_days, decay_rate, min_retrieval_confidence,
             archive_before_delete, allow_summarization)
        VALUES
            ('transient',        7,    0.1,   0.1,  FALSE, FALSE),
            ('episodic',         30,   0.03,  0.15, FALSE, TRUE),
            ('operational',      NULL, 0.008, 0.2,  FALSE, TRUE),
            ('personal_profile', NULL, 0.0,   0.0,  TRUE,  FALSE),
            ('health_log',       NULL, 0.002, 0.1,  TRUE,  TRUE),
            ('financial_log',    NULL, 0.002, 0.1,  TRUE,  FALSE),
            ('rule',             NULL, 0.01,  0.2,  FALSE, TRUE),
            ('anti_pattern',     NULL, 0.0,   0.0,  FALSE, FALSE)
        ON CONFLICT (retention_class) DO NOTHING
    """)

    # =========================================================================
    # 8. rule_applications
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS rule_applications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL,
            rule_id UUID NOT NULL
                REFERENCES rules(id) ON DELETE CASCADE,
            session_id UUID,
            request_id TEXT,
            outcome TEXT NOT NULL,
            notes JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_rule_applications_outcome
                CHECK (outcome IN ('helpful', 'harmful', 'neutral', 'skipped'))
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rule_applications_tenant_rule
        ON rule_applications (tenant_id, rule_id, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rule_applications_outcome
        ON rule_applications (tenant_id, outcome, created_at DESC)
    """)

    # =========================================================================
    # 9. embedding_versions
    # =========================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS embedding_versions (
            id SERIAL PRIMARY KEY,
            model_name TEXT NOT NULL,
            dimension INTEGER NOT NULL,
            description TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_embedding_versions_model UNIQUE (model_name)
        )
    """)

    # Seed row for the current model.
    op.execute("""
        INSERT INTO embedding_versions (model_name, dimension, description, active)
        VALUES (
            'all-MiniLM-L6-v2',
            384,
            'Sentence-Transformers all-MiniLM-L6-v2; 384-dimensional cosine embeddings',
            TRUE
        )
        ON CONFLICT (model_name) DO NOTHING
    """)

    # =========================================================================
    # 10. public.entities partial unique index (from mem_018 + mem_021)
    # =========================================================================
    # Drop the old absolute unique constraint if it exists, then create the
    # partial unique index excluding tombstoned entities (merged or soft-deleted).
    op.execute("""
        ALTER TABLE public.entities
        DROP CONSTRAINT IF EXISTS uq_entities_tenant_canonical_type
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_tenant_canonical_type_live
        ON public.entities (tenant_id, canonical_name, entity_type)
        WHERE (metadata->>'merged_into') IS NULL
          AND (metadata->>'deleted_at') IS NULL
    """)


def downgrade() -> None:
    # Drop partial unique index on public.entities and restore absolute constraint.
    op.execute("DROP INDEX IF EXISTS public.uq_entities_tenant_canonical_type_live")
    op.execute("""
        ALTER TABLE public.entities
        ADD CONSTRAINT uq_entities_tenant_canonical_type
        UNIQUE (tenant_id, canonical_name, entity_type)
    """)

    op.execute("DROP TABLE IF EXISTS embedding_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS rule_applications CASCADE")
    op.execute("DROP TABLE IF EXISTS memory_policies CASCADE")
    op.execute("DROP TRIGGER IF EXISTS trg_predicate_registry_search_vector ON predicate_registry")
    op.execute("DROP FUNCTION IF EXISTS predicate_registry_search_vector_trigger()")
    op.execute("DROP TABLE IF EXISTS predicate_registry CASCADE")
    op.execute("DROP TABLE IF EXISTS memory_events CASCADE")
    op.execute("DROP TABLE IF EXISTS memory_links CASCADE")
    op.execute("DROP TABLE IF EXISTS rules CASCADE")
    op.execute("DROP TABLE IF EXISTS facts CASCADE")
    op.execute("DROP TABLE IF EXISTS episodes CASCADE")
