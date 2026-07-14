# switchboard-rule-promotion Specification

## Purpose
TBD - created by archiving change switchboard-rule-promotion. Update Purpose after archive.
## Requirements
### Requirement: Routing Verdict Log

The system SHALL record every triage-layer decision — however it was reached —
in a `switchboard.routing_verdict_log` table, so that repeated agreement can be
mined without excavating per-butler `sessions.tool_calls` JSONB.

The table MUST have columns: `id` (UUID PK), `ingestion_event_id` (UUID, FK to
`public.ingestion_events`), `sender_key` (TEXT, normalized lowercase sender
address), `source_channel` (TEXT), `verdict_source` (TEXT, one of `llm`,
`rule`, `pinned`, `spot_check`), `verdict_action` (TEXT, one of `route_to`,
`skip`, `metadata_only`, `pass_through`, `block`), `verdict_target` (TEXT,
nullable, butler name when `verdict_action = 'route_to'`), `matched_rule_id`
(UUID, nullable, FK to `switchboard.ingestion_rules`), `session_id` (UUID,
nullable, FK to `switchboard.sessions`), `decided_at` (TIMESTAMPTZ).

A row MUST be written at each of: the pipeline's existing rule-bypass sites
(`route_to`/`skip`/`metadata_only`, `verdict_source='rule'`), the LLM verdict
resolution site after `route_to_butler` tool calls are parsed
(`verdict_source='llm'`), the dashboard pinned-target bypass
(`verdict_source='pinned'`), and demotion spot-checks (`verdict_source
='spot_check'`, see Requirement: Demotion via Spot-Check Sampling).

#### Scenario: LLM verdict recorded

- **WHEN** the LLM classification session for an inbound email resolves a
  `route_to_butler` call targeting `finance`
- **THEN** a `routing_verdict_log` row MUST be written with
  `verdict_source='llm'`, `verdict_action='route_to'`,
  `verdict_target='finance'`, and `session_id` set to the classification
  session's id

#### Scenario: Rule-bypass verdict recorded

- **WHEN** an inbound event matches an existing `ingestion_rules` row with
  `action='skip'` and the pipeline takes the bypass (no LLM session spawned)
- **THEN** a `routing_verdict_log` row MUST still be written, with
  `verdict_source='rule'`, `verdict_action='skip'`, `matched_rule_id` set to
  the matched rule, and `session_id` NULL

#### Scenario: Pinned-target verdict excluded from mining

- **WHEN** an event is routed via an explicit dashboard `control.pinned_target`
  override
- **THEN** a `routing_verdict_log` row MUST be written with
  `verdict_source='pinned'`
- **AND** rows with `verdict_source='pinned'` MUST be excluded from the
  promotion-trigger scan (Requirement: Promotion Trigger)

### Requirement: Promotion Trigger

The system SHALL periodically scan `routing_verdict_log` grouped by
`(sender_key, source_channel)` and propose a new ingestion rule when evidence
of consistent LLM agreement crosses a configurable threshold.

A sender/channel pair becomes promotion-eligible when: no `enabled` (and not
soft-deleted) `ingestion_rules` row already covers it, no `pending_review`
suggestion already exists for it (existing suggestions instead have their
`evidence_count` incremented and `last_evidence_at` updated), the most recent N
`routing_verdict_log` rows with `verdict_source='llm'` (N configurable, default
3) all agree on the same `(verdict_action, verdict_target)` pair, and those N
rows' `decided_at` values span at least 2 distinct calendar days.

The 2-distinct-days requirement is a mandatory evidence-quality gate, not an
optional tuning parameter — it exists specifically to reject a burst of
near-simultaneous, superficially-repeated verdicts (e.g. several near-identical
automated notifications arriving within minutes of each other) as insufficient
evidence for a standing rule.

"Distinct calendar days" MUST be computed against a pinned timezone anchor
(UTC, matching `decided_at`'s `TIMESTAMPTZ` storage) and MUST NOT be
implementable as a bare date-difference check with no minimum-elapsed-time
floor — a naive `decided_at::date` comparison lets a burst straddling a
day boundary (e.g. two verdicts 2 minutes apart at 23:59 and 00:01) satisfy
the "2 distinct days" count on the exact single-burst evidence shape this
gate exists to reject. The implementation MUST additionally enforce a
minimum elapsed-time floor between `first_evidence_at` and
`last_evidence_at` (see design.md D5) so a midnight-adjacent burst cannot
qualify.

#### Scenario: Promotion-eligible pattern creates a suggestion

- **WHEN** a sender has 3 `routing_verdict_log` rows with
  `verdict_source='llm'`, all `verdict_action='route_to'` /
  `verdict_target='finance'`, with `decided_at` timestamps on 3 different
  calendar days
- **THEN** a `switchboard.rule_promotion_suggestions` row MUST be created with
  `status='pending_review'`, `proposed_rule_type='sender_address'`,
  `proposed_action='route_to:finance'`, and `evidence_count=3`

#### Scenario: Single-burst evidence does not trigger promotion

- **WHEN** a sender has 3 `routing_verdict_log` rows with matching
  `verdict_source='llm'` verdicts, all with `decided_at` within the same
  10-minute window on a single calendar day
- **THEN** no suggestion MUST be created, regardless of the count meeting the
  numeric threshold

#### Scenario: Midnight-boundary burst does not trigger promotion

- **WHEN** a sender has 3 `routing_verdict_log` rows with matching
  `verdict_source='llm'` verdicts, all within a 10-minute span that happens to
  straddle a UTC calendar-day boundary (e.g. 23:59 and 00:01 the next day)
- **THEN** no suggestion MUST be created — a naive count of distinct
  `decided_at::date` values crossing midnight MUST NOT be treated as
  satisfying the evidence-quality gate; the minimum-elapsed-time floor
  applies regardless of how many calendar dates the timestamps nominally fall
  on

#### Scenario: Existing rule suppresses re-proposal

- **WHEN** a sender/channel pair already has an `enabled` `ingestion_rules` row
  covering it
- **THEN** the promotion trigger MUST NOT create a new suggestion for that
  sender/channel, even if fresh LLM verdicts continue to accumulate

#### Scenario: Repeated evidence bumps an existing pending suggestion

- **WHEN** a sender/channel pair already has a `pending_review` suggestion and
  a new matching LLM verdict is recorded
- **THEN** the existing suggestion's `evidence_count` MUST be incremented and
  `last_evidence_at` updated, rather than a duplicate suggestion being created

### Requirement: Rule Promotion Suggestion Data Model

The `switchboard.rule_promotion_suggestions` table MUST exist with columns:
`id` (UUID PK), `suggestion_kind` (TEXT, one of `promotion`, `demotion` —
discriminates the kind of suggestion independent of its lifecycle `status`),
`sender_key` (TEXT, nullable), `source_channel` (TEXT, nullable),
`proposed_rule_type` (TEXT, nullable, `sender_address` or `sender_domain`),
`proposed_condition` (JSONB, nullable), `proposed_action` (TEXT, nullable),
`evidence_count` (INTEGER), `first_evidence_at` / `last_evidence_at`
(TIMESTAMPTZ), `is_clearly_automated` (BOOLEAN, default FALSE), `status`
(TEXT, one of `pending_review`, `confirmed`, `dismissed`, `superseded` — a
pure suggestion lifecycle, identical in shape for both `suggestion_kind`
values; see "Scenario: `superseded` has no defined trigger yet" below),
`target_rule_id` (UUID, nullable, FK to `ingestion_rules`; the existing rule
a `demotion` suggestion proposes to revoke), `created_rule_id` (UUID,
nullable, FK to `ingestion_rules`; the *new* rule minted when a `promotion`
suggestion is confirmed — deliberately distinct from `target_rule_id`),
`dismissal_reason` (TEXT, nullable), `cooldown_until` (TIMESTAMPTZ,
nullable), `created_at` / `decided_at` (TIMESTAMPTZ), `decided_by` (TEXT,
nullable).

A CHECK constraint MUST tie `suggestion_kind` to column population: a
`promotion` row MUST have non-empty `sender_key`, `source_channel`,
`proposed_rule_type`, `proposed_condition`, and `proposed_action`, and
`target_rule_id` MUST be NULL; a `demotion` row MUST have `target_rule_id`
set and `proposed_rule_type`, `proposed_condition`, and `proposed_action`
MUST all be NULL. Empty-string values for `sender_key`, `source_channel`,
and `proposed_action` MUST be rejected by the same constraint as NULL — an
empty string is just as vacuous as no value for these required
identity/action fields.

A unique partial index MUST exist on `(sender_key, source_channel) WHERE
status = 'pending_review' AND suggestion_kind = 'promotion'` so at most one
pending promotion suggestion can exist per sender/channel at a time. A
separate unique partial index MUST exist on `target_rule_id WHERE status =
'pending_review' AND suggestion_kind = 'demotion'` so at most one pending
demotion suggestion can exist per rule at a time.

#### Scenario: Table enforces one pending promotion suggestion per sender/channel

- **WHEN** the promotion trigger attempts to create a second `pending_review`,
  `suggestion_kind='promotion'` suggestion for a `(sender_key,
  source_channel)` pair that already has one
- **THEN** the unique partial index MUST prevent the duplicate insert, and the
  trigger's upsert path MUST update the existing row instead

#### Scenario: Table enforces one pending demotion suggestion per rule

- **WHEN** an attempt is made to create a second `pending_review`,
  `suggestion_kind='demotion'` suggestion for a `target_rule_id` that already
  has one
- **THEN** the unique partial index MUST prevent the duplicate insert

#### Scenario: Kind-shape CHECK rejects a malformed row

- **WHEN** an insert attempts `suggestion_kind='promotion'` with
  `target_rule_id` set, or with `sender_key`, `source_channel`, or
  `proposed_action` NULL or an empty string
- **THEN** the CHECK constraint MUST reject the insert

#### Scenario: `superseded` has no defined trigger yet

- **WHEN** any currently-specified flow (promotion trigger, confirm, dismiss,
  or demotion spot-check) runs to completion
- **THEN** no suggestion's `status` MUST be set to `superseded` —
  `superseded` is reserved, CHECK-accepted vocabulary with no trigger
  condition defined by this spec; defining that trigger is tracked
  separately (bu-2djc4) and MUST NOT be inferred or implemented ahead of
  that decision

### Requirement: Clearly-Automated Sender Classification

The system SHALL classify a promotion suggestion's `is_clearly_automated` flag
using the same bulk-mail signal vocabulary already seeded as `ingestion_rules`
in the initial routing migration: presence of a `List-Unsubscribe` header,
`Precedence: bulk` or `Precedence: list`, `Auto-Submitted: auto-generated`, or
a sender local-part matching `noreply`/`no-reply`/`notifications`/`alerts`
(case-insensitive prefix) on the evidence events backing the suggestion.

#### Scenario: Automated sender flagged

- **WHEN** a suggestion's evidence events all carry a `List-Unsubscribe` header
- **THEN** `is_clearly_automated` MUST be `TRUE` on the created suggestion

#### Scenario: Non-automated sender not flagged

- **WHEN** a suggestion's evidence events carry none of the bulk-mail signals
  and the sender's local part does not match a known automated prefix
- **THEN** `is_clearly_automated` MUST be `FALSE`

### Requirement: Promotion Application (Owner-Confirmed with Automated-Tier Auto-Apply)

The system SHALL apply a clearly-automated suppression suggestion automatically:
a `rule_promotion_suggestions` row with `is_clearly_automated = TRUE` and
`proposed_action` in (`skip`, `metadata_only`) MUST have its `ingestion_rules`
row minted without an explicit confirm. This is the owner disposition (gate
bu-4pq0s) — that tier only ever suppresses or downgrades an already-automated
sender (low blast radius) and never routes owner-facing traffic. RFC 0021's
"no unattended auto-write" ratchet is scoped to `autonomy_suggestions`
(butler-autonomy tool-calls), not ingestion routing rules; this requirement
supersedes the original bead-0 sketch that gated the automated tier behind a
batched confirm.

Every other suggestion SHALL require an explicit owner (or authenticated human
actor) confirm before its `ingestion_rules` row is created — every
`route_to:<butler>` (higher blast radius: a wrong route sends real traffic to
the wrong butler) and any non-automated `skip`/`metadata_only`. The system MUST
NOT transition such a suggestion from `pending_review` to `confirmed` without an
explicit confirm call.

Applying a suggestion (auto or owner-confirmed) MUST be atomic and idempotent:
minting the rule and transitioning the suggestion to `confirmed` happen in one
transaction under a row lock, so a double-apply (concurrent auto-apply + confirm
click) mints exactly one rule and the second attempt fails on the already-decided
status rather than double-writing.

#### Scenario: Confirming a suggestion creates the rule

- **WHEN** an authenticated human actor calls confirm on a `pending_review`
  suggestion
- **THEN** a new `ingestion_rules` row MUST be created with `created_by
  ='promotion'`, `promoted_from_suggestion_id` set to the suggestion's id, and
  `condition`/`action` copied from `proposed_condition`/`proposed_action`
- **AND** the suggestion MUST transition to `status='confirmed'` with
  `decided_at` and `decided_by` set

#### Scenario: Automated skip/metadata_only auto-applies

- **WHEN** a suggestion sits in `pending_review` with `is_clearly_automated
  =TRUE` and `proposed_action` in (`skip`, `metadata_only`)
- **THEN** the auto-apply pass MUST mint its `ingestion_rules` row
  (`created_by='promotion'`, `promoted_from_suggestion_id` set) and transition
  it to `status='confirmed'` with a distinct auto-apply `decided_by` marker,
  without any confirm call
- **AND** the resulting rule MUST be reversibly disable-able (the approvals
  surface offers an enable/disable of the minted rule), so the auto-apply is an
  informational, reversible action rather than an irreversible one

#### Scenario: route_to is never auto-applied

- **WHEN** a suggestion has `proposed_action` starting `route_to:` (even with
  `is_clearly_automated=TRUE`)
- **THEN** the auto-apply pass MUST NOT mint its rule; it remains
  `pending_review` until an explicit owner confirm, and confirming an unroutable
  `route_to` target (not a registered butler) MUST fail without minting a rule

### Requirement: Rule Provenance

`switchboard.ingestion_rules` SHALL gain a nullable `promoted_from_suggestion_id
UUID` column, a foreign key to `switchboard.rule_promotion_suggestions(id)`.
The existing `created_by` column (already unconstrained TEXT) gains a
conventional value `'promotion'` for rules minted through this flow.

#### Scenario: Promoted rule carries provenance

- **WHEN** a rule is created via suggestion confirmation
- **THEN** the created `ingestion_rules` row MUST have `created_by='promotion'`
  and `promoted_from_suggestion_id` set to the originating suggestion's id

#### Scenario: Manually-created rules are unaffected

- **WHEN** a rule is created via the existing dashboard CRUD API (not through
  suggestion confirmation)
- **THEN** `promoted_from_suggestion_id` MUST be NULL and `created_by` MUST
  retain its existing `'dashboard'` value — this requirement does not change
  behavior for human-authored rules

### Requirement: Demotion via Spot-Check Sampling

The evaluator SHALL sample a configurable fraction (1-in-K matches, default
K=20) of events matching an `ingestion_rules` row with
`created_by='promotion'` that would otherwise bypass the LLM, routing them
through normal LLM classification instead and comparing the fresh LLM verdict
to the rule's action. `IngestionPolicyEvaluator`'s `PolicyDecision` gains a
`spot_check` boolean field, set when a promoted rule matched but the sampled
event is being routed through the LLM instead of bypassed.

The system SHALL maintain a rolling per-rule agreement score over the most
recent 20 spot-checks. When the agreement score for a rule drops below a
configurable threshold (default 90%), the system MUST create a
`rule_promotion_suggestions` row with `suggestion_kind='demotion'` and
`target_rule_id` set to the rule under scrutiny, proposing revocation of
that rule, surfaced through the same dashboard suggestions surface as
promotion suggestions. Demotion MUST require the same
owner-confirmed action as promotion — the system MUST NOT auto-disable a
promoted rule based on spot-check disagreement alone.

#### Scenario: Spot-check samples a promoted rule's match

- **WHEN** an event matches an `ingestion_rules` row with
  `created_by='promotion'` and the 1-in-K sample is hit
- **THEN** the event MUST be routed through normal LLM classification instead
  of the rule bypass
- **AND** a `routing_verdict_log` row MUST be written with
  `verdict_source='spot_check'` and `matched_rule_id` set to the rule that
  would have fired

#### Scenario: Sustained disagreement creates a demotion suggestion

- **WHEN** a promoted rule's rolling spot-check agreement score drops below
  90% over its last 20 spot-checks
- **THEN** a demotion suggestion MUST be created for that rule, requiring
  owner confirmation before the rule is disabled

#### Scenario: Rule is never auto-disabled

- **WHEN** a promoted rule's agreement score is below threshold and no owner
  confirmation of the demotion suggestion has occurred
- **THEN** the rule MUST remain `enabled` and continue to be evaluated
  normally

