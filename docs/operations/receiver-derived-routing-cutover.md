# Receiver-derived routing: dev cutover and rollback

This is the L3 operator boundary for `REQ-butler-control-plane-liveness-002/004/008`
and `REQ-butler-switchboard-002/004`. The code defaults to legacy routing when
`BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER` is unset. Dev hotreload Compose sets
it to `1` for both daemon and Dashboard API; their base services, also used in
production, default to `0`.
Production activation remains a separate exact-environment decision.

## Dev cutover behavior

- The default route still uses legacy `resolve_routing_target` and its
  `eligibility_state` projection. A test explicitly proves the separated-facts
  resolver is not called with the flag absent.
- With the flag set to `1` in a separately authorized process, Switchboard
  reads policy, observation, compatibility, and current boot epoch separately.
  A policy hold or incompatible/not-accepting target is refused before a
  target call. An otherwise eligible stale target gets one Switchboard-owned,
  exact-Git-roster identity probe through L2 reserve/record CAS; only a fresh
  current-epoch reread permits one target call. `not_attempted` is retained
  for pre-target refusal. Caller `allow_stale`/`allow_quarantined` overrides do
  not grant authority in this path.
- Candidate classification uses separated policy and compatibility reads and
  keeps otherwise eligible stale targets available for the bounded route probe.
  Local schedules use administrative policy, while the old TTL sweep stops
  mutating legacy state. This leaves stale legacy rows fail-closed on rollback.
  The fleet board and heartbeat API show receiver-verified health.
- A new daemon boot invalidates its prior receiver observation. The Dashboard
  observer probes promptly at startup and retries an unverified startup fleet
  at a bounded cadence before returning to the normal TTL/2 interval. The
  board's STALE chip reports that observation state; only a projected
  policy-held row offers the administrative Restore action. The board
  reconciles receiver liveness every 30 seconds even if the session event
  socket is healthy, because receiver probes do not emit that event; both
  loss and recovery of health must reach an open page promptly.
- The internal Switchboard backend endpoint
  `GET /internal/control-plane/route-preflight` is independent of that flag.
  It selects the lexically first configured non-paused domain target, uses the
  same read-only selection logic and one bounded identity GET, and returns a
  content-blind Boolean/category. Every request rechecks policy and epoch
  before reusing a positive cache; concurrent reads coalesce, rapid identity
  GETs are rate-limited, and stale cache fails closed. It makes no MCP target
  call and no probe reservation, registry, routing, inbox, session, ingestion,
  notification, or condition write. It is not exposed on the Dashboard and is
  not a substitute for Q4's cached public `/ready`.

## Writer and reader inventory

| Surface | Ownership during dev cutover |
| --- | --- |
| `register_butler`, legacy read reconciliation, confirmed-route `last_seen_at` touch, old Dashboard heartbeat POST | May still write the legacy projection. None grants receiver-derived health or clears policy. The old TTL sweep is inert while the flag is `1`; the heartbeat POST remains owner-authenticated and rejected for daemon reporters. |
| `public.register_butler_boot` and `butler_boot_registrations` | Daemon role owns boot succession; immutable UUID-to-epoch receipt survives rollback. |
| Dashboard periodic observer | Receiver evidence through L2's role-bound reserve/record operations. |
| Switchboard on-demand route probe | The same L2 reserve/record operations, behind the dev hotreload cutover flag and only after non-health gates pass. |
| Authenticated owner eligibility API | Sole administrative policy mutation; neither probe nor confirmed route may clear its hold. |
| Direct route, classifier and correction candidates, local scheduler, recovery notification admission | Read separated control-plane facts when the flag is `1`; a policy hold still denies the route. Legacy resolver remains for flag-off rollback. |
| Fleet board and system heartbeat API | Follow the same cutover flag as the daemon. With `1`, project the receiver's last verified healthy observation and administrative policy; with `0`, retain the legacy projection. The board does not turn a failed probe into a fresh heartbeat. |
| Generic `list_butlers` | Still reads legacy state and may show stale; it is not route authority under the flag. |
| QA infra-state heartbeat discovery | With the flag set, reads receiver observations through the QA-only `public.v_qa_butler_receiver_state` view from `sw_037`. Its legacy view remains for flag-off deployments. QA cannot read the control-plane table directly. |
| L3 pure resolver and route preflight | Exact-name Switchboard reads of control-plane facts and Git-roster endpoint; no database write. |

## Activation and rollback

Dev activation requires `sw_035`, the `sw_036` evidence repair, the QA receiver
view in `sw_037`, current boot registrations, fresh receiver observations,
and active policy for the exact intended targets. Verify the internal route preflight, a real
authenticated route, and policy-denied negative case; then observe health and
route acceptance beyond two legacy TTL windows. The dev hotreload Compose
default supplies the flag, but production's base service remains default-off.
Production needs its own exact-environment verification and activation.

For rollback, set the flag to `0` in the exact process deployment. Retain
`sw_035`/`sw_036` policy and provenance, the `sw_037` QA read view, the restrictive
legacy trigger, boot-registration ledger, and latest epoch. Recheck that the
legacy projection still denies every paused, quarantined, or review-required
target before serving traffic; do not treat code downgrade, successful route,
or a stale last-seen value as an owner release. Do not drop the new schema,
restore an older process UUID, or re-enable the daemon heartbeat mutation as a
shortcut. A rollback whose old projection cannot prove this restriction must
keep routing unavailable and escalate rather than widen eligibility.
