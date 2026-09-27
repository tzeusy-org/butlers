## MODIFIED Requirements

### Requirement: Quiet Hours Suppression
The system SHALL use the global Owner Attention Policy in
`public.approvals_policy` to configure quiet hours during which routine insights
are not delivered. The policy is evaluated in its IANA timezone as the
end-exclusive interval `[quiet_start_hour, quiet_end_hour)`. Accumulated
candidates are NOT burst-delivered after quiet hours end.

`public.insight_settings` SHALL retain only insight verbosity and budget
controls; it SHALL NOT be a second quiet-hours authority. Missing, incomplete,
invalid, or unreadable policy data SHALL fail open for the regular delivery
cycle after logging the diagnostic condition.

#### Scenario: Canonical quiet-hours configuration
- **WHEN** the owner configures quiet hours through the Owner Attention Policy
- **THEN** the setting is stored in `public.approvals_policy` as
  `quiet_start_hour` (INTEGER, hour 0-23), `quiet_end_hour` (INTEGER, hour
  0-23), and `timezone` (TEXT, IANA timezone)
- **AND** no broker-private quiet-hours setting is read at runtime

#### Scenario: Delivery suppression during quiet hours
- **WHEN** the regular delivery cycle runs and the current time falls within
  the Owner Attention Policy interval
- **THEN** the delivery cycle SHALL skip routine delivery entirely
- **AND** pending candidates SHALL remain for the next non-quiet delivery cycle

#### Scenario: Exact quiet end resumes routine delivery
- **WHEN** the regular delivery cycle runs at exactly `quiet_end_hour` in the
  policy timezone
- **THEN** the policy does not suppress routine candidates on that boundary

#### Scenario: No burst after quiet hours
- **WHEN** the delivery cycle runs after quiet hours have ended
- **AND** candidates accumulated during quiet hours
- **THEN** the daily budget SHALL still apply — at most B insights are delivered
- **AND** candidates that exceed the budget remain pending for the next day
  (they do not get a "bonus" delivery slot)

#### Scenario: No usable policy configured
- **WHEN** `public.approvals_policy` has no complete usable quiet window
- **THEN** delivery SHALL proceed at the scheduled delivery cycle time without
  time-based suppression

### Requirement: Priority-Urgent Bypass of Quiet Hours and the Context Bus
The delivery cycle SHALL allow a candidate whose `priority` is at or above
`URGENT_PRIORITY_THRESHOLD` (90 — RFC 0011's "time-critical" floor) to bypass
both the global Owner Attention Policy and a context-bus `dnd`/`sleeping`
signal. When at least one such candidate is pending during what would otherwise
be a fully-suppressed cycle, the delivery cycle proceeds for urgent candidates
only; candidates below the threshold remain `status='pending'`, untouched, for
a later non-suppressed cycle.

#### Scenario: Urgent candidate delivered during quiet hours, routine candidate untouched
- **WHEN** the delivery cycle runs during active Owner Attention Policy quiet
  hours
- **AND** one pending candidate has `priority=95` and another has `priority=70`
- **THEN** the `priority=95` candidate is delivered (or included in a digest)
- **AND** the `priority=70` candidate's status remains `'pending'` — it is
  neither delivered nor marked `filtered`/`expired` by this cycle

#### Scenario: Fully suppressed cycle when no candidate is urgent
- **WHEN** the delivery cycle runs during active Owner Attention Policy quiet
  hours (or an active context-bus `dnd`/`sleeping` signal)
- **AND** every pending candidate has `priority < 90`
- **THEN** the cycle returns `skipped=True` and delivers nothing
- **AND** one `public.attention_ledger` row is written with `outcome="suppressed"`
  and the triggering `reason`

#### Scenario: Expiry runs regardless of suppression
- **WHEN** the delivery cycle would otherwise be fully suppressed (Owner
  Attention Policy or context bus, no urgent candidate)
- **THEN** the expiry step (marking `expires_at`-past candidates as `expired`)
  still runs unconditionally before the suppression check

## REMOVED Requirements

### Requirement: Seeded Owner-Level Quiet Hours
**Reason**: Owner quiet-window authority is consolidated into
`public.approvals_policy`; `public.insight_settings` no longer stores quiet
fields.

**Migration**: A guarded core migration preserves a complete legacy insight
window only when the canonical Owner Attention Policy is incomplete, then drops
the legacy quiet columns. Existing canonical policy wins conflicts.
