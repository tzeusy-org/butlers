## Why

`cost_claim_retract` retracts a Relationship loan's claim but leaves the loan fact active, and the live-unique claim index is partial. The daily loan backfill therefore inserted a fresh live claim for a loan the owner had just retracted (found in the #4704 review, bu-boqjzb).

## What changes

- `backfill_loan_cost_claims` skips a `loan:{fact_id}` whose claim was retracted and has no live row, and counts it as `skipped_retracted`. It also skips settled loan facts (counted as `skipped_settled`): `loan_settle` supersedes the fact with a settled successor under a new id and retracts the old claim, so projecting the successor would resurrect a claim for a settled loan. There is no loan amend path.
- Supersession semantics and the claim tables are unchanged.
