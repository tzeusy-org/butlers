# Receiver-derived routing: staged cutover and rollback

This is the L3 operator boundary for `REQ-butler-control-plane-liveness-002/004/008`
and `REQ-butler-switchboard-002/004`. The code is staged with
`BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER` **unset by default**. This document
does not authorize a deployment, restart, live route probe, credential use, or
cutover. Those effects require a separate exact-environment operator approval.

## What is staged

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

| Surface | Ownership during L3 staging |
| --- | --- |
| `register_butler`, legacy eligibility sweep/reconciliation, confirmed-route `last_seen_at` touch, old Dashboard heartbeat POST | Still write the legacy projection; L1's trigger preserves restrictive policy. None grants receiver-derived health. L4 retires the heartbeat writer later. |
| `public.register_butler_boot` and `butler_boot_registrations` | Daemon role owns boot succession; immutable UUID-to-epoch receipt survives rollback. |
| Dashboard periodic observer | Shadow-only receiver evidence through L2's role-bound reserve/record operations. |
| Switchboard on-demand route probe | The same L2 reserve/record operations, but only behind the default-off L3 route branch and only after non-health gates pass. |
| Authenticated owner eligibility API | Sole administrative policy mutation; neither probe nor confirmed route may clear its hold. |
| `resolve_routing_target`, `list_butlers`, Dashboard status, QA heartbeat view | Continue reading the legacy projection until their own allocated cutovers. L3 does not silently rewrite these readers. |
| L3 pure resolver and route preflight | Exact-name Switchboard reads of `sw_035` facts and Git-roster endpoint; no database write. |

## Separate live gate

Before any owner-authorized activation, verify protected landing of L3 at an
exact reviewed head, migrated `sw_035`, current daemon boot registrations, and
L2 shadow observations across multiple TTL windows. Reconcile every legacy
versus shadow mismatch and prove a route under a real auth-enabled runtime role
with malformed/old-epoch/timeout and owner-policy negative cases. Confirm the
fixed backend preflight is reachable only on the trusted internal network and
that Q4 consumes its bounded cached result, not a per-public-request probe.
Do not activate merely because this PR or its CI is green. PR #3960's held
conversation-identity path and D1 target-intent work remain separately owned.

For rollback, disable the L3 flag in the exact process deployment only under
the same operator authority. Retain `sw_035` policy/provenance, its restrictive
legacy trigger, boot-registration ledger, and latest epoch. Recheck that the
legacy projection still denies every paused, quarantined, or review-required
target before serving traffic; do not treat code downgrade, successful route,
or a stale last-seen value as an owner release. Do not drop the new schema,
restore an older process UUID, or re-enable the daemon heartbeat mutation as a
shortcut. A rollback whose old projection cannot prove this restriction must
keep routing unavailable and escalate rather than widen eligibility.
