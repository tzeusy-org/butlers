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
exact card. Only a model may propose; the proposal installs nothing, and mail, source text and
read-check output are evidence only. Delivery is the owner only, through the RFC 0011 broker, with digest as the default.

## D4: Finance alert_config migrates by explicit mapping

Finance `alert_config` facts have four types. Each maps to a watch shape, is covered by its
existing single-output path, or is shown blind. `large_transaction` becomes a watch and its
response flag is suppressed while the watch is active or paused, and restored on delete or expiry.
For `price_change`, `budget_exceeded` and `new_merchant` the stored config does not govern the
existing path today (price changes are detected unconditionally, budget insights come from
`budget_status` thresholds, new merchants from `anomaly_scan`), so no watch exists and the config is
shown as superseded or blind, never as an active setting. The old and new path never both alert.

## D5: Modes only tighten automatically

Automatic entry is limited to the tighten-only `asleep` and `house_unattended` modes and the
travel-derived in-flight posture flag, which is not the `travel` mode (RFC 0038 S3). In-flight
entry never arms the absence plan; that needs declared or confirmed travel. The adopted sick
exemptions (safety, medication, bills due within 48h, replies to people) apply. Any loosening
uses the existing step-up boundary.
