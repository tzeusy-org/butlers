# Preserve premise-amendment migration evidence

## Why

The historical core_255 correction changed grants and downgrade retention together. A privilege-filtered inventory comparison cannot distinguish those causes or prove runtime row authority and function ownership.

## What Changes

- Add one bounded requirement distinguishing metadata inventory, rows, DDL ownership and SECURITY DEFINER authority.
- Retain the current evidence-preserving rollback and its documented data loss, without modifying applied migrations or bootstrap grants.
- Add isolated historical/current real-PG controls to the owning tests and document the diagnosis.

## Impact

- Affected spec: proactive-insight-engine. The existing premise and amendment requirements and their six scenarios remain unchanged.
- Affected implementation: tests/migrations/test_core_255_insight_premise_binding_migration.py, tests/config/test_migrations.py (diagnostic only), historical test data, and docs/data_and_storage/migration-patterns.md.
- Production migrations, runtime grants, security posture, FK lifecycle and core revision allocation remain with their existing owners. bu-q7vx1q.33 retains the related security/lifecycle work.
