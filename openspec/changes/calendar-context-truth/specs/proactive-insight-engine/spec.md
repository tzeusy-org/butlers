## MODIFIED Requirements

### Requirement: Context-Bus Gating of the Delivery Cycle
The delivery cycle SHALL consult the situational context bus
(`public.user_context`) for an active `dnd`, `meeting`, `sleeping`, or
`traveling` signal, deterministically, as an additional suppression input
alongside the global Owner Attention Policy. When more than one such signal
is active, precedence is `dnd`, then `meeting`, then `sleeping`, then
`traveling` — the first of these, in that order, with an active
non-max-held instance wins and is reported as the suppression signal.
- Each signal type has its own max-hold TTL bounding how long that signal
alone may suppress routine delivery, independent of the signal's own (often
much longer) context-bus expiry: `dnd` 4 hours, `meeting` 2 hours, `sleeping`
10 hours, `traveling` 6 hours. A signal whose `set_at` is older than its
max-hold TTL, relative to the delivery cycle's `now`, no longer suppresses
delivery even while it otherwise remains active on the context bus — this
exists because `traveling` may legitimately stay active for up to 30 days
(per the context-bus module's own TTL clamp), and routine insights must not
silently queue for the length of a trip.
- Typed OOO SHALL not be represented as a calendar-derived meeting hold. Away, focused and working_location SHALL not be added to the broker suppression set by this change. An independently active DND, sleeping, traveling, real meeting or Owner Attention Policy hold still governs normally.

ID: REQ-proactive-insight-engine-001
Source: RFC 0009; RFC 0020; vision.md shared situational awareness; approved bu-s11n0s.4 Outcome
Scope: v1-mandatory

#### Scenario: dnd signal suppresses when no quiet hours are configured
- **WHEN** `public.approvals_policy.quiet_start_hour`/`quiet_end_hour` are NULL
  (quiet hours not configured or not active)
- **AND** `public.user_context` has an active `dnd` signal within its max-hold
  TTL
- **AND** no pending candidate is priority>=90
- **THEN** the cycle is suppressed exactly as if quiet hours were active, with
  `reason="context_bus:dnd"`

#### Scenario: meeting or traveling signal suppresses like dnd/sleeping
- **WHEN** `public.user_context` has an active `meeting` or `traveling`
  signal within its max-hold TTL, and no higher-precedence signal is active
- **AND** no pending candidate is priority>=90
- **THEN** the cycle is suppressed with `reason="context_bus:meeting"` (or
  `"context_bus:traveling"`), exactly as `dnd`/`sleeping` suppress today

#### Scenario: A signal beyond its max-hold TTL no longer suppresses
- **WHEN** `public.user_context` has an active `traveling` signal whose
  `set_at` is more than 6 hours before the delivery cycle's `now`
- **AND** no other suppressing signal is active
- **THEN** the cycle is NOT suppressed by that signal — the context-bus
  consult returns no suppression from it, even though the signal itself
  remains active (not yet expired) on the context bus

#### Scenario: A lower-precedence active signal still suppresses when a higher one has expired its hold
- **WHEN** `public.user_context` has an active `dnd` signal beyond its 4-hour
  max-hold TTL, and an active `meeting` signal within its 2-hour max-hold TTL
- **THEN** the cycle is suppressed with `reason="context_bus:meeting"` — the
  suppression check does not stop at the first (expired-hold) signal in
  precedence order, it falls through to the next eligible one


#### Scenario: OOO-only calendar does not hold under meeting
- **WHEN** the calendar producer has published only OOO away and cleared its meeting/focused assertions and no other suppressor applies
- **THEN** the broker has no `context_bus:meeting` hold
- **AND** a planted real meeting positive still produces the existing meeting hold


#### Scenario: Independent suppressor remains authoritative during OOO
- **WHEN** OOO away coexists with an independently active authorized DND or sleeping signal
- **THEN** the established suppressor and precedence still apply
- **AND** away is not treated as a bypass authority
