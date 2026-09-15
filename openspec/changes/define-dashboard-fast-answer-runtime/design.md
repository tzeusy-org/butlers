## Context

The dashboard API, Switchboard, target butlers, and their spawners are separate processes. The
current durable turn control records message-scoped runtime sessions and makes Stop cross that
boundary through each owning butler's registered `cancel_session` MCP tool. The structured
classifier predates that control contract: it invokes provider adapters in process and then calls
resolved FastMCP `.fn()` handlers directly. Dashboard traffic is therefore excluded from that path.

The accepted question-lane delta adds Lane D and keeps domain questions on the normal
`answer_question(scope="domain")` to `route.execute` spine. RFC 0030 separately authorizes
Concierge to answer fleet-operational questions through its `dashboard_read` MCP tools backed by
two consumer-owned, column-allowlisted views. It does not authorize Switchboard to read those views,
import Concierge handlers, or execute domain logic.

Catalog search already exposes held-sensitivity-filtered provenance, but the delegation helper asks
for one hit and returns `(target, id, score)`. Its empty tuple means at least three different things:
an authoritative empty result, an unavailable memory hook, or an exception. A fast answer cannot
make a truthful decline until those states are distinct.

The dashboard SSE poller starts one 300-second timer while waiting for the persisted
`conversation_reply`. That timer observes the reply channel. The model catalog's `timeout_s`
controls a provider invocation. One expiring must not be reported as the other.

## Goals / Non-Goals

**Goals:**

- Remove CLI process startup from the successful system-plane dashboard answer path.
- Preserve one durable, cross-process, message-scoped Stop authority from before first invoke
  through reply settlement.
- Admit only a validated, side-effect-free system answer plan.
- Call only Concierge-owned registered read handlers through MCP under existing checks.
- Make catalog absence, catalog unavailability, selection evidence, and fallback points explicit.
- Give answer and non-answer turns honest reply-observation budgets without suppressing late replies.
- Preserve exact model, purpose, source, and latency attribution without content-bearing telemetry.

**Non-goals:**

- Token streaming, new tools, write fast paths, cross-schema shortcuts, handler imports, non-dashboard
  routing, approval-policy changes, provider selection changes, or deployment.
- Adopting draft PR #4056 or #4059, changing their approval state, or implementing their contracts.
- Replacing the existing durable turn projection or the foreign PR #3960 conversation identity work.

## Decision 1: Admit a narrow system-plane fast path in two phases

Structured classification becomes an admission phase. Its schema returns a lane, question scope,
candidate-owner confirmation, and bounded read plan. It does not expose or execute
`route_to_butler`, `file_bug_report`, `cannot_answer`, `conversation_reply`, or any other terminal
tool. A schema-valid result can enter the fast execution phase only when all of these are true:

1. source is `dashboard` with an immutable message and conversation identity;
2. the durable turn is active and not cancelling or terminal;
3. classification is Lane D and `scope="system"`;
4. catalog selection resolves Concierge under Decision 4;
5. the plan contains at most three calls and every planned tool belongs to the exact V1 allowlist in
   Decision 5, resolves in Concierge's live registry, and has its module enabled;
6. exact-cheap API catalog candidates for both classification and phrasing have been resolved with
   tier fallthrough and static fallback disabled; and
7. no direct tool, terminal reply, route, bug report, dead letter, or other effect has started.

The execution matrix is:

| Admission result | Direct fast execution | Required continuation |
|---|---:|---|
| Lane D, system, selected Concierge, valid read plan | yes | Concierge reads, one phrasing call, one idempotent reply |
| Lane D, domain | no | existing `answer_question(scope="domain")` to `route.execute` |
| Statement or action request | no | existing Spawner classification/routing path |
| Bug report | no | existing Spawner classification/QA path |
| Ambiguous classification | no | existing clarifying/dead-letter behavior |
| Invalid schema, absent exact-cheap API model, or unsupported direct runtime | no | existing Spawner classifier, before any fast tool call |
| Catalog unavailable | no | existing Spawner classifier or its existing fail-closed outcome |
| Write-capable, unregistered, disabled, or non-Concierge read plan | no | reject admission and use the pre-effect fallback |

A successful empty catalog result cannot terminate a statement, action, bug report, or ambiguous
turn. Only a validated Lane D question may use that evidence to call the existing `cannot_answer`
path, with zero `invoke_structured` calls made for ownership. No dashboard case falls back to General.

## Decision 2: One durable runtime identity spans every fast-answer phase

The fast path allocates one ordinary session UUID owned by Switchboard and registers it against the
dashboard message as phase `fast_answer` before catalog resolution or the first provider
invocation. The registration must atomically observe prior Stop. The same UUID covers catalog
resolution, structured classification, all Concierge read calls, answer phrasing, and reply
settlement; individual provider calls do not mint replacement runtime identities. If admission is
not eligible, the fast runtime releases before the existing Spawner continuation registers its own
session; any Stop that lands between them remains durable and is observed by the later registration.

Registration also creates durable liveness evidence specific to the fast runtime: a boot-scoped
`owner_instance_id`, monotonically increasing `lease_generation`, `heartbeat_at`,
`lease_expires_at`, nullable `reconcile_anchor_expires_at`, and nullable
`reconcile_deadline_at`. The initial lease lasts 60 seconds, and every successful heartbeat uses the
durable database clock to set a new `lease_expires_at = now() + 60 seconds`. The owning Switchboard
process refreshes at least every 20 seconds while invoke or reply work remains active. Registration
age is not an execution deadline: a healthy runtime may renew beyond minute 15 and until its separate
catalog provider budget or normal completion ends the work. Every heartbeat, phase advance, invoke
release, reply claim/receipt, and terminal transition presents the same owner instance and lease
generation. A stale predecessor cannot commit after a later generation wins.

After registration, the runtime claims the existing durable pre-invoke fence once for the whole
sequence. Switchboard also registers the live coroutine in its process-local cancellable-runtime
map under that session UUID. Its existing registered `cancel_session` MCP handler resolves both
Spawner-owned and fast-answer tasks. The dashboard API never reaches into Switchboard memory or
trusts its own process-local map.

The sequence is:

```text
durable turn already open
  -> create Switchboard session UUID
  -> durable register(message, session, owner=switchboard, phase=fast_answer,
       owner_instance_id, generation=1, lease_expires_at=db_now+60s)
  -> process-local cancellable registration
  -> durable claim_invoke(message, session)
  -> resolve exact-cheap API candidates for classification and phrasing
  -> typed catalog lookup
  -> structured admission provider call
  -> target-owned registered Concierge MCP reads
  -> one answer-phrasing provider call
  -> fenced idempotent conversation reply
  -> durable complete/release
  -> process-local unregister
```

Every transition after `claim_invoke` checks the durable cancellation state and presents the current
lease generation before beginning the next read, the phrasing call, or reply persistence. Cleanup
releases the invoke claim and unregisters the live task on success, deterministic failure, or
confirmed cancellation. A process crash may prevent cleanup; recovery handles that as Decision 3
specifies.

## Decision 3: Stop is a durable fence, not a client detach

The canonical message-scoped Stop records intent first, enumerates the durable session, and calls
Switchboard's existing `cancel_session` MCP boundary with the fast-answer session UUID. The handler
cancels the live provider/read/phrase coroutine and waits for it to unwind. A cancellation response
is confirmed only after the fast runtime has released every active invoke claim and persisted the
durable cancellation acknowledgement.

Race behavior is fixed:

- **Stop before invoke:** registration observes Stop or `claim_invoke` refuses; no provider or tool
  call starts.
- **Stop during classification:** the provider task is cancelled; no read or reply starts.
- **Stop during a read:** cancellation propagates through the MCP request; completion is not called
  cancelled until the request and invoke claim settle.
- **Stop between read and phrasing:** the next durable fence suppresses phrasing and reply.
- **Stop during phrasing:** the phrasing provider task is cancelled and no reply starts.
- **Stop between phrase completion and reply:** the reply claim loses to Stop and persists nothing.
- **Stop during an uncertain reply attempt:** the system reports pending reconciliation or ambiguity,
  never confirmed cancellation, until a durable reply receipt proves the outcome.
- **Repeated or concurrent Stop:** all callers converge on the same durable intent and terminal
  outcome; no second cancellation, reply, or replay is created.
- **Stop after durable completion:** the canonical endpoint reports the existing already-finished
  meaning and does not rewrite the answer.

Switchboard owns a supervised fast-runtime reconciler. It scans at startup and at most every 60
seconds. It may inspect only a row whose durable 60-second lease has expired. Lease expiry,
registration age, or a changed process instance is never itself proof that the predecessor died,
stopped, failed, or completed. The reconciler first reads durable runtime, Stop, and deterministic
reply-receipt evidence in the same transaction used to claim.

Heartbeat renewal and reconciliation claim are reciprocal conditional writes over the exact
`owner_instance_id`, `lease_generation`, and `lease_expires_at`. A heartbeat may renew only while
its generation still owns the row and its lease is not expired under the database clock. If it wins
before expiry, a claim using the old expiry fails. A reconciler may claim only while the exact
predecessor generation and expiry remain current and expired. If it wins, it advances the generation
and atomically copies that predecessor's last durable `lease_expires_at` into immutable
`reconcile_anchor_expires_at = L`, then writes `reconcile_deadline_at = D = L + 15 minutes`. If an
anchor already exists for that fenced generation, repeated sweeps and replacement reconcilers reuse
it; they never recompute either value from claim time, scan time, startup time, or the current clock.

The winning generation fences a merely partitioned predecessor: if the old process later regains the
database, its stale heartbeat, phase, reply, and completion writes fail, and it must cancel its local
work. The reconciler performs observation and classification only. On every sweep, including the
deadline sweep, a proven reply receipt completes the turn, a proven cancellation acknowledgement
confirms cancellation, and a proven deterministic failure records failure according to the existing
durable precedence. Lease expiry or age never overwrites a receipt.

With no conclusive receipt, the turn remains `pending_reconciliation` (or
`pending_cancellation` after Stop) before `D`. At the first successful sweep at or after `D`, it
becomes `ambiguous` with reason `fast_answer_runtime_outcome_unknown`. Because sweeps are at most 60
seconds apart, the earliest ambiguity is `D` and the latest is `D + 60 seconds` when the Switchboard
supervisor runs and the durable store accepts the scan/claim/read/write transactions continuously
from `L` through `D + 60 seconds`. The total bound after the last accepted heartbeat is therefore at
most 60 seconds of remaining lease, 15 minutes of reconciliation budget, and 60 seconds of scan
latency. It does not bound healthy execution.

If the durable store is unavailable around lease expiry or `D`, the system cannot promise a timely
durable transition. It preserves the last readable pending/unavailable state and never fabricates
completion, confirmed cancellation, or ambiguity. On recovery, the next successful startup/periodic
sweep (no later than 60 seconds after availability returns while the supervisor and store remain
available) captures or reuses the original `L`, checks receipts first, and immediately applies the
already-expired `D` when applicable. An outage, restart, or repeated sweep cannot extend the budget.
The reconciler never invokes a provider, repeats a Concierge read, persists a reply, or calls the
runtime cancelled by inference. A late provider response cannot cross the superseded generation or
reply fence.

## Decision 4: Catalog resolution is typed and selection is deterministic

The fast-answer call site consumes one of:

- `matched(candidates, selected_owner)`: one to three ordered candidates survived server-held
  sensitivity filtering and validation;
- `no_match`: the catalog hook, authority source, embedding/search path, and query all succeeded,
  and zero eligible candidates remained; or
- `unavailable(reason_code)`: the hook is absent, held authority cannot be loaded, a dependency or
  query fails, or returned evidence is malformed.

Each candidate contains only `catalog_id`, resolved owner (`source_butler`, falling back to
`source_schema`), `source_schema`, `source_table`, `source_id`, `ranking_method="rrf"`, `rrf_score`,
`semantic_rank`, and `keyword_rank`. The list excludes canonical memory content and stored
sensitivity. These fields are provenance and ranking evidence; they do not grant a canonical fetch
or MCP invocation.

The proposed selected-hit rule is deliberately non-probabilistic:

1. Request catalog `limit=3`, then validate the response envelope and every returned candidate before
   selection. A count above three, missing resolved owner, missing provenance field/rank/finite score,
   or unsupported envelope shape poisons the entire response to `unavailable`.
2. Preserve every returned candidate and catalog order; no candidate is discarded or reordered. A
   well-formed owner outside the current eligible roster remains visible as provenance but cannot be
   selected.
3. Return `no_match` only when the underlying search and held-authority path were authoritative and
   returned zero candidates. Any malformed envelope or candidate returns `unavailable`.
4. Select the first candidate's owner when there is exactly one wholly valid candidate and that
   owner is currently eligible.
5. With two or three wholly valid candidates, select the first owner's value only when the first two candidates
   name the same owner and every candidate tied at the highest score names that owner.
6. A cross-owner top-two result, cross-owner top-score tie, or well-formed but ineligible selected
   owner yields `matched(..., selected_owner=null)`. Numeric score margin never overrides
   disagreement. Malformed evidence never yields `matched`.

Before preselection is enabled, this rule runs against a fixed, labeled, seeded catalog corpus and
records corpus digest, result-limit, rule version, selected coverage, wrong-owner count, per-owner
breakdown, and confusion matrix. A wrong-owner selected hit blocks enablement; engineering may
tighten the rule without owner intervention, but broadening it requires a new proposal. This is
calibration/verification of a deterministic rule, not conversion of RRF into confidence.

## Decision 5: An exact V1 allowlist supplies executable tool authority

Current `ToolMeta` describes argument sensitivity only, and `DashboardReadModule` publishes no
`tool_metadata()`. Neither is effect authority. This proposal instead introduces one checked-in
`FAST_ANSWER_CONCIERGE_TOOLS_V1` grant map. Every entry has fixed
`target="concierge"`, `module="dashboard_read"`, and `effect="read"`, keyed by exactly these names:

- `dashboard_read_fleet_status`
- `dashboard_read_butler_detail`
- `dashboard_read_sessions_recent`
- `dashboard_read_session_detail`
- `dashboard_read_sessions_aggregate`
- `dashboard_read_sessions_trigger_breakdown`
- `dashboard_read_fleet_errors_recent`
- `dashboard_read_fleet_search`
- `dashboard_read_timeline_recent`
- `dashboard_read_butler_activity`
- `dashboard_read_spend_summary`
- `dashboard_read_spend_daily`
- `dashboard_read_spend_top_sessions`
- `dashboard_read_spend_breakdown_by_butler`
- `dashboard_read_spend_breakdown_by_model`
- `dashboard_read_insight_delivery_state`

The executable eligible set is the intersection of:

- this exact checked-in V1 set;
- handlers currently registered by the Concierge FastMCP server;
- Concierge's enabled `dashboard_read` module state; and
- the existing accepted `butler-concierge`, `module-dashboard-read`, and RFC 0030 contracts, which
  require every named module tool to be a read through the sanctioned views/public read source.

The executor validates the grant map before admission. A missing, duplicate, malformed, or
contradictory target/module/effect entry disables fast admission fail-closed. An allowlisted name
missing from live registration is unavailable, and an additional registered
`dashboard_read_*` name is ineligible until an owner-approved amendment broadens V1. Disabled module
state, a name outside V1, a registered-name mismatch, or a future contradiction with the accepted
read-only module/RFC contracts rejects the whole proposed plan before its first read. Emergency or
security narrowing may disable a name; it cannot silently broaden the set. `ToolMeta`, discovery,
and visibility metadata may narrow presentation but cannot grant invocation authority.

Every call crosses the registered Switchboard-to-Concierge MCP path and therefore retains schema
validation, module-state, schema-role, transport, call-time authorization, and future middleware
checks. Switchboard does not import Concierge code, call `.fn()`, query a Concierge view, or
dereference a catalog pointer. An admitted plan contains at most three calls; an overlong plan is
invalid before any read starts.

The answer model receives only validated read results and their required `source` envelopes.
Server code derives the persisted `sources` list from those envelopes; model prose cannot invent or
expand it. A disabled module, unregistered name, write risk, unavailable target, source-envelope
failure, or MCP error prevents reply success. Once the first read starts, the whole turn is not
blindly restarted through the CLI path.

## Decision 6: Reply completion is one-shot and restart-safe

The fast runtime claims a deterministic answer-reply identity derived from the immutable dashboard
message before it persists the answer. An identical repeat returns the existing reply; a changed
payload conflicts; an indeterminate write is reconciled from the durable receipt and is never
replayed on absence of process-local state. Exactly one assistant reply can complete one fast-answer
turn.

The reply goes through the existing registered conversation-reply contract and preserves its
conversation validation and source rules. Fast execution does not add a direct database or handler
bypass. Durable completion occurs only after the reply receipt is known. If Stop already won the
reply claim, no reply is persisted. If the reply won first, a later Stop observes the completed turn.

## Decision 7: Intent lane controls observation, not execution

Persist a monotonic `intent_lane` separate from `target_kind` with values `answer` and
`non_answer`. The validated classifier records `answer` before the first fast read or domain-answer
dispatch. It records `non_answer` for a definitive statement, action, or bug lane before dispatch.
Ambiguous, not-yet-classified, and legacy turns may remain null. Once non-null, the value cannot
change for the immutable message.

SSE chooses its deadline from the durable lane on every safe observation:

| Durable intent lane | Reply-observation window |
|---|---:|
| `answer` | 45 seconds |
| `non_answer` | 300 seconds |
| null, unknown, or legacy | 300 seconds |

Both deadlines start at the existing reply-observation `start_ts`; learning the lane does not reset
or extend the timer. `SESSION_TIMEOUT` text names `answer` or `non-answer/unknown` and states that
the stream was waiting for an in-thread reply. The timeout emits `done` and closes only that SSE
connection. It does not call Stop, alter the turn/conversation, claim provider timeout, mark failure,
delete draft/attempt state, or authorize retry. A reply persisted later remains visible through the
normal message history and unread refresh.

## Decision 8: Model resolution is exact-cheap and call bounds are literal

Before any provider or Concierge read, the fast path resolves both phase candidates through the
existing model catalog with requested tier `cheap`, `allow_tier_fallthrough=false`, and distinct
phase intents. Both candidates must be catalog-backed `runtime_type="api"` entries with effective
tier `cheap`. No matching exact-tier entry, quota denial, a non-API entry, a non-cheap fallthrough,
or static fallback makes the fast path ineligible before effects and hands the turn to the existing
Spawner. Static configuration is never a fast-answer model authority.

Classification uses a structured-output, no-terminal-tool dispatch intent and records purpose
`dashboard_fast_answer_classification`. It makes one normal provider attempt and permits only the
existing one schema-invalid retry against that same candidate. A provider/runtime failure does not
try another catalog candidate in the fast path; because no read has started, it releases the fast
runtime and uses the existing Spawner continuation. Phrasing uses the frozen validated read results,
records purpose `dashboard_fast_answer_phrasing`, and makes exactly one provider call with no retry
or provider failover.
These remain ephemeral LLM operations. Deterministic daemon code owns registration, validation,
fencing, dispatch, attribution, and settlement; it does not retain or perform model reasoning.

Each actual attempt records requested tier `cheap`, effective tier `cheap`, resolution source
`catalog`, selected runtime type, model ID, catalog entry, provider execution timeout, token usage,
duration, and outcome under the shared fast-runtime
and dashboard request identities. Content-bearing prompt, tool arguments/results, and answer text do
not enter attribution telemetry. Catalog `timeout_s` remains that phase's provider execution budget;
it is never replaced by the 45-second SSE observation window.

The normal successful path uses one classification call, zero CLI spawns, at most three registered
read calls from the admitted plan, one phrasing call, and one idempotent reply. The existing single
same-candidate schema-invalid classification retry may run before any read, producing exactly two
attributed classification attempts; exhaustion falls back to the existing Spawner path. Provider
failover is disabled for both fast phases. After a read starts, no fallback may re-execute the whole
turn.

## Decision 9: Performance evidence stays hermetic by default

The required slow benchmark runs 20 representative seeded system-plane questions against a seeded
database with a fixed-latency stub model adapter. It records the stub latency, sample count, per-run
wall time, p50, p95, provider-call count, registered tool-call count, and CLI-spawn count. The p95
target is `<3s`. The test remains marked slow and may be skipped in normal CI; its latest result is
recorded in the future implementation PR body. It proves orchestration overhead, bounded calls, and
absence of CLI startup under the declared stub; it is never labeled live-provider latency.

A live-provider benchmark is optional. It may run only under separate authorization and, if run,
records sample size, date, runtime/model/catalog tuple, execution-timeout tuple, p50, p95, and
failures separately from hermetic evidence. Its absence does not block this contract or future
implementation, and its result cannot be inferred from the stub.

## Failure Matrix

| Failure point | Durable interpretation | Fallback/replay rule | Owner-visible result |
|---|---|---|---|
| Catalog authoritative empty | no owner evidence | only Lane D may decline; no General route | honest `cannot_answer` reply/dead letter |
| Catalog malformed/hook/authority unavailable | evidence unavailable | poison whole catalog result; pre-effect Spawner fallback | existing fallback/error behavior |
| V1 grant map malformed or contradictory | tool authority unavailable | disable fast admission before any read | existing Spawner behavior |
| Exact-cheap API candidate absent/non-cheap/static | fast model ineligible | pre-effect existing Spawner fallback | existing lane result |
| Admission provider failure | no admitted plan | no fast provider failover; pre-effect Spawner fallback | existing lane result |
| Admission schema invalid twice | no admitted plan | one same-candidate retry, then pre-effect Spawner fallback | existing lane result |
| Stop before invoke | cancelled | no provider/tool/reply | confirmed Stop after release |
| Stop during provider/read | cancelling until settled | no whole-turn replay | cancelled or ambiguity if unprovable |
| Concierge unavailable before first read | fast path unavailable | no direct read; existing fail-closed answer behavior | honest unavailable/decline |
| Read fails after any read began | fast runtime failed | no CLI restart or repeated plan | sourced failure/decline, or ambiguity if uncertain |
| Phrasing fails | fast runtime failed | no second phrasing call | honest failure without fabricated answer |
| Reply write conflicts | canonical reply wins | no changed overwrite | existing reply or structured conflict |
| Reply write outcome unknown | pending reconciliation/ambiguous | receipt lookup only; no replay | durable unknown outcome |
| SSE reaches 45/300 seconds | observation ended | runtime continues; Stop remains available | lane-named timeout; thread stays open |
| Healthy runtime passes registration minute 15 | live lease remains authoritative | continue normal execution; no reconciliation | active until normal provider/completion outcome |
| Runtime lease expires around a sweep | predecessor liveness unproven | claim exact expiry/generation; capture immutable `L`; inspect receipts | takeover between `L` and `L+60s` under availability |
| Heartbeat races takeover | one conditional write wins | renewal blocks stale claim, or generation fence blocks stale heartbeat | active under renewal or pending reconciliation under takeover |
| Reconciler restarts/repeats | captured `L` and `D` remain durable | reuse anchor/deadline; no replay or extension | unchanged pending or one terminal outcome |
| Durable store unavailable around `D` | durable outcome unavailable | preserve pending/unavailable; receipt-first next sweep after recovery | no false timely terminal claim |
| Runtime process crashes active | outcome unproven | startup/60s reconciler; `D=L+15m`; no replay | receipt-backed result or ambiguity in `[D,D+60s]` under availability |

## Compatibility and Sequencing

The delta is additive and leaves active whole-requirement bodies untouched. Future implementation
must first consume the landed durable dashboard turn projection and message-scoped Stop API. It must
then widen the durable session phase, intent-lane representation, fenced runtime lease, immutable
expired-lease reconciliation anchor/deadline, and answer receipt through a new cumulative core
migration and real-Postgres replay tests; no current migration is edited.

PR #3960 changes dashboard conversation identity and API/query seams. Implementation must serialize
behind its landed result, re-read the canonical baseline, and rederive affected SQL, API, and tests.
Draft PR #4059 proposes a separate content-blind exact-message read dependent on the same durable
projection; this capability does not consume or define that resolver. Draft PR #4056 consumes that
resolver for browser attempts; this capability does not adopt its client policy.

If `add-runtime-tool-surface-discovery` lands first, implementation may use its registered-handler
projection to enumerate candidates. It must still apply this capability's registered-target,
module-state, read-only, and call-time checks. Presentation remains guidance, not authority.

## Verification Strategy

Future implementation must cover the scenario matrix at these existing seams:

- admission, provider-call bounds, fallback, and no CLI spawn:
  `roster/switchboard/tests/test_structured_classify.py` and
  `tests/modules/test_module_pipeline.py`;
- durable registration, invoke fencing, release, crash recovery, and idempotence:
  `tests/core/test_dashboard_turns.py`, `tests/core/test_core_spawner.py`, and a new real-Postgres
  migration/replay test beside `tests/config/test_dashboard_turn_cancellation_migration.py`;
- cross-process Stop and every race:
  `tests/api/test_dashboard_turn_cancellation.py`;
- typed catalog outcomes, held filtering, candidate provenance, and selected-hit policy:
  `tests/core/test_delegation_ledger.py` and `tests/modules/test_module_pipeline.py`;
- registered Concierge MCP reads, disabled/unregistered/write rejection, source envelopes, and
  database-role enforcement: `roster/concierge/tests/test_dashboard_read.py` plus Switchboard
  structured-classifier integration coverage;
- 45/300-second observation, legacy/null compatibility, timeout copy, and late replies:
  `tests/api/test_conversations.py`; and
- deterministic reply claim/reconciliation and model-purpose attribution: core/API unit tests plus
  real-Postgres transaction and crash-boundary coverage.

Every failure-matrix row must have a named test. Hermetic benchmark output and any separately
authorized live evidence must be reported as different evidence classes.

## Owner Review Gate

The exact product choices requiring approval are the twelve numbered decisions in `proposal.md`.
Routine implementation choices may be resolved within those constraints. Any proposal to widen the
eligible lanes or butlers, execute writes, weaken the selected-hit rule, bypass MCP, reinterpret an
SSE timeout as cancellation/failure, replay uncertain work, or require live-provider evidence needs
a new owner-approved amendment.

No implementation work begins until the owner approves this exact draft digest or a reviewed
successor.
