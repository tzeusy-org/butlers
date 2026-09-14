## Context

See [proposal.md](proposal.md) and the paired capability deltas. The existing
catalog policy is server-held, but a local direct UUID read updated and returned
a row without applying that policy. The context assembler also appended a
withheld receipt after its section had already consumed its allocation.

## Goals / Non-Goals

**Goals:**

- Reuse the existing server-held policy vocabulary for all local reads without
  adding a caller-controlled authority input.
- Enforce UUID retrieval in its SQL mutation so denied rows are neither exposed
  nor reference-bumped.
- Make context accounting strict enough that headers and withheld receipts do
  not exceed the requested budget.
- Repair the affected Health historical data without changing measurement
  classification or creating cross-schema grants.

**Non-Goals:**

- No new sensitivity tier, encryption layer, cross-butler query path, or
  caller-selectable authority.
- No reclassification of Health measurements or unrelated memory predicates.
- No restoration of catalog rows during downgrade; an irreversible privacy
  repair must not republish sensitive history.

## Decisions

### Resolve authority at the module boundary and filter in the storage mutation

Every public MCP read closure resolves the existing runtime-config-held policy
and passes it to its local reader; callers cannot supply a ceiling. This keeps
private memory schemas, such as Chronicler's, from substituting a missing or
stale local `runtime_config` row for their owning daemon's authority. The direct
UUID retrieval adds the allowed-sensitivity predicate to the same `UPDATE ...
RETURNING` statement that increments reference metadata. This makes a denied
row indistinguishable from an absent UUID and prevents a side-effecting
pre-fetch.

Filtering after a generic fetch was rejected because it would still read a
more-sensitive row and bump its reference metadata. A caller argument was
rejected because it would turn authority into an assertion rather than a
server-held decision.

### Reserve fixed context text before rendering variable facts

The assembler reserves the preamble from the total character budget before
partitioning section allocations. Profile rendering reserves the withheld
receipt before fitting facts; if a header and receipt cannot both fit, the
entire Profile Facts section is omitted. This favors the stated hard budget and
content exclusion over a partial or overflowing receipt.

Appending the receipt after `_fill_section` was rejected because it breaks the
section partition. Truncating the receipt was rejected because a partial count
would be misleading.

### Repair Health facts in a serialized, idempotent core migration

`core_234` follows the current `core_233` head. It updates only the four
clinical Health predicates and removes catalog rows by authoritative source
fact identity, not by the catalog's stale recorded sensitivity. It uses
relation guards for heterogeneous schemas and has a no-op downgrade: lowering
the sensitivity or reconstructing catalog entries would reintroduce a privacy
exposure.

Broad catalog deletion was rejected because it could remove unrelated rows;
reclassification by a blanket `health` scope was rejected because measurements
remain intentionally discoverable.

## Risks / Trade-offs

- [A very small context budget omits the Profile Facts section] → The complete
  result still fits the requested budget and never exposes excluded content.
- [A correct UUID can look absent to a lower-authority caller] → This is
  intentional non-disclosure; no detail distinguishes authorization from
  absence.
- [A historical catalog row cannot be reconstructed on downgrade] → The
  migration is intentionally irreversible and the source fact remains durable.
- [Migration sequence advances while this PR is open] → Recheck the exact
  current head and use the next free revision before each rebase/push.

## Migration Plan

1. Rebase onto the current core migration head and validate there is exactly
   one head with `core_234` following `core_233`.
2. Apply the guarded migration; reruns reclassify no already-confidential fact
   and delete no additional catalog row.
3. Verify SQL-level direct retrieval denial and unchanged reference metadata.
4. Do not downgrade in production; rollback disables new code while preserving
   the privacy repair already applied to historical data.
