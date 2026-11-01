## ADDED Requirements

### Requirement: A retracted loan claim stays retracted across backfill

The Relationship loan backfill SHALL NOT assert a new live claim for a `loan:{fact_id}` claim key whose claim was retracted and has no live row, and SHALL count the skip. It SHALL also skip a loan fact marked settled: settlement supersedes the loan fact with a settled successor under a new fact id and retracts the old key's claim, and settlement SHALL NOT re-key a live claim. The backfill SHALL NOT alter claim supersession.

ID: REQ-finance-cost-claims-001
Source: bu-boqjzb; finance-cost-claims "The asserting butler's own domain record SHALL remain the source of truth"
Scope: v1-mandatory

#### Scenario: Settle then backfill

- **WHEN** a loan is settled and the backfill runs again
- **THEN** no live claim SHALL exist for the settled successor fact

#### Scenario: Retract then backfill

- **WHEN** the owner retracts a backfilled loan claim and the backfill runs again while the loan fact is still active
- **THEN** no live claim for that loan SHALL exist
- **AND** the retracted row SHALL be kept
