## Context

The first-switch evaluator (`butlers.conditions`, bu-dhloi1.11) validates one closed tagged AST
in Python and mirrors it in `perception_private.valid_predicate` (`scripts/init-db.sql`). The
perception registry already carries the typed rows the new leaves read: `expect.window`
(deviation `pending|within|missed`), `owner.mode`, `system.dep`, and the owner condition ledger
(`public.owner_conditions`) carries opened/resolved concerns. No watch, read-check, mode leaf or
correlation code exists on main (cf35f4e6ab).

## D1: Extend the one AST, add no second language

Every M7 consumer registers an instance through the same register/evaluate/consume_fire contract
(REQ-condition-evaluation-001/002). New leaves are tags of the same closed AST, so depth, leaf
count, 8KiB bound, grant check and three-valued unknown propagation apply unchanged. A watch is
an instance with an owner-origin approval receipt, not a new evaluator or scheduler.

## D2: Leaves read typed registry rows, never free text

`expectation`, `routine_deviation`, `mode` and `condition` read existing typed rows by registered
kind and subject UUID. `read_check` names a registered, owner-granted read capability and a typed
comparison over its declared output schema. Its output is data: it can make a leaf true or
unknown, and it can never author, widen or edit a watch or rule.

## D3: Authoring is an owner action behind an exact card

Create, update and delete of a watch or rule are owner-origin administer effects approved on an
exact card. A model, a mail body or a routine suggestion can only propose; the proposal installs
nothing. Delivery is the owner only, through the RFC 0011 broker, with digest as the default.

## D4: Finance alert_config migrates by explicit mapping

Finance `alert_config` facts have four types. Each maps to exactly one watch shape or is refused
as blind when no evaluator seam measures it. The old and new path never both alert.

## D5: Modes only tighten automatically

Automatic entry is limited to the closed registry's tighten-only modes (`asleep`, in-flight
`travel`, `house_unattended`). Any loosening uses the existing step-up boundary.

## Open verification points

- `budget_exceeded` and `new_merchant` are stored by `alert_configure` but only `large_transaction`
  and `price_change` have an evaluator consuming the stored config on main. Slice 2.3 verifies
  this against the code before mapping.
