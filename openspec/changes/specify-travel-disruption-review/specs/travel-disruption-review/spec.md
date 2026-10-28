## Purpose

Review the consequences of a changed itinerary for plans the owner explicitly linked to the trip. Preserve source uncertainty, bounded inference and owner control while delivering one coherent, revision-bound account of affected arrangements.

## ADDED Requirements

### Requirement: Only owner-confirmed plans enter a trip impact review
Travel SHALL enroll a trip dependency only from an authenticated owner action selecting a current readable canonical reservation or exact Calendar occurrence and confirming its arrival deadline and transfer buffer. A link MUST NOT grant new read or effect authority; updates and removal SHALL require the current link revision.

ID: REQ-travel-disruption-review-001
Source: heart-and-soul/vision.md Rules 3 and 6; RFC 0038 D4-D7
Scope: v1-mandatory

#### Scenario: A selected occurrence is the assessed plan
- **WHEN** the owner confirms a readable Calendar occurrence and its arrival relation to an enrolled trip leg
- **THEN** Travel records one versioned link to that exact occurrence and the confirmed relation
- **AND** neighboring occurrences and similarly named reservations remain unlinked

#### Scenario: External content cannot enroll a plan
- **WHEN** airline prose, a model suggestion or a time/location match proposes a new dependency without owner confirmation
- **THEN** the review excludes it and labels it not assessed
- **AND** no inferred link or expanded grant is persisted

#### Scenario: Concurrent link changes have one winner
- **WHEN** two edits or a removal compete against the same link revision
- **THEN** one current transition commits and a stale request receives the current revision as a conflict
- **AND** removed or changed links invalidate their pending review choices

### Requirement: Accepted meaningful revisions produce one bounded review
Travel SHALL consume only its authoritative writer's committed accepted itinerary revisions and SHALL deterministically coalesce meaningful transitions according to the admitted profile. Poll timestamps, unchanged values and replay MUST NOT admit reasoning; duplicate composite review identities MUST reuse their admission and outcome rather than create new owner noise.

ID: REQ-travel-disruption-review-002
Source: heart-and-soul/vision.md Rule 4; RFC 0038 D5 and S3-S4; REQ-event-response-contract-001 and REQ-event-response-contract-003
Scope: v1-mandatory

#### Scenario: Frequent duplicate observations do no reasoning work
- **WHEN** ten thousand repeated observations change only fetch timestamps or repeat an accepted event identity
- **THEN** they produce zero model invocations and no new review admission
- **AND** current source freshness and prior review history remain truthful

#### Scenario: Small shifts accumulate into a meaningful revision
- **WHEN** several accepted arrival shifts cumulatively cross the profile threshold relative to the last reviewed baseline
- **THEN** one meaningful review candidate is admitted even if no individual step crosses that threshold
- **AND** a linked deadline crossing is evaluated independently of the numeric threshold

#### Scenario: Coalescing retains transient disruption evidence
- **WHEN** a cancellation and recovery arrive within one fixed coalescing window
- **THEN** the current review uses the latest valid accepted state while preserving both transition references
- **AND** the window is not extended by each new observation

#### Scenario: Queue overflow cannot imply that plans are safe
- **WHEN** pending transitions exceed the finite profile capacity
- **THEN** affected coverage becomes unknown and stale candidate admission is cancelled
- **AND** recovery evaluates only the newest current unexpired snapshot rather than launching work per buffered sample

#### Scenario: Concurrent leg changes cannot collide
- **WHEN** two different legs of one trip change concurrently
- **THEN** their accepted updates receive distinct trip-wide revisions identifying the resulting complete trip snapshots
- **AND** coalescing or deduplication cannot mistake one leg's change for the other

### Requirement: Source freshness and local response bounds remain distinct
Travel SHALL enforce the admitted `polling-review-v1` profile defined in this change's design, including finite source-population, freshness, coalescing, deadline and miss-detection bounds. Local fetch time MUST NOT masquerade as provider observation time, and extrapolated arrival MUST NOT masquerade as an independently observed arrival. Unknown source freshness SHALL permit only a labelled provisional review.

ID: REQ-travel-disruption-review-003
Source: RFC 0038 S3.2 and S6; REQ-event-response-contract-002 and REQ-event-response-contract-005
Scope: v1-mandatory

#### Scenario: A successful poll can still have unknown provider freshness
- **WHEN** a successfully fetched result lacks trustworthy provider observation time or an exact flight-occurrence match
- **THEN** the review exposes source-freshness unknown or ambiguous source identity respectively
- **AND** it cannot report affected plans as confirmed unaffected from that evidence

#### Scenario: Derived arrival cannot prove a plan remains feasible
- **WHEN** an arrival time was shifted from a departure estimate rather than independently reported or owner-confirmed
- **THEN** the result labels the extrapolation and any projected conflict
- **AND** the assessed relation remains unknown rather than confirmed unaffected

#### Scenario: Evidence expires independently of new events
- **WHEN** the admitted maximum evidence age or monitoring coverage is exceeded
- **THEN** current reads immediately expose stale or unknown state
- **AND** pending options lose validity and status propagation meets the profile's bound

#### Scenario: Deadline misses stay visible after recovery
- **WHEN** a provisional card or optional enrichment misses its declared endpoint deadline
- **THEN** Travel records the miss within the finite detection bound and retains it in the complete response denominator
- **AND** a later response is not relabelled timely and cannot execute an expired option

### Requirement: Impact conclusions retain their evidence and revision
Travel SHALL derive affected, unaffected or unknown for each confirmed arrival relation from current authorized evidence, preserving source and target versions. Model explanation MUST NOT override deterministic unknowns, rewrite source records or add unlinked plans. A changed source, link or target version MUST invalidate conclusions that depended on the previous version.

ID: REQ-travel-disruption-review-004
Source: heart-and-soul/vision.md Rules 3 and 4; RFC 0038 D6 and D12
Scope: v1-mandatory

#### Scenario: The arrival relation determines the bounded conclusion
- **WHEN** a fresh independent arrival plus the owner-confirmed transfer buffer is later than the confirmed latest-arrival deadline
- **THEN** the linked relation is affected and the review shows the supporting times and evidence references
- **AND** an equal or earlier arrival may be unaffected only for that declared relation, not a promise that all travel arrangements are safe

#### Scenario: Missing authority or evidence remains unknown
- **WHEN** a linked target becomes unreadable, its source conflicts or its time zone is unavailable
- **THEN** that relation is unknown with a permitted reason and unauthorized detail is withheld
- **AND** the lead cannot substitute a convenient source or an inferred time

#### Scenario: A target changes during review
- **WHEN** the Calendar occurrence or reservation version changes before publication
- **THEN** the prior result is superseded or unknown instead of being published as current
- **AND** a changed owner-confirmed arrival relation requires confirmation again before assessment

### Requirement: Every review shares the finite feature inference allowance
Travel SHALL enforce the initial profile's per-revision, per-invocation and aggregate daily inference limits through the canonical admission/budget owner, including retries, replacements and any model-based preprocessing. Reservations MUST use a stable feature identity and captured fixed UTC window; unavailable enforceable limits MUST refuse inference while retaining deterministic review and owner controls.

ID: REQ-travel-disruption-review-005
Source: RFC 0038 D10 and S2.2; REQ-event-response-contract-004
Scope: v1-mandatory

#### Scenario: Concurrent trips compete for the same remaining allowance
- **WHEN** multiple trips or retries request the final available invocation or token allowance
- **THEN** atomic reservations admit only work whose complete enforceable bounds fit the remaining allowance
- **AND** a denied invocation leaves a deterministic review with inference-held status

#### Scenario: A later model turn exhausts the invocation ceiling
- **WHEN** accumulated input, including cached input counted once, or the next output reservation would exceed the invocation ceiling
- **THEN** the next model turn does not start and existing results remain explicitly partial or inference-held
- **AND** tool-turn context expansion cannot evade the aggregate feature limit

#### Scenario: Midnight and recovery preserve prior accounting
- **WHEN** an invocation settles after its captured UTC window or restarts without authoritative usage
- **THEN** the original window retains its consumption or conservative reservation
- **AND** new goals, profile versions and manual Review now cannot reset consumed capacity

#### Scenario: Superseded work retains its occupied capacity
- **WHEN** a new revision supersedes a review whose model invocation is still running
- **THEN** the old result is fenced and cancellation is requested without releasing its concurrency slot until termination is confirmed
- **AND** reservations remain charged until authoritative settlement and unknown usage is not refunded
- **AND** replacement work can start only within the remaining actual concurrency and feature allowance

### Requirement: Review controls preserve honest outcomes across Stop and expiry
Travel SHALL expose one canonical current review with clear changed, affected, unknown and not-assessed states, source age and actionable recovery. Its initial mode MUST be review_only with no active effect choices. Stop, expiry and duplicate controls MUST execute deterministically; a completed control request, provider acceptance or old visible card MUST NOT confer new effect authority or claim a repaired trip.

ID: REQ-travel-disruption-review-006
Source: RFC 0038 D14, D19 and S6; dashboard-design-language Interaction Affordances; REQ-event-response-contract-005
Scope: v1-mandatory

#### Scenario: Initial review mode offers no mutation controls
- **WHEN** the feature runs in its initial review_only mode
- **THEN** no active Calendar mutation or message-send choice is exposed
- **AND** inspecting an impacted plan neither changes it nor authorizes a future effect

#### Scenario: The owner can act while inference is held
- **WHEN** a review is loading, its source is degraded or its inference allowance is exhausted
- **THEN** the current truthful state and Stop monitoring remain accessible through keyboard and labelled controls
- **AND** local interaction feedback appears within 100 milliseconds without falsely showing the request as committed

#### Scenario: Stop races with review work
- **WHEN** the owner stops monitoring while a review or callback is pending
- **THEN** the durable stop fence prevents later admission, publication and uncommitted effect dispatch within the declared control bound
- **AND** already committed receipts remain visible and existing independent flight/connection alerts are unaffected

#### Scenario: Repeated review requests remain one operation
- **WHEN** the owner repeats Review now for the same current composite revision
- **THEN** Travel returns the existing review/admission rather than creating duplicate reasoning
- **AND** a stale visible card cannot reactivate expired or stopped work

#### Scenario: Retention expiry cannot revive old work
- **WHEN** minimized feature details reach the admitted retention deadline
- **THEN** those details are removed under the proposed retention policy while independent audit/effect receipts retain their own policies
- **AND** stale source events remain history-only and cannot recreate an owner review after dedupe detail expires

#### Scenario: Renewal does not resurrect a terminal review
- **WHEN** the owner renews stopped or expired monitoring
- **THEN** a new enrollment generation requires fresh current source evaluation and creates a distinct review identity
- **AND** prior reviews remain terminal and all generations retain the same feature budgets and per-trip concurrency accounting

### Requirement: Later recovery choices bind exact approved effects
For the post-v1 `approved_choices` mode, Travel SHALL offer only exact message-draft preparation or a single Calendar occurrence change through adopted M7 decision sets and existing owner approval rules. Every item MUST bind current source/link/target revisions, exact arguments and recipients, expiry and its canonical effect identity; stale or unverifiable conditions MUST refuse dispatch rather than silently substitute a revised action.

ID: REQ-travel-disruption-review-007
Source: RFC 0038 D4, D12-D14 and S8
Scope: post-v1

#### Scenario: A chosen subset executes only exact current items
- **WHEN** the owner approves a subset of a future decision set whose exact calls, resources and recipients are shown
- **THEN** only selected current items can dispatch under their existing grants and approvals
- **AND** any changed itinerary, link, recipient, target version or expired item requires a new explicit proposal

#### Scenario: Conditional mutation support is absent
- **WHEN** the owning Calendar capability cannot enforce the expected occurrence version at its write boundary
- **THEN** the Calendar effect option remains unavailable rather than relying on a prior read to prevent a race

#### Scenario: Partial or ambiguous effects remain unresolved
- **WHEN** one approved effect commits and another fails or has unknown provider outcome
- **THEN** the review reports those separate receipt-backed outcomes and never claims all arrangements repaired
- **AND** unknown execution is not automatically replayed and a prepared message draft is never reported sent

## Source References

- `about/heart-and-soul/vision.md`, Rules 3-7: source ownership, deterministic infrastructure, operational tuning, domain scope and transport responsibility.
- RFC 0038, `about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md`, D4-D7, D10-D14, D19 and S2-S8: canonical grants, goals, budgets, effects, perception and owner delivery; this spec does not activate pending consumers.
- `openspec/changes/specify-realtime-feature-contracts/specs/event-response-contract/spec.md`, adopted REQ-event-response-contract-001 through 005.
- `openspec/specs/dashboard-design-language/spec.md`, Interaction Affordances, Interface Copy and Viewport and Modality Contract.
- `roster/travel/MANIFESTO.md`, complete trip timeline and notifications of itinerary changes.
