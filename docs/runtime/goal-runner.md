# Goal Runner

> **Status:** first package of RFC 0038 M3 (bead `bu-u5s4w8`). The runner, goal tools, delegation,
> budgets, Stop and recovery exist and are tested against Postgres. They are **not yet wired** to
> admission/ingress (M6), and the session host is a protocol whose production adapter is still
> open (see [Not yet delivered](#not-yet-delivered)).
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
| `tools.py` | `GoalTools`: the `goal__*` MCP tools, listed by invocation depth |
| `contracts.py` | `SessionHost` protocol, `Launch`, `HostOutcome`, prompt-composer protocol |

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
| `goal__escalate` | lead | Hand the goal to a fresh lead on the next ladder tier |
| `goal__return` | child | Return a result validated against the brief's `result_schema` |

Every goal-tool call is charged against the goal budget and the caller's invocation share.

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
  completes only if its reply effect committed; otherwise it ends `partial` with the
  `delivery_failure` (`reply_missing` or `reply_not_committed`) recorded.
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

## Roles, tiers and ladders (D16)

Each role resolves its model through the catalog with `CallSpec(exact_tier=True)`: an exact-tier
call never falls through to another tier, and refuses before any attempt if no model of the
requested tier qualifies. See [Model Routing](model-routing.md#roles-tiers-and-ladders).

| Role | Ladder |
| --- | --- |
| `lead` | workhorse → reasoning |
| `monitor` | cheap → workhorse → reasoning |
| child tiers | cheap, workhorse |

`goal.escalate` moves to the next qualifying tier of the role's ladder. It is finite: at the top
of the ladder, or with no invocation room left, it refuses and the current lead keeps the goal.

## Telemetry (D17)

Spans (`goal.delegate`, `goal.complete`, `goal.escalate`, `goal.stop`, `goal.recover`, and the
per-launch span) carry identifiers only: goal id, invocation id, depth, lead epoch and tier. No
brief text, arguments or results.

## Not yet delivered

- `context.search` (AC8) and `guidance.search` / `guidance.load` (M4).
- The production session-host adapter and runtime usage relay (depends on `bu-kiuh8v`).
- `awaiting_approval` and parked-wait continuation (`bu-dhloi1.7`).
- The four-section starting-context packet; `MinimalComposer` is a placeholder.
- Wiring to admission and ingress (M6), and per-invocation model credential staging.
