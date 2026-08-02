## ADDED Requirements

### Requirement: System Verdict Source Settlement

The computed `SystemVerdictBanner` SHALL include database facts and the data
egress catalog in its existing source-settlement gate. It SHALL use the actual
query-hook flags for those sources and SHALL never render an all-clear verdict
from an unsettled or ordinarily unavailable source.

#### Scenario: Healthy database and egress preserve the all-clear verdict

- **WHEN** every System verdict source, including database facts and the data
  egress catalog, has settled without an error
- **THEN** the banner preserves its existing all-clear verdict when no other
  problem clause applies

#### Scenario: Database loading keeps the verdict unsettled

- **WHEN** `useDatabaseFacts()` reports `isLoading=true`
- **THEN** the banner renders its existing loading state
- **AND** it does not render an all-clear verdict

#### Scenario: Database failure names the unavailable source

- **WHEN** `useDatabaseFacts()` reports a settled `isError=true`
- **THEN** the banner includes the clause `database facts unavailable`
- **AND** it does not render an all-clear verdict

#### Scenario: Egress loading keeps the verdict unsettled

- **WHEN** `useEgressFacts()` reports `isLoading=true`
- **THEN** the banner renders its existing loading state
- **AND** it does not render an all-clear verdict

#### Scenario: Ordinary egress failure names the unavailable source

- **WHEN** `useEgressFacts()` reports `isError=true` and `isForbidden=false`
- **THEN** the banner includes the clause `data egress catalog unavailable`
- **AND** it does not render an all-clear verdict

#### Scenario: Owner-only egress denial is settled limited visibility

- **WHEN** `useEgressFacts()` reports `isForbidden=true` for the expected HTTP
  403 owner-only response
- **THEN** the banner treats the egress source as settled, limited visibility
  rather than an unavailable service
- **AND** it does not render the egress-unavailable clause or change endpoint
  authorization behavior
