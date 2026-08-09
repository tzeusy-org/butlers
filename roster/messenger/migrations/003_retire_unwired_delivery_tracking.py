"""Retire the unused Messenger delivery tracking tables.

The upgrade takes an ``ACCESS EXCLUSIVE`` lock on every existing retired table
before checking for rows and before issuing any ``DROP TABLE``.  A non-empty
table therefore fails closed instead of creating a check-to-drop race with a
concurrent writer.

Downgrade recreates an empty ``msg_002`` compatibility schema only. It cannot
recover any rows removed by an externally authorized retention process.
"""

from alembic import op

revision = "msg_003"
down_revision = "msg_002"
branch_labels = None
depends_on = None

_RETIRED_TABLES = (
    "delivery_dead_letter",
    "delivery_receipts",
    "delivery_attempts",
    "delivery_requests",
)

_RETIRE_SQL = """
DO $$
DECLARE
    retired_tables TEXT[] := ARRAY[
        'delivery_dead_letter',
        'delivery_receipts',
        'delivery_attempts',
        'delivery_requests'
    ];
    table_name TEXT;
    lock_targets TEXT;
    has_rows BOOLEAN;
BEGIN
    SELECT string_agg(
        format('%I.%I', current_schema(), candidate.table_name),
        ', ' ORDER BY candidate.ordinality
    )
    INTO lock_targets
    FROM unnest(retired_tables) WITH ORDINALITY AS candidate(table_name, ordinality)
    WHERE to_regclass(format('%I.%I', current_schema(), candidate.table_name)) IS NOT NULL;

    IF lock_targets IS NOT NULL THEN
        EXECUTE 'LOCK TABLE ' || lock_targets || ' IN ACCESS EXCLUSIVE MODE';
    END IF;

    FOREACH table_name IN ARRAY retired_tables LOOP
        IF to_regclass(format('%I.%I', current_schema(), table_name)) IS NOT NULL THEN
            EXECUTE format(
                'SELECT EXISTS (SELECT 1 FROM %I.%I LIMIT 1)',
                current_schema(),
                table_name
            ) INTO has_rows;
            IF has_rows THEN
                RAISE EXCEPTION
                    'Cannot retire messenger tracking table %: it contains rows. '
                    'Stop before DDL and obtain an explicit retention migration decision.',
                    table_name;
            END IF;
        END IF;
    END LOOP;

    FOREACH table_name IN ARRAY retired_tables LOOP
        IF to_regclass(format('%I.%I', current_schema(), table_name)) IS NOT NULL THEN
            EXECUTE format('DROP TABLE %I.%I', current_schema(), table_name);
        END IF;
    END LOOP;
END $$;
"""

_RESTORE_MSG_002_SQL = """
CREATE TABLE IF NOT EXISTS delivery_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL UNIQUE,
    request_id UUID,
    origin_butler TEXT NOT NULL,
    channel TEXT NOT NULL,
    intent TEXT NOT NULL CHECK (intent IN ('send', 'reply')),
    target_identity TEXT NOT NULL,
    message_content TEXT NOT NULL,
    subject TEXT,
    request_envelope JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'delivered', 'failed', 'dead_lettered')),
    terminal_error_class TEXT,
    terminal_error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    terminal_at TIMESTAMPTZ,
    priority TEXT NOT NULL DEFAULT 'medium',
    CONSTRAINT delivery_requests_priority_check
        CHECK (priority IN ('high', 'medium', 'low'))
);

CREATE INDEX IF NOT EXISTS idx_delivery_requests_request_id
    ON delivery_requests (request_id)
    WHERE request_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_delivery_requests_origin_butler
    ON delivery_requests (origin_butler, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_delivery_requests_channel_status
    ON delivery_requests (channel, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_delivery_requests_priority_status
    ON delivery_requests (priority, status)
    WHERE status IN ('pending', 'in_progress');

CREATE TABLE IF NOT EXISTS delivery_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_request_id UUID NOT NULL REFERENCES delivery_requests(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    latency_ms INTEGER,
    outcome TEXT NOT NULL
        CHECK (outcome IN ('success', 'retryable_error', 'non_retryable_error', 'timeout', 'in_progress')),
    error_class TEXT,
    error_message TEXT,
    provider_response JSONB,
    UNIQUE (delivery_request_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS idx_delivery_attempts_request_started
    ON delivery_attempts (delivery_request_id, started_at);

CREATE TABLE IF NOT EXISTS delivery_receipts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_request_id UUID NOT NULL REFERENCES delivery_requests(id) ON DELETE CASCADE,
    provider_delivery_id TEXT,
    receipt_type TEXT NOT NULL
        CHECK (receipt_type IN ('sent', 'delivered', 'read', 'webhook_confirmation')),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_delivery_receipts_request
    ON delivery_receipts (delivery_request_id, received_at);

CREATE INDEX IF NOT EXISTS idx_delivery_receipts_provider_id
    ON delivery_receipts (provider_delivery_id)
    WHERE provider_delivery_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS delivery_dead_letter (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_request_id UUID NOT NULL REFERENCES delivery_requests(id) ON DELETE CASCADE,
    quarantine_reason TEXT NOT NULL,
    error_class TEXT NOT NULL,
    error_summary TEXT NOT NULL,
    total_attempts INTEGER NOT NULL,
    first_attempt_at TIMESTAMPTZ NOT NULL,
    last_attempt_at TIMESTAMPTZ NOT NULL,
    original_request_envelope JSONB NOT NULL,
    all_attempt_outcomes JSONB NOT NULL DEFAULT '[]',
    replay_eligible BOOLEAN NOT NULL DEFAULT true,
    replay_count INTEGER NOT NULL DEFAULT 0,
    discarded_at TIMESTAMPTZ,
    discard_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (delivery_request_id)
);

CREATE INDEX IF NOT EXISTS idx_delivery_dead_letter_replay
    ON delivery_dead_letter (replay_eligible, created_at)
    WHERE replay_eligible = true AND discarded_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_delivery_dead_letter_error_class
    ON delivery_dead_letter (error_class, created_at DESC);
"""


def upgrade() -> None:
    """Lock, inspect, and retire only an empty legacy delivery schema."""
    op.execute(_RETIRE_SQL)


def downgrade() -> None:
    """Recreate exact empty msg_002 tables and indexes; historic rows stay gone."""
    op.execute(_RESTORE_MSG_002_SQL)
