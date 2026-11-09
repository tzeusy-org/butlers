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
| `contracts.py` | `SessionHost` protocol, `Launch`, `HostOutcome`, prompt-composer protocol |
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
  breaker never counts it. Any reply but a bare `completed` is a reported runtime failure.
  `terminate` sends `revoke`. A launch whose send fails (including a reply lost to the channel's
  read timeout) is revoked before it is reported `session_host_unavailable`, so the host frees
  the credential and its active slot.

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
| `goal__await` | lead | Read a child's validated result |
| `goal__stop_child` | lead | End one child (`child_stopped`) |
| `goal__complete` | lead | Record the outcome; the goal settles |
| `goal__inbox` | lead | Take the owner replies joined to this goal since the last call |
| `goal__escalate` | lead | Hand the goal to a fresh lead on the next ladder tier |
| `goal__return` | child | Return a result validated against the brief's `result_schema` |
| `context__search` | lead, child | Grant-scoped, read-pure recall (see below); served only when a `ContextSearch` is configured |

Every goal-tool call is charged against the goal budget and the caller's invocation share.

### Joined replies (inbox)

An owner reply that admission joins to a goal lands in `switchboard.goal_inbox`. A joined goal is
always `running` (a settling goal takes no joins, and D11 has no `waiting` state yet), so its
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

## State machine and settling (D11)

`running → settling → completed | partial | failed`, plus `stopped`, `budget_exhausted` and
`recovering`.

- `goal.complete` validates every relied-on effect, records the outcome in `goal_outcomes`, stops
  the children and moves the goal to `settling`.
- The sweep syncs parked effects and resolves settling goals. A goal answering an owner message
  completes only if its reply is an owner delivery that committed; otherwise it ends `partial`
  with the `delivery_failure` (`reply_missing`, `reply_not_owner_delivery` or
  `reply_not_committed`) recorded.
- An owner delivery is an effect whose `recipient_entity` is the live owner. The broker records
  it only for a `communicate` capability whose every party resolved to the owner. `goal.complete`
  refuses (`RESOURCE`) any other effect named as the reply, and settling checks it again. An
  owner-edited approval re-dispatches as a new effect without a recorded recipient, so it cannot
  be the reply.
- A lead that exits without calling `goal.complete` leaves the goal `failed`
  (`returned_without_outcome`); a child that exits without `goal.return` fails the same way.

## Stop, recovery and relaunch (D14)

- **Stop** takes the goal row `FOR UPDATE`, marks it `stopped`, revokes every invocation token,
  terminates the processes, then cancels parked effects. A cancellation the broker cannot perform
  now is retried by the sweep; a stopped goal never executes an approval.
- **Leases.** Each live invocation renews a lease (`lease_seconds=90`, `renew_every=30`). When a
  lead's lease lapses the sweep expires it, moves the goal to `recovering`, and launches a summary
  lead under a new lead epoch. Children past their deadline end `timed_out` (`deadline`).
- **Relaunch.** `call_model` owns failover and relaunch (`max_relaunches=1`). The runner attaches
  the capability calls the broker recorded for the failed attempt to the failure, which makes any
  attempt that already acted ineligible for relaunch. A lead whose relaunch is spent ends the goal
  `failed`; a failed child is the lead's to handle.
- **Idempotent launches.** A launch whose goal is no longer runnable, or whose lead slot is already
  held, opens nothing and ends nothing. A duplicate `start_goal` (an at-least-once admission
  retry) leaves the running lead alone, and a recovery or escalation that loses a race is a no-op.
  Only a failure of an invocation the launch itself opened can end the goal.
- **Resuming after a runner crash.** A goal that is `running` or `recovering`, has no live lead
  and no outcome, and whose latest lead ended more than `resume_grace_seconds` ago (default 90)
  is resumed by the sweep. A lapsed lead is replaced at the next epoch, and an escalated one by a
  lead on the next ladder tier. A lead that failed at runtime gets its one relaunch only if it was
  not itself a relaunch and the broker recorded no call of its token. Anything else ends the goal:
  `budget_exhausted` after a lead's turns or tokens ran out, otherwise `failed`. The failure class
  is not persisted, so a sweep relaunch cannot apply the retryable classifier: it applies the two
  ledger rules only.

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

## Not yet delivered

- `guidance.search` / `guidance.load` (M4), and the per-domain `ContextSource` adapters for `context.search`.
- The deployed session host and runtime usage relay (`bu-kiuh8v`). Its control listener today runs
  one invocation at a time, answers only `completed`/`refused` (no failure class or usage), and
  its client reads replies with a 3 s timeout, shorter than any launch.
- Constructing the runtime in the control plane: a live `Broker` needs the signing key and the
  isolated `butlers-control` process (`bu-jnhxc5`).
- Calling `GoalRuntime.admit` from ingress, confirms and obligations, and an owner Stop surface
  (the D19 dashboard change, or a control-plane service route).
- Waking a `waiting` goal on a joined reply or condition (RFC 0038 S2; there is no `waiting`
  state yet), and the reply text itself: `goal__inbox` returns references only.
- `awaiting_approval` and parked-wait continuation (`bu-dhloi1.7`).
- The four-section starting-context packet; `MinimalComposer` is a placeholder.
