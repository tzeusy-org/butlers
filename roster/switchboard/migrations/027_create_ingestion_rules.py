"""Create ingestion_rules table and migrate data from triage_rules + source_filters.

Revision ID: sw_027
Revises: sw_026
Create Date: 2026-03-08 00:00:00.000000

Migration notes:
- Creates unified ingestion_rules table per unified-ingestion-policy design.md D9.
- scope column: 'global' for post-ingest/pre-LLM rules, 'connector:<type>:<identity>'
  for pre-ingest connector-scoped rules.
- Connector-scoped rules constrained to action='block' via CHECK.
- Migrates triage_rules → ingestion_rules (scope='global'), preserving IDs.
- Migrates source_filters × connector_source_filters → ingestion_rules:
  * Blacklist patterns → individual block rules per pattern per connector assignment.
  * Whitelist patterns → individual pass_through rules + catch-all block rule.
- Downgrade drops ingestion_rules (old tables remain untouched).
"""

from __future__ import annotations

import json
import logging
import uuid

from alembic import op

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision = "sw_027"
down_revision = "sw_026"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Whitelist catch-all priority offset (design.md D4)
# ---------------------------------------------------------------------------
_WHITELIST_CATCHALL_PRIORITY_OFFSET = 1000


def upgrade() -> None:
    # --- 1. Create the ingestion_rules table (design.md D9 DDL) -----------
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
                CHECK (scope = 'global' OR action = 'block'),

            CONSTRAINT ingestion_rules_priority_check
                CHECK (priority >= 0)
        )
        """
    )

    # --- 2. Create indexes ------------------------------------------------
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

    # --- 3. Migrate triage_rules → ingestion_rules (scope='global') -------
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

    # --- 4. Migrate source_filters × connector_source_filters -------------
    _migrate_source_filters()


def _migrate_source_filters() -> None:
    """Expand source_filter patterns into individual ingestion_rules rows.

    For each enabled connector_source_filters assignment:
    - Blacklist: one 'block' rule per pattern (priority from csf.priority).
    - Whitelist: one 'pass_through' rule per pattern + one catch-all 'block'
      rule at priority = csf.priority + 1000.

    The rule_type is mapped from source_filters.source_key_type:
      'domain' → 'sender_domain'
      others   → kept as-is (sender_address, substring, chat_id, channel_id)
    """
    conn = op.get_bind()
    rows = conn.execute(
        __import__("sqlalchemy").text(
            """
            SELECT
                sf.id::text          AS filter_id,
                sf.name              AS filter_name,
                sf.description       AS filter_description,
                sf.filter_mode       AS filter_mode,
                sf.source_key_type   AS source_key_type,
                sf.patterns          AS patterns,
                csf.connector_type   AS connector_type,
                csf.endpoint_identity AS endpoint_identity,
                csf.priority         AS priority
            FROM connector_source_filters csf
            JOIN source_filters sf ON sf.id = csf.filter_id
            WHERE csf.enabled = true
            ORDER BY csf.connector_type, csf.endpoint_identity, csf.priority
            """
        )
    ).fetchall()

    if not rows:
        logger.info("sw_027: no active source_filter assignments to migrate")
        return

    inserts: list[dict] = []
    for row in rows:
        scope = f"connector:{row.connector_type}:{row.endpoint_identity}"
        raw_key_type = row.source_key_type
        rule_type = "sender_domain" if raw_key_type == "domain" else raw_key_type
        patterns: list[str] = list(row.patterns) if row.patterns else []

        if row.filter_mode == "blacklist":
            for pat in patterns:
                condition = _build_condition(rule_type, pat)
                inserts.append(
                    {
                        "id": str(uuid.uuid4()),
                        "scope": scope,
                        "rule_type": rule_type,
                        "condition": json.dumps(condition),
                        "action": "block",
                        "priority": row.priority,
                        "enabled": True,
                        "name": row.filter_name,
                        "description": row.filter_description,
                        "created_by": "migration",
                    }
                )
        elif row.filter_mode == "whitelist":
            # One pass_through per pattern
            for pat in patterns:
                condition = _build_condition(rule_type, pat)
                inserts.append(
                    {
                        "id": str(uuid.uuid4()),
                        "scope": scope,
                        "rule_type": rule_type,
                        "condition": json.dumps(condition),
                        "action": "pass_through",
                        "priority": row.priority,
                        "enabled": True,
                        "name": row.filter_name,
                        "description": row.filter_description,
                        "created_by": "migration",
                    }
                )
            # Catch-all block at priority + offset (design.md D4)
            catchall_condition = _build_catchall_condition(rule_type)
            inserts.append(
                {
                    "id": str(uuid.uuid4()),
                    "scope": scope,
                    "rule_type": rule_type,
                    "condition": json.dumps(catchall_condition),
                    "action": "block",
                    "priority": row.priority + _WHITELIST_CATCHALL_PRIORITY_OFFSET,
                    "enabled": True,
                    "name": f"{row.filter_name} (catch-all block)",
                    "description": (
                        f"Auto-generated catch-all block for whitelist filter '{row.filter_name}'"
                    ),
                    "created_by": "migration",
                }
            )
        else:
            logger.warning(
                "sw_027: skipping unknown filter_mode %r for filter %s",
                row.filter_mode,
                row.filter_id,
            )
            continue

    if not inserts:
        logger.info("sw_027: no source_filter patterns to expand")
        return

    # Bulk insert via parameterised multi-row INSERT
    sa_text = __import__("sqlalchemy").text
    for ins in inserts:
        conn.execute(
            sa_text(
                """
                INSERT INTO ingestion_rules
                    (id, scope, rule_type, condition, action, priority, enabled,
                     name, description, created_by)
                VALUES
                    (:id, :scope, :rule_type, :condition::jsonb, :action, :priority,
                     :enabled, :name, :description, :created_by)
                """
            ),
            ins,
        )

    logger.info("sw_027: migrated %d source_filter pattern(s) into ingestion_rules", len(inserts))


def _build_condition(rule_type: str, pattern: str) -> dict:
    """Build a condition JSONB object from a source_filter pattern.

    Maps source_filter patterns to the condition schema used by the unified
    ingestion_rules table. The condition must be interpretable by the new
    IngestionPolicyEvaluator.
    """
    if rule_type == "sender_domain":
        return {"domain": pattern, "match": "suffix"}
    if rule_type == "sender_address":
        return {"address": pattern}
    if rule_type == "substring":
        return {"pattern": pattern}
    if rule_type == "chat_id":
        return {"chat_id": pattern}
    if rule_type == "channel_id":
        return {"channel_id": pattern}
    # Fallback: store raw pattern
    return {"pattern": pattern}


def _build_catchall_condition(rule_type: str) -> dict:
    """Build a catch-all condition that matches everything for a given rule_type.

    Used for whitelist catch-all block rules: any message not explicitly allowed
    by a pass_through rule should be blocked.
    """
    if rule_type == "sender_domain":
        return {"domain": "*", "match": "any"}
    if rule_type == "sender_address":
        return {"address": "*"}
    if rule_type == "substring":
        return {"pattern": "*"}
    if rule_type == "chat_id":
        return {"chat_id": "*"}
    if rule_type == "channel_id":
        return {"channel_id": "*"}
    return {"pattern": "*"}


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_ingestion_rules_global_active")
    op.execute("DROP INDEX IF EXISTS ix_ingestion_rules_scope_active")
    op.execute("DROP TABLE IF EXISTS ingestion_rules")
