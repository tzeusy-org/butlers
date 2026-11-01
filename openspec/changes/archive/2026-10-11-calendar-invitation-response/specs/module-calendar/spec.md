# Calendar invitation response execution

## ADDED Requirements

### Requirement: Approval-Bound Self Invitation Response

The Calendar module SHALL expose occurrence-only calendar_respond through its actual server approval gate and shared owning executor. It MUST prepare one immutable source/account/self/occurrence/version command before approval, claim at most one write attempt durably, preserve all other event fields, and expose honest noop, rejection, uncertainty, confirmed receipt and guarded self-only inverse outcomes. Missing capability or target proof MUST refuse before effect; no caller assertion, generic retry, provider-read inference or new request key may bypass a started unresolved command.
For this dedicated response write only, this requirement SHALL narrow the existing Google OAuth and Rate Limiting rate-limit retry scenario: a started participant-response request MUST NOT be retried, including after 401, 429 or 503. Readiness and token refresh precede its write-start claim; all existing operations retain their current OAuth, retry and error-redaction behavior.

ID: REQ-module-calendar-029
Source: bu-q7vx1q.18 original AC1-4; bu-q7vx1q.3 consequential-write posture; heart-and-soul/security.md Approval Gates; RFC0023 authority separation; design.md
Scope: v1-mandatory

#### Scenario: Exact self participant response preserves the event

- **WHEN** an approved response targets a verified configured self attendee on an eligible exact occurrence
- **THEN** only that attendee response is changed by one conditional participant-only write
- **AND** organizer, every other attendee, title/time and recurrence remain unchanged
- **AND** send_updates is the exact approved choice, default none

#### Scenario: Ambiguous or ineligible response targets cause no write

- **WHEN** self/account/source/ownership or occurrence is missing, ambiguous, cancelled, read-only, foreign or a series root
- **THEN** the command is refused before any provider write
- **AND** legacy normalized response status does not supply explicit provider truth
- **AND** existing provider/tool read defaults remain unchanged

#### Scenario: Same response is a no-op

- **WHEN** the exact current self response already equals the approved choice
- **THEN** execution returns noop with zero writes
- **AND** no applied inverse is created

#### Scenario: Unapproved autonomous response is parked

- **WHEN** an autonomous session invokes the registered response without the exact approved executor context
- **THEN** the real gate prepares and parks one canonical command without provider write
- **AND** caller actor, recipient, approval-id or bypass flags cannot authorize it
- **AND** an outbound-owner exemption or standing rule cannot supply the missing exact decision

#### Scenario: Unavailable capability fails closed

- **WHEN** the owning daemon lacks the registered canonical preparer, enabled approval gate, executor or verified eligible source
- **THEN** response is unavailable and performs no provider effect
- **AND** another butler capability cannot be substituted

#### Scenario: Approved replay is bound to one task and command

- **WHEN** dispatch uses the owning action and exact persisted tool args
- **THEN** only the executor task with the matching immutable command binding can execute
- **AND** wrong owner/tool/args and inherited child context refuse

#### Scenario: Concurrent requests share one atomic command

- **WHEN** concurrent calls use the same request key and full command digest
- **THEN** one owning command and one pending action are admitted
- **AND** identical replays return that outcome
- **AND** changed digest is refused without replacing its target or approval

#### Scenario: Post-start uncertainty never resends

- **WHEN** timeout, cancellation, restart or disconnect follows a committed write-start marker
- **THEN** the attempt remains uncertain with no applied success receipt or inverse
- **AND** same or replacement request cannot dispatch a second write for that unresolved target
- **AND** current-status readback alone cannot attribute the prior effect or clear the fence

#### Scenario: Provider rejection preserves honest failure

- **WHEN** a provider explicitly rejects the one conditional request including an ETag conflict
- **THEN** failure audit is retained without an applied receipt or usable inverse
- **AND** no write retry occurs
- **AND** projection/UI do not fabricate a responded status

#### Scenario: Confirmed provider effect survives projection failure

- **WHEN** the original provider response verifies the exact target effect but projection persistence fails
- **THEN** the applied provider receipt reports projection unavailable
- **AND** the action is not labelled rolled back and is not resent

#### Scenario: Restart and retention preserve non-reacceptance

- **WHEN** an unresolved started response outlives a request task or ordinary receipt-detail retention
- **THEN** durable owning evidence or a safe compacted fence prevents a duplicate attempt
- **AND** no elapsed deadline, guessed update sequence or detached periodic resend supplies authority

#### Scenario: Guarded inverse restores only the previous self response

- **WHEN** the applied receipt has retained prior self status and exact unchanged post-status/version
- **THEN** a newly approved single-use inverse restores only that self status including server-recorded needsAction
- **AND** all other event fields are preserved
- **AND** unknown, failed, stale, repeated or missing pre-state requests cause zero write
