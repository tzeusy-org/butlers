# Generalist lead, focused delegation and capability authorization

**Status:** Owner-adopted direction (2026-10-14); successor contract proposed in
RFC 0038, awaiting owner design and release signoff.
**Tracking:** `bu-rn8frx` (doctrine); `bu-7p9ll5` (successor contract).

## Why

Domain ownership is valuable, but mandatory specialist reasoning per domain
conflates durable responsibility with model context. A cross-domain goal is
split by a classifier, handled by several sessions and never reconciled. The
owner adopted a generalist lead per goal, searchable capabilities and focused
children at depth one, delivered as one hard cutover rather than a long
migration with two runtimes. Security can no longer come from "a session sees
only its own butler", so a deterministic authorization broker is part of the
same change. The [decision record](../../../about/legends-and-lore/decisions/2026-10-14-doctrine-focused-delegation.md)
records evidence and tradeoffs; [RFC 0038](../../../about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md)
is the design.

## What Changes

- Every reasoning session becomes a lead or depth-1 child invocation of an
  admitted goal. Switchboard hosts admission, the goal ledger, the goal runner
  and the capability broker. Message classification and route dispatch are
  removed.
- Domain butlers stop spawning sessions. Scheduled prompts become obligation
  admissions with capability sets declared in git.
- Sessions run sandboxed, reach only the broker, and hold grants derived from
  the goal's origin. Exposure to third-party content gates consequential
  effects. Domains verify a broker-signed assertion before any model-reachable
  handler runs.
- Budgets, effect ledger, approvals, Stop and fenced recovery are goal-level.
- Hard cutover: dark build on main, cutover gate in dev, one switching release,
  rollback by redeploying the previous release.

## Capabilities

### New Capabilities

- `goal-orchestration`: one accountable lead per goal, depth-1 focused
  children, finite budgets, honest outcomes, durable domain care, and goals as
  the only path to a reasoning session.
- `capability-authorization`: sandboxed sessions, catalog metadata, origin
  ceilings, grant checks, delegation attenuation, exposure gating, domain-side
  assertion verification, effect ledger, exact-approval execution, Stop fencing
  and goal audit.

### Modified Capabilities

Deltas to `butler-base-spec`, `core-spawner`, `core-daemon`, `core-sessions`,
`core-modules`, `butler-switchboard`, `cross-butler-delegation`,
`staffer-archetype`, `core-scheduler` and `core-credentials` are authored with
the implementation plan (tasks 1.3) and applied by the cutover release. RFC
0038 lists the superseded contracts.

## Impact

Doctrine (`about/heart-and-soul/`), RFCs 0001, 0002, 0003, 0008 and 0027
(supersession headers), v1 scope and success criteria, Switchboard's manifesto
at cutover, every domain butler's session spawning, every LLM-presentable
tool's metadata, deployment (session sandbox pod class or network), and the
second process-bound deployment control key. No runtime code changes in this
change's documentation stage.
