"""Switchboard routing tables: triage rules, ingestion rules, routing instructions, source filters, thread affinity.

Revision ID: sw_003
Revises: sw_002
Create Date: 2026-03-26 00:00:00.000000

Collapsed migration covering original sw_017 (triage_rules), sw_021 (thread_affinity_settings),
sw_024 (routing_instructions), sw_026 (source_filters + connector_source_filters),
sw_027 (ingestion_rules), and sw_028 (noreply seed rows).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_003"
down_revision = "sw_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── triage_rules (sw_017) ─────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE triage_rules (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          rule_type TEXT NOT NULL,
          condition JSONB NOT NULL,
          action TEXT NOT NULL,
          priority INTEGER NOT NULL,
          enabled BOOLEAN NOT NULL DEFAULT TRUE,
          created_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          deleted_at TIMESTAMPTZ NULL,

          CONSTRAINT triage_rules_rule_type_check
            CHECK (rule_type IN ('sender_domain', 'sender_address', 'header_condition', 'mime_type')),

          CONSTRAINT triage_rules_action_check
            CHECK (
              action IN ('skip', 'metadata_only', 'low_priority_queue', 'pass_through')
              OR action LIKE 'route_to:%'
            ),

          CONSTRAINT triage_rules_created_by_check
            CHECK (created_by IN ('dashboard', 'api', 'seed')),

          CONSTRAINT triage_rules_priority_check
            CHECK (priority >= 0)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX triage_rules_active_priority_idx
          ON triage_rules (enabled, priority, created_at, id)
          WHERE deleted_at IS NULL
        """
    )

    op.execute(
        """
        CREATE INDEX triage_rules_rule_type_idx
          ON triage_rules (rule_type)
          WHERE deleted_at IS NULL
        """
    )

    op.execute(
        """
        CREATE INDEX triage_rules_condition_gin_idx
          ON triage_rules
          USING GIN (condition)
        """
    )

    # 9 seed rows from sw_017
    op.execute(
        """
        INSERT INTO triage_rules
          (id, rule_type, condition, action, priority, enabled, created_by)
        VALUES
          (
            '00000000-0000-0000-0001-000000000010',
            'sender_domain',
            '{"domain": "chase.com", "match": "suffix"}',
            'route_to:finance',
            10,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000011',
            'sender_domain',
            '{"domain": "americanexpress.com", "match": "suffix"}',
            'route_to:finance',
            11,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000020',
            'sender_domain',
            '{"domain": "delta.com", "match": "suffix"}',
            'route_to:travel',
            20,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000021',
            'sender_domain',
            '{"domain": "united.com", "match": "suffix"}',
            'route_to:travel',
            21,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000030',
            'sender_domain',
            '{"domain": "paypal.com", "match": "suffix"}',
            'route_to:finance',
            30,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000040',
            'header_condition',
            '{"header": "List-Unsubscribe", "op": "present"}',
            'metadata_only',
            40,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000041',
            'header_condition',
            '{"header": "Precedence", "op": "equals", "value": "bulk"}',
            'low_priority_queue',
            41,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000042',
            'header_condition',
            '{"header": "Auto-Submitted", "op": "equals", "value": "auto-generated"}',
            'skip',
            42,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000050',
            'mime_type',
            '{"type": "text/calendar"}',
            'route_to:relationship',
            50,
            TRUE,
            'seed'
          )
        ON CONFLICT (id) DO NOTHING
        """
    )

    # ── ingestion_rules (sw_027 + sw_028 noreply seeds) ───────────────────────
    op.execute(
        """
        CREATE TABLE ingestion_rules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            scope TEXT NOT NULL,
            rule_type TEXT NOT NULL,
            condition JSONB NOT NULL,
            action TEXT NOT NULL,
            priority INTEGER NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            name TEXT,
            description TEXT,
            created_by TEXT NOT NULL DEFAULT 'migration',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            deleted_at TIMESTAMPTZ,

            CONSTRAINT ingestion_rules_scope_check
                CHECK (scope = 'global' OR scope LIKE 'connector:%'),

            CONSTRAINT ingestion_rules_connector_action_check
                CHECK (scope = 'global' OR action IN ('block', 'pass_through')),

            CONSTRAINT ingestion_rules_priority_check
                CHECK (priority >= 0)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_ingestion_rules_scope_active
            ON ingestion_rules (scope, priority, created_at, id)
            WHERE enabled = TRUE AND deleted_at IS NULL
        """
    )

    op.execute(
        """
        CREATE INDEX ix_ingestion_rules_global_active
            ON ingestion_rules (priority, created_at, id)
            WHERE scope = 'global' AND enabled = TRUE AND deleted_at IS NULL
        """
    )

    # Migrate triage_rules -> ingestion_rules (scope='global') from sw_027
    op.execute(
        """
        INSERT INTO ingestion_rules
            (id, scope, rule_type, condition, action, priority, enabled,
             created_by, created_at, updated_at, deleted_at)
        SELECT
            id, 'global', rule_type, condition, action, priority, enabled,
            created_by, created_at, updated_at, deleted_at
        FROM triage_rules
        """
    )

    # 2 noreply seed rows from sw_028
    op.execute(
        """
        INSERT INTO ingestion_rules
          (id, scope, rule_type, condition, action, priority, enabled,
           name, description, created_by)
        VALUES
          (
            '00000000-0000-0000-0001-000000000060',
            'global',
            'sender_address',
            '{"address": "noreply", "match": "local_part_prefix"}',
            'metadata_only',
            5,
            TRUE,
            'Skip noreply senders',
            'Emails from noreply@* addresses are ingested as metadata only — not routed for interactive response.',
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000061',
            'global',
            'sender_address',
            '{"address": "no-reply", "match": "local_part_prefix"}',
            'metadata_only',
            5,
            TRUE,
            'Skip no-reply senders',
            'Emails from no-reply@* addresses are ingested as metadata only — not routed for interactive response.',
            'seed'
          )
        ON CONFLICT (id) DO NOTHING
        """
    )

    # ── routing_instructions (sw_024) ─────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS routing_instructions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            instruction TEXT NOT NULL,
            priority INT NOT NULL DEFAULT 100,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_by TEXT NOT NULL DEFAULT 'dashboard',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_routing_instructions_active
            ON routing_instructions (priority ASC, created_at ASC)
            WHERE enabled = TRUE AND deleted_at IS NULL
    """)

    # ── source_filters (sw_026) ───────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE source_filters (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            filter_mode TEXT NOT NULL CHECK (filter_mode IN ('blacklist', 'whitelist')),
            source_key_type TEXT NOT NULL,
            patterns TEXT[] NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # ── connector_source_filters (sw_026) ─────────────────────────────────────
    op.execute(
        """
        CREATE TABLE connector_source_filters (
            connector_type TEXT NOT NULL,
            endpoint_identity TEXT NOT NULL,
            filter_id UUID NOT NULL REFERENCES source_filters(id) ON DELETE CASCADE,
            enabled BOOLEAN NOT NULL DEFAULT true,
            priority INTEGER NOT NULL DEFAULT 0,
            attached_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (connector_type, endpoint_identity, filter_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_connector_source_filters_connector
        ON connector_source_filters (connector_type, endpoint_identity)
        WHERE enabled = true
        """
    )

    op.execute(
        """
        CREATE INDEX ix_connector_source_filters_filter_id
        ON connector_source_filters (filter_id)
        """
    )

    # ── thread_affinity_settings (sw_021) ─────────────────────────────────────
    # Note: routing_log columns (thread_id, source_channel) and their index are
    # already included in sw_001's routing_log table definition.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_affinity_settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            thread_affinity_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            thread_affinity_ttl_days INTEGER NOT NULL DEFAULT 30,
            thread_overrides JSONB NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT thread_affinity_settings_singleton CHECK (id = 1),
            CONSTRAINT thread_affinity_ttl_days_positive CHECK (thread_affinity_ttl_days > 0)
        )
        """
    )

    op.execute(
        """
        COMMENT ON TABLE thread_affinity_settings IS
        'Singleton settings row (id=1) for email thread-affinity routing. '
        'Controls global enable/disable, TTL window, and per-thread overrides. '
        'See docs/switchboard/thread_affinity_routing.md sections 5 and 6.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN thread_affinity_settings.thread_affinity_enabled IS
        'Global enable/disable for thread-affinity routing. When FALSE, affinity '
        'is skipped for all threads and the pipeline falls through to LLM classification.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN thread_affinity_settings.thread_affinity_ttl_days IS
        'Max age in days for routing history rows considered for affinity lookup. '
        'Default 30 days. Rows older than this window are treated as stale.'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN thread_affinity_settings.thread_overrides IS
        'Per-thread override map: {"<thread_id>": "disabled" | "force:<butler>"}. '
        '"disabled" suppresses affinity for that thread. '
        '"force:<butler>" routes directly to the named butler regardless of history.'
        """
    )

    # Seed the singleton settings row with defaults.
    op.execute(
        """
        INSERT INTO thread_affinity_settings (id, thread_affinity_enabled, thread_affinity_ttl_days, thread_overrides)
        VALUES (1, TRUE, 30, '{}')
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS thread_affinity_settings")
    op.execute("DROP TABLE IF EXISTS connector_source_filters CASCADE")
    op.execute("DROP TABLE IF EXISTS source_filters CASCADE")
    op.execute("DROP INDEX IF EXISTS idx_routing_instructions_active")
    op.execute("DROP TABLE IF EXISTS routing_instructions")
    op.execute("DROP INDEX IF EXISTS ix_ingestion_rules_global_active")
    op.execute("DROP INDEX IF EXISTS ix_ingestion_rules_scope_active")
    op.execute("DROP TABLE IF EXISTS ingestion_rules")
    op.execute("DROP INDEX IF EXISTS triage_rules_condition_gin_idx")
    op.execute("DROP INDEX IF EXISTS triage_rules_rule_type_idx")
    op.execute("DROP INDEX IF EXISTS triage_rules_active_priority_idx")
    op.execute("DROP TABLE IF EXISTS triage_rules")
