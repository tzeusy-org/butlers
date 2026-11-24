## Goal-runtime scope

This active change is re-scoped before archive by
`generalist-lead-focused-delegation/collision-dispositions.md` and that change's
task 4.2. RFC 0038 D1-D3, D7 and D18 govern the target below. This is a spec
amendment, not evidence of a switched runtime. Historical implementation and
owner-run evidence remain dated; no credential use, key mount, provider call,
probe, deployment, archive or task completion is authorized by this text.

## Why

The runtime can currently disagree with the dashboard about whether a model is
healthy. In the observed incident, a stale schema-local Codex credential
overwrote the newer public credential in the daemon's shared runtime volume;
the dashboard's isolated test passed while real routed sessions failed to
refresh. Independently, a prefix-stripping OpenCode execution mapper turned
provider-qualified catalog identities into invalid bare CLI arguments, and
breaker notifications could send duplicates because their debounce was a
non-atomic read-send-write sequence.

The owner needs one trustworthy answer to "is this runtime usable?" and one
bounded, explainable alert per actual outage episode. A dashboard probe,
best-effort telemetry write, or a different butler's stale local secret must
not change that answer.

## Goal-runtime amendment

- The goal runner resolves the explicit system-global Codex authority. Device
  authentication and runtime OAuth synchronization reach the session host's
  credential volume through the control-plane service listener. Neither the
  host nor its invocations read the database or credential store. Domain daemons
  hold no provider credential for launching reasoning sessions.
- Each goal invocation receives only its staged model credential, its own
  broker MCP config and permitted adapter-owned non-secret file pointers inside
  its isolated domain. The dashboard has no direct inbound path to the session
  host. Stale local credentials, unknown post-crash files and concurrent rotations
  cannot become authority; the existing atomicity, conditional persistence,
  health fencing and no-replay guarantees survive the move.
- Verification and benchmark probes remain D18 model functions: deterministic,
  tool-less, single-turn structured-output measurements using fixed prompts.
  They share the applicable provider credential, adapter/model mapping and
  generated configuration with invocation execution, not its broker access.
  They neither admit goals nor call domain capabilities, and never manufacture
  qualifying dispatch success or close a breaker.
- Canonical model identity, OpenCode provider/model mapping, serialized breaker
  edges, Switchboard-owned attention delivery, no ambiguous replay, explicit
  idempotent reissue and best-effort post-send observations remain unchanged.
  Goal execution supplies the qualifying dispatch evidence; attention remains
  deterministic service-plane work rather than a model tool or reasoning goal.
- The existing Models/Spend fact and degraded-source contracts remain in force
  until their separately governed D19 successor is verified at the switch.
  This amendment designs no replacement UI and changes no recipient approval.

## Retained pre-switch reliability work

The following original delivery scope records the reliability work and its
invariants. Its daemon/shared-home actor references describe the pre-switch
implementation; the goal-runtime owner and credential placement are those
above and in the amended deltas. None is a license to retain per-domain
reasoning or move domain credentials to the session host.

- Establish an explicit authoritative shared credential scope for
  `cli-auth/codex`. Runtime restore and rotation persistence will use that
  scope only; stale schema-local Codex rows are retained as ignored diagnostics
  and cannot overwrite shared runtime files. Existing authority behavior for
  other CLI providers is unchanged by this change.
- Preserve provider-qualified OpenCode Go catalog identifiers as the canonical
  identity used by pricing, spend rules, and history. Pass that provider-qualified
  identity unchanged as the current OpenCode `provider/model` execution argument
  only at the OpenCode adapter boundary, and run probes through that same
  translation and generated-runtime configuration path.
- Replace the breaker alert's audit-marker debounce with an atomic
  closed-to-open transition and a durable attention-delivery outbox. The
  Switchboard alone claims and sends queued attention; delivery is at-most-once
  for an alert episode, with ambiguous transport outcomes made visible as
  `uncertain` rather than automatically retried.
- Make post-send routing and attention-ledger telemetry best-effort so a
  confirmed Messenger response remains successful even when later bookkeeping
  fails. The migrated model-breaker and fleet-halt producers will no longer
  invoke `switchboard.*` delivery SQL directly.
- Apply the shared outbox to the fleet-halt owner page, and make the Models
  page distinguish a catalog probe from a routed-success breaker recovery.
  A fail-closed dashboard-owner control can inspect an uncertain alert and
  explicitly request a new alert episode; no automatic replay can create a
  duplicate page.
- Add migration-safe targeted grants, operator-facing diagnostics, traceable
  lifecycle state, and regression coverage for credential scope, concurrent
  breaker openings, worker restart, ACL isolation, post-send failure, and
  canonical-to-execution OpenCode Go mapping.

## Capabilities

### New Capabilities

- `runtime-attention-outbox`: Durable, Switchboard-owned, at-most-once
  operational-attention episode delivery with explicit terminal and uncertain
  states.

### Modified Capabilities

- `core-credentials`: `cli-auth/codex` authority, restore, rotation
  persistence, and safe conflict diagnostics become explicitly shared-scope
  behavior.
- `core-daemon`: daemon and connector startup restore CLI auth from the
  authoritative shared scope without per-butler overwrite ordering.
- `model-catalog`: breaker opening becomes an atomic transition that produces
  one alert episode while retaining canonical provider-qualified model identity.
- `runtime-opencode`: OpenCode model selection preserves its provider-qualified
  execution argument from that canonical identity only at the CLI boundary.
- `core-notify`: confirmed delivery is insulated from post-send bookkeeping,
  and attention records remain observations rather than idempotency authority.
  The notify-boundary attention-ledger requirement also drops
  `butlers.core.fleet_halt_attention` from its illustrative list of
  process-boundary-forced consumers, since this change deletes that module.
- `database-security`: the public outbox and dispatch-transition access receive
  narrowly scoped runtime-role grants; Switchboard retains delivery ownership.
- `dashboard-model-settings`: model verification, routed breaker state, alert
  delivery state, and explicit operator recovery are shown as distinct facts.
- `dashboard-spend-dashboard`: fleet-halt attention uses the shared durable
  outbox rather than an audit-marker debounce.

## Impact

- Core credential, daemon lifecycle, Codex auth synchronization, model routing,
  spawner provenance, and OpenCode runtime code.
- Switchboard notification routing, deterministic background delivery work,
  attention ledger/audit telemetry, and role-grant migrations.
- Dashboard API/frontend contracts for Models and fleet-halt status; no model
  catalog identifier or pricing-history rewrite.
- No credential values, existing local secret rows, historical dispatch
  attempts, notification records, or audit evidence are deleted by rollout.
