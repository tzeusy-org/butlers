## ADDED Requirements

### Requirement: Parked notification review doors preserve action and source identity
The notification read API and feed SHALL expose a parked approval door only from the dedicated server-written correlation for that same routed notification and a complete authorized unique same-source/action read. The bounded projection SHALL expose `approval_review` as the canonical action UUID plus owning Messenger source or null, with `approval_review_state` equal to `available`, `none` or `unavailable`. The feed SHALL use the adopted `/approvals/{id}` path with fixed `review_source=messenger`; the mounted dossier's authenticated GET SHALL enforce that expected source and repeat unique action resolution without first-match fallback. Missing legacy correlation is no recorded reference, not proof that approval was unnecessary; malformed, wrong-source, ambiguous or unavailable evidence MUST NOT fabricate a door or calm state. Existing blocked/failure status, authorization/redaction, session/trace doors, statistics, pagination, loading and actions SHALL remain intact; navigation adds no prepare, approve, execute, send, retry, escalation or provider authority.

ID: REQ-dashboard-visibility-004
Source: Closed owner release bu-3b6goa; run13 dossier ranked move5 S4; parked-notification-approval-review-door/design.md
Scope: v1-mandatory

#### Scenario: A real parked notification opens its own dossier
- **WHEN** the authenticated notification read verifies a dedicated reference against the uniquely resolved committed action in its recorded Messenger source
- **THEN** it projects `approval_review_state=available` and that bounded same-action reference
- **AND** the failed row offers a keyboard-operable Review approval link to `/approvals/{id}?review_source=messenger` with visible focus
- **AND** failed/effective status, error, session and trace links remain truthful and unchanged

#### Scenario: The mounted destination cannot substitute a different source
- **WHEN** the review link opens the adopted dossier, including after source state changes since the feed read
- **THEN** the mounted dossier GET enforces the recorded expected Messenger source and complete unique action resolution
- **AND** absent, duplicated, wrong-source or unavailable resolution refuses that qualified dossier without falling back to the first action found in another pool
- **AND** ordinary unqualified dossier doors and all existing server-derived decision permissions retain their contracts

#### Scenario: Invalid or partial evidence withholds only the review door
- **WHEN** a stored reference is malformed, its action is absent or belongs to another source, an action ID is duplicated across pools, or a required read/enumeration is unavailable
- **THEN** the item projects a null reference with `approval_review_state=unavailable` and no interactive review door
- **AND** the row names review-reference unavailability without claiming no approval, successful delivery or complete source health
- **AND** reachable notification rows and their existing session/trace doors remain usable

#### Scenario: Legacy and ordinary failure have no inferred approval link
- **WHEN** a legacy row has no dedicated correlation or an ordinary failure has only apparent action IDs in metadata or error text
- **THEN** the projection supplies a null review reference with `approval_review_state=none` for no recorded correlation
- **AND** the feed adds no inferred door and retains the real failed status, safe existing error and ordinary triage behavior

#### Scenario: Permission and privacy survive review navigation
- **WHEN** the owner opens a valid notification review door or an unauthenticated caller requests its dossier
- **THEN** the existing dashboard authentication, dossier authorization/redaction and explicit decision boundaries govern the read
- **AND** correlation discloses no additional message, recipient, tool arguments, credentials, actor assertion or arbitrary URL
- **AND** following the door performs no notification or approval mutation

#### Scenario: List and action projections remain compatible
- **WHEN** global or butler-scoped notification lists, mark-read, loading, degraded reads, filtering, statistics, pagination or existing retry/escalate/acknowledge controls execute
- **THEN** their existing contracts and fields remain intact beside the additive bounded correlation projection
- **AND** verification is finite within the existing page bound and cannot turn loading, source failure or incomplete evidence into an empty or all-clear state
