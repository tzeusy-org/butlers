# Approval Gating Module — Delta

## ADDED Requirements

### Requirement: Structured Decision Dossier

Every gated, non-owner-target tool invocation SHALL carry a structured decision
dossier sufficient for the owner to judge the action from a push notification:
a required human-readable `why`, an optional `blast_radius` in
`none | self | contact | external`, an optional `reversibility` in
`reversible | compensable | irreversible`, and `evidence` as a list of typed
references `{"type": "fact" | "entity" | "url" | "text", "ref": string,
"note": string}`. `blast_radius` and `reversibility` are stored as nullable
CHECK-constrained columns on `pending_actions`; legacy plain-string evidence
entries MUST be migrated to typed `text` entries by the approvals-chain
migration. New gate/API inputs MUST be strictly validated at the boundary and
MUST NOT use runtime coercion for invalid or legacy-shaped evidence.

#### Scenario: Gated call without why is rejected retryably

- **WHEN** an LLM session invokes a gated tool targeting a non-owner recipient
  without a `_why`/`why` argument
- **THEN** the gate returns a structured error naming the missing `why` field
  and does not create a pending action
- **AND** the same invocation retried with `why` provided parks normally

#### Scenario: Owner-target calls are exempt from the dossier requirement

- **WHEN** a gated tool call resolves to a verified owner channel and
  auto-approves via the owner-role path
- **THEN** the call executes without requiring `why`, `blast_radius`, or
  `reversibility`

#### Scenario: Dossier fields persist and surface

- **WHEN** a gated call parks with `why`, `blast_radius = "contact"`,
  `reversibility = "irreversible"`, and a typed evidence list
- **THEN** the `pending_actions` row stores all dossier fields
- **AND** the approvals detail API returns them on the action detail

#### Scenario: Invalid dossier enum is rejected

- **WHEN** a gated call supplies `blast_radius` or `reversibility` outside the
  defined enums
- **THEN** the gate returns a structured validation error naming the field and
  allowed values, and does not create a pending action

#### Scenario: Legacy string evidence is migrated, not coerced at runtime

- **WHEN** the approvals-chain migration encounters an existing pending action
  whose `evidence` array contains a plain string
- **THEN** the row is rewritten to a typed evidence entry with `type = "text"`
  before the structured dossier contract is enforced
- **AND** after the migration, any new input containing plain-string evidence
  is rejected with a structured validation error and does not create or update
  a pending action

### Requirement: Approval-Request Push on Park

When the gate parks a pending action, the module SHALL emit exactly one
deterministic, templated owner notification through the standard delivery plane
(`notify.v1` envelope with `intent = "approval_request"`), rendering the
decision dossier and the action's expiry. The push MUST be deterministic daemon
infrastructure — no LLM session composes it. Push delivery MUST respect the
approvals quiet-hours policy by deferring (not dropping) the push, MUST NOT
re-push for the same action on retries or edits, and MUST collapse bursts: when
more than 3 actions park within a 10-minute window, subsequent pushes in the
window are replaced by a single digest message with a dashboard deep link.

#### Scenario: Park emits one approval-request push

- **WHEN** a gated call parks as a pending action outside quiet hours
- **THEN** exactly one `approval_request` notification targeting the owner is
  submitted for delivery, containing the tool name, `why`, blast radius,
  reversibility, and expiry
- **AND** subsequent lifecycle events for that action (retry, edit, decision)
  emit no further approval-request push

#### Scenario: Quiet hours defer the push without affecting expiry

- **WHEN** a gated call parks during the configured quiet-hours window
- **THEN** the push is deferred and delivered after the window ends
- **AND** the pending action's `expires_at` is unchanged by the deferral

#### Scenario: Burst collapses into a digest

- **WHEN** a fourth action parks within a 10-minute window
- **THEN** the fourth and subsequent pushes in the window are replaced by a
  single digest notification stating the number of actions awaiting review with
  a dashboard deep link

### Requirement: Decision Memory Writeback

The module SHALL upsert a deterministic, templated decision-tally fact into
the owning butler's own memory store on every terminal decision outcome — a
rejection, or an approval once its execution outcome is recorded:
predicate `decision:approval_tally`, keyed per (pattern fingerprint, resolved
target entity), with metadata containing approve/reject counts, the last
outcome, the last `action_id`, and the fingerprint version. The fact MUST be
entity-linked when the action's channel identity resolves to a contact entity.
On standing-rule creation and revocation the module SHALL write a
`decision:standing_rule` fact describing the granted or revoked autonomy scope
with the rule id. Writeback MUST be deterministic (no LLM), MUST write only to
the owning butler's own schema, and MUST fail open (a memory write failure
never blocks or reverses the decision itself).

#### Scenario: Approved and executed action updates the tally fact

- **WHEN** a pending action targeting a resolvable contact is approved and its
  execution outcome is recorded
- **THEN** a `decision:approval_tally` fact for that (fingerprint, entity) pair
  exists in the owning butler's memory store with `approve_count` incremented,
  the last outcome, and the action id in its metadata

#### Scenario: Rejection updates the same tally

- **WHEN** a pending action with the same fingerprint and target is rejected
- **THEN** the same tally fact's `reject_count` increments and its last outcome
  reflects the rejection

#### Scenario: Rule creation writes an autonomy-grant fact

- **WHEN** a standing approval rule is created (directly or by confirming a
  promotion suggestion)
- **THEN** a `decision:standing_rule` fact is written describing the rule's
  tool, pinned constraints, and rule id
- **AND** revoking the rule writes the revocation to the same fact's state

#### Scenario: Memory write failure does not block the decision

- **WHEN** the memory store is unavailable during a terminal decision
- **THEN** the decision transition, audit event, and execution proceed normally
- **AND** the writeback failure is logged
