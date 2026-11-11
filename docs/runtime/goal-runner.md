# Goal Runner

> **Status:** RFC 0038 M3 (bead `bu-u5s4w8`) plus the M6 control-plane seams (`bu-dhloi1.206`).
> The runner, goal tools, delegation, budgets, Stop and recovery exist and are tested against
> Postgres. `GoalRuntime` joins admission, periodic supervision and owner Stop to the runner, and
> `ControlSessionHost` stages each launch's model credential over the session-host control
> channel. All of it stays **dark** behind `GoalRuntimeSwitch` (default off): nothing constructs a
> runtime yet, and no ingress calls it (see [Not yet delivered](#not-yet-delivered)).
>
> **Design contract:** [RFC 0038](../../about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md)
> D8 (goal tools), D9 (delegation), D10 (budgets), D11 (state machines), D14 (Stop, recovery,
> relaunch), D16 (roles and ladders), D17 (telemetry).

The goal runner (`src/butlers/goal_runner/`) runs an admitted goal: it launches the lead
invocation, serves the reserved `goal` tools, admits delegated children, meters budgets, and
fences invocations on Stop, lease lapse, deadline or budget exhaustion. Every state change goes
through the switchboard ledger in a transaction that locks the goal row first, so each operation
serialises with Stop.

## Components

| Module | Owns |
| --- | --- |
| `runner.py` | `GoalRunner`: launch/supervise loop, goal-tool handlers, Stop, the recovery sweep |
| `ledger.py` | `RunnerLedger`: every SQL statement the runner issues (invocations, budgets, outcomes) |
| `delegation.py` | `parse_delegation` (closed request shape) and `child_grant` (grant intersection) |
| `models.py` | `ModelLadders`: per-role tier ladders; `qualifies` / `next_tier` |
| `tools.py` | `GoalTools`: the `goal__*` and `context__search` MCP tools, listed by invocation depth |
| `context_search.py` | `ContextSearch`: the closed query, grant-scoped fan-out, fusion and the bounded envelope |
| `guidance.py` | `Guidance`: `guidance.search` / `guidance.load` over per-domain `GuidanceSource`s |
| `roster_guidance.py` | The roster `GuidanceSource`s (butler `CLAUDE.md` and skills, the lead policy) and the `roster_guidance` factory |
| `contracts.py` | `SessionHost` protocol, `Launch`, `HostOutcome`, `LaunchContext`, `LaunchFacts`, prompt-composer protocol |
| `packet.py` | `PacketComposer`: the four-section starting-context packet |
| `control_plane.py` | `GoalRuntime`: admission to launch, the periodic sweep, owner Stop; `GoalRuntimeSwitch` |
| `staging.py` | `ControlSessionHost` (the `SessionHost` over the control channel), `ModelCredentials` |

## Control-plane wiring (M6, dark)

- **Admission to launch.** `GoalRuntime.admit(source, attempt_id)` calls
  `GoalAdmission.admit_submission` and starts an `admitted` goal's lead. Admission is idempotent
  per attempt and a duplicate start opens nothing, so a retried submission is safe. A `joined`
  reply is delivered to the goal inbox by admission and starts nothing. Admission commits before
  the start, so a process that dies in between leaves the goal `admitted` with no lead: the sweep
  starts any such goal older than `resume_grace_seconds`.
- **Supervision.** `GoalRuntime.start()` runs `supervise_once` every `supervise_every` seconds
  (default 15). A failed sweep is logged and the next one runs. The switchboard daemon starts it
  at startup (step 16d) when trusted construction set `daemon.goal_runtime` and its switch is on,
  and `close()` runs at shutdown (step 2d, after the scheduler loop stops): supervision stops
  and in-flight launches are abandoned, not terminated. Their leases lapse and the next runtime's sweep fences and replaces them.
- **Owner Stop.** `GoalRuntime.stop(goal_id)` is the single entry to `GoalRunner.stop_goal`. It
  returns `StopResult("stopped", n)` when this call fenced the goal, else `"not_running"`
  (terminal or unknown goal; anything of it still live is still fenced).
- **Credential staging (D3).** `ControlSessionHost.launch` resolves the launch's one model
  credential from the credential store at launch time (`ModelCredentials`; both reviewed
  session-host profiles use `cli-auth/openai`) and sends it with the launch command; nothing else
  of the store is read or sent. A runtime with no reviewed profile, or a missing credential, raises
  `CredentialUnavailable`, a `NotInvoked` refusal: the goal fails with no model run, and the
  breaker never counts it. The host replies when the invocation settles, in a closed shape
  (`docs/runtime/session-host.md`):
  - `completed` ends cleanly. `failed` maps its `failure_class` onto the D14 taxonomy: `timeout`
    is `SessionHostTimeout` (a `TimeoutError`, so it relaunches once if no call was recorded);
    `runtime_failed`, `containment_failed` and `cancelled` are non-retryable `SessionHostFailed`;
    any other class is `failure_class_unknown`, also non-retryable.
  - Either may carry counts-only `usage`. The runner charges `input_tokens + output_tokens`
    (cached input is part of input) to the invocation and goal budgets through `record_usage`
    before deciding on a relaunch, so a run that spent its budget is never relaunched.
  - `refused` with `reason: host_busy` (the host at its concurrency bound, the id not burned) is
    offered again with the same invocation id, with backoff (2, 4, 8 s); a host that stays busy
    raises `SessionHostBusy`, a `HostAtCapacity` refusal the runner defers (see Stop, recovery and relaunch). A
    bare `refused` is non-retryable
    `session_host_refused`.
  - Anything else, including `usage` the host itself would not send (a non-`int` or `bool` count,
    a negative or over-2^53 one, an unknown key), is `session_host_malformed_reply`; no reply
    content is logged.

  `terminate` sends `revoke`. The client waits `timeout_s + 35` for the reply, so a send that
  fails is a broken channel, not a long run: the launch is revoked before it is reported
  `session_host_unavailable`, so the host frees the credential and its active slot.

Migration `sw_062` (`roster/switchboard/migrations/062_goal_runner.py`) adds the per-invocation
budget share, deadline, model binding, relaunch lineage and closed `end_reason` to
`switchboard.invocations`, and the `switchboard.goal_outcomes` table. Triggers fix the share,
deadline, role, tier and lineage at insert; `model_id` and `end_reason` are set once and
`used_calls` only grows.

## Goal tools

The `goal` capability namespace is reserved: the broker never authorises a `goal.*` catalog
capability and never lists one. The runner serves these tools on the same per-goal endpoint:

| Tool | Depth | Effect |
| --- | --- | --- |
| `goal__delegate` | lead | Admit a child with a brief, capabilities, tier and budget share |
| `goal__await` | lead | Read a child's status, closed `end_reason` (`launch_failed`: it never ran) and validated result |
| `goal__stop_child` | lead | End one child (`child_stopped`) |
| `goal__complete` | lead | Record the outcome; the goal settles |
| `goal__wait_approval` | lead | End the lead and wait on one effect parked for approval (see below) |
| `goal__wait_owner` | lead | End the lead and wait for the owner's answer to a delivered prompt (see below) |
| `goal__wait_until` | lead (dark) | End the lead and wait on a predicate over this goal's own decisions and effects (see below); not listed or callable until goal events feed the evaluator |
| `goal__plan` | lead | Record the plan as the goal's next version of goal-owned references (see below) |
| `goal__inbox` | lead | Take the owner replies joined to this goal since the last call |
| `goal__escalate` | lead | Hand the goal to a fresh lead on the next ladder tier |
| `goal__return` | child | Return a result validated against the brief's `result_schema` |
| `context__search` | lead, child | Grant-scoped, read-pure recall (see below); served only when a `ContextSearch` is configured |
| `guidance__search`, `guidance__load` | lead, child | Find, and load by `<domain>:<name>` reference, the domain guidance the grant covers (see below); served only when a `Guidance` is configured |

Every goal-tool call is charged against the goal budget and the caller's invocation share.

### Domain guidance (D2, D8; M4)

`Guidance` (`guidance.py`) serves a domain's guidance documents (its `CLAUDE.md` and skills,
versioned) through `GuidanceSource`s, one per domain. It follows `context.search` throughout:

- **Admission.** One transaction authenticates, checks the live catalog, refuses an exposed
  reader or a third-party-origin goal (`POLICY`), finds the domains the grant covers (any
  granted capability the catalog lists at the caller's depth, in a domain with a source) and
  charges one call (`BUDGET` when spent), all before a source is asked. A child reaches only its
  own grant's domains, whatever its brief names.
- **Closed arguments.** `guidance__search` takes `query` and optional `k` (1-20);
  `guidance__load` takes one `ref`, `<domain>:<name>`, as search returns it and a brief's
  `guidance_refs` name it. A malformed reference, a domain outside the grant (refused before the
  charge) or a name its domain does not know (after it) is refused `RESOURCE`.
- **Trust.** Only `owner` or `owner_correction` text that is provably untainted is returned
  (excerpt on search, bounded text on load, with title and version). Anything else is a bare
  reference with its authority and taint, never its text, title or version.
- **Closed references.** A reference reaches the model on every hit, bare ones included, so a
  domain is a lowercase slug (`[a-z][a-z0-9_]{0,31}`, checked when `Guidance` is built) and a
  name is `[A-Za-z0-9][A-Za-z0-9._/-]{0,127}`: no space, newline, colon or markup. A source
  returning any other name fails, like any malformed answer; a `ref` naming one is refused
  `RESOURCE` before the charge and before a source is asked.
- **Read-only.** No effect row. Inlined guidance joins the reader as owner data (D6). A failing
  source is `degraded` on search; all failing, or a failing load, is `UNAVAILABLE`.
- **Sources see no caller.** A source gets the query or name and a `GuidanceReader` (depth
  only), never a token, goal or grant. It must not record the read.
- **The lead policy.** `Guidance(..., lead_policy=source)` serves one more domain, `lead`, to a
  lead (depth 0) whatever its grant, and never to a child (`RESOURCE`): the lead policy governs
  every lead (D1), and a child has its brief instead. No butler may be named `lead`.

The roster adapters (`roster_guidance.py`) are the real sources. `roster_guidance(broker, roster)`
builds a `Guidance` with one `ButlerGuidance` per domain butler (a `roster/<butler>/` holding a
`butler.toml`; `shared/` is not a domain) and `LeadPolicyGuidance` for
`roster/switchboard/goal-policy/LEAD.md`. Nothing constructs it in a daemon yet (the goal runtime
itself is unconstructed until its wiring lands).

- **Documents.** A butler's `CLAUDE.md` (name `CLAUDE.md`) and each
  `.claude/skills/<skill>/SKILL.md` (name `skills/<skill>`, `<skill>` a lowercase slug, so every
  name is a closed reference); the lead policy is `lead:LEAD.md`.
  Each is `owner` authority and untainted (git-reviewed owner config), versioned
  `sha256:<16 hex>` of the served text, so the version is stable across reads and changes on any
  edit, including to an included file.
- **Includes and session notes.** Whole-line `@<file>.md` includes resolve as the CLI does (depth
  3, no cycles). Every `Notes to self` section is dropped: sessions write it, not the owner (D2
  moves it to a record capability), so it is never served as owner text.
- **Confinement.** A name is a key in the set the adapter enumerates, never a path, so `../x` is
  just unknown (`RESOURCE`). Every file, include and symlink target is resolved and must stay
  inside the roster root; only `.md` files are read, never an env or secret-looking name. A
  document whose served text (after includes, with notes stripped) is over 64 KiB is omitted,
  never served cut, so session notes cannot push a butler's guidance out. Each raw file read,
  and the raw expansion, is capped at 8 x 64 KiB, since raw text still carries the notes. Reads are fresh per call
  on a worker thread; nothing is cached or recorded.
- **Search.** Term counts over the document, a name or title hit weighing three body hits;
  documents with no hit are not returned; ties break by name.

### Joined replies (inbox)

An owner reply that admission joins to a goal lands in `switchboard.goal_inbox`. A joined goal is
always `running` or `admitted` (a waiting goal takes no joins until owner-thread joining lands), so its
reader is the live lead, or the lead the sweep starts next after a lapse, resume or escalation.
The session host cannot push input into a live session, so the lead pulls replies between tool
calls with `goal__inbox` (`Broker.goal_inbox`):

- Each unconsumed row is consumed once, oldest first (at most 20 per call), through
  `consume_inbox`. The row's label joins the calling lead (cause `inbox`) in the same transaction,
  so the lead is exposed before it learns of the reply.
- The caller's token fences the epoch. A superseded, Stopped or ended lead fails authentication
  and consumes nothing, so its rows wait for the successor lead. A child cannot call it.
- A reply carries references only (`inbox_id`, `source_ref`, `reply_effect_id`, `received_at`).
  The inbox stores no message body, so reading the reply text is a separate read.

## Context search (D8, D15, S5)

`context.search` (`context__search`) is served to both depths, beside the goal tools. The `context`
namespace is reserved like `goal`: no catalog capability of that domain is listed or callable.

- **Closed query.** Keys `query`, `anchors`, `as_of`, `since`, `until`, `regions`, `kinds`, `k`;
  anything else, or any value outside `ContextSearchLimits` (query 1024 UTF-8 bytes, 16 anchors,
  16 regions/kinds, `k` 1-20), refuses `POLICY` before anything is charged. Anchors are entity
  UUIDs, instants must carry a zone, and a point in time (`as_of`) cannot be combined with a window.
- **Grant-scoped.** A domain is searched only if the invocation holds an unrestricted grant on a
  `read` capability of that domain that the catalog lists at its depth. A domain reached only
  through resource-restricted grants is not searched, because a source cannot honour a per-resource
  restriction. A grant that covers no source refuses `CAPABILITY` and charges nothing. A child holds
  a subset of its lead's grant, so it sees a subset of the sources.
- **S5 trust rules.** An exposed reader, or one of third-party origin, is refused `POLICY` before
  anything is charged: S5 gives it availability only, never recall. Only owner-authored
  (`owner`, `owner_correction`), provably untainted text is inlined; every other hit is a bare
  reference (`inline: false`, no excerpt) carrying its `category`, `authority` and `taint` label,
  and a missing authority or taint never inlines. The six sensitive categories are excluded unless
  the query names one in `regions` or `kinds` and a granted read capability declares it (otherwise
  `RESOURCE`, uncharged). A hit in an excluded category is dropped whatever the source returned,
  and not counted. A source gets `ReaderContext` (depth, exposure, the categories allowed), never a
  token, goal or grant.
- **Read-pure.** A call is charged to the goal budget and the caller's share like every goal tool
  (`BUDGET` when spent, before any source is asked). It writes no effect row. The only other write
  is the reader's label join for what it read (D6): a source's stamp account sets the label, and
  hits without an owner-untainted account, or from a tainted writer, expose the reader. A source
  that returned nothing joins nothing.
- **Envelope.** Whole items with `ref` (`<source>:<ref>`), region, kind, a bounded excerpt,
  `observed_at`, `authority` and `conflict_set`; `sources` (per-source `ok`, `timeout`, `failed`);
  `omitted` (count and reason `k` or `packet_budget`). Sources are fused by rank, never raw score,
  and shown in the order of credence. One slow or failed source makes the status `degraded`; all
  failing refuses `UNAVAILABLE`.
- **Closed labels.** A hit's `ref` and `conflict_set` (`[A-Za-z0-9][A-Za-z0-9._:/-]`, up to 256)
  and its region and kind (`[A-Za-z0-9][A-Za-z0-9._/-]`, up to 64) reach the model on every hit,
  bare references included, so anything else (a space, a newline, markup) fails the whole source,
  as does an `observed_at` that is not a zoned `datetime`.
- **Not yet covered.** Preconditions for any production `ContextSource` adapter, which the engine
  cannot enforce from outside a source:
  - an availability-only view for exposed and third-party goals (`busy_until` or unknown); they are
    refused outright today;
  - private-custody exclusion and raw-coordinate exclusion in every hit;
  - per-hit `category`, `authority` and `tainted` supplied truthfully (a hit with no taint is never
    inlined, so an adapter that cannot say loses inlining, not safety);
  - resolving an opaque reference to its text, which must be its own label-joining read;
  - owner-law `never` terms on a domain's read capability do not yet exclude its source.
  Limits are a constructor argument, not yet read from `runtime_config`.

## Starting-context packet (D15)

`PacketComposer` replaces the minimal prompt. Every launch gets `<goal_packet version=1>` with the
same sections, built from the ledger rows the broker enforces (`RunnerLedger.launch_facts`), never
from the model:

1. `task` (leads: runner-authored) or `brief` (children: objective, success conditions and the
   fixed result schema), including the brief's context and guidance references, all inside one
   block labelled lead-written. Every lead-authored item is rendered as a JSON string literal
   (newlines and quotes escaped) and entity-escaped, so it cannot forge a packet line, close its
   fence or open another section.
2. `grant` and `budget`: the invocation's own grant and what is left of its calls, turns, tokens
   and wall time. Leads also see the goal's remaining calls, tokens and seconds.
3. `ledger`: only for a lead that follows one that ran (recovery, escalation, relaunch): the
   effects and children, as identifiers and statuses, and the goal's pending inbox replies as
   `inbox_pending: <count>` and up to 20 `inbox: <id>` lines. Stating them consumes nothing and
   joins no label; the lead reads them with `goal__inbox`, which does both. When the goal has a
   plan version, the ledger also states it: `plan_version: <n>` and its `plan_premise:`,
   `plan_evidence:` and `plan_step:` references, exactly as `goal__plan` validated them. A
   first lead has no ledger, and neither does a lead re-offered after every earlier lead ended `launch_failed`
   (the host had no room, so none ran). A child never gets one.
4. `refs`: the context tools the broker serves (runner-authored only). `GoalTools` points the
   runner's `PacketComposer` at exactly the tools it serves (`context__search` when a
   `ContextSearch` is configured, `guidance__search` and `guidance__load` when a `Guidance` is);
   none are advertised otherwise.

A child's packet carries its own brief, grant and share only: no origin, exposure, epoch,
goal-wide budget, ledger or parent identifier. The runner uses `PacketComposer()` unless a
composer is passed. The lead's goal input, owner profile, lead policy and domain index (D15) are not
in the packet yet: admission and ingress (M6) own them.

## Delegation (D9)

- The request shape is closed (`brief`, `capabilities`, `model_tier`, `budget_share`); anything
  else refuses `POLICY`.
- The child grant is the requested grants intersected with the lead's grant. An empty request, a
  request that is not a subset of the lead's grant, or one naming a capability absent from the live
  catalog refuses `CAPABILITY`. `administer` and `transact` never pass to a child; a grant left
  empty by that refuses.
- Depth is read from the parent invocation row, never from the request: a child (depth 1) cannot
  delegate.
- The child's share (calls, tokens, wall seconds) must fit inside what the goal has left; its call
  share is enforced by the broker on every call.

## Budgets (D10)

Budgets fail closed and are charged atomically inside the broker's per-call transaction.

- The goal budget is checked first, then the invocation share. An exhausted share refuses
  `BUDGET` for that invocation without ending the goal.
- Usage relayed from the runtime (`record_usage`) meters turns, tokens and spend. Exhausting the
  invocation's turn or token ceiling ends it (`turns_exhausted` / `tokens_exhausted`); exhausting
  the goal budget ends the goal `budget_exhausted` and fences every live invocation.
- Launch goes through `call_model`, so the monthly spend ceiling is enforced at every launch.

## State machine and settling (D11, S2)

`admitted → running → waiting → completed | partial | failed`, plus `stopped` and
`budget_exhausted`. Since `sw_064` the old `settling`, `awaiting_approval` and `recovering` states
are gone: `settling` is `waiting` on a `settle` envelope, `awaiting_approval` is `waiting` on an
`approval` envelope bound to the parked effect (one with no parked effect is held on a
`conditions` envelope whose predicate is `evidence_unavailable`, reason `legacy_approval_unbound`:
never approved, ended only by Stop or an owner action), and `recovering` is `admitted` with an ended lead
and no successor (the resume sweep replaces it at the next epoch). `goals.legacy_status` keeps
the original status of every converted row.

- `goal.complete` validates every relied-on effect, records the outcome in `goal_outcomes`, stops
  the children and moves the goal to `waiting` on a `settle` envelope.
- The sweep syncs parked effects and resolves waiting goals with an open settle envelope. The
  settle fire is the envelope's one wake and runs `resolve()` directly: no LLM epoch. A goal answering an owner message
  completes only if its reply is an owner delivery that committed; otherwise it ends `partial`
  with the `delivery_failure` (`reply_missing`, `reply_not_owner_delivery` or
  `reply_not_committed`) recorded.
- An owner delivery is an effect whose `recipient_entity` is the live owner. The broker records
  it only for a `communicate` capability whose every party resolved to the owner. `goal.complete`
  refuses (`RESOURCE`) any other effect named as the reply, and resolving checks it again. An
  owner-edited approval re-dispatches as a new effect without a recorded recipient, so it cannot
  be the reply.
- A lead that exits without calling `goal.complete` leaves the goal `failed`
  (`returned_without_outcome`); a child that exits without `goal.return` fails the same way.

## Stop, recovery and relaunch (D14)

- **Stop** takes the goal row `FOR UPDATE`, marks it `stopped`, cancels its open wait envelopes and
  unlaunched wake intents, revokes every invocation token,
  terminates the processes, then cancels parked effects. A cancellation the broker cannot perform
  now is retried by the sweep; a stopped goal never executes an approval.
- **Leases.** Each live invocation renews a lease (`lease_seconds=90`, `renew_every=30`). When a
  lead's lease lapses the sweep expires it, moves the goal back to `admitted`, and launches a summary
  lead under a new lead epoch. Children past their deadline end `timed_out` (`deadline`).
- **Relaunch.** `call_model` owns failover and relaunch (`max_relaunches=1`). The runner attaches
  the capability calls the broker recorded for the failed attempt to the failure, which makes any
  attempt that already acted ineligible for relaunch. A lead whose relaunch is spent ends the goal
  `failed`; a failed child is the lead's to handle. A relaunched, recovered or escalated lead
  holds the ceiling ∩ the latest lead's grant, never more (see [Wake grants](#wake-grants-s22)).
- **Idempotent launches.** A launch whose goal is no longer runnable, or whose lead slot is already
  held, opens nothing and ends nothing. A duplicate `start_goal` (an at-least-once admission
  retry) leaves the running lead alone, and a recovery or escalation that loses a race is a no-op.
  Only a failure of an invocation the launch itself opened can end the goal.
- **Resuming after a runner crash.** A goal that is `running` or `admitted` after a lapse, has no live lead
  and no outcome, and whose latest lead ended more than `resume_grace_seconds` ago (default 90)
  is resumed by the sweep. A lapsed lead is replaced at the next epoch, and an escalated one by a
  lead on the next ladder tier. A lead that failed at runtime gets its one relaunch only if it was
  not itself a relaunch and the broker recorded no call of its token. Anything else ends the goal:
  `budget_exhausted` after a lead's turns or tokens ran out, otherwise `failed`. The failure class
  is not persisted, so a sweep relaunch cannot apply the retryable classifier: it applies the two
  ledger rules only.
- **A host with no room defers the launch.** When the session host stays at its concurrency bound
  through every re-offer (`HostAtCapacity`), nothing ran: the invocation ends `failed` with
  `launch_failed`, its invocation goes back to the budget, the kernel records a `suppressed`
  attempt (never a runtime failure, never a relaunch), and nothing is charged. A child's lead
  keeps running and sees the child failed. A lead's goal stays `running`, and the sweep above
  offers the same role and tier again at the next epoch, until `busy_defer_seconds` (default
  900) have passed since the first lead in the unbroken run the host turned away; after that the
  goal ends `failed`. A lead that replaces a turned-away relaunch is that relaunch, so it gets
  none of its own. Stop during a deferral wins: a stopped goal is never resumed, and a Stop during
  the re-offers keeps the invocation's `stopped` end.

## Roles, tiers and ladders (D16)

Each role resolves its model through the catalog with `CallSpec(exact_tier=True)`: an exact-tier
call never falls through to another tier, and refuses before any attempt if no model of the
requested tier qualifies. It also ignores a spend rule's model reroute, which could name a model
on any tier. See [Model Routing](model-routing.md#roles-tiers-and-ladders).

| Role | Ladder |
| --- | --- |
| `lead` | workhorse → reasoning |
| `monitor` | cheap → workhorse → reasoning |
| child tiers | cheap, workhorse |

`goal.escalate` moves to the next qualifying tier of the role's ladder. It is finite: at the top
of the ladder, or with no invocation room left, it refuses and the current lead keeps the goal.

## Telemetry (D17)

Spans (`goal.context_search`, `goal.delegate`, `goal.complete`, `goal.escalate`, `goal.stop`, `goal.recover`, `goal.resume`, and the
per-launch span) carry identifiers only: goal id, invocation id, depth, lead epoch and tier. No
brief text, arguments or results.

## Waiting records (S2, sw_063, dark)

Migration `sw_063` (`roster/switchboard/migrations/063_goal_waiting.py`) adds the records a goal
needs to wait without holding a session slot and to resume through one fenced successor epoch.
The runner uses the settle envelope since `sw_064`; wake admission and joins are later PRs.

| Record | What it holds |
| --- | --- |
| `owner_threads` | The one owner thread registry; `goals.thread_id` is a foreign key to it (legacy ids are backfilled as `legacy` rows) |
| `goal_plans` | Append-only plan versions: premise, evidence and step references, the epoch that wrote them; `goals.plan_version` and each envelope bind `(goal_id, version)` |
| `goal_epochs` | `(goal_id, epoch)` with reason (`initial`, `wake`, `recovery`, `escalation`), label snapshot, grant digest and a forward-only state. A trigger derives a lead's epoch from its invocation until the runner records epochs itself |
| `wait_envelopes` | Immutable bounds (predicate and digest, source set, grant, label, deadline, `max_wakes`, plan binding, approval effect and digest) plus the only mutable parts: `state`, the cumulative `consumed_wakes` and `generation`. One live envelope per goal |
| `wait_generations` | One registration row per generation: condition instance ids, the predecessor epoch and fire event, the registration cursor |
| `wake_intents` | The launch outbox, unique per `fire_key`, per `(envelope, generation)` successor and per fire event. A fire the ledger refused ends as a terminal `not_admitted` row with its reason. Stop, expiry or a superseding rearm ends an admitted, unlaunched wake as a terminal `cancelled` row (reason `stopped`, `expired` or `superseded`), out of the open index but still the generation's one successor |

An envelope's `kind` decides what a wake does. `conditions`, `owner_answer` and `approval` launch a
successor epoch; `settle` only runs the deterministic `resolve()` and launches nothing.

`goal_inbox` is one discriminated queue: `input_kind` is `owner_reply`, `owner_non_reply` or
`condition_fire`, matched to a joined admission decision of the same kind (`reply_joined`,
`input_joined`, `condition_joined`). A condition fire has no delivery or reply effect and is bound
to its envelope generation and fire event. Consumption is the one-time
`(consumed_at, consumed_epoch)` tuple; rows consumed before `sw_063` are `legacy_consumed` with no
invented epoch, and the broker's inbox read now records the reader's lead epoch.

`WaitLedger` (`src/butlers/core/goal_waiting.py`) holds the lifecycle operations: the thread
registry, plan versions, opening an envelope (superseding a fired predecessor) and `rearm`, which
opens the next generation only when the goal is live, no lead is running, the fired generation's
successor launched, the cumulative cap and the original deadline hold. Plan versions are written
by `goal__plan` (below) and, when a goal first waits with none recorded, as an empty version 1 for
the envelope to bind. Nothing calls the thread registry yet (`bu-dhloi1.296`). `goal__wait_until`
opens a `conditions` envelope (below); nothing calls `rearm` yet (`bu-dhloi1.305`).

### Plan versions (S2)

`goal__plan {premise_refs, evidence_refs, step_refs}` records the running lead's plan as the
goal's next version. Each list holds at most 32 references, none repeated, in the closed spelling
`effect:<uuid>`, `inbox:<uuid>`, `child:<uuid>` or `plan:<version>`. Free text, another spelling or
a repeat is refused `policy`. In the goal's locked transaction, every reference must name a row of
this goal (its effect, its inbox row, its child invocation, an earlier version of its plan), else
the call is refused `resource` and nothing is recorded. Content identical to the goal's latest
version returns that version (a retry). Changed content appends the next version with its
predecessor and the lead's epoch, and moves `goals.plan_version` to it. A recorded version never
changes, so a wait keeps the version it bound when it opened. A child cannot plan (`capability`),
and neither can a goal that is not running. A successor, recovery or escalation lead reads the
latest version's references in its packet ledger.

## Waking a waiting goal (S2.1, S2.2; sw_065)

A condition fire becomes one successor epoch in two committed steps, bridged by the unique
`wake_intents.fire_key`:

1. The evaluator commits its fire intent in `public.condition_fire_intents` (its own transaction).
2. `GoalRunner.admit_wake` (`core/goal_wake.py`) runs `WakeAdmission.admit` on the Switchboard
   runtime role, under the goal row lock Stop takes. It either admits the wake (the envelope's
   `open -> fired` CAS and wake count, the `condition_joined` decision, the `condition_fire` inbox
   row, the `wake` epoch record and the `pending` wake intent commit together, and the waiting goal
   becomes `admitted`) or records a terminal `not_admitted` receipt with its reason (`stopped`,
   `goal_not_waiting`, `stale_generation`, `envelope_not_open`, `expired`, `cap_reached`,
   `admit_error`) and
   changes nothing else. A condition instance id names its envelope generation (ids are
   generation-owned); the evaluator's own instance generation is a different counter.

The sweep (`supervise_once`) admits any fire intent of a goal's wait that has no wake intent after
`wake_grace_seconds`, so a crash between the two steps is completed, and replaying a settled fire
returns the stored outcome and writes nothing. A refused fire has a wake intent row, so it is never
retried; a fire whose event already settled the generation under another fire key is not swept
either. The wake stage runs last in the sweep and each fire or intent is guarded on its own: a
fire that raises is logged and counted, and after three failed sweeps it is closed with an
`admit_error` receipt, so it can never block lease lapse, budget fencing or settle resolution.

The sweep then launches due wake intents: `claim_wake` takes one under the goal lock, `open_lead`
opens the lead at the minted epoch and marks the claimed intent `launched` in the same transaction,
and a launch that opens nothing marks it `failed` so the next sweep claims the same intent again
(a lapsed claim returns to `pending` first; a lapsed claim past the envelope deadline is cancelled
`expired`). Stop cancels envelopes and unlaunched intents; an
intent whose envelope deadline passed is cancelled `expired`. A goal with a pending, claimed or
failed wake intent is not resumed as leaderless: its lead is the wake's to launch. The lead reads
what woke it through `goal__inbox` as `kind: condition_fire` with the envelope and event ids and no
reply effect. `sw_065` makes a `condition_fire` inbox row bindable only to an admitted wake intent.

### Wake grants (S2.2)

A lead's grant only ever narrows after the goal's first lead (`RunnerLedger.open_lead`,
bu-dhloi1.305):

| Lead | Grant |
| --- | --- |
| The goal's first lead | The goal ceiling |
| A wake lead | Ceiling ∩ the fired envelope's `grant_snapshot` ∩ current policy |
| Any other successor (relaunch, recovery, escalation) | Ceiling ∩ the latest lead's grant |

Current policy is the broker's live published catalog, read in the launch's transaction: a grant
survives only while its capability is still published and not `transact` (admission's eligible
set), so a policy change while the goal waited narrows its wake. An `owner_answer` or `approval`
envelope snapshots the waiting lead's grant ∩ the ceiling, so its wake is not narrowed beyond the
lead that waited; a `settle` envelope snapshots no grant, since nothing is launched from it. A
`conditions` envelope carries the snapshot its opener recorded (`goal__wait_until`: the waiting
lead's grant ∩ the ceiling). Because a relaunch, recovery or
escalation takes the latest lead's grant, a failed narrowed wake lead's replacement stays narrowed.

### Waiting on an approval (bu-dhloi1.204)

`goal__wait_approval {effect_id}` is for a lead that has parked an effect and has nothing else to
do until the owner decides. The effect must be parked for this goal with an approval; the lead
and its live children end, and the goal enters `waiting` on an `approval` envelope bound to that
effect and its argument digest (one wake, the goal's label and budget deadline, and a grant
snapshot of the waiting lead's grant ∩ the ceiling; see [Wake grants](#wake-grants-s22)). The
sweep syncs the domain's decisions onto the effect and then reads the effect once it has left
`requested`, `parked`, `approved` and `dispatched`:

- An approved effect that ran (`committed`, `failed`) is the envelope's one wake: the runner
  admits a synthetic fire (keyed on envelope, effect and status, so a replay mints nothing new)
  through the same wake admission as a condition fire, and the wake path launches a successor
  summary lead at the next epoch.
- A `rejected`, `expired` or `cancelled` effect closes the envelope and ends the goal `partial`;
  no lead is launched and no wake is counted.
- A wake that cannot be admitted ends the goal `failed`: refused for any reason but Stop (such as
  the envelope's deadline passing), or mapped to no envelope generation. The fire is keyed on the
  envelope's current-generation condition instance id, so an approval wait `sw_064` converted
  (instance `legacy:awaiting_approval:<goal>`) continues like one `goal__wait_approval` opened.
- A wait whose deadline passes with the effect still parked is expired by the expiry sweep below.
- Stop cancels the parked effect and the envelope through the existing Stop path.

### Waiting for the owner's answer (S2.1, S2.2)

`goal__wait_owner {effect_id}` is for a lead that asked the owner a question and needs nothing
else until the answer. The effect must be this goal's committed owner delivery that accepts input
and has not expired. The lead and its live children end, and the goal enters `waiting` on an
`owner_answer` envelope bound to that prompt (predicate `prompt_effect_id`; one wake; a grant
snapshot of the waiting lead's grant ∩ the ceiling; deadline is the prompt's expiry or the goal's
budget deadline, whichever is sooner). The wait
needs no approval: the answer is authenticated by admission's exact reply binding and cannot
approve an unrelated effect.

A prompt exists because the lead asked for one: `notify(awaits_reply_seconds=N)` (30-86400, Telegram
send or reply). Messenger reports where the message landed (`locator` `<chat_id>:<message_id>`, the
sending bot's `endpoint`, the `conversation`); the forwarder reads that additive `delivery` block
into a `DeliveryReport`. At settle the broker, not the domain, derives the reply ref with
`core.reply_scope.scoped_reply_ref` from the report and the owner entity and channel it recorded at
reserve (the channel in the ingress vocabulary, `telegram_bot`), so a domain cannot steer which
identity a reply binds to. Admission derives the same ref from the inbound reply's own scope with
the same function. The broker writes `delivery_ref`, `prompt_accepts_input` and `prompt_expires_at`
once, in the UPDATE that commits the effect (`sw_066`: the trigger allows NULL to a value only in
that transition and never changes them after). The expiry is the report's, clamped to `now +
awaits_reply_seconds`; a call that did not ask for an answer, or whose window is outside 30-86400,
records the ref and accepts no input. Only an owner delivery with a recorded channel can accept
input, and nothing is bound for a failed, unknown or still-parked effect.

Admission is the wake. A reply whose delivery binds this prompt, from the owner entity and channel
the prompt went to, joins the waiting goal as `reply_joined`; in the same transaction
`WakeAdmission.admit_answer` fires the envelope (`open -> fired`, one wake), writes the `wake`
epoch naming the `owner_reply` inbox row and a `pending` wake intent keyed on the decision, and
returns the goal to `admitted`. The sweep then launches the successor through the wake path
above, and the lead reads the reply from `goal__inbox` as `kind: owner_reply`. A reply to a
waiting goal in any other case (another prompt, an expired wait, a settle, approval or conditions
wait, a stopped goal) is new owner intent, as before. Same-chat non-reply joining of a running
goal is not part of this change.

### Waiting on the goal's own conditions (S2.2, S4)

`goal__wait_until {predicate, deadline, max_wakes}` is for a lead that has nothing to do until
something of its own goal happens (bu-dhloi1.305). It is dark: `tools.WAIT_UNTIL_LIVE` is off, so
the tool is neither listed nor callable (`capability`) until the goal-event feed (below) is wired
into the sweep, because until then a wait that is not already true can only expire. The runner and
ledger path below exists and is tested directly. The predicate is the closed S4 language
(`all`/`any`, at most two levels and 16 leaves) restricted to the goal's own leaves, which need no
approval:

- `decision {subject_id, outcome}`: the owner's decision on one of this goal's effects that parked
  for approval. Past `parked` the call was approved whatever its dispatch did since; `rejected`,
  `expired` and `cancelled` are the decision itself.
- `effect_settled {effect_id, status_set}`: one of this goal's effects settled as `succeeded`
  (`committed`), `failed`, `unknown` or `cancelled` (an effect that never ran).

`owner_input` is refused `policy`: admission joins a reply only to an `owner_answer` wait, so a
conditions instance on it could not be fed; `goal__wait_owner` is the reply wait. `at` and
`transition` (clock and perception, third-party evidence) are refused `policy` too. So are free
text, a `deadline` that is not an ISO 8601 timestamp with an offset, one later than 45 days after
the goal's admission or not in the future, and `max_wakes` outside 1-6. A decision or effect that is
not this goal's (or a decision on an effect that never parked) is refused `resource`. Nothing is
recorded on a refusal; a child is refused `capability`.

In one transaction, under the goal lock, the lead and its live children end, a `conditions`
envelope opens (the predicate and its S4 digest, its sources, the goal's label, the deadline or the
budget deadline if sooner, `max_wakes`, a grant snapshot of the waiting lead's grant ∩ the
ceiling), and its condition instance registers with the evaluator on the same connection
(`ConnectionConditionStore`, one savepoint per unit). The subscription grant and the registration
snapshot are read from the goal's effect rows, never from the model. A predicate that already holds
fires at registration and is admitted in that transaction as the wait's wake: the call returns
`woken` and the sweep launches the successor. Otherwise it returns `waiting`. `max_wakes` above 1
has no effect until `rearm` (a later step of bu-dhloi1.305). Stop cancels the envelope and, in the same transaction, the goal's own condition
instance, so nothing is left for the evaluator to fire; a fire that raced Stop is still recorded
`not_admitted: stopped`.

#### The goal-event feed (dark)

`GoalRunner.feed_conditions` wakes a goal whose conditions held after it began waiting. For every
`waiting` goal with an open `conditions` envelope it syncs the domain's decisions onto the goal's
parked effects (as an approval wait does), derives the goal's own events from its effect rows
(`own_conditions.events_for`, the same mappings as the registration snapshot), evaluates them
against the envelope's current condition instance and admits a fire as the wait's one wake.
Events come from current state, not a log. That is sound because the facts only accumulate (an
owner decides once and a settled effect stays settled), so feeding current state can neither lose
nor invent a fire. A `decision` event names the effect id as its `subject_id`, the id a
`decision` leaf binds. Each event's id derives from its source, effect and value, so a redelivered
event is the same event; its position is the effect's last change in microseconds.

The feed is dark: `supervise_once` does not call it. A feed-triggered wake comes from evidence
outside the waiting lead (the owner's or a domain's action), and its lead would hold the
envelope's full waiting grant. Under the bu-dhloi1.305 ruling, no live feed-triggered wake may
hold that grant until the label narrowing of third-party-sourced wakes lands (PR5), so the feed
is wired into the sweep, and `goal__wait_until` listed, only then. `owner_input` stays refused
until admission joins an owner's reply to a conditions wait on that prompt.

### Expiry of a wait

No wait lasts forever. The sweep ends every `waiting` goal whose open envelope passed its deadline
without firing, whatever its kind: the envelope closes `expired`, its undelivered wake intents are
cancelled `expired`, and the goal ends `failed`, or `partial` for a settle wait whose recorded
outcome is resolved `partial`. This includes a legacy approval wait that `sw_064` held on an
`evidence_unavailable` envelope. A parked effect left behind is refused later because its goal has
ended.

## Owner failure cause line (S6, sw_068, dark)

An owner message that became a goal and ended `failed` or `budget_exhausted`, with no owner delivery
in its own lineage, would leave the owner with silence. The sweep's last step
(`GoalRunner._report_owner_failures`, given an `OwnerFailureReporter`) records one
`switchboard.pending_asks` row per such goal and sends one model-free cause line.

- Identity: `pending_asks.source_key` is the goal's own `source_key` (unique), so one failure has one
  row however often it is swept. The row also opens an `owner_delivery` subject of kind `pending_ask`
  (`source_ref` is the row id).
- "Already told": a `committed`, `dispatched` or `unknown` effect to the owner entity inside this goal's
  lineage that is not an awaits-reply prompt (a question is not a failure report) (the goal and the goals it caused). A parked, requested, failed or refused effect does not
  count, and an owner delivery by an unrelated goal does not silence this failure.
- Cause: `failed`, `budget_exhausted`, or `capability_unavailable` when the broker recorded a
  `capability_unavailable` refusal for that goal. The health cause itself lives in published health, not
  in the refusal record, so it is not repeated here. `admission_refused` is written at admission (below).
- The line is one fixed sentence per cause (`CAUSE_LINES`): no ask text, token or provider detail. The
  presentation (`owner_delivery.present`, surface `telegram`) is the retry identity and is marked
  handed-off before the send, so a crash or an ambiguous send leaves `unknown` and is never sent again. Only a result decided
  before any provider call (suppressed, no recipient, no client) is a proven non-send (`failed`): the next
  sweep presents it again and sends once. A failed call that began is ambiguous (`unknown`). One ask's
  failure never stops the others in the sweep.
  Quiet hours and context holds (the owner notify suppression check) delay the line to a later sweep.
- Dark: nothing is written or sent unless `public.owner_delivery_rollout` is on and exactly one owner
  entity exists. Rolling back disables the sweep and keeps every row and receipt.
- The state column admits Run, Drop, ack and `dropped_stale`; only `dropped_stale` is written so far
  (Run/Drop recovery is the next package).

### Refused owner asks and the 24 hour expiry (sw_069)

- Producer: `GoalLedger.remember_refused_owner_ask`, called when admission refuses. It writes a
  `pending_asks` row (`cause = admission_refused`, `goal_id` NULL) only for a message whose origin is
  `owner` and whose evidence says the sender is the owner, refused for a retryable reason
  (`RETRYABLE_REFUSALS`: `quota_exhausted`, `limits_unavailable`), with the rollout on. Authorization and
  owner-law refusals, and any non-owner sender, never write one. A write failure is logged and never
  changes the refusal.
- Retry cap: `line_attempts` counts presentations of the cause line; after `MAX_LINE_ATTEMPTS` (3) a
  line that keeps failing before any provider call (for example no owner recipient) is no longer
  presented, and the ask waits for its 24 hour expiry.
- `original_ask` is a versioned reference `{v, source:{kind,id}, refusal}`, not message text: a refused
  admission can be re-admitted with a fresh attempt id, so the reference is enough to replay it. It
  exists only while the ask is open (CHECK `ck_pending_ask_original`).
- Expiry: every ask carries `expires_at` (24h from the failure for goal asks, from the refusal for
  refused asks). The sweep turns an expired open ask into `dropped_stale`, nulls `original_ask`, closes
  its subject as expired and never sends a line for it. This runs while delivery is dark. A goal that
  ended more than 24 hours ago is not reported on first rollout.
- The production sender is `OwnerNotifySender` (the existing `send_owner_telegram` path). It needs the
  Switchboard client that path binds, so it is wired where the runner is constructed; the runner is not
  constructed in production yet.

### Run or Drop a refused ask (package 4b, dark)

`goal_runner/owner_recovery.OwnerRecovery` is the service behind the Run/Drop card.

- Cards: `cards()` presents each runnable ask (open, unexpired) on the dashboard surface with an
  approve (Run) and a reject (Drop) token, and returns one batch with counts by cause. Runnable means
  a refused admission that kept its reference, or a `failed` / `budget_exhausted` goal ask.
  Capability-unavailable asks get no card until health can say the dependency is back. Empty while
  the rollout is off. No "Run all".
- Run: `decide(token, ask, "approve")` redeems the token on the `pending_ask` subject, then admits
  the stored source reference again through the full current admission rules on the redemption's own
  connection, with an attempt id derived from the ask. Evidence is loaded afresh, so Run is exactly
  an owner re-send now and never pre-satisfies an authorization check. If admission refuses again
  (`still_refused`) the whole decision rolls back and the card stays. A Run on an expired ask is refused `ask_expired` (Drop still works). A fault in admission becomes `admission_unavailable`. A second Run is a `duplicate`
  and admits once.
- Run on a failed or budget-exhausted goal cannot reuse the message's source key (the failed goal
  owns it), so it admits a new owner goal from the source `("message", "owner_retry:<ask id>")`.
  `OwnerRetryEvidence` answers that source with owner evidence and the failed goal's exposure label;
  grants come from admission, never from the failed goal. It is not linked as `caused_by_goal`
  (admission accepts that only with a causing effect and an obligation). A retry that fails is a new
  owner goal and gets its own ask; a failed goal that ran from a refused ask is keyed `<source>#run`
  because the refused ask holds the source key. The production evidence reader must understand the
  `owner_retry:` prefix.
- Drop: reject ends the ask `dropped`.
- Both clear `original_ask` in the same update as the state change.
- `owner_delivery.redeem` takes approve/reject for `approval` and `pending_ask` subjects only
  (`DECIDABLE_KINDS`), each through its own `decide` callback. The kind and source reference bound
  into a token mean an approval token never redeems a pending ask and the reverse; the dashboard's
  approval route still refuses a `pending_ask` token (`action_unsupported` at the route, and
  `subject_mismatch` if it reached `redeem` as an approval).
- Not wired: the HTTP route and Telegram buttons that carry a token to `decide`, and the production
  evidence reader that `admit` needs, belong to the cutover with the runner.

## Not yet delivered

- The per-domain `ContextSource` adapters (a domain's memory and facts).
- The lead policy's adoption. `roster/switchboard/goal-policy/LEAD.md` is drafted by an
  agent, and `lead:LEAD.md` serves it through `guidance.load`. Carrying it in the starting
  packet is the packet item below.
  - Authorship (owner decision, 2026-11-10): an agent may draft it and open a PR. Text that
    conforms to the RFC and specs is adopted by independent conformance review.
  - Reply style, voice and any non-conforming text merge only with the owner's explicit
    approval on that PR. LEAD.md has no reply-style section yet; it waits for that approval.
- The deployed session host (`bu-kiuh8v`), and a usage relay between tool calls; today the
  runner charges only the counts a settled run reports.
- Constructing the runtime in the control plane: a live `Broker` needs the signing key and the
  isolated `butlers-control` process (`bu-jnhxc5`).
- Calling `GoalRuntime.admit` from ingress, confirms and obligations, and an owner Stop surface
  (the D19 dashboard change, or a control-plane service route).
- Same-chat non-reply joining of a running goal within the engineering window, the receipt chip
  and `/new` (`bu-dhloi1.7`); it needs a conversation key from verified ingress. The reply text
  itself: `goal__inbox` returns references only.
- The lead's goal input, owner profile, lead policy and domain index in the packet (M6).

## Descriptor `depends_on`

A tool descriptor declares the providers it needs in `depends_on` (RFC 0038 S6), next to
`categories`. The value is a sorted, duplicate-free tuple of IDs from the closed set in
`butlers.core.capability_dependencies.DEPENDENCY_IDS`; empty means the tool needs none. An unknown,
empty, non-string, duplicate or unsorted entry makes the descriptor fail authorization validation
(`depends_on`), and a descriptor with `depends_on` set to `None` is ungrantable like any other missing
authorization field. The field is part of the published authorization digest. It only records the
declaration: nothing reads it yet, and it never grants a tool. The capability-health reducer
(`bu-dhloi1.14`, next package) reads it to turn a dependency's health into a typed unavailable result.
