# Design — Telemetry Distillation Layer

The historical grounding — code
citations, per-source gap analysis (Home Assistant domain-retention gap,
OwnTracks place-clustering gap, the universal rollup/anomaly gap), the
adapter/rollup architecture, doctrine-compliance mapping, risks, and the
sequenced implementation-bead list — lives at:

`docs/plans/2026-07-06-telemetry-distillation-design.md`

The RFC 0038 conformance below governs reasoning execution and supersedes any
standalone model-call or domain-spawn mechanism in that historical plan. The
plan remains grounding for the deterministic adapters and rollup rules, not
an alternative authorization or rollout contract.

Summary of the retained load-bearing decisions:

1. **Reuse, don't reinvent projection infra.** Both new adapters
   (`home_assistant.sensor_activity`, `owntracks.place_cluster`) are plain
   `ProjectionAdapter` subclasses — same contract as every adapter in
   `src/butlers/chronicler/adapters/` today. No new adapter framework.
2. **The daily-rollup materializer is the one new piece of infrastructure.**
   It must call the exact same `aggregations.lane_for_activity`/
   `union_seconds` functions the live `aggregate/by-category` endpoint uses —
   never a parallel counting implementation — so the rollup and the live
   endpoint can never diverge (the class of bug bu-whhll.1 already fixed once
   for the KPI endpoint).
3. **Corroboration gates promotion to `layer=activity`,** reusing
   `reconciliation.py`'s existing merge seam. Raw HA sensor pings are
   `layer=evidence` until something independent corroborates them.
4. **Classify before flagging.** Every anomaly rule consults
   `source_adapter_state` before emitting a behavioral flag, so a known
   feeder outage produces `feeder_dark`, not a fabricated "no sleep" or "no
   presence" behavioral claim — the same distinction the fleet-wide
   degraded-source convention already requires of API fan-out endpoints,
   applied here to batch anomaly detection.
5. **Narration is an optional Chronicler obligation goal**, with at most one
   model call per local day over already-reduced rollup output, never per raw
   event (RFC 0014 §D5 and RFC 0038 D18). A goal is the authority/lifecycle
   boundary; it does not relax the original model-call bound.
6. **A genuinely new adapter-risk class**: this is the first time a
   Chronicler adapter reads a table with a rolling TTL
   (`connectors.filtered_events`, 12-month default retention) rather than a
   TTL-free connector table or its own schema. The HA sensor-activity
   adapter's implementation bead must include lag-vs-retention-cutoff
   monitoring; no prior adapter needed this.

## Narration admission and custody

The deterministic adapter, retention-lag, reconciliation, rollup and flag jobs
stay jobs (RFC 0038 D2). They gather the bounded reduced rollup/flag rows and
top episode titles before narration. No raw sensor/point payloads enter the
model input, and projection never invokes a model. Missing or unhealthy
sources still produce honest unavailable/`feeder_dark` results rather than
fabricated behavioral anomalies; optional narration cannot repair that absence.

When enabled and eligible, the scheduled day-close narration producer submits
an obligation admission for the closed local day. The obligation and its
capability set are declared in git; admission establishes origin, cause,
exposure, ceiling and budget from server-held context (D5). Repeated submission
of the same obligation occurrence returns the existing goal. Any caused
admission retains trusted effect lineage, inherited exposure and the D5
intersection with the causing origin's ceiling; neither input text nor a
model-selected label establishes authority.

The lead uses only the obligation's declared capabilities through the broker
and verified domain boundary (D4/D7). The declaration does not add a grant,
subscription or new owner-facing message here. Existing approved read
surfaces, tenant boundaries, privacy/retention metadata and provenance remain
binding. Source stamps and taint follow the reduced data into admission and
capability results: missing/unreadable stamps count as exposed, third-party
editable feed labels remain untrusted, exposure only increases, and exposed
writes retain taint (D6). Reduction or an obligation origin never upgrades
source content into owner authority.

D10's finite goal and invocation budgets, deadline and fail-closed accounting
apply in addition to the existing at-most-one model-call-per-local-day limit.
That limit covers every model turn/call, retry, replacement and recovery for
the represented local day; another goal or invocation cannot reset it. A
failed call consumes that day's call opportunity, so a second call cannot be
used to repair prose or complete a tool loop. Deterministic retries and
idempotent rollup recomputation do not authorize additional model calls.
Budget exhaustion, Stop, admission refusal, disablement or a failed/skipped
call leave deterministic rollups/flags correct and narration absent or retain
only an already-valid narrative; normal null narration is not source failure.
No domain daemon launches a reasoning session or calls a provider directly,
and narration is not a D18 model function.

## Delivery boundary

The existing task checkboxes record their original implementation history,
not proof of this goal-runtime conversion. Historical scope/sign-off tasks
remain unchecked; conforming text requires no new owner exact-text sign-off.
This amendment neither ticks implementation tasks nor changes RFC decisions,
archives the change, deletes old producers, applies schema/configuration,
wires subscriptions, or activates live obligations. Runtime conversion and
release evidence remain subject to the adopted one-release cutover gates and
the generalist change's task 4.2 collision reconciliation.
