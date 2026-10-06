## ADDED Requirements

### Requirement: Premise Amendment Migration Evidence

The premise-amendment migration lifecycle SHALL distinguish the ordinary migration login's privilege-filtered metadata inventory from table row visibility, ownership and SECURITY DEFINER execution authority. A controlled bounded core_255 rollback SHALL retain existing `public.insight_amendments` rows while their referenced candidates remain, preserve the existing runtime row policy, and remove the candidate premise and delivery-reference columns with their documented lossy folds. Retention SHALL NOT be treated as permission to broaden runtime grants or as proof that bootstrap-recreated functions have converged to the ordinary migration owner.

#### Scenario: Bootstrap installation preserves metadata visibility without widening row authority

- **WHEN** core_255 installs the amendment table under managed bootstrap authority after an ordinary migration to core_254
- **THEN** the existing targeted Switchboard table grant makes its columns visible to the ordinary migration login through its configured inherited membership
- **AND** actual runtime roles remain subject to the amendment table's current Switchboard row policy before and after a replay of the production bootstrap
- **AND** producer enqueue and the narrow finance probe use their existing fixed function interfaces rather than new direct peer-schema access

#### Scenario: A bounded rollback retains queued correction evidence

- **WHEN** an owning or managed-bootstrap migration bounds its upgrade at core_255 and rolls back to core_254
- **THEN** existing pending, applied, fold and folded amendment rows retain their ids and stored fields while their candidates remain
- **AND** candidate premise and delivery-reference data is removed, candidate withdrawn status folds to filtered, and the ledger's documented withdrawn and amended folds remain cumulative-CHECK safe
- **AND** the rollback does not claim that the surviving table means amendment delivery is available to the older code

#### Scenario: Metadata inventory loss is diagnosed with a positive object witness

- **WHEN** a disposable PostgreSQL round-trip comparison reports the amendment table only in the fresh database
- **THEN** the investigation compares a positive qualified administrative object witness with the ordinary login's metadata inventory and records login, object owner, database/schema ownership, effective inherited privileges and per-creator defaults before attributing the failure to teardown
- **AND** grant-only and retention-only controls are tested separately when the historical correction changed both
- **AND** missing function execution or ownership convergence is recorded independently of table inventory equality
