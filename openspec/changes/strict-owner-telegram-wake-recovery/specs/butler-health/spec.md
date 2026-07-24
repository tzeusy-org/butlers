## ADDED Requirements

### Requirement: Health-Owned Policy-Sleep Wake Commit
Health SHALL participate in a valid wake-recovery run only by validating and,
at fenced commit, superseding its own active deterministic `sleeping` context
whose durable metadata identifies the Owner Attention Policy producer and the
same canonical policy window. Health SHALL validate the authenticated
Switchboard caller, accepted-event/run identity, participant digest, and fence
for prepare, commit, and abort replay safety.

Health SHALL NOT clear explicit DND, a non-policy sleep context, another
butler's context, or any generic user context because of a direct Telegram DM.
If explicit DND is active when Health evaluates the request, Health SHALL
retain its policy-sleep context and report a DND block without authorizing
egress. Health SHALL consume the landed
`canonical-dnd-generation-guard` (`bu-12iab`) snapshot/admission boundary for
any durable wake decision: it has no DND mutation authority and does not treat
an observation as final egress authorization. A changed, active, missing, or
unprovable guard result fails closed and is carried through the parent
`blocked_dnd` / retained path.

Only after every participant has supplied a compatible durable prepare result
for the complete current-fence cohort may Health supply its local
`ordinary_precommit_cancel` decision to authenticated Switchboard. It SHALL
not call Messenger, move an origin row, or create a scheduler return. A partial
prepared cohort remains protocol-bound. The landed
`durable-precommit-cancellation-admission` (`bu-qs702`) contract owns the final
Messenger admission and all-cohort publication; this packet consumes that
contract without restating its fields or transitions.

#### Scenario: Matching policy sleep is superseded only at commit
- **WHEN** Health receives a valid fenced prepare followed by a valid commit
  for its active Owner Attention Policy sleep record in the same window
- **THEN** prepare leaves the sleep context intact
- **AND** commit supersedes only that matching Health-owned policy-sleep record

#### Scenario: Non-policy context remains untouched
- **WHEN** Health receives any wake-recovery request while DND is active or
  its `sleeping` context was created by a source other than the deterministic
  Owner Attention Policy producer
- **THEN** Health does not clear or supersede that context
- **AND** it reports the appropriate block or mismatch to Switchboard
