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
5. the plan contains at most three calls and every planned tool resolves to a currently registered
   Concierge `dashboard_read` read handler;
6. the selected runtime supports the structured admission and phrasing calls; and
7. no direct tool, terminal reply, route, bug report, dead letter, or other effect has started.

The execution matrix is:

| Admission result | Direct fast execution | Required continuation |
|---|---:|---|
| Lane D, system, selected Concierge, valid read plan | yes | Concierge reads, one phrasing call, one idempotent reply |
| Lane D, domain | no | existing `answer_question(scope="domain")` to `route.execute` |
| Statement or action request | no | existing Spawner classification/routing path |
| Bug report | no | existing Spawner classification/QA path |
| Ambiguous classification | no | existing clarifying/dead-letter behavior |
| Invalid schema or unsupported direct runtime | no | existing Spawner classifier, before any fast tool call |
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

After registration, the runtime claims the existing durable pre-invoke fence once for the whole
sequence. Switchboard also registers the live coroutine in its process-local cancellable-runtime
map under that session UUID. Its existing registered `cancel_session` MCP handler resolves both
Spawner-owned and fast-answer tasks. The dashboard API never reaches into Switchboard memory or
trusts its own process-local map.

The sequence is:

```text
durable turn already open
  -> create Switchboard session UUID
  -> durable register(message, session, owner=switchboard, phase=fast_answer)
  -> process-local cancellable registration
  -> durable claim_invoke(message, session)
  -> typed catalog lookup
  -> structured admission provider call
  -> target-owned registered Concierge MCP reads
  -> one answer-phrasing provider call
  -> fenced idempotent conversation reply
  -> durable complete/release
  -> process-local unregister
```

Every transition after `claim_invoke` checks the durable cancellation state before beginning the
next read, the phrasing call, or reply persistence. Cleanup releases the invoke claim and unregisters
the live task on success, deterministic failure, or confirmed cancellation. A process crash may
prevent cleanup; recovery handles that as Decision 3 specifies.

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

If the process or transport dies with an invoke or reply attempt in an unprovable state, recovery
uses durable evidence to preserve a proven result or records ambiguity. It never reconstructs the
prompt, reruns classification, repeats a read plan, rephrases, or reissues reply persistence merely
because the live task is gone. A late provider response cannot cross a lost invoke/reply fence.

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

1. Reject malformed candidates, missing owners, non-finite scores, and owners outside the current
   eligible roster before ranking.
2. Keep catalog order and at most the first three valid candidates.
3. If no candidate remains, return `no_match` only when the underlying search was authoritative;
   otherwise return `unavailable`.
4. Select the first candidate's owner when there is exactly one valid candidate.
5. With two or three candidates, select the first owner's value only when the first two candidates
   name the same owner and every candidate tied at the highest score names that owner.
6. A cross-owner top-two result, cross-owner top-score tie, missing score, or malformed provenance
   yields `matched(..., selected_owner=null)`. Numeric score margin never overrides disagreement.

Before preselection is enabled, this rule runs against a fixed, labeled, seeded catalog corpus and
records corpus digest, result-limit, rule version, selected coverage, wrong-owner count, per-owner
breakdown, and confusion matrix. A wrong-owner selected hit blocks enablement; engineering may
tighten the rule without owner intervention, but broadening it requires a new proposal. This is
calibration/verification of a deterministic rule, not conversion of RRF into confidence.

## Decision 5: Concierge remains the owner and MCP remains the call boundary

The eligible read projection is the intersection of:

- handlers currently registered by the Concierge FastMCP server;
- tools belonging to its enabled `dashboard_read` module/group;
- tools declared read-only under the existing tool metadata; and
- a bounded fast-answer allowlist derived from those registered names.

Missing, stale, or contradictory metadata excludes a tool. Discovery or visibility metadata may
narrow presentation but cannot add a handler or grant invocation authority. Every call crosses the
registered Switchboard-to-Concierge MCP path and therefore retains schema validation, module-state,
schema-role, transport, call-time authorization, and future middleware checks. Switchboard does not
import Concierge code, call `.fn()`, query a Concierge view, or dereference a catalog pointer.
An admitted plan contains at most three calls; an overlong plan is invalid before any read starts.

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

## Decision 8: Model resolution and attribution remain explicit

The initial system-plane fast path resolves both provider phases through the existing model catalog
at the cheap tier. Classification uses a structured-output, no-terminal-tool dispatch intent and
records purpose `dashboard_fast_answer_classification`. Phrasing uses the frozen validated read
results, records purpose `dashboard_fast_answer_phrasing`, and makes exactly one provider call.
These remain ephemeral LLM operations. Deterministic daemon code owns registration, validation,
fencing, dispatch, attribution, and settlement; it does not retain or perform model reasoning.

Each phase records the selected runtime type, model ID, catalog entry, effective tier, resolution
source, provider execution timeout, token usage, duration, and outcome under the shared fast-runtime
and dashboard request identities. Content-bearing prompt, tool arguments/results, and answer text do
not enter attribution telemetry. Catalog `timeout_s` remains that phase's provider execution budget;
it is never replaced by the 45-second SSE observation window.

The normal successful path uses one classification call, zero CLI spawns, at most three registered
read calls from the admitted plan, one phrasing call, and one idempotent reply. The
existing single schema-invalid classification retry may run before any read; exhaustion falls back
to the existing Spawner path. Provider failover follows existing no-effect rules before reads.
After a read starts, no fallback may re-execute the whole turn.

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
| Catalog/hook/authority unavailable | evidence unavailable | pre-effect existing Spawner fallback | existing fallback/error behavior |
| Classification invalid/unsupported | no admitted plan | pre-effect existing Spawner fallback | existing lane result |
| Stop before invoke | cancelled | no provider/tool/reply | confirmed Stop after release |
| Stop during provider/read | cancelling until settled | no whole-turn replay | cancelled or ambiguity if unprovable |
| Concierge unavailable before first read | fast path unavailable | no direct read; existing fail-closed answer behavior | honest unavailable/decline |
| Read fails after any read began | fast runtime failed | no CLI restart or repeated plan | sourced failure/decline, or ambiguity if uncertain |
| Phrasing fails | fast runtime failed | no second phrasing call | honest failure without fabricated answer |
| Reply write conflicts | canonical reply wins | no changed overwrite | existing reply or structured conflict |
| Reply write outcome unknown | pending reconciliation/ambiguous | receipt lookup only; no replay | durable unknown outcome |
| SSE reaches 45/300 seconds | observation ended | runtime continues; Stop remains available | lane-named timeout; thread stays open |
| Runtime process crashes active | outcome unproven | no automatic runtime replay | durable ambiguity unless receipt proves outcome |

## Compatibility and Sequencing

The delta is additive and leaves active whole-requirement bodies untouched. Future implementation
must first consume the landed durable dashboard turn projection and message-scoped Stop API. It must
then widen the durable session phase and intent-lane representation through a new cumulative core
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

The exact product choices requiring approval are the ten numbered decisions in `proposal.md`.
Routine implementation choices may be resolved within those constraints. Any proposal to widen the
eligible lanes or butlers, execute writes, weaken the selected-hit rule, bypass MCP, reinterpret an
SSE timeout as cancellation/failure, replay uncertain work, or require live-provider evidence needs
a new owner-approved amendment.

No implementation work begins until the owner approves this exact draft digest or a reviewed
successor.
