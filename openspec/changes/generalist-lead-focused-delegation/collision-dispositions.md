# Cross-change collision dispositions

Task 4.2 applies this change's deltas at the switch release. Any other active change with a delta
on a capability this change modifies or removes must be settled first, or the two deltas
overwrite each other. This ledger records how each one is settled.

## Census

A change intersects when its `specs/` directory has a delta for one of the 79 existing capabilities
this change modifies or removes. On main at 2026-10-22, 52 active changes intersect.
`define-dashboard-fast-answer-runtime` is also listed: it touches none of those capabilities, but
it adds a model path outside goals, which owner decision `bu-u5euwb.21` = C names as the D18
dashboard fast-answer kind. `education-mind-map-lifecycle-integrity` is listed because
it has already been archived.

## Dispositions

- **archive-first**: the change lands and is archived before the switch. Its requirements then
  become baseline, and this change's blocks for the shared capability are rebuilt on that baseline
  at task 4.2.
- **re-scope**: the change is amended to goal-runtime terms (goal admission, broker capabilities,
  D18 functions) before it is archived.
- **fold-in**: the change's overlapping deltas move into this change, and the change no longer
  edits that capability.
- **withdraw**: the change is abandoned unarchived. A recorded owner decision is required.
- **hold**: the disposition waits on an owner decision; the reason names the recommended
  option.
- **archived**: already settled.

The owner column names the bead or PR that carries out the disposition. Where no other epic owns
the change, the owner is the M6 epic `bu-u5euwb`.

| Change | Shared capabilities | Disposition | Reason | Owner |
| --- | --- | --- | --- | --- |
| `define-dashboard-fast-answer-runtime` | none | re-scope | Owner decision `bu-u5euwb.21` = C (2026-10-24): RFC 0038 D18 is amended to name dashboard fast answers as a model-use kind, and the change is re-scoped to that kind's contract, neither withdrawn nor rewritten as a goal. Owner latency on the goal path continues in `bu-dhloi1.12`. | `bu-u5euwb.28` (re-scope); D18 amendment `bu-u5euwb.27` |
| `account-security-perception` | domain-event-bus, ingestion-policy | re-scope | Deterministic alert extraction still publishes the security event. Model judgment becomes an admitted goal, and a security label never authenticates a sender. | `bu-u5euwb` |
| `add-connector-oauth-scope-surface` | connector-base-spec | archive-first | Scope and auth fields are deterministic connector observations. | `bu-u5euwb` |
| `add-health-workout-last` | butler-health | archive-first | A read-only Health tool that becomes a grant-scoped read capability. | `bu-u5euwb` |
| `add-meeting-debrief` | butler-relationship, commitment-lifecycle | archive-first | Collection is deterministic. Owner answers use granted Relationship record capabilities. | `bu-q7vx1q.92` |
| `add-person-posture` | butler-finance, butler-relationship, core-notify, cross-butler-briefing-contribution | archive-first | Posture checks stay deterministic and fail closed. Briefing reasoning becomes an obligation goal. | `bu-q7vx1q.89` |
| `add-personal-baselines` | butler-health | archive-first | The baseline job stays deterministic. Later judgment is a declared obligation. | `bu-q7vx1q.94` |
| `add-runtime-tool-surface-discovery` | core-daemon, core-modules, core-spawner, runtime-config-api | archive-first | The listing and presentation contracts survive. This change then removes the route tools and scopes search to the broker grant. | `bu-ondtw.8` |
| `anchor-roster-schedule-times` | core-scheduler | archive-first | Schedule time zones and the quiet-window warning are deterministic scheduler behavior. | `bu-kqnum.3` |
| `artifact-bound-filtered-event-restore-verification` | testing | archive-first | Restore-verification evidence is database proof, and goal lineage does not replace it. | `bu-u5euwb` |
| `ask-once-knowledge-gaps` | butler-switchboard, module-memory, relationship-facts | re-scope | A declined owner goal records the gap through granted Memory capabilities, not a classifier lane. | `bu-q7vx1q.93` |
| `authorize-qa-local-scheduler-policy` | staffer-qa | archive-first | QA-local scheduler reads are deterministic. QA triage becomes a staffer goal. | `bu-u5euwb` |
| `capture-ci-shard-watchdog-diagnostics` | testing | archive-first | CI diagnostics only; no runtime overlap. | `bu-ly3lv5.10.3` |
| `chronicler-telemetry-distillation` | butler-chronicler | re-scope | Projection adapters stay deterministic. Day-close narration becomes a Chronicler obligation goal. | `bu-u5euwb` |
| `compose-general-capture-with-private-custody` | butler-general | fold-in | The whole General Butler Tool Surface block is folded here and removed from the source delta: collection/item/calendar inventory and complete ordinary-capture/privacy/source/group scenario retained; control-plane registration supersedes runtime spawning. Capture reasoning becomes deterministic ingress-deduplicated goal admission, while the ledger, receipts, search and collections stay domain-owned. The source remains active for all six general-capture requirements; historical packet/task evidence is retained and successor implementation remains pending. No archive or runtime completion is claimed. | `bu-u5euwb.18`; outstanding delivery `bu-2jtfw.9.4.2`, `bu-2jtfw.9.4.4` |
| `confine-qa-publication-authority` | qa-investigation-dispatch, staffer-qa | archive-first | Publication fences hold. Engineering executors stay the named D18 exception. | `bu-mc01y5` |
| `dashboard-one-mind-goal-surface` | fleet-case-file | archive-first | Adds REQ-fleet-case-file-015, a dark, read-only contributor filter on the case list for D19 region Situations. It edits no block this change modifies, so there is no same-name block. | `bu-85bqeq` |
| `decide-consolidation-throughput-policy` | module-memory | archive-first | Records four policy declines and no runtime change. The rebuilt consolidation block keeps them. | `bu-u5euwb` |
| `decision-loop-one-tap-approvals` | autonomy-tracker, core-notify, module-approvals, module-telegram | re-scope | Archive first, then amend the synced specs. Approvals carry goal, invocation and exposure, and park-only effects always wait for the owner tap. | `bu-24lu6.8` |
| `durable-dashboard-terminal-action-recovery` | butler-switchboard, staffer-qa | re-scope | Chat-classifier terminal lanes become admission and one goal. The QA report handoff becomes an authenticated service relay to the durable QA inbox. | `bu-u5euwb`; linked from `bu-ratgnb` |
| `durable-precommit-cancellation-admission` | butler-messenger, butler-switchboard | re-scope | Cancellation stays deterministic. Its route proxy becomes named, allowlisted service-plane endpoints. | `bu-u5euwb` |
| `education-mind-map-lifecycle-integrity` | butler-education, module-education-curriculum, module-education-teaching-flows | archived | Archived 2026-10-18. This change's Education blocks are rebuilt on the new baseline. | none |
| `fail-closed-ci-assurance` | testing | archive-first | CI verdict contracts; no runtime overlap. | `bu-ly3lv5.2` |
| `generation-fenced-codex-auth-rotation-provenance` | core-credentials, core-daemon, core-spawner | re-scope | OAuth projection and login move to the session host's credential volume. Domain daemons never regain provider credentials (D3). | `bu-u5euwb` |
| `harden-memory-retention-schedule-recovery` | core-scheduler, memory-retention-policy | re-scope | Cleanup stays a job. Consolidation recovery submits partitioned obligation goals. | `bu-kqnum.13` |
| `harden-runtime-auth-and-breaker-attention` | core-credentials, core-daemon, core-notify, model-catalog, runtime-opencode | re-scope | Device-auth restoration moves to the session host. Verification probes stay tool-less D18 functions. | `bu-0uqgo` |
| `ingest-google-health-body-measurements` | butler-health | archive-first | Deterministic connector ingestion and an owner-dominance read rule. | `bu-u5euwb` |
| `memory-honesty-last-mile` | module-memory | re-scope | The model consolidation spawn becomes one obligation goal per tenant, source domain and taint partition. | `bu-kqnum.14` |
| `models-exact-path-vision-proof` | model-catalog | re-scope | Vision probes are D18 model functions. The RFC 0036 amendment makes the executor one tool-less, single-turn call. | `bu-o7lumy` (draft PR #4757) |
| `provider-allowance-windows` | catalog-token-limits, core-scheduler, core-spawner, model-failover | re-scope | Allowance gates obligation deferral and invocation selection within a goal. Fallback follows D14. | `bu-q7vx1q.90` |
| `reconcile-dashboard-conversation-contracts` | ingestion-event-registry | re-scope | A dashboard message admits a goal instead of routing to a target session. Dashboard bodies belong to D19. | `bu-85bqeq` |
| `recover-ingestion-target-deliveries` | butler-switchboard, conversation-decomposition, ingestion-event-registry, module-pipeline | re-scope | Reasoning ingress becomes one deduplicated goal admission. Legacy target receipts stay readable and drain before the switch. | `bu-mmax8j` |
| `relationship-fact-effective-time` | butler-relationship, relationship-facts | fold-in | Its temporal fact tools join this change's Relationship tool-surface block. The bitemporal writer contract is unchanged. | `bu-u5euwb`; implementation `bu-h3b7t` |
| `repair-condensation-request-observation` | testing | withdraw | The owner chose to retire the condensation ledger it observes (`bu-lm2b4v` = B); the change is deleted unarchived. | `bu-28av8x.2` |
| `repair-notification-metadata-read` | core-notify | archive-first | Legacy metadata normalization and bounded repair are deterministic. | `bu-u5euwb` |
| `repair-telegram-delivery-truth` | core-notify, module-telegram | archive-first | Delivery truth stays with Messenger's service-plane endpoint. | `bu-u5euwb` |
| `repair-whatsapp-identity-reconciliation` | connector-base-spec, conversation-decomposition, switchboard-identity | re-scope | Speaker attribution stays. Model decomposition and fan-out become deterministic span preservation into one goal. | `bu-wda3im` |
| `restore-butler-control-plane-liveness` | butler-base-spec, butler-switchboard, staffer-qa | re-scope | Route eligibility becomes service eligibility. Domains report no reasoning-session health, and region operator views belong to D19. | `bu-fvw4ap` |
| `restore-drill-recovery-truthfulness` | core-notify, testing | archive-first | Restore-drill truth is database evidence that a goal receipt cannot upgrade. | `bu-viat6h` |
| `restore-qa-remediation-loop` | qa-investigation-dispatch, staffer-qa | re-scope | QA launches, lineage and budgets become staffer goals with invocation-bound evidence. Incident admission maps onto goal admission. | `bu-6a9xxw` |
| `scrub-before-hash-dedupe-keys` | connector-base-spec | archive-first | Ingest derives dedupe keys deterministically. | `bu-u5euwb` |
| `specify-host-authorized-dashboard-enrollment` | butler-health | re-scope | Enrollment stays. The Health voice briefing becomes an obligation or owner goal. | `bu-7y7z2` |
| `specify-improvement-proposal-spine` | core-skills | fold-in | The whole AGENTS.md Read/Write Access block (`REQ-core-skills-004`) is folded here and removed from the source delta. The eight-name scenario union preserves pure tooling helpers, content/workflow boundaries, the goal/sandbox ban, and direct/thin-wrapper MCP prevention. Future tracked-roster amendments remain explicit-owner-accepted proposals through `propose_amendment` and the corresponding target adapter, distinct from D2 guidance-notes `record`. The source remains active with its other 20 requirements, 41 scenarios and historical/pending tasks; no archive or runtime completion is claimed. | `bu-u5euwb.20`; outstanding proposal-spine delivery `bu-8cdl1.15` |
| `specify-messenger-voice-egress-contract` | core-notify | re-scope | Voice delivery stays Messenger service traffic. Narration belongs to a goal, and transcription is a D18 function. | `bu-7exe4.13` |
| `specify-relationship-read-entity-group` | butler-relationship | archive-first | A read-only tool group becomes grant-scoped presentation. | `bu-h40h2b.16` |
| `specify-roster-identity-owner-operations-overlay` | butler-base-spec, core-skills, core-spawner, staffer-archetype, staffer-qa | re-scope | Domain `CLAUDE.md` text becomes versioned guidance. The lead prompt comes from lead policy and the owner profile (D15). | `bu-u5euwb` |
| `specify-state-first-secrets-passport-index` | butler-secrets | archive-first | An owner-only Secrets surface; no goal capability returns credential content. | `bu-u5euwb` |
| `specify-telegram-confirm-interaction-contract` | core-notify, module-telegram | re-scope | Confirm stays deterministic, recipient-bound service traffic. A non-owner reply stays third-party and exposed. | `bu-ow5a4` |
| `specify-telegram-continuity-routed-context-contract` | butler-switchboard | re-scope | Sticky target affinity and misroute re-dispatch retire. Bounded conversation context carries into goal admission. | `bu-u5euwb` |
| `specify-telegram-receipt-acknowledgement-contract` | module-telegram | re-scope | Typing and receipt bind to goal admission and the goal reply, not to routing callbacks. | `bu-z0b0ah.4` |
| `specify-telegram-voice-note-transcription-contract` | connector-base-spec | archive-first | Speech-to-text is a named D18 function, and the transcript enters a goal as untrusted text. | `bu-r0dsz` |
| `strict-owner-telegram-wake-recovery` | butler-health, butler-messenger, butler-switchboard, core-notify | re-scope | Wake coordination stays deterministic. Its Switchboard route hops become named service-plane endpoints. | `bu-kqnum.3.6` |
| `surface-known-contact-drops` | connector-base-spec, ingestion-policy | archive-first | Known-contact classification is deterministic evidence, never owner authentication. | `bu-q7vx1q.88` |
| `true-bidirectional-email-correspondence` | butler-messenger, butler-relationship | withdraw | The owner chose to withdraw the change and park RFC 0024. | `bu-n1ft7f` |

## Same-name requirement blocks

When both deltas edit the same requirement, task 4.2 rebuilds this change's block from the
baseline that the other change's disposition produces. The rebuilt block keeps the other change's
surviving scenarios unless this change removes the requirement.

| Change | Capability | Requirement | This change |
| --- | --- | --- | --- |
| `add-runtime-tool-surface-discovery` | core-daemon | Core Tool Surface | removes |
| `add-runtime-tool-surface-discovery` | core-spawner | Ephemeral MCP Config Generation | removes |
| `add-runtime-tool-surface-discovery` | runtime-config-api | PATCH runtime config endpoint | modifies |
| `compose-general-capture-with-private-custody` | butler-general | General Butler Tool Surface | folded here; source block removed, ordinary-capture scenario preserved and registration trigger retained |
| `decide-consolidation-throughput-policy` | module-memory | LLM-driven memory consolidation pipeline | modifies |
| `durable-dashboard-terminal-action-recovery` | butler-switchboard | Dashboard Chat-Widget Classification Lanes | removes |
| `durable-dashboard-terminal-action-recovery` | staffer-qa | Pluggable Discovery Source Architecture | modifies |
| `durable-dashboard-terminal-action-recovery` | staffer-qa | V1 Discovery Sources | modifies |
| `memory-honesty-last-mile` | module-memory | LLM-driven memory consolidation pipeline | modifies |
| `recover-ingestion-target-deliveries` | conversation-decomposition | Decomposition Output Storage | removes |
| `recover-ingestion-target-deliveries` | module-pipeline | Decomposition-to-Routing Fan-Out | removes |
| `relationship-fact-effective-time` | butler-relationship | Relationship Butler Registered Tool Surface | modifies |
| `repair-whatsapp-identity-reconciliation` | conversation-decomposition | Cherry-Picked Message Excerpts | removes |
| `repair-whatsapp-identity-reconciliation` | conversation-decomposition | Multi-Butler Fan-Out from Single Ingestion | removes |
| `repair-whatsapp-identity-reconciliation` | conversation-decomposition | Signal Extraction for Decomposition | removes |
| `restore-butler-control-plane-liveness` | butler-base-spec | Butler as Architectural Primitive | modifies |
| `restore-butler-control-plane-liveness` | butler-base-spec | Instance Facts Internal Interface | modifies |
| `restore-butler-control-plane-liveness` | butler-base-spec | Staffers vs Domain Butlers | modifies |
| `restore-qa-remediation-loop` | staffer-qa | Patrol Loop Lifecycle | modifies |
| `specify-roster-identity-owner-operations-overlay` | butler-base-spec | CLAUDE.md as System Prompt Entry Point | modifies |
| `specify-roster-identity-owner-operations-overlay` | core-spawner | System Prompt Composition | modifies |
| `specify-telegram-continuity-routed-context-contract` | butler-switchboard | Misroute Correction Re-dispatch | removes |

## Other conflicts

| Conflict | Disposition | Owner |
| --- | --- | --- |
| REQ-core-spawner-010 and RFC 0038 D3 allow a goal invocation's environment to hold only `PATH`, the staged credential and the MCP config. The Gemini adapter also sets non-secret file pointers (`GEMINI_CLI_SYSTEM_SETTINGS_PATH`, `GEMINI_SYSTEM_MD`). | Amended. The owner admitted adapter-owned, non-secret pointers to files staged inside the invocation's sandbox (`bu-u5euwb.22` = A, 2026-10-24T14:30Z). D3 and REQ-core-spawner-010 now admit them, and secrets stay limited to the staged model credential and the invocation's own MCP config. Each adapter still proves D3 eligibility at the switch (`bu-enbrs2`). | `bu-u5euwb.26` |
| `tool_exposure_policy` in runtime config has no consumer, and RFC 0038 does not name it. | Keep it or delete it at the switch. Either way, the runtime-config card's copy changes to match. | `bu-ondtw.8` |

## Frozen-path beads

| Bead | Disposition |
| --- | --- |
| `bu-p6v8k` (Telegram sticky routing) | Obsolete at the switch. Owner replies join goals through admission, and `specify-telegram-continuity-routed-context-contract` retires sticky affinity. |
| `bu-0ynlk.6` (in-process dashboard answers) | Superseded by D18 and D19. PR #4485 stays unmerged, `define-dashboard-fast-answer-runtime` is re-scoped to the D18 dashboard fast-answer kind (owner decision `bu-u5euwb.21` = C; `bu-u5euwb.27`, `bu-u5euwb.28`), and owner latency on the goal path stays with `bu-dhloi1.12`. |
| `bu-2jtfw.9.4` (General capture admission and routing) | Capture classification becomes goal admission through the `compose-general-capture-with-private-custody` fold-in. The capture ledger and receipts remain. |
| `bu-60pwv6.6` (split oversized dashboard specs) | Superseded by `bu-lsxqb0.20`. The dashboard capabilities belong to the D19 change, not this one. |
