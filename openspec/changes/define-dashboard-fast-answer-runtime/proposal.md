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
  Register it before catalog or provider work, claim the existing pre-invoke fence, heartbeat a
  fenced 60-second lease at least every 20 seconds, and make the live task addressable through
  Switchboard's existing `cancel_session` MCP boundary. A startup/60-second reconciler preserves
  receipts and, after lease takeover, anchors a 15-minute ambiguity budget to the predecessor
  generation's last durable lease expiry without imposing an execution-duration limit or replay.
- Replace the fast-answer call site's nullable catalog tuple with `matched`, `no_match`, and
  `unavailable` outcomes. A match carries at most three ordered, provenance-bearing candidates and
  an explicit selected-hit rule; any malformed returned candidate poisons the whole result to
  `unavailable`, and RRF score remains ranking evidence, not confidence.
- Persist a monotonic dashboard intent lane separate from `target_kind`. `answer` uses a 45-second
  SSE reply-observation window; `non_answer`, unknown, and legacy/null use 300 seconds. Closing an
  SSE stream never cancels or fails the runtime, and a late reply remains visible.
- Resolve classification and phrasing from exact `cheap` API catalog entries with tier fallthrough
  and static fallback disabled, and with distinct purpose attribution. Resolve both before reads;
  fast admission does not provider-failover, while its one same-candidate schema retry remains.
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

Owner approval is requested for this exact set of twelve choices:

1. Limit the initial fast path to dashboard Lane D system-plane questions selected for Concierge;
   domain questions keep the accepted `answer_question` to `route.execute` path.
2. Require side-effect-free structured admission, cap its admitted plan at three reads, and execute no
   terminal tool before admission succeeds.
3. Use one Switchboard-owned `fast_answer` runtime identity across catalog resolution,
   classification, Concierge reads, answer phrasing, and reply settlement. Register it before
   catalog/provider work with a 60-second fenced lease, heartbeat at least every 20 seconds, and
   reconcile on startup and at most every 60 seconds. A healthy runtime may renew for its full
   execution duration regardless of registration age. On expired-lease takeover, atomically capture
   that predecessor generation's last durable `lease_expires_at` as immutable anchor `L` and set
   `D = L + 15 minutes`; neither restarts nor repeated sweeps may move `L` or `D`. Lease expiry alone
   is not death or Stop proof; a newly claimed generation fences a partitioned predecessor. Under a
   running supervisor and continuously writable durable store from `L` through `D + 60 seconds`, the
   first scan at or after `D` preserves any proven receipt or records
   `fast_answer_runtime_outcome_unknown` ambiguity between `D` and `D + 60 seconds`, without replay.
   A storage outage suspends only that wall-clock promise; recovery settles receipt-first within the
   next 60-second sweep from the original `L` and `D`.
4. Address Stop through the existing message-scoped dashboard API and Switchboard's registered
   `cancel_session` MCP tool; confirm cancellation only after all invoke claims are released.
5. Authorize only the exact checked-in V1 tool-name allowlist in `design.md`, intersected with
   Concierge's enabled `dashboard_read` module and live target registration. The accepted Concierge,
   module-dashboard-read, and RFC 0030 contracts provide the read-only guarantee; `ToolMeta`, tool
   discovery, and visibility metadata provide no effect authority. Broadening V1 requires an
   owner-approved amendment.
6. Use typed catalog outcomes with up to three provenance-bearing candidates. Any malformed
   returned envelope or candidate makes the whole result `unavailable`; no record is discarded to
   strengthen selection. For a wholly valid match, allow preselection only under the deterministic
   same-owner rule in `design.md`, and never call RRF score confidence.
7. Treat an authoritative empty catalog result as `cannot_answer` only after Lane D classification;
   make zero `invoke_structured` ownership calls in that no-match branch, and treat catalog
   unavailability as a pre-effect fallback condition, never as no match.
8. Persist `answer` versus `non_answer` separately from `target_kind`; use 45 seconds versus 300
   seconds from the existing SSE observation start, with legacy/null retaining 300 seconds.
9. Permit an SSE observation timeout to close only that stream. It does not cancel the runtime,
   fail the conversation, suppress a late reply, or license replay.
10. Resolve both phase candidates before reads by requesting exact `cheap` entries with
    `allow_tier_fallthrough=false`; require `runtime_type="api"`, and treat no exact candidate,
    non-API selection, quota denial, non-cheap fallthrough, or static fallback as pre-effect fast-path
    ineligibility that continues through the existing Spawner.
11. Make one normal admission provider call, permit only its existing one same-candidate
    schema-invalid retry, and prohibit provider failover in the fast path. After reads begin, make
    exactly one phrasing call with no retry/failover; record every actual call under its distinct
    phase purpose and shared runtime.
12. Retain the hermetic fixed-latency-stub `<3s` p95 benchmark as the required performance gate,
    marked slow/may-skip-CI with its result in the implementation PR body. Any live p95 run remains
    optional and separately authorized.

## Review Correction Risk Delta

This correction makes four material choices explicit for owner review:

- Tool authority is a checked-in exact V1 name set backed by the accepted read-only module/RFC
  contracts, rather than nonexistent `ToolMeta` effect metadata. The benefit is executable authority;
  the cost is that adding a new fast-answer tool requires an approved allowlist amendment.
- Any malformed catalog candidate poisons the whole result to `unavailable`. This gives up partial
  selection when one row is corrupt so incomplete evidence can never strengthen an owner choice.
- Fast runtimes add durable instance/generation/lease/heartbeat evidence and a bounded operational
  reconciler. The ambiguity budget begins at an expired generation's last durable lease expiry, so a
  healthy runtime may renew beyond minute 15 while a crashed runtime still converges under explicit
  lease, scan, and durable-store availability bounds. This is more schema and operational machinery,
  but it closes the forever-live invoke and unacknowledgeable Stop failure without creating an
  execution timeout or a restart-extendable deadline.
- Fast model resolution is exact-cheap and catalog-only, with no provider failover. This sacrifices
  fast-path availability when cheap API capacity is absent or fails, while preserving predictable
  latency/cost, an executable call bound, and safe pre-effect fallback to the existing Spawner.

Until the owner approves the exact artifact digest or commit and all twelve choices above, this
change remains a draft and `bu-0ynlk.6` remains blocked from implementation.
