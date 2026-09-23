## ADDED Requirements

### Requirement: [TARGET-STATE] Semantic fleet readiness
The canonical exact public `GET /ready` and its sole owner-auth method/path
exception SHALL be implemented by Q4. It SHALL report content-blind
control-plane readiness separately from lightweight `GET /health`. Success
SHALL require a working database, complete and fresh controller observation of
every configured expected non-paused daemon at its latest registered boot
epoch, advertised route acceptance and compatibility, a qualifying completed
QA patrol whose enabled sources all succeeded, progress from the existing
process-fenced supervised-job health projection for named Dashboard lifespan
control loops, and a fresh successful cached result from the Switchboard-owned
read-only route preflight in `REQ-butler-switchboard-004`. Unavailable, unknown,
stale, or stopped Dashboard supervisor or preflight evidence SHALL fail closed;
this requirement consumes existing projections and defines no second job-health
store. The Switchboard runtime-attention delivery worker is not covered by the
Dashboard supervisor projection; its pending, failed, uncertain, or unavailable
delivery state SHALL remain separately visible through linked condition/outbox
evidence and SHALL NOT be implied healthy by `/ready.checks.supervisors`.
Switchboard's internal preflight traverses production target selection, route
policy, registry eligibility, compatibility, and exact endpoint resolution for
one fixed configured non-paused domain target, then performs one bounded
shared-verifier identity GET. Neither the public request nor Dashboard's
readiness handler SHALL execute that path or call `route.execute`; public
requests read only the controller's bounded snapshot. The preflight proves
control-plane selection and reachability, not transactional target acceptance
or session success. Delivery receipts and conditions are separate evidence.
An intentional QA pause remains separately visible but does not satisfy
readiness. A database connection and nonempty roster alone SHALL NOT make
`/ready` successful. K3s and Compose consume Q4's route; they SHALL NOT
register a second readiness handler or public auth exception.

ID: REQ-dashboard-api-063
Source: RFC 0007 §API Surface; openspec/changes/k3s-deployment-helm-chart/specs/dashboard-api/spec.md §Readiness probe endpoint; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.4
Scope: v1-mandatory

#### Scenario: Control plane is ready
- **WHEN** all required observations and dependencies are fresh, the QA patrol is current, and the bounded route preflight confirms fixed-target selection, reachability, and current advertised acceptance without creating an inbox or session
- **THEN** `GET /ready` returns HTTP 200 with a content-blind ready result

#### Scenario: Preflight has no transactional acceptance claim
- **WHEN** the fixed target is reachable and advertises route acceptance but a separate `route.execute` inbox transaction would fail
- **THEN** the preflight does not claim an accepted delivery receipt or downstream session success
- **AND** the failure remains detectable through actual target-delivery evidence rather than being hidden as a passed end-to-end canary
- **AND** repeated public `/ready` reads do not trigger new Switchboard probes or write routing evidence

#### Scenario: Incomplete patrol cannot make deployment ready
- **WHEN** a recent QA row is `running`, `error`, `skipped_overlap`, synthetic `suppressed`, legacy-provenance-unknown, from an older enabled-source configuration, or lacks successful completion evidence for any enabled discovery source, and no earlier qualifying patrol remains fresh
- **THEN** the `qa_patrol` readiness check remains false even if that nonqualifying row has a recent timestamp
- **AND** a completed scheduled `clean`, `findings_dispatched`, or genuine filtered-finding `suppressed` patrol with current-config all-source-success provenance can renew it

#### Scenario: Nonqualifying row does not erase a still-fresh completed patrol
- **WHEN** a synthetic, skipped, running, or failed row is newer than a still-fresh qualifying patrol under the current enabled-source configuration
- **THEN** the newer row does not advance the freshness clock or itself establish readiness
- **AND** the earlier qualifying patrol remains valid until its own cadence bound expires

#### Scenario: Explicitly paused domain daemon does not fabricate fleet failure
- **WHEN** an expected domain daemon is explicitly paused by the owner while all non-paused expected daemons satisfy the functional checks
- **THEN** fleet readiness evaluates the non-paused set and preserves the paused policy as an intentional exclusion
- **AND** a quarantined or review-required daemon remains an ineligible failure rather than a paused exclusion

#### Scenario: Process lives while routing is unavailable
- **WHEN** the dashboard process is live but controller progress, an expected daemon, QA patrol, or the route canary is not ready
- **THEN** `GET /health` remains process-only while `GET /ready` returns HTTP 503 with bounded per-check status
- **AND** the response exposes no private roster identity, payload, credential, or endpoint address

#### Scenario: Readiness remains narrowly public
- **WHEN** an unauthenticated deployment probe requests exact `GET /ready`
- **THEN** it may read only the content-blind readiness verdict and bounded check categories
- **AND** no other protected dashboard API is opened by this exception
- **AND** Q4 owns the one handler and exact method/path exception; k3s and Compose only consume it

### Requirement: [TARGET-STATE] Deployment waits for sustained semantic readiness
The canonical deployment launcher SHALL refuse to declare completion until
semantic readiness remains true across more than one full liveness TTL, with
at least two distinct complete receiver-observer cycles and two distinct
qualifying scheduled QA patrol completions after the deployment window starts.
The patrols SHALL have different durable IDs, strictly increasing completion
times, current enabled-source configuration, and all enabled sources
successful; two reads of one patrol or synthetic/failed rows SHALL NOT count
as two cycles. A false or unavailable readiness result restarts the sustained
window. The launcher SHALL preserve a bounded receipt of failing fixed check
categories and classify rollback without exposing private content. Its finite
default timeout SHALL be at least two configured QA patrol cadences plus the
longest configured fleet liveness TTL and a ten-minute margin (35 minutes with
the current ten-minute patrol cadence and five-minute TTL); an override below
that derived minimum SHALL fail configuration validation rather than create a
healthy deployment that can never pass its own gate.

ID: REQ-dashboard-api-064
Source: heart-and-soul/vision.md:51-53,120-137; RFC 0008 deployment boundary; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.4
Scope: v1-mandatory

#### Scenario: Brief post-restart freshness cannot pass deploy
- **WHEN** process `/health` is green but the observer or QA has not advanced through the required sustained window
- **THEN** the launcher remains pending or fails within its bounded deadline rather than reporting deployment complete

#### Scenario: Distinct completed patrol cycles are required
- **WHEN** `/ready` is repeatedly healthy but only one qualifying scheduled QA patrol has completed since the deployment window began
- **THEN** deployment completion remains pending even if that patrol is read twice and the liveness TTL has elapsed
- **AND** a second distinct current-config all-source-success patrol plus a second complete observer cycle can satisfy the cycle gate before the validated deadline

#### Scenario: Timeout follows configured cadence
- **WHEN** QA cadence or the longest expected liveness TTL makes the configured deployment deadline shorter than two patrol cadences plus that TTL and the safety margin
- **THEN** the launcher rejects that configuration before declaring readiness
- **AND** a bounded timeout or a false readiness sample never produces a successful deployment receipt

#### Scenario: Sustained functional route passes deploy
- **WHEN** required observation, patrol, supervisor, database, and effect-free route checks remain valid beyond a full TTL
- **THEN** the launcher may record semantic deployment completion with a content-blind receipt

### Requirement: [TARGET-STATE] External functional monitoring remains owner-gated
Enabling the public, content-blind `GET /ready` SHALL NOT configure an external functional monitor or claim that a separate host is observing it. Activation of such monitoring SHALL require a separately approved operator contract for the target, checks, notification route, and evidence. The already-approved separate-host minimal process-health pull monitor remains valid and distinct.

ID: REQ-dashboard-api-065
Source: heart-and-soul/security.md; openspec/changes/define-infrastructure-reliability-lifecycle/specs/infrastructure-reliability/spec.md §Expected-infinite dashboard loops; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §10
Scope: v1-mandatory

#### Scenario: Internal readiness does not provision external monitoring
- **WHEN** this change enables internal semantic readiness and deployment checks
- **THEN** it does not configure an external monitor or claim independent functional coverage from `/ready` being reachable
- **AND** an unconfigured `EXTERNAL_DEADMAN_URL` retains the separately specified durable content-blind assurance condition without impersonating a missed ping or QA patrol finding
