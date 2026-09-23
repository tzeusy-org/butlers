## ADDED Requirements

### Requirement: [TARGET-STATE] Semantic fleet readiness
The canonical `GET /ready` SHALL report content-blind semantic readiness separately from lightweight `GET /health`. Success SHALL require a working database, complete and fresh controller observation of every configured expected non-paused daemon at its latest registered boot epoch, route compatibility and acceptance, a qualifying completed QA patrol whose enabled sources all succeeded, supervised control-loop progress, and an effect-free Switchboard route canary. An intentional QA pause remains separately visible but does not satisfy readiness. A database connection and nonempty roster alone SHALL NOT make `/ready` successful.

ID: REQ-dashboard-api-063
Source: RFC 0007 §API Surface; openspec/changes/k3s-deployment-helm-chart/specs/dashboard-api/spec.md §Readiness probe endpoint; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.4
Scope: v1-mandatory

#### Scenario: Fleet is functionally ready
- **WHEN** all required observations and dependencies are fresh, the QA patrol is current, and the synthetic route confirms acceptance without creating an inbox or session
- **THEN** `GET /ready` returns HTTP 200 with a content-blind ready result

#### Scenario: Incomplete patrol cannot make deployment ready
- **WHEN** the latest QA row is `running`, `error`, `skipped_overlap`, synthetic `suppressed`, or lacks successful completion evidence for any enabled discovery source
- **THEN** the `qa_patrol` readiness check remains false even if that row has a recent timestamp
- **AND** only a completed `clean` or `findings_dispatched` all-source-success patrol can renew it

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

### Requirement: [TARGET-STATE] Deployment waits for sustained semantic readiness
The canonical deployment launcher SHALL refuse to declare completion until semantic readiness remains true across more than one full liveness TTL and multiple observer and QA patrol checks, including one effect-free route canary. It SHALL preserve a bounded receipt of the failing check categories and classify rollback without exposing private content.

ID: REQ-dashboard-api-064
Source: heart-and-soul/vision.md:51-53,120-137; RFC 0008 deployment boundary; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.4
Scope: v1-mandatory

#### Scenario: Brief post-restart freshness cannot pass deploy
- **WHEN** process `/health` is green but the observer or QA has not advanced through the required sustained window
- **THEN** the launcher remains pending or fails within its bounded deadline rather than reporting deployment complete

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
