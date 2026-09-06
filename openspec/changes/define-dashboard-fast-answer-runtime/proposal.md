# Define Dashboard Fast-Answer Runtime

**Status: DRAFT — UNAPPROVED**

This proposal records product choices for owner review. It grants no implementation,
migration, runtime, provider, deployment, archive, or merge authority.

## Why

Dashboard questions currently take the same CLI-spawn route used for statement and action work so
that message-scoped Stop can address a durable runtime. That preserves cancellation truth, but it
also adds two cold process starts to a watched read-only interaction. The accepted question-lane
contract now distinguishes system-plane questions, and RFC 0030 gives Concierge a narrow,
read-only, source-attributed system telemetry surface. The missing contract is a fast execution
path that uses those existing authorities without weakening Stop, tool ownership, replay safety,
or reply honesty.

The current implementation cannot safely be widened by removing the dashboard exclusion:

- its structured classifier executes selected tools immediately, so it cannot first prove that a
  turn is an eligible read-only system question;
- only Spawner-managed tasks are registered behind the cross-process `cancel_session` boundary;
- catalog resolution returns one nullable tuple and collapses an empty search, an unavailable
  memory module, and a search failure;
- durable `target_kind` does not distinguish answer observation from non-answer observation; and
- the single 300-second SSE reply window is an observation budget, while model catalog timeouts
  are execution budgets. Conflating them would fabricate cancellation or failure.

## What Changes

- Add a dashboard-only fast-answer capability for a valid Lane D, system-plane question whose
  selected owner is Concierge and whose planned tools are already registered in Concierge's
  `dashboard_read` read-only surface.
- Split structured classification from terminal execution. Admission returns a typed lane, scope,
  owner decision, and a plan of at most three reads without invoking any tool. Only the admitted
  fast-answer phase may call reads and phrase one answer.
- Give the whole classify/read/phrase operation one Switchboard-owned durable runtime identity.
  Register it before the first provider invocation, claim the existing pre-invoke fence, and make
  the live task addressable through Switchboard's existing `cancel_session` MCP boundary.
- Replace the fast-answer call site's nullable catalog tuple with `matched`, `no_match`, and
  `unavailable` outcomes. A match carries at most three ordered, provenance-bearing candidates and
  an explicit selected-hit rule; its RRF score remains ranking evidence, not confidence.
- Persist a monotonic dashboard intent lane separate from `target_kind`. `answer` uses a 45-second
  SSE reply-observation window; `non_answer`, unknown, and legacy/null use 300 seconds. Closing an
  SSE stream never cancels or fails the runtime, and a late reply remains visible.
- Resolve classification and phrasing through the model catalog with distinct purpose attribution.
  Keep the original hermetic 20-question fixed-latency-stub benchmark and its `<3s` p95 target.
  Keep it marked slow and eligible to skip in normal CI, and record its result in the future
  implementation PR body. Optional live evidence is a separate authorized activity and is never
  inferred from a stub.

## Capabilities

### New Capabilities

- `dashboard-fast-answer`: Defines dashboard fast-path admission, one durable Stop-addressable
  runtime, typed catalog evidence, target-owned MCP reads, lane-aware SSE observation, replay-safe
  reply completion, model attribution, and latency evidence.

### Modified Capabilities

None. The delta uses uniquely named ADDED requirements and does not replace the active whole-body
requirements for Switchboard classification lanes, dashboard reply streaming, or durable turn
recovery.

## Impact

Future implementation will touch the Switchboard pipeline and structured classifier, dashboard
turn control and its real-Postgres schema/functions, the dashboard conversation SSE poller, catalog
resolution, Concierge MCP dispatch, answer-reply idempotency, model/token attribution, and their
owning tests. No implementation is part of this proposal.

This capability is constrained by:

- `add-dashboard-question-lane`: domain questions continue through
  `answer_question(scope="domain")` and `route.execute`;
- `durable-dashboard-terminal-action-recovery`: implementation must consume the landed durable
  turn projection and message-scoped Stop semantics, then rederive any overlapping schema or SQL
  function changes;
- RFC 0030 and `butler-concierge`: system-plane data remains owned by Concierge and is read only
  through its registered MCP tools and sanctioned views;
- `memory-discovery-catalog`: held sensitivity is server-derived and candidate provenance never
  grants canonical read authority;
- `add-runtime-tool-surface-discovery`: if it lands first, its projection may narrow presentation,
  but visibility metadata still cannot grant invocation authority; and
- open PR #3960: future implementation must serialize its dashboard conversation/API/schema work
  behind the landed foreign head and rederive affected seams.

Open draft PR #4056 (browser-local client policy) and draft PR #4059 (content-blind message-attempt
resolver) remain unapproved. This proposal neither adopts their choices nor makes them
implementation dependencies. Their shared dependency on the durable owner-facing turn projection
must still be honored independently.

## Explicit Non-Goals

- Token-level streaming.
- New question-lane, Concierge, catalog, or write tools.
- In-process write-tool execution or a mutation fast path.
- Direct cross-schema reads, direct imports of another butler's handlers, or direct `.fn()` handler
  calls that bypass the registered MCP boundary.
- Changes to non-dashboard routing, approval policy, provider configuration, or deployment.
- Treating tool visibility, catalog provenance, or RRF rank as authorization.
- Treating catalog unavailability as proof that no owner exists.
- Replaying an uncertain runtime, reply write, route, or tool attempt.
- Making a live-provider latency run mandatory or treating hermetic stub timing as live proof.

## Proposed Owner Decisions

Owner approval is requested for this exact set of choices:

1. Limit the initial fast path to dashboard Lane D system-plane questions selected for Concierge;
   domain questions keep the accepted `answer_question` to `route.execute` path.
2. Require side-effect-free structured admission before any terminal tool execution.
3. Use one Switchboard-owned `fast_answer` runtime identity across classification, Concierge reads,
   answer phrasing, and reply settlement, registered durably before the first provider call.
4. Address Stop through the existing message-scoped dashboard API and Switchboard's registered
   `cancel_session` MCP tool; confirm cancellation only after all invoke claims are released.
5. Allow at most three target-owned read calls, each to a tool that is both currently registered and in Concierge's
   `dashboard_read` read-only projection, under their existing module, schema-role, validation,
   and call-time checks.
6. Use typed catalog outcomes with up to three provenance-bearing candidates; allow preselection
   only under the deterministic same-owner rule in `design.md`, and never call RRF score confidence.
7. Treat an authoritative empty catalog result as `cannot_answer` only after Lane D classification;
   make zero `invoke_structured` ownership calls in that no-match branch, and treat catalog
   unavailability as a pre-effect fallback condition, never as no match.
8. Persist `answer` versus `non_answer` separately from `target_kind`; use 45 seconds versus 300
   seconds from the existing SSE observation start, with legacy/null retaining 300 seconds.
9. Permit an SSE observation timeout to close only that stream. It does not cancel the runtime,
   fail the conversation, suppress a late reply, or license replay.
10. Resolve both provider phases through the model catalog at the cheap tier with distinct purpose
    attribution, and retain the hermetic fixed-latency-stub `<3s` p95 benchmark as the required
    performance gate, marked slow/may-skip-CI with its result in the implementation PR body. Any live
    p95 run remains optional and separately authorized.

Until the owner approves the exact artifact digest or commit, this change remains a draft and
`bu-0ynlk.6` remains blocked from implementation.
