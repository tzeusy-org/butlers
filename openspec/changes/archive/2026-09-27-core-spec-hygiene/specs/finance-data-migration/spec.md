## REMOVED Requirements

### Requirement: Four-phase migration from SPO-primary to dedicated-table-primary
**Reason**: The finance migration phases 1 to 3 have shipped; the capability described a plan, not a standing contract.

**Migration**: The standing backfill contract moved to finance-transaction-schema "SPO transaction backfill"; the unshipped phase 4 moved to finance-transaction-schema "[TARGET-STATE] SPO transaction mirror retirement".

### Requirement: Migration Alembic structure
**Reason**: Describes how one-time migrations were laid out; the migrations exist and are not a behavioral contract.

**Migration**: None.
