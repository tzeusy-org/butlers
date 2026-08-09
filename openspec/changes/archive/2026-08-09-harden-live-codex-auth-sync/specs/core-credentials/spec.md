## ADDED Requirements

### Requirement: Live Codex Device-Auth Reconciliation
The runtime SHALL treat the shared/public Tier 1 `cli-auth/codex` value as the
authoritative Codex device-auth state when its adapter is supplied a credential
store with a shared fallback; only flat topology SHALL use its local store as
authority. Before a new Codex subprocess is launched, it SHALL reconcile that
DB-backed value to the canonical local `~/.codex/auth.json` path when the
contents differ. The reconciliation SHALL never log credential content, SHALL
write a replacement atomically with mode `0600`, and SHALL refresh the local
rotation baseline after a DB-originated write.

#### Scenario: Dashboard refresh takes effect on the next invocation
- **WHEN** the dashboard has stored a newer `cli-auth/codex` value while a
  daemon remains running with a different local `auth.json`
- **THEN** the next Codex invocation SHALL use the stored value without
  requiring a daemon restart
- **AND** no completed or already-running session SHALL be changed or replayed

#### Scenario: Stale schema-local state cannot shadow a dashboard refresh
- **WHEN** a schema-isolated daemon has an older local `cli-auth/codex` row
  and the public/shared row contains a newer dashboard credential
- **THEN** Codex reconciliation and runtime-originated persistence SHALL use
  the shared row
- **AND** the local row SHALL not prevent the newer dashboard credential from
  reaching the next invocation

#### Scenario: Matching local token is left untouched
- **WHEN** the stored `cli-auth/codex` value exactly matches the canonical
  local `auth.json`
- **THEN** reconciliation SHALL not replace the file
- **AND** it SHALL record the existing file as the rotation baseline

#### Scenario: Reconciliation remains credential-safe under degradation
- **WHEN** the credential store has no `cli-auth/codex` value, cannot be read,
  exceeds the bounded best-effort synchronization wait, or the local
  replacement cannot be written
- **THEN** reconciliation SHALL log only safe context and SHALL not expose a
  raw credential value
- **AND** it SHALL not itself prevent the existing runtime invocation path

#### Scenario: Concurrent reconciliation cannot expose a partial file
- **WHEN** multiple local runtime invocations reconcile the same Codex
  auth-file path concurrently
- **THEN** every visible file state SHALL be a complete credential document
- **AND** the final file mode SHALL remain `0600`

#### Scenario: A stale runtime rotation cannot overwrite a dashboard refresh
- **WHEN** a Codex subprocess was launched with an older authority snapshot
  and the dashboard writes a newer `cli-auth/codex` value before that process
  finishes
- **THEN** post-invocation rotation persistence SHALL perform a conditional
  update using the launch snapshot
- **AND** its update SHALL be skipped when the shared value has changed

#### Scenario: A stale runtime health result cannot affect a dashboard replacement
- **WHEN** a Codex subprocess launched on an older authority reports an auth
  failure after the dashboard has stored a replacement credential
- **THEN** its credential health update SHALL be conditional on the exact
  credential bytes used by that subprocess
- **AND** it SHALL not mark the replacement credential failing

#### Scenario: Value replacement atomically clears prior health state
- **WHEN** a runtime health update for credential A obtains the row lock before
  a dashboard refresh or a winning runtime rotation replaces A with B
- **THEN** the value-changing write SHALL clear the prior test status, code,
  message, and verification timestamp in the same database statement
- **AND** B SHALL not inherit A's healthy or failing state

#### Scenario: Dashboard Codex probe binds to the canonical authority it tests
- **WHEN** a dashboard Codex test begins with a shared credential B while the
  canonical local auth file still contains A
- **THEN** the test endpoint SHALL reconcile the canonical file to B before
  running the provider status command
- **AND** it SHALL persist health, probe history, and audit evidence only when
  that file still matches B and the shared credential value remains B
- **AND** those durable records SHALL share the value-fenced credential-row
  transaction so a later replacement cannot interleave between them
- **AND** a concurrent replacement or local-file change SHALL leave the HTTP
  probe response intact while withholding its durable health result
- **AND** when the status command itself rotates B to B-prime, the endpoint
  SHALL finalize B-prime through the same B-bound conditional write while
  still withholding that probe's health, history, and audit result
- **AND** a concurrent shared replacement C SHALL win that conditional write
  and be reconciled locally rather than overwritten by B-prime

#### Scenario: Absent or unavailable authority is never implicitly bootstrapped
- **WHEN** the shared Codex credential is absent, revoked, unavailable, or
  malformed while a canonical local auth file exists
- **THEN** a runtime preflight or post-operation finalizer SHALL NOT create or
  recreate the shared credential from that local file
- **AND** it SHALL NOT attach a runtime health result to the absent authority
- **AND** explicit dashboard device authentication remains the supported
  bootstrap path for a new shared credential

#### Scenario: Direct dispatcher authority is explicit
- **WHEN** a direct `DiscretionDispatcher` has only a schema-local model pool
- **THEN** it SHALL not construct a Codex credential authority from that pool
- **AND** callers with a known shared/public credential pool SHALL pass it
  explicitly to the runtime adapter

#### Scenario: Unknown post-crash local state is recovered conservatively
- **WHEN** a fresh process has no launch-bound local rotation baseline and the
  canonical local auth file differs from shared credential authority
- **THEN** reconciliation SHALL apply the shared authority rather than infer
  that the local file is a valid successor
- **AND** durable cross-process rotation provenance remains follow-up
  `bu-gg4fo`

#### Scenario: Invalid stored auth preserves a valid local file
- **WHEN** the authority contains an empty or malformed non-object Codex auth
  document while the local canonical file is valid
- **THEN** reconciliation and startup restoration SHALL not replace that local
  file
- **AND** safe logs SHALL not disclose either credential value
