# Generalist lead, focused delegation and capability authorization

**Status:** Owner-adopted direction (2026-10-14); successor contract proposed in
RFC 0038 with owner decisions 1, 2, 3, 6 and 7 answered; full design signoff
pending.
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
  admitted goal. Switchboard, moved into its own process, hosts admission, the
  goal ledger, the goal runner and the capability broker. Message
  classification and route dispatch are removed. Engineering executors and
  tool-less model functions are the only named model use outside goals.
- Domain butlers stop spawning sessions. Scheduled prompts become obligation
  admissions with capability sets declared in git.
- Sessions run sandboxed on a separate session host, reach only the broker,
  and hold grants derived from the goal's origin and cause. Exposure to
  third-party content, including through tainted stored records, gates
  consequential effects. Domains verify a single-use, audience-bound,
  broker-signed assertion before any model-reachable handler runs.
- Budgets, effect ledger, approvals, Stop and fenced recovery are goal-level.
- Hard cutover: dark build on main, cutover gate in dev, one switching release,
  rollback by redeploying the previous release.

## Capabilities

### New Capabilities

- `goal-orchestration`: one accountable lead per goal, depth-1 focused
  children, finite budgets, honest outcomes, durable domain care, and goals as
  the only path to a reasoning session.
- `capability-authorization`: sandboxed sessions, catalog metadata, origin
  ceilings, grant checks, delegation attenuation, exposure and taint, domain-side
  assertion verification, revocable tokens, effect ledger, exact-approval
  execution, Stop fencing, goal audit, bounded caused admissions and
  unreachable owner decisions.

### Modified Capabilities

Deltas to `butler-base-spec`, `core-spawner`, `core-daemon`, `core-sessions`,
`core-modules`, `butler-switchboard`, `cross-butler-delegation`,
`staffer-archetype`, `core-scheduler`, `core-credentials`, `core-notify` and the
approvals capability are authored after RFC 0038 signoff (tasks 1.4) and applied
by the cutover release. RFC 0038 gives every RFC from 0001 to 0037 a
disposition.

## Impact

Doctrine (`about/heart-and-soul/`), RFCs 0001, 0002, 0003, 0008 and 0027
(supersession headers), v1 scope and success criteria, Switchboard's manifesto
at cutover, every domain butler's session spawning, every LLM-presentable
tool's metadata, deployment (session sandbox pod class or network), and the
second process-bound deployment control key. No runtime code changes in this
change's documentation stage.
