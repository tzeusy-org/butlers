## ADDED Requirements

### Requirement: Real-DB migration integration tests for the memory module

The memory module SHALL have integration tests that apply the full migration chain to a real PostgreSQL schema and verify the resulting schema matches what runtime code expects. These tests prevent code-vs-schema drift from going undetected.

#### Scenario: Full chain applies cleanly to fresh schema

- **WHEN** the complete memory migration chain (mem_001 through the latest) is applied to an empty PostgreSQL schema
- **THEN** all migrations MUST succeed without error
- **AND** the resulting schema MUST contain tables: `episodes`, `facts`, `rules`, `memory_links`, `memory_events`, `predicate_registry`, `memory_policies`, `rule_applications`

#### Scenario: Critical columns exist with correct types

- **WHEN** the full chain has been applied
- **THEN** the `episodes` table MUST have columns `tenant_id` (TEXT NOT NULL), `request_id` (TEXT), `retention_class` (TEXT NOT NULL), `sensitivity` (TEXT NOT NULL), `leased_until` (TIMESTAMPTZ), `leased_by` (TEXT), `dead_letter_reason` (TEXT), `consolidation_attempts` (INTEGER)
- **AND** the `facts` table MUST have columns `tenant_id` (TEXT NOT NULL), `request_id` (TEXT), `retention_class` (TEXT NOT NULL), `sensitivity` (TEXT NOT NULL), `idempotency_key` (TEXT), `observed_at` (TIMESTAMPTZ), `invalid_at` (TIMESTAMPTZ), `valid_at` (TIMESTAMPTZ)
- **AND** the `rules` table MUST have columns `tenant_id` (TEXT NOT NULL), `request_id` (TEXT), `retention_class` (TEXT NOT NULL), `sensitivity` (TEXT NOT NULL)

#### Scenario: Store cycle succeeds against real schema

- **WHEN** a `store_episode`, `store_fact`, and `store_rule` are called against the migrated schema
- **THEN** all three operations MUST succeed
- **AND** the inserted rows MUST have `tenant_id`, `retention_class`, and `sensitivity` set to the values passed by the caller (not only migration defaults)

#### Scenario: memory_policies seeded correctly

- **WHEN** the full chain has been applied
- **THEN** the `memory_policies` table MUST contain exactly 8 rows with retention classes: `transient`, `episodic`, `operational`, `personal_profile`, `health_log`, `financial_log`, `rule`, `anti_pattern`
- **AND** each row MUST have non-null `decay_rate` and `min_retrieval_confidence` values
