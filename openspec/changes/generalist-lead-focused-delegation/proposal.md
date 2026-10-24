# Generalist lead, focused delegation and capability authorization

**Status:** Owner-adopted direction and owner-signed successor contract
(RFC 0038, accepted 2026-10-14 with amendments recorded as owner decisions 8
to 11).
**Tracking:** `bu-rn8frx` (doctrine); `bu-7p9ll5` (successor contract).

## Proposed shared first-switch contract (2026-10-24)

The original adoption above remains historical. bu-dhloi1.19 now supplies
concrete review text for the recorded two-cutover programme: independent shared
label/catalog, waiting/epoch/migration, perception/event/CAS, minimal evaluator,
read-pure context, owner-fast, delivery/token, health and gateway interfaces.
[RFC0038 S0–S8](../../../about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md#proposed-shared-switch-and-m7-interfaces-2026-10-24)
is the field/state/API authority; [shared-contract-coverage.md](shared-contract-coverage.md)
is the requirement/source-owner, approval and M6 handoff map.

This is spec-only, with reserved exact-text adoption and independent normative
review pending. All existing task states, D6 conservative handling, ASR,
private-custody/approval/selection boundaries and .17 context remain intact.
.18 owns later policy wording; .9/Dots own representation/reduction separately.
Held typed-value and cited-label decisions remain held. No runtime, archive,
switch, M7 activation, research, credential use or operational change is claimed.

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
  classification and route dispatch are removed. Engineering executors,
  tool-less model functions and dashboard fast answers are the only named model
  use outside goals.
- Domain butlers stop spawning sessions. Scheduled prompts become obligation
  admissions with capability sets declared in git.
- Sessions run sandboxed on a separate session host, reach only the broker,
  and hold grants derived from the goal's origin and cause. Exposure to
  third-party content, including through tainted stored records, gates
  consequential effects. Domains verify a single-use, audience-bound,
  broker-signed assertion before any model-reachable handler runs.
- Budgets, effect ledger, approvals, Stop and fenced recovery are goal-level.
- Two owner rules bind every goal: communication with anyone other than the
  owner needs owner approval (per event or by an owner-created standing rule),
  and `transact` capabilities are disabled.
- The dashboard presents one mind with specialized regions: goals replace
  sessions as the unit of activity, and domain pages become regions. The
  detailed experience contract is a separate change signed off before it is
  built.
- After the switch, the RFC corpus is rewritten around the goal architecture.
- Hard cutover: dark build on main, cutover gate in dev, one switching release,
  rollback by redeploying the previous release.

## Capabilities

### New Capabilities

- `goal-orchestration`: one accountable lead per goal, depth-1 focused
  children, finite budgets, honest outcomes, durable domain care, and goals as
  the only path to a reasoning session.
- `capability-authorization`: sandboxed sessions, catalog metadata (including
  party arguments), origin ceilings, grant checks, delegation attenuation,
  exposure and taint, domain-side assertion verification, revocable tokens,
  effect ledger, exact-approval execution, Stop fencing, goal audit, bounded
  caused admissions, unreachable owner decisions, `transact` disabled, and
  owner approval for communication with anyone other than the owner.

### Modified Capabilities

MODIFIED, REMOVED and, where a successor contract is needed, ADDED deltas were
authored in this change after RFC 0038 signoff (tasks 1.4) and are applied by
the cutover release:

- Core runtime: `butler-base-spec`, `core-spawner`, `core-daemon`,
  `core-sessions`, `core-modules`, `core-scheduler`, `core-credentials`,
  `core-notify`, `core-skills`, `core-telemetry`, `core-fleet-events`,
  `module-approvals`, `staffer-archetype`, `testing`.
- Control plane and ingress: `butler-switchboard`, `switchboard-identity`,
  `switchboard-rule-promotion`, `dispatch-intent`, `ingestion-policy`,
  `ingestion-event-registry`, `ingress-injection`, `error-recovery-corrections`,
  `scorecard-reporting`, `domain-event-bus`, `fleet-case-file`,
  `connector-base-spec` and the `connector-activitywatch`, `connector-gmail`,
  `connector-home-assistant`, `connector-live-listener`, `connector-steam` and
  `connector-telegram-user-client` connectors.
- Removed entirely: `cross-butler-delegation`, `conversation-decomposition`,
  `routing-scorecard`; `complexity-classification` and `module-pipeline` lose
  their routing requirements.
- Models and runtimes: `model-catalog`, `model-failover`,
  `model-benchmark-harness`, `catalog-token-limits`, `runtime-opencode`,
  `runtime-config-api`.
- Staffers and engineering executors: `staffer-qa`, `qa-investigation-dispatch`,
  `healing-session-tracking`.
- Domains: `butler-chronicler`, `chronicler-api`, `butler-concierge`,
  `butler-education` and the `module-education-curriculum`,
  `module-education-mastery`, `module-education-spaced-repetition` and
  `module-education-teaching-flows` modules, `butler-finance`,
  `csv-extraction-skill`, `butler-general`, `butler-health`, `butler-home`,
  `module-home-assistant`, `butler-lifestyle`, `butler-messenger`,
  `butler-relationship`, `relationship-facts`, `butler-secrets`,
  `butler-travel`, `module-calendar`, `module-telegram`, `module-memory`,
  `memory-discovery-catalog`, `memory-retention-policy`, `event-chains`,
  `seasonal-awareness`, `deadline-tracking`, `commitment-lifecycle`,
  `autonomy-tracker`, `autonomy-suggestions`,
  `cross-butler-briefing-contribution`, `docs-information-architecture`.

The `dashboard-*` capabilities are changed by the separate D19 dashboard
change. RFC 0038 gives every RFC from 0001 to 0037 a disposition.

## Impact

Doctrine (`about/heart-and-soul/`, with a D19 pointer in
`design-language.md`), RFCs 0001, 0002, 0003, 0007, 0008, 0027 and 0037
(supersession headers), v1 scope and success criteria, the dashboard (rebuilt
around goals and regions by the D19 change), Switchboard's manifesto at
cutover, every domain butler's session spawning, every LLM-presentable tool's
metadata, deployment (session host, `butlers-control` and the second
process-bound deployment control key), and calendar writes that invite
non-owner attendees, which become approval-gated. No runtime code changes in
this change's documentation stage.
