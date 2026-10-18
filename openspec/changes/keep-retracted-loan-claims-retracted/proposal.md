## Why

`cost_claim_retract` retracts a Relationship loan's claim but leaves the loan fact active, and the live-unique claim index is partial. The daily loan backfill therefore inserted a fresh live claim for a loan the owner had just retracted (found in the #4704 review, bu-boqjzb).

## What changes

- `backfill_loan_cost_claims` skips a `loan:{fact_id}` whose claim was retracted and has no live row, and counts it as `skipped_retracted`. A loan changed on purpose (amended, settled) is a new fact id and so a new claim key.
- Supersession semantics and the claim tables are unchanged.
