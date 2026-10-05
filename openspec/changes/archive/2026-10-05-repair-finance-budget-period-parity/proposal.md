## Why

Quarterly budgets promised by RFC 0012 and finance-budgets reach a database CHECK that rejects them. The installed CHECK also permits daily budgets that current status/removal tools cannot manage, while the RFC's annual spelling differs from the shipped yearly token.

## What Changes

- Admit daily, weekly, monthly, quarterly and yearly consistently through budget tools and the current migrated CHECK.
- Preserve legacy daily/yearly data and all existing period identities; add owner-calendar day bounds and daily alert scope.
- Install an additive finance-chain CHECK repair and refuse downgrade while any quarterly history remains.
- Explicitly clarify RFC 0012's annual calendar span as the canonical yearly token.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `finance-budgets`: supported periods, invalid-period rejection and owner-calendar bounds.
- `finance-supporting-tables`: current budget CHECK and data-preserving upgrade/downgrade guarantees.
- `finance-alerts`: daily budget scope while preserving every existing summary and alert guarantee.

## Impact

Finance budget tools, registered period descriptions, alert period scopes, a forward finance migration, existing budget/migration/calendar tests, RFC 0012 and finance implementation notes. No new dependency, API or dashboard surface. Historical finance_006 and all budget rows remain unchanged. Closed bu-lsxqb0.7 assigns this standalone repair to bu-aubi0k; bu-lsxqb0.18 family consolidation and bu-lsxqb0.21 RFC condensation retain their separate source gates.
