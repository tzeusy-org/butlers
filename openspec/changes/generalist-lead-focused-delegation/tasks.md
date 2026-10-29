## 1. Contract

- [x] 1.1 Record owner adoption of the generalist-lead doctrine (2026-10-14).
- [x] 1.2 Draft RFC 0038 with the authorization broker, hard cutover plan and owner decisions.
- [x] 1.3 Owner answers RFC 0038 decisions 1, 2, 3, 6 and 7 (2026-10-14).
- [x] 1.4 Owner signoff on the full RFC 0038 design (2026-10-14, with amendments recorded as owner decisions 8 to 11); author MODIFIED/REMOVED deltas for the baseline capabilities listed in proposal.md.
- [x] 1.5 Derive the implementation epic and beads from the signed contract (bu-4c7nvp and bu-n7zmbp; task 2.0 final acceptance remains open).

## 2. Dark build (merged to main, not wired to ingress or schedules)

- [ ] 2.0 Move Switchboard into its own process and container with separate service and session listeners. Adopt the owner-approved Recommendation B profiles (2026-10-15, bu-jnhxc5):
  - KEY_ABSENT_STAGE (bu-n7zmbp.1) separates today's service without changing classification, routing, notify or deliver. It provisions, mounts and resolves no GOAL private key; selection with private material refuses startup. Its separate session port serves only fixed GET /health unavailable (503) and refuses all other routes. Service liveness/readiness remains on the service port. Source preparation does not establish the required actual dev compatibility/owner Telegram smoke.
  - ISOLATED_CANDIDATE (bu-jnhxc5) retains all seven original criteria, the closed trusted goal harness, full launcher and D18/adapter/helper/descendant custody proof before any key mount, goal-only session endpoint with health refused, and no live ingress, schedules, dashboard or local legacy launches. KEY_ABSENT_STAGE evidence cannot close it.
  - SWITCHED_RELEASE requires the complete one-release owner gates before actual ingress/obligations change, legacy path deletion, domain verification and archive. Effective profiles are immutable trusted startup/deployment selections. Affected changes require fresh exact-profile evidence.
  - The staged deployment preserves the owner's total session capacity five through exactly one control process with one slot and one domain process with four; unsplit production retains five. This changes the topology contract, not the process-local semaphore. No live rollout precedes aggregate-cap proof and required independent admission/review.
- [ ] 2.1 Catalog authorization metadata for every LLM-presentable tool, with a CI check.
- [ ] 2.2 Goal ledger migrations: goals, invocations, effects, budgets.
- [x] 2.3 Goal admission with origin ceilings, starting exposure and obligation declarations, including connector marking of forwarded, quoted and embedded spans.
- [ ] 2.4 Goal runner, session sandbox profiles and runtime adapter proofs, including per-invocation model credential staging, tool-less model-function configurations, and retargeting Codex auth synchronization and the dashboard CLI login to the session host's credential volume.
- [ ] 2.5 Broker endpoint: per-invocation listing, call checks, exposure, approvals (with goal lineage and the `cancelled` status), effect ledger, signed assertions and `GOAL_BROKER_SIGNING_KEY` lifecycle.
- [ ] 2.6 Domain-side assertion verification in the core tool wrapper (enabled by the cutover release).
- [ ] 2.7 Goal tools, delegation, budgets, Stop, recovery and escalation.
- [ ] 2.8 RFC 0027 native deferred discovery on the broker endpoint.
- [ ] 2.9 Taint stamping on domain writes under exposed assertions, and caused-admission lineage on domain events.
- [ ] 2.10 Route every direct service call to a model-reachable tool through the control plane service listener, and replace the generic `route` tool with named service-plane relay endpoints that authenticate their calling daemon against a per-endpoint allowlist.
- [ ] 2.11 Owner surface (D19): design the `dashboard-*` experience change, obtain owner signoff, and build the goal and region views without replacing the current ones.
- [ ] 2.12 Domain conversion: obligation declarations and admissions for every scheduled prompt, session-waking subscription and model-backed job (memory consolidation, teaching and review flows, briefings), goal-outcome episode capture, the lead policy (`LEAD.md`), the domain guidance corpus and its `owner_authority` guidance-notes capability, and the feed path for device and telemetry channels.
- [ ] 2.13 Non-owner communication coverage: `recipient_params` on every party-reaching capability (calendar, Home Assistant messaging services, shares), Messenger's `render_links_inert` handling, and the refusal path for party-reaching calendar writes on butlers without an approvals module.

- [ ] 2.14 Brokered web research (D20): after owner approval and merge of the amendment PR and the existing session-host prerequisites, build the credential-agnostic separate research host and Codex profile, broker-owned catalog capability, origin ceilings, exposure-selected live/cached mode, transactional reservations, finite per-call and fleet caps, event-derived audit, lineage/taint and adversarial cases. Build and hermetic or other no-real-credential proofs may proceed without actual research credential provisioning; bu-6qaju2.2 is closed (2026-10-15), answer B: reuse the existing Codex ChatGPT-subscription login. Actual research credential provisioning or use and live research proof still require the corresponding provisioning and spend conditions. Keep implementation dark behind the default-off program switch; the switch release remains dependent on the broker capability and the existing cutover gate.

## 3. Cutover gate (dev namespace, release candidate)

- [ ] 3.1 All goal-orchestration and capability-authorization scenarios, and every scenario of the signed D19 dashboard change, pass.
- [ ] 3.2 Adversarial suite: zero unauthorized effects. Each owner rule and exposure rule has a test that fails against the current release's behaviour where the rule is new, and passes on the release candidate.
- [ ] 3.3 Replay evaluation meets or beats recorded outcomes; model tiers and default budgets set from it.
- [ ] 3.4 Stop, lease-loss, runner-crash, escalation and `GOAL_BROKER_SIGNING_KEY` rotation drills pass.

## 4. Switch

- [ ] 4.1 One release routes all ingress and obligations through admission, deletes classification, route dispatch, per-butler spawning and cross-butler delegation, enables domain verification, and switches the dashboard to the D19 owner surface, removing the butler-unit views it replaces.
- [ ] 4.2 Apply baseline deltas, archive this change, update RFC statuses, topology, operational docs, `CLAUDE.md` and the Switchboard manifesto. Every active change that modifies or adds requirements in a capability this change modifies or removes (listed in design.md, "Cross-change collisions") archives first, is re-scoped against the goal runtime, or has its overlapping deltas folded into this change before it archives. Capabilities this change empties (`cross-butler-delegation`, `conversation-decomposition`, `routing-scorecard`) are removed by hand, because archive refuses an empty spec.

The RFC corpus rewrite (owner decision 10) runs after this change is archived, so it is not a task
here; it is tracked as `bu-bda935`, deferred until 4.2 is done.

## 5. Proposed shared-contract conversion (all pending)

These pending tasks do not alter the completed historical tasks above or claim
new runtime delivery. Requirements and exact ownership are in
[shared-contract-coverage.md](shared-contract-coverage.md).

- [ ] 5.1 Independently review the complete RFC S0–S8 and eleven capability delta interfaces, including field/state/API and every failure/retry/concurrency/rollback scenario; record reserved exact-text adoption and immutable adopted commit.
- [ ] 5.2 Hand the exact adopted requirement/owner/source map to .6–.15 and named perception, delivery and gateway successors; coordinator updates existing M6 candidate coverage and verifies real predecessor edges before .19 closure.
- [ ] 5.3 Deliver selected first-switch schemas/roles, labels/producers, original-identity replay, shadow reader parity and one sole-writer/enforcement transition in their existing source owners; retain all migrated-Postgres and current contract evidence.
- [ ] 5.4 Qualify the exact M6 candidate against the selected first-switch map and existing release/approval gates; do not archive/release absent source successors or depend on M7/later consumers.
- [ ] 5.5 Retain separate M7 typed/cited holds and later decision-set, streaming, speech, surface, watch and healing obligations; none is activated or ticked by authoring this contract.
