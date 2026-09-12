## Why

Travel medication-preparation workflows need a privacy-safe view of active medications, but the
canonical medication data is private to the Health butler. The current Travel spec still suggests
public-schema access even though doctrine requires interactive cross-butler reads to use MCP through
the Switchboard.

## What Changes

- Add a versioned Health MCP response containing only active medication name, dosage, frequency,
  and schedule fields needed for travel preparation.
- Add a Travel-side MCP consumer that requests that response through the Switchboard, checks the
  Travel butler's cross-butler permission, and validates the response contract.
- Define successful-empty, permission-denied, unavailable-provider, and malformed-response behavior.
- Remove the Travel spec's stale public-schema access wording and require Health MCP routing.
- Reuse Health's canonical `facts` storage; no table, column, grant, or migration is added.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `butler-health`: Add the narrow, active-only medication snapshot MCP provider contract.
- `butler-travel`: Add the Switchboard-routed medication snapshot consumer contract and replace
  stale public-schema access language.

## Impact

- Health medication tools and MCP registration under `roster/health/`.
- Travel health-data consumer and MCP registration under `roster/travel/`.
- Shared typed contract code under `src/butlers/`.
- Provider, consumer, and cross-butler contract tests.
- No dependency, storage, database permission, or migration changes.
