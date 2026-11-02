## ADDED Requirements

### Requirement: Finance field origins and claims survive atomic writes and rollback

Finance MUST persist server-derived origins and value revisions for renewal date, complete amount/currency pair, service and bill payee, with immutable claims and confirmation epochs for date and price disagreements. Missing legacy evidence SHALL be extracted, and migration or rollback MUST NOT erase the authority or unresolved history needed to prevent the two security gaps.

ID: REQ-finance-supporting-tables-002
Source: bu-svqpzo.19 proposed storage contract; RFC 0038 D6; REQ-finance-supporting-tables-001
Scope: v1-mandatory

#### Scenario: W07 Concurrent owner and extracted writers preserve one logical subscription

- **WHEN** owner and extracted updates race in either order, including concurrent creation for the same service and frequency
- **THEN** one logical subscription MUST remain and its baseline, complete pair, origins, revision and claims MUST commit atomically
- **AND** owner state MUST survive; a subsequent extracted disagreement MAY add pending history but MUST NOT replace owner state; an independent committed read MUST observe no partial pair

#### Scenario: W08 Missing or forged attestation cannot manufacture owner origin

- **WHEN** server-held writer attestation is absent, unreadable or contradicted by caller metadata or arguments
- **THEN** the write MUST be extracted and MUST NOT derive owner authority from a caller source label
- **AND** field origins MUST NOT confer expected-signal producer measurability or replace the separate endpoint-attestation contract

#### Scenario: W12 Identical price replay adds no authority

- **WHEN** the same complete price pair and source binding are replayed in the same confirmation epoch
- **THEN** the stored claim MUST be idempotent and MUST NOT create another warning or promote its origin
- **AND** distinct claims MUST retain their exact typed values and available authenticated source bindings; a missing source binding MUST NOT be invented

#### Scenario: W15 Legacy values retain content without fabricated origin

- **WHEN** existing subscription or bill rows are upgraded or read without trustworthy per-field origins
- **THEN** date, price pair and display-name origins MUST read as extracted while raw values remain intact
- **AND** last-writer metadata MUST NOT upgrade them; existing cancellation URL and status provenance from finance_017 MUST remain unchanged

#### Scenario: W16 Rollback preserves unresolved and owner authority

- **WHEN** upgrade, reapply, failed transaction, downgrade or a concurrent writer encounters stored field origins or claims
- **THEN** history, owner state and earlier-warning eligibility MUST survive; an unsafe downgrade MUST refuse atomically or retain the required data and enforcement
- **AND** rollback MUST NOT restore an unprotected writer or active extracted rendering; roles, row ownership and existing expected-signal authority MUST remain unchanged
