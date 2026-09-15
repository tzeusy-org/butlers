## 1. Successor preparation and adoption

- [x] 1.1 Preserve September 15 owner directions, PR4164 source, existing author/reviewer/Models ownership and unrelated root changes; refresh source, decisions, dependencies and PR identities.
- [x] 1.2 Define complete design D1-D9, exact host/browser protocol, verifier policy, durable authority, all UX flows and bounded runbook.
- [x] 1.3 Reconcile all whole owner-only requirements in route-reconciliation.md, preserving scenario names and unrelated clauses; verify strict OpenSpec, trace, overwrite, countable-task and repository guards.
- [x] 1.4 Obtain independent security/spec/UX review on exact candidate bytes, correct findings and re-review every changed artifact.
- [x] 1.5 Obtain owner adoption naming the exact reviewed successor artifact. Earlier direction and old review do not satisfy this gate; preserve all separate live-operation boundaries.

## 2. Cohesive vertical delivery after adoption

- [x] 2.1 Route adopted requirements 001-011 through project-direction into existing bu-7y7z2 with one accountable owner; reuse backend commit 5e31158065b099e92aae887cb919523d8cb6b79b, retain dependent Home/HA gates and serialize shared auth edits.
- [x] 2.2 Add dedicated auth schema, least-privilege operations and migration/rollback checks for singleton initialization, contexts, intents, credentials, sessions, CSRF, epochs and receipts; classify storage per D6, with no runtime or generic credential access.
- [x] 2.3 Implement D3 host authorize-registration/recovery/rebind/revoke/reconcile-mode commands with exact intent binding, explicit destructive confirmations and no public host-authority endpoint; prove API/runtime roles cannot authorize or reinitialize state.
- [x] 2.4 Pin and lock the maintained verifier, implement D4-D5 strict ceremony adapter and all bound endpoints, atomic installation/consume/session issuance, counter/backup policy and content-blind errors.
- [x] 2.5 Integrate one central pre-domain/pre-body auth boundary, configured-key sessions/header compatibility, per-request epochs, independent CSRF and server-derived actor attribution across mounted routes and separately scoped callbacks.
- [x] 2.6 Implement the complete D8 shell/login/register/recovery experience, native chooser cancellation, protected query/stream teardown, CSRF reload and all raw-fetch/stream callsites; never replay unsafe actions or retain auth material.
- [x] 2.7 Prepare canonical Serve origin/RP/proxy configuration and distinct deployment state/cookies; update RFC0008 topology notes, operator docs and stale source auth descriptions without running live proxy/config/restart operations.

## 3. Verification and reviewed landing

- [x] 3.1 Exercise real verifier registration/assertion vectors: signature, RP/origin, UP/UV, user handle, malformed input, backup flags/counters and unknown credential; no mock-only crypto proof.
- [x] 3.2 Exercise real PostgreSQL role denial, first-registration race, login/recovery race, replay/restart, receipt cleanup, lost-response and injected credential/session/audit rollback seams.
- [x] 3.3 Exercise mounted full-app auth/CSRF before protected body/domain pool/cache/owner work for every inventoried route, including Health briefing and CLI rotate; recursively enumerate lazy routers and verify scoped callback boundaries.
- [x] 3.4 Exercise synthetic browser registration/login/cancel/new-browser/expiry/recovery and exact Secure cookie behavior through isolated HTTPS; verify canonical Serve browser behavior only after separately bounded live authorization.
- [x] 3.5 Exercise D4 positive allowlists and non-vacuous absence sentinels across response, proxy/log/audit/telemetry, prompt/MCP/connector/notification and frontend cache/build sinks; account Tests: +a ~b -c with no redundant tests.
- [ ] 3.6 Follow targeted test ladder, make test-plan BASE=origin/main, collection for topology, exact CI static/frontend steps including knip, independent exact-head review and terminal hosted merge-group evidence when landing is authorized.

## 4. Operational acceptance and closeout

- [ ] 4.1 Prepare and independently review exact migration/exposure/host enrollment/recovery procedures with failure/rollback conditions; separately request only genuinely remaining live authority after safe isolated evidence is complete.
- [ ] 4.2 Under explicit live authorization verify canonical HTTPS and actual Bitwarden registration/login/cancel, restart/revocation/recovery outcomes; store only content-blind receipts, never credential values or vault data.
- [ ] 4.3 Reconcile source/specs/docs/Beads, dependent Models/Home/HA outcomes, merged identity and operational evidence; archive/close only after required authorized delivery and verification, never merely after spec completion.

Preparation test delta: `Tests: +0 ~0 -0`.

Delivery verification scope: the checked 3.4 item is the isolated HTTPS proof,
including native passkeys, synthetic sync, configured-key mode, actual stream
revocation and pending-query cancellation. Actual Serve/Bitwarden evidence
remains explicitly unchecked under 4.2. The uncompleted 3.6 item includes
independent final-head approval and hosted merge-group evidence.

Measured Python test-definition delta against the governing baseline:
`Tests: +42 ~29 -6`. Existing domain test imports now explicitly install a
synthetic authenticated transport; they are not counted as new tests or
credited as authentication proof. New frontend coverage adds 18 focused unit
cases and two real isolated HTTPS scenarios. The Python lane budgets were not
raised (16,856 unit and 4,600 integration cases at the final budget check).
