## ADDED Requirements

### Requirement: A retracted loan claim stays retracted across backfill

The Relationship loan backfill SHALL NOT assert a new live claim for a `loan:{fact_id}` claim key whose claim was retracted and has no live row, and SHALL count the skip. A change to the loan itself (an amendment or settlement) carries a new fact id and therefore a new claim key. The backfill SHALL NOT alter claim supersession.

ID: REQ-finance-cost-claims-001
Source: bu-boqjzb; finance-cost-claims "The asserting butler's own domain record SHALL remain the source of truth"
Scope: v1-mandatory

#### Scenario: Retract then backfill

- **WHEN** the owner retracts a backfilled loan claim and the backfill runs again while the loan fact is still active
- **THEN** no live claim for that loan SHALL exist
- **AND** the retracted row SHALL be kept
