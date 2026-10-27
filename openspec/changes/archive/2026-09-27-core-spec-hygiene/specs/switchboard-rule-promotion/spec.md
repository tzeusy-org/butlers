## MODIFIED Requirements

### Requirement: Promotion Application (Owner-Confirmed with Automated-Tier Auto-Apply)

The system SHALL apply a clearly-automated suppression suggestion automatically:
a `rule_promotion_suggestions` row with `is_clearly_automated = TRUE` and
`proposed_action` in (`skip`, `metadata_only`) MUST have its `ingestion_rules`
row minted without an explicit confirm. This is the owner disposition: that tier only ever suppresses or downgrades an already-automated
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
transaction under a sender/channel identity lock followed by a row lock, so a
double-apply (concurrent auto-apply + confirm click) mints exactly one rule and
the second attempt fails on the already-decided status rather than double-writing.

#### Scenario: Confirming a suggestion creates the rule

- **WHEN** an authenticated human actor calls confirm on a `pending_review`
  suggestion
- **THEN** a new `ingestion_rules` row MUST be created with `created_by
  ='promotion'`, `promoted_from_suggestion_id` set to the suggestion's id, and
  `condition`/`action` copied from `proposed_condition`/`proposed_action`
- **AND** the suggestion MUST transition to `status='confirmed'` with
  `decided_at` and `decided_by` set

#### Scenario: Confirmation racing a trigger does not recreate a card

- **WHEN** a confirmation and a promotion-trigger scan overlap for the same
  sender/channel identity
- **THEN** they MUST serialize on that identity, and the trigger MUST reload
  enabled rules from its locked connection before proposing or bumping
- **AND** once confirmation creates a covering rule, the trigger MUST not
  create another `pending_review` suggestion for that identity

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
