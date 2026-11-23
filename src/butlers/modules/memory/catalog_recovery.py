"""Operator-only catalog re-sync after a completed memory rollback."""

RESTORE_LEGACY_RULE_CATALOG_SQL = """
DO $restore$
DECLARE
    owning_schema text := current_schema();
    restored bigint;
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'Legacy rule catalog re-sync requires READ COMMITTED';
    END IF;

    -- A narrow search path binds the three unqualified tables to the same owner.
    IF to_regclass(format('%I.rules', owning_schema)) IS DISTINCT FROM to_regclass('rules')
       OR to_regclass(format('%I.memory_events', owning_schema))
          IS DISTINCT FROM to_regclass('memory_events')
       OR to_regclass(format('%I.alembic_version', owning_schema))
          IS DISTINCT FROM to_regclass('alembic_version') THEN
        RAISE EXCEPTION 'Pin search_path to the owning Memory schema';
    END IF;

    -- Block rule writes and concurrent schema changes until the re-sync commits.
    LOCK TABLE rules IN SHARE ROW EXCLUSIVE MODE;
    IF (SELECT array_agg(version_num::text) FROM alembic_version
        WHERE left(version_num, 4) = 'mem_') IS DISTINCT FROM ARRAY['mem_012']
       OR EXISTS (
            SELECT 1 FROM pg_attribute
            WHERE attrelid = 'rules'::regclass AND attname = 'content_authority'
              AND attnum > 0 AND NOT attisdropped
       ) THEN
        RAISE EXCEPTION 'Legacy rule catalog re-sync requires Memory at mem_012';
    END IF;

    -- mem_013 stamps both catalog timestamps and writes its receipt in the
    -- same transaction. Previously invalid rows retain their old timestamps.
    UPDATE public.memory_catalog AS catalog
    SET confidence = rule.confidence, invalid_at = NULL, updated_at = now()
    FROM rules AS rule
    WHERE catalog.source_schema = owning_schema
      AND catalog.source_table = 'rules' AND catalog.memory_type = 'rule'
      AND catalog.source_id = rule.id AND catalog.tenant_id = rule.tenant_id
      AND rule.retired_at IS NULL AND (rule.metadata->>'forgotten')::boolean IS NOT TRUE
      AND catalog.confidence = 0 AND catalog.invalid_at = catalog.updated_at
      AND EXISTS (
          SELECT 1 FROM memory_events AS receipt
          WHERE receipt.event_type = 'content_authority_legacy_receipt'
            AND receipt.actor = 'migration' AND receipt.created_at = catalog.invalid_at
            AND receipt.payload->>'schema' = owning_schema
            AND jsonb_path_exists(receipt.payload, '$.catalog_rows_staled ? (@ > 0)')
      );
    GET DIAGNOSTICS restored = ROW_COUNT;
    RAISE NOTICE 'Legacy rule catalog rows restored: %', restored;
END
$restore$;
"""
