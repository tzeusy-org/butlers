Reviewed commit: `bb86cef2070756a343d76086e7499d8e6207b94a`

# Focused Project Review: Liveness Control Plane, QA Independence, and Delivery Recovery

**Date:** 2026-09-23
**Project type:** single-owner backend/full-stack agent system
**Maturity:** production-intent, live personal data
**Review mode:** `$th-projects` focused project review plus direction handoff
**Mutation boundary:** research only; no runtime, configuration, replay, Beads, or external-monitor mutation

## 1. Normative baseline

The project-shape scan reports `SHAPED`: all five pillars exist and their local doctrine
navigators are valid. Capability specs are mixed because 62 active changes remain, and the mature
traceability gate fails (103 of 385 active specs carry source references).

Source-of-truth order used:

1. `about/heart-and-soul/` and `about/legends-and-lore/`
2. `openspec/` baseline plus active changes
3. `about/lay-and-land/`
4. implementation, tests, live content-blind evidence, and Beads

Binding requirements:

- Reliability, continuous operation, and correct automatic routing are product requirements, not
  enhancements (`about/heart-and-soul/vision.md:51-53,120-130`).
- Deterministic lifecycle, scheduling, routing, and recovery belong in infrastructure, not LLM
  judgment (`about/heart-and-soul/vision.md:80-84`).
- Cross-butler delivery remains Switchboard-mediated (`about/heart-and-soul/vision.md:71-78`).
- QA is a permanently running system SRE with a ten-minute patrol contract
  (`openspec/specs/staffer-qa/spec.md:143-190`).
- Failed or partial observation must never impersonate recovery
  (`openspec/changes/define-infrastructure-reliability-lifecycle/specs/infrastructure-reliability/spec.md:45-70`).
- The existing process-bound signing key is the sole approved key exception; another service key
  would require explicit doctrine and capability-spec adoption
  (`about/heart-and-soul/security.md:161-195`).

No vision amendment is required. The present implementation violates the vision already adopted.

## 2. Executive summary

**Scoped verdict: At risk.** The repository has strong local building blocks—structured logs,
traces, condition episodes, supervised dashboard loops, route transport outcomes, and layered
tests—but the control-plane composition defeats them. Owner authentication rejects the daemon's
unauthenticated heartbeat mutation, all butlers become stale, the scheduler then disables QA and
the eligibility sweep, and process-only health remains green.

The most secure and least doctrinally invasive correction is to retire caller-asserted heartbeat
mutation and make liveness receiver-derived. A separately supervised Dashboard/control-plane
controller should probe exact Git-roster endpoints, verify bounded daemon identity/readiness, and
write DB-server observations. A stale route gets one bounded on-demand probe before refusal.
Operator policy, observed health, and route compatibility become separate state dimensions; no
health observation or restart may clear quarantine.

That repairs future routing but not lost work. The current failed-event “replay” changes
`failed -> ingested` without re-routing the message. Durable self-correction therefore also needs
a Switchboard-owned per-target delivery-intent outbox and atomic target acceptance key. Only
proven pre-acceptance failures may retry automatically; ambiguous attempts remain visible and
never duplicate effects.

## 3. Scoped scorecard

| Area | Score | Confidence |
|---|---:|---|
| Correctness and reliability | 2/5 | High |
| Error handling and failure behavior | 2/5 | High |
| Observability and debuggability | 3/5 | High |
| Testing strategy | 3/5 | High |
| Tooling and engineering hygiene | 3/5 | High |
| Security posture | 2/5 | High |
| Data/API design | 2/5 | High |
| Release and operations | 2/5 | High |

The average is 2.4/5. The three critical composition failures override the otherwise mature local
tooling.

## 4. Confirmed findings and risk register

### P0. Owner auth and daemon liveness are incompatible

**[Observed][Confirmed] Critical; likelihood high; impact high; effort M.**

The owner middleware exempts only exact `GET /health` and `GET /api/health`, then requires owner
authority for other `/api/*` requests (`src/butlers/api/owner_auth/http.py:163-175,226-251`). The
liveness reporter posts JSON only (`src/butlers/background.py:420-458`). The dated content-blind
runtime receipt in Appendix C confirms 13/13 registry rows stale and repeated heartbeat 401s.

Supporting evidence: mounted middleware, client code, registry transitions, and Appendix C's
read-only live logs/DB receipt agree.
Contradictory evidence checked: connector callbacks have exact separate authority, but no such
heartbeat authority exists; owner auth itself is correctly fail-closed.

**Direction:** remove `POST /api/switchboard/heartbeat` as a liveness authority. Do not make it
public, distribute the owner key, reuse the approval token, or repurpose the runtime-probe key.

### P0. QA and the eligibility sweep are circularly self-gated

**[Observed][Confirmed] Critical; likelihood high; impact high; effort M-L.**

QA's `infra_state` source can detect stale butlers, but only inside a scheduler-driven patrol
(`openspec/specs/staffer-qa/spec.md:109-178`). Scheduler dispatch reuses inbound routing
eligibility and suppresses all cron/deadline work for a stale local butler
(`src/butlers/core/scheduler.py:1978-2027,2169-2193`). The five-minute liveness TTL expires before
QA's next ten-minute patrol.

Supporting evidence: Appendix C records the latest QA patrol at 23:42:05 SGT and its stale
transition at 23:47:06 SGT; scheduler code gates subsequent due work.
Contradictory evidence checked: registry lookup exceptions fail open and dashboard lifespan loops
are supervised, but a successful lookup returning `stale` gates closed and neither QA patrol nor
eligibility sweep is a dashboard loop.

**Direction:** derived remote staleness must not suppress local deterministic schedules. Only
explicit administrative pause/quarantine may gate them. Fleet-liveness and QA-patrol freshness
checks must also run in a separately supervised controller.

### P0. Process liveness is presented as deployment readiness

**[Observed][Confirmed] Critical; likelihood high; impact high; effort M.**

Compose checks only `/health` (`docker-compose.yml:280-288`), whose implementation reports
lifespan/process state rather than routability (`src/butlers/api/app.py:767-826`). The launcher
waits only for Docker `healthy` (`scripts/compose.sh:707-763,829-830`). It declared health while
heartbeats returned 401, every butler was stale, routing failed, and schedulers were suppressed.

Supporting evidence: Appendix C records HTTP 200/`status=ok`, 13/13 stale registry rows, and 102
stale-target routing failures in the same read-only observation.
Contradictory evidence checked: the health endpoint truthfully returns 503 during startup and the
launcher has strong proxy-peer validation; neither claims or tests functional fleet readiness.

**Direction:** preserve lightweight `/health`; add distinct content-blind semantic readiness and a
deployment canary covering controller freshness, expected fleet identity/freshness, QA patrol age,
DB/control dependencies, and one effect-free synthetic route.

### P1. Failed-event replay can erase a failure without replaying it

**[Observed][Confirmed] High; likelihood high; impact high; effort S honesty / L recovery.**

The failed-event path advertises replay, but `ingestion_event_replay_request()` performs only
`failed -> ingested`, leaves terminal `message_inbox` unchanged, and performs no route
(`src/butlers/core/ingestion_events.py:1107-1182`). This can turn a real delivery failure green
without an effect.

Supporting evidence: function documentation and SQL explicitly describe the status-only path.
Contradictory evidence checked: connector ingress replay has strong fail-closed policy and locking,
but that is a different operation and does not recover target delivery.

**Direction:** first remove/rename the dishonest status-only action. A failed row stays failed
unless durable work was actually queued; write-off/acknowledgement must use its own name and audit.

### P2. Non-dashboard ingestion-to-domain `route.execute` retry lacks atomic target acceptance identity

**[Observed][Overstated -> Confirmed scoped] Medium; likelihood medium; impact high; effort L.**

For ordinary non-dashboard ingestion-to-domain `route.execute`, deduplication occurs only after a
successful session exists, while `route_inbox_insert` mints a new row UUID for every acceptance
(`src/butlers/core_tools/_routing.py:990-1030`;
`src/butlers/core/route_inbox.py:56-79`). Processing leases fence workers for one row, not two
rows representing one delivery.

Supporting evidence: target-unavailable returns before transport; its legacy top-level field says
`retryable=false` while the canonical transport envelope correctly says
`outcome=not_attempted,retryable=true`. Ordinary target acceptance has no unique delivery key.
Contradictory evidence checked: dashboard routes already claim/enqueue atomically
(`src/butlers/core_tools/_routing.py:880-930`), Messenger uses a separate synchronous path
(`src/butlers/core_tools/_routing.py:866-869`), and domain events already use unique
`(event_id, subscriber_butler)` identity with retry in place (`src/butlers/core/domain_events.py`).
Domain events invoke `receive_domain_event`, not this `route.execute` lane. Route transport
certainty plus processing leases are also valuable. The finding is scoped to the remaining
non-dashboard ingestion-to-domain path.

**Direction:** unique delivery identity `(ingestion_event_id, target_butler, segment_id)` with an
acceptance upsert/receipt, plus a Switchboard delivery-intent state machine.

### P1. Owner-set and liveness-derived quarantine share one state and either can be cleared

**[Observed][Confirmed] High; likelihood medium; impact high; effort S-M.**

The heartbeat endpoint can change `quarantined -> active` and clear metadata
(`roster/switchboard/api/router.py:886-915`); startup registration also clears quarantine on
conflict (`roster/switchboard/tools/registry/registry.py:336-380`). Confirmed route bookkeeping,
by contrast, preserves quarantine (`roster/switchboard/tools/routing/route.py:668-730`).

Supporting evidence: heartbeat, startup registration, and operator routes all operate on the same
stored quarantine fields.
Contradictory evidence checked: the eligibility sweep also writes `quarantined` automatically
after 2x TTL (`roster/switchboard/tools/registry/sweep.py:85-132,202-205`), while the operator API
uses that same state (`roster/switchboard/api/router.py:977-1055`). Existing quarantines therefore
cannot all be presumed owner policy.

**Direction:** represent observed health, administrative policy, and compatibility separately.
Migrate existing state by provenance: automatic TTL quarantines become observation state;
operator-set quarantines become policy; ambiguous historic provenance fails closed into an
operator-review state. Only after that migration may policy quarantine become sticky. No health
observation or restart can revoke owner policy.

### P1. No repository/Beads-governed functional readiness alert is confirmed

**[Observed][Confirmed] High; likelihood high; impact high; effort M plus owner operation.**

`EXTERNAL_DEADMAN_URL` is empty in the live dev configuration, and the durable
`ExternalDeadmanUnconfigured` condition has aged to L3. Existing Bead `bu-g4jg1` is blocked on
owner-authorized configuration of a separate-host Uptime Kuma pull monitor, but its adopted
contract intentionally checks only minimal `/api/health`, not functional readiness. Whether an
untracked outside monitor exists is unknown.

Supporting evidence: runtime config presence check, condition ledger, code, and live Beads.
Contradictory evidence checked: operator-facing registry/QA/condition surfaces exist and improve
diagnosis, but they do not interrupt the owner independently when the in-host control plane fails.

The code also creates durable `ExternalDeadmanUnconfigured` evidence
(`src/butlers/core/qa/sources/infra_state.py:298-301`), while the still-active
`infrastructure-reliability` requirement says not to create a synthetic condition solely for
missing configuration
(`openspec/changes/define-infrastructure-reliability-lifecycle/specs/infrastructure-reliability/spec.md:162-170`).

**Direction:** reconcile that spec/code drift. Preserve the already-adopted minimal host/process
monitor contract, and separately decide whether to add a functional readiness monitor/canary.
This is a renewed owner gate because the recorded monitor decision explicitly excluded aggregate
health and Push monitoring.

## 5. Recommended target architecture

### 5.1 Receiver-derived fleet liveness

Use a separately supervised controller in Dashboard/control-plane infrastructure:

1. Enumerate exact expected daemons from Git roster/config, never a caller-supplied URL or name.
2. Probe each internal daemon endpoint on a bounded cadence.
3. Extend internal daemon health with bounded identity/readiness: exact name, boot instance ID,
   route contract range, and whether `route.execute` accepts work.
4. Compare observation to the expected identity and write only DB-server timestamps.
5. Store current boot generation so an old process cannot refresh a successor.
6. Track separately:
   - observation: `healthy | stale | unavailable | observer_unknown`
   - administrative policy: `active | paused | quarantined`
   - compatibility: contract/capability match
7. Compute routability as their conjunction. Observation never clears policy.
8. Keep confirmed route success as secondary positive evidence.
9. Before rejecting a stale target, perform one bounded on-demand probe. A successful probe
   restores routing without restart; a failed probe returns a typed transient no-attempt result.
10. Retire the incoming dashboard heartbeat mutation and old reporter after cutover.

This requires no new process-bound secret under the trusted-host deployment premise. If future
deployments put butlers on mutually untrusted hosts, use mTLS/per-instance signatures only after a
security-doctrine amendment.

### 5.2 QA and watchdog independence

- Derived staleness no longer gates local schedules; administrative policy still may.
- Move fleet reconciliation and QA patrol-age observation into the supervised controller.
- Open one fleet-level condition for correlated expiry and attach per-butler impact evidence;
  never launch thirteen investigations for one control-plane seam.
- Add deterministic conditions/metrics for observer failure, stale-fleet ratio, QA patrol age,
  routing target-unavailable rate, and supervisor termination.
- QA discovery continues even when investigation model/GitHub authority is unavailable; dispatch
  may suppress while evidence remains durable.
- Reconcile `roster/qa/MANIFESTO.md` with the five-source inventory and remove the fictional legacy
  direct-self-healing fallback deprecated by RFC 0015.

### 5.3 Durable per-target delivery recovery

Add a Switchboard-owned delivery-intent outbox after ingestion classification for the
non-dashboard ingestion-to-domain `route.execute` lane:

| State | Meaning |
|---|---|
| `pending` | durable target intent, not attempted |
| `attempting` | fenced worker owns the attempt |
| `accepted` | target returned the stable acceptance receipt |
| `retry_wait` | proven pre-accept transient failure |
| `ambiguous` | target may have accepted; no automatic duplicate |
| `terminal_failed` | policy/contract/quarantine/expiry failure |

Rules:

- Commit classification/decomposition and every per-target intent atomically before the first
  target call, or run a deterministic reconciler that reconstructs a missing intent before any
  dispatch can proceed. The current call-before-persistence ordering in
  `src/butlers/modules/pipeline.py:3503-3558` must not survive the cutover.
- Preserve the committed classification/decomposition and retry only delivery.
- Target acceptance is an atomic upsert on the stable delivery identity.
- Retry only proven pre-accept/no-effect outcomes with bounded backoff and jitter.
- `accepted` is terminal for the Switchboard intent. Once accepted, target `route_inbox` owns
  crash recovery and downstream session outcome; Switchboard does not resend or add a parallel
  `completed` state without a separately specified authenticated completion receipt.
- Ambiguous transport resolves by the same acceptance key/receipt; it never creates a second row.
- Partial fanout retries only failed target intents.
- Recovery begins automatically when liveness returns.
- Connector ingress replay, especially email, remains a separate fail-closed contract.

### 5.4 Health, deploy, and external assurance

- `/health`: process liveness only.
- Internal semantic readiness: DB, observer freshness, all expected daemon identities/freshness,
  route compatibility, QA patrol age, supervised-loop state, and one effect-free route canary.
- Canonical launcher: fail deployment completion if semantic readiness is false; retain a bounded
  evidence receipt and rollback classification.
- Soak acceptance beyond at least one full TTL and multiple probe/patrol cycles.
- External assurance: preserve the separate-host minimal pull monitor, then obtain owner signoff
  for a second content-blind functional check or synthetic route canary.

## 6. Sequenced program

### Gate 0 — feature/spec amendment, no code

1. **[Confirmed]** Create and adopt one control-plane reliability change covering
   receiver-derived liveness, policy separation, anti-circular scheduling, readiness, and QA
   correlation.
2. **[Confirmed]** Create and adopt a separate delivery-recovery change for atomic target
   acceptance/outbox semantics in non-dashboard ingestion-to-domain `route.execute`.
3. **[Confirmed]** Reconcile every named RFC, baseline spec, active change, topology document,
   and existing ownership collision below before allocation.

Do not allocate Beads before signoff.

Required amendments:

- RFC 0001: supervised/receiver-derived liveness and local schedule authority.
- RFC 0003: liveness controller interaction, atomic target acceptance, delivery retry/ambiguity.
- RFC 0007/0008: liveness versus readiness and internal control-plane topology.
- RFC 0015: anti-circular QA independence and fleet-level correlation.
- Baseline specs: `core-daemon`, `butler-base-spec`, `butler-switchboard`, `staffer-qa`,
  `qa-dashboard`, `ingestion-event-registry`, and `dashboard-ingestion-dispatch-console`.
- Active changes: reconcile rather than silently widen
  `specify-host-authorized-dashboard-enrollment` and
  `define-infrastructure-reliability-lifecycle` (the latter explicitly excludes generic heartbeat,
  scheduler-default, and external-provisioning work).
- Reconcile `k3s-deployment-helm-chart`, which already defines public `/ready` and lightweight
  `/health`; do not create a competing readiness vocabulary.
- Reuse the shipped `confirmed | rejected | uncertain | not_attempted` route transport taxonomy
  (`roster/switchboard/tools/routing/transport.py`) and the runtime-attention outbox's certainty
  rules; do not invent parallel certainty labels. Treat approval-delivery intent storage as a
  design precedent only, not a reusable approval-specific table.
- Topology/runbooks: replace the documented heartbeat POST and record the new observer boundary.

### Phase 1 — make truth representable

1. **[Confirmed]** Add boot-generation and separated observation/policy/compatibility state.
2. **[Confirmed]** Add stable ingestion-to-domain target-delivery identity and target acceptance
   receipt schema, with atomic
   classification/decomposition/intent persistence before dispatch.
3. **[Confirmed]** Migrate legacy quarantine by provenance: TTL-derived to observation, operator-set to policy,
   ambiguous to fail-closed review; only then make policy quarantine sticky across
   probe/registration/restart.
4. Stop the status-only failed-event “replay” from erasing failure.

### Phase 2 — propagate without authority cutover

1. **[Confirmed]** Add the supervised controller in shadow/read-only mode.
2. **[Confirmed]** Add bounded daemon identity/readiness responses.
3. **[Confirmed]** Add controller/route/QA/readiness metrics and durable condition evidence.
4. Add production-shaped cross-layer and real-Postgres tests.

### Phase 3 — enforce and self-correct

1. **[Confirmed]** Make receiver-derived observations authoritative.
2. **[Confirmed]** Enable stale-path on-demand probes.
3. **[Confirmed]** Decouple local schedules from derived staleness.
4. Enable fleet condition correlation and QA patrol deadman.
5. Add semantic deploy/readiness gate.
6. Retire the old POST heartbeat and reporter.

### Phase 4 — delivery recovery

1. **[Confirmed]** Enable atomic ingestion classification/decomposition/intent creation and target acceptance.
2. **[Confirmed]** Enable delivery-intent workers using the existing transport-certainty taxonomy and bounded
   pre-accept retry.
3. **[Confirmed]** Add same-key ambiguity resolution and partial-fanout behavior, with `accepted` terminal for
   Switchboard ownership.
4. Expose honest waiting/ambiguous/terminal states in ingestion APIs/UI.

### Phase 5 — operational assurance and incident recovery

1. **[Confirmed]** Run a content-blind historical dry run grouped by time, channel, target, and failure class.
2. **[Confirmed]** Auto-queue only exact deliveries carrying canonical `not_attempted` transport
   evidence (or equivalent proven pre-accept no-effect evidence) and within an approved age policy;
   exclude acknowledged, canonical `rejected`, ambiguous, and pruned rows.
3. **[Confirmed]** Require owner review for the exact historic set and late-delivery policy; never rewind provider
   cursors or broadly replay email.
4. Configure/verify external monitors only under the existing owner-operation gate.
5. Complete terminal adversarial reconciliation; extend or succeed `bu-27dxl.6.7` rather than
   closing it on narrower evidence.

## 7. Acceptance evidence

- Auth-enabled deployments remain routable beyond multiple TTL windows.
- Controller repair restores an expected daemon within one probe interval without restart.
- Wrong identity, wrong port, arbitrary endpoint, stale generation, malformed response, future
  timestamp, and observer failure all fail closed and remain distinguishable.
- Probe/route/registration/restart never clears quarantine.
- QA patrol continues when registry liveness is intentionally broken.
- Fleet expiry creates one condition/attention stream with per-agent evidence.
- `/health` stays process-only; semantic readiness becomes false during the injected outage.
- Deployment cannot succeed until multiple probe advances, a QA/watchdog snapshot, and one
  effect-free route canary pass beyond a TTL boundary.
- Concurrent duplicate delivery produces one target acceptance receipt, one route-inbox row, and
  at most one target session.
- Partial fanout retries only the failed target.
- Ambiguous transport never auto-duplicates.
- Failed-event replay never reports pending/success unless durable work exists.
- Complete healthy snapshots resolve conditions; partial/failed scans do not.

Suggested initial SLOs for owner review:

- detect fleet-control failure within two probe intervals;
- durable owner-visible condition within ten minutes;
- restore routing within one probe interval after recovery;
- QA patrol overdue threshold at twice its configured cadence.

## 8. Existing work and collision fences

- `bu-27dxl.6.7` is the open terminal infrastructure-reliability reconciliation. Its parent
  explicitly excluded generic heartbeat, scheduler/default-schedule, and external-monitor work;
  it cannot absorb implementation silently. It should become a downstream reconciliation
  dependency after an approved companion change.
- `bu-g4jg1` is the blocked owner/ops monitor action. It covers minimal separate-host
  `/api/health` polling only, not functional fleet readiness.
- `bu-vwz4c` concerns Tailscale Serve data-plane verification and must not be treated as liveness
  control-plane implementation.
- `bu-0uqgo` owns model runtime authentication and breaker attention; do not reuse its exact
  signing key or expand its blocked graph for butler liveness.
- Closed `bu-0uqgo.3` owns the typed route/deliver certainty vocabulary; the new work must consume
  it rather than creating another transport taxonomy.
- No live Bead currently names this owner-auth/heartbeat integration defect or the status-only
  routing-failure replay defect. A fresh approved spec should precede graph creation.

## 9. Adjacent incident lanes

### WhatsApp classification

**[Observed][Confirmed] Separate P2.** Appendix C confirms four WhatsApp classification failures,
two explicit `private_content_remote_refused` model attempts, and a currently fresh/healthy bridge.
The repository contains the private-content locality gate
(`src/butlers/core/model_routing.py:2546-2648`). This proves a classification-policy lane distinct
from transport; it does not prove that all four rows share one cause or that the next event will
fail. A bounded follow-up should reconcile server-derived endpoint locality, startup/readiness
validation, and honest pending/unavailable classification behavior. It must not be folded into
historical routing recovery.

Supporting evidence: category-only live counts plus the locality enforcement code.
Contradictory evidence checked: the bridge transport is currently healthy/fresh and only two of
the four failed events have matching model-attempt refusal evidence in the inspected ledger.

### Steam transport

**[Observed] No current remediation program.** Two `HTTP 0` poll failures recovered without
intervention and later polls/ingestions succeeded. Preserve error classification and normal
backoff; historical red rows are truthful evidence, not an active outage.

## 10. Owner decisions and unknowns

Hard gates before implementation:

1. Adopt receiver-derived liveness under the current trusted-host premise, or choose a stronger
   split-host identity mechanism and amend security doctrine.
2. Approve semantic functional readiness and whether external monitoring may observe it; the prior
   monitor decision deliberately chose minimal health only.
3. Approve exact late-delivery age/channel policy after the content-blind historical dry run.
4. Approve the separate WhatsApp locality/pending-classification contract if pursued.

Material unknowns:

- Exact retained payload/receipt coverage for every historic failed event.
- Whether a monitor exists outside repository/runtime evidence; only the blocked Bead and empty
  in-repo deadman configuration are confirmed.
- Future multi-host/untrusted-host topology; the no-new-key recommendation is bounded to the
  current trusted-host deployment doctrine.

## 11. Explicit deprioritizations

- No anonymous heartbeat exemption.
- No dashboard owner key in daemon/runtime children.
- No reuse of approval or runtime-probe credentials.
- No direct cross-schema heartbeat write shortcut.
- No restart-as-repair acceptance.
- No broad or automatic historical replay.
- No one-QA-case-per-butler handling for a fleet-correlated failure.
- No conflation of the recovered Steam transport errors with the control-plane outage.

## 12. Strengths to preserve

- Complete-snapshot condition reconciliation and bounded re-escalation.
- Typed route transport outcomes and post-confirmation bookkeeping discipline.
- Target route-inbox leases and crash recovery after acceptance.
- Supervised dashboard expected-infinite loops.
- Strong CI/test infrastructure; the missing evidence is cross-layer scenario coverage.
- Fail-closed owner authentication and narrow callback authority.

## Appendix A — veracity ledger

| Prior claim | Classification | Resolution |
|---|---|---|
| A new fixed-purpose heartbeat credential is required. | [Overstated] | A receiver-derived controller avoids a new secret and is preferable under current trusted-host doctrine. |
| The external deadman proves there is no outside monitor. | [Unverifiable] | Only `EXTERNAL_DEADMAN_URL` absence, its L3 condition, and blocked monitor Bead are proven. The risk was narrowed accordingly. |
| Existing failed-event replay re-routes failed messages. | [Incorrect] | Code explicitly performs a status-only transition and leaves the terminal inbox unchanged. |
| Every non-dashboard domain delivery lacks atomic acceptance identity. | [Overstated] | Dashboard, Messenger, and domain-event lanes already have different or idempotent ownership; the confirmed gap is scoped to non-dashboard ingestion-to-domain `route.execute`. |

## Appendix B — evidence gaps

| Unknown | Why material | Evidence sought | Blocking? | Bounded investigation | Revisit trigger |
|---|---|---|---|---|---|
| Historic delivery acceptance | Controls safe recovery | Content-blind receipt/session/inbox join | Yes for replay | Dry-run only after delivery identity design | Approved recovery phase |
| External monitor live state | Controls independent alert claim | Owner-authorized category-only monitor evidence | No for code | Existing `bu-g4jg1` operation | Explicit authority |
| Split-host future | Controls endpoint identity strength | Adopted deployment topology | No | New feature request | Topology change |
| WhatsApp failure equivalence | Controls whether all four rows share one locality cause | Bounded classification-attempt correlation | No | Content-blind read-only join | WhatsApp follow-up |

## Appendix C — dated content-blind live evidence receipt

Observed read-only at **2026-09-23 02:10:22 SGT** from the dev stack:

- `switchboard.butler_registry`: `total=13`, `stale=13`, `active=0`; all latest
  `last_seen_at` values were 2026-09-22 23:41:58 SGT.
- latest `public.qa_patrols`: 2026-09-22 23:42:05 SGT, `status=suppressed`, two findings.
- latest QA `active -> stale` transition: 2026-09-22 23:47:06 SGT.
- stale-target routing failures since 2026-09-22 00:00 SGT: 102 route attempts.
- `GET http://127.0.0.1:42200/api/health`: HTTP 200, top-level `status=ok`.
- last ten minutes of `butlers-up-hotreload` logs: 65 periodic liveness failures and 130
  `401 Unauthorized` occurrences (traceback plus summary lines); no payloads were inspected.
- `EXTERNAL_DEADMAN_URL`: not configured; the category-only condition row is `state=aging`,
  `escalation_level=L3`.
- WhatsApp on 2026-09-22 SGT: four `classification_error` failed events; two model-dispatch rows
  with `failure_reason=private_content_remote_refused`; current connector state `healthy` with a
  fresh heartbeat.

Commands used only aggregate registry/patrol/transition/routing counts and HTTP status. No prompt,
message, sender, payload, connector/source endpoint identity, credential, or secret was selected
or recorded. The loopback URL above is an operational health probe target, not a connector/source
identity.

## Planning handoff

`project-review` confirms the findings above. `$th-projects` direction should next run the single
feature-request funnel for the control-plane reliability contract, then a separate funnel for
durable delivery recovery. No Beads graph is authorized until those spec deltas are adopted.
