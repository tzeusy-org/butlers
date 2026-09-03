# Runtime Auth and Breaker Attention — Generation-1 Reconciliation

Bead `bu-0uqgo.7`. Audited head `02277aa88` (`main`); design baseline `b6460e852`.

This is a falsification record, not a completion certificate. Every claim below was traced to
code at the audited head, not to a sibling bead's diff or a green CI badge.

## Verdict

**Not complete.** Thirteen of the fifteen v1-mandatory requirements are implemented and
behaviourally verified. Two are implemented but **inert or unreachable in a deployed stack**, and
one mandatory `SHALL` clause was never written:

- **`REQ-runtime-attention-outbox-002` is satisfied only vacuously.** The producers are live; the
  delivery worker is never constructed. A breaker opening pages nobody.
- **`REQ-dashboard-model-settings-002` and the owner-gated half of
  `REQ-dashboard-spend-dashboard-001` are unreachable from a browser.** No frontend code path
  sends `X-API-Key`.
- **`REQ-dashboard-model-settings-001`'s owner-control clause is absent** from the Test and
  Verify-all routes.

Acceptance criteria 1, 2, 4 and 6 are discharged by this document. Criterion 3 is discharged for
the spec gates and partially for the test gates (see §3). Criterion 5 is **blocked** on owner
authorization and is expected to remain so (§5).

## 1. Requirement → implementation → evidence

Fifteen `Scope: v1-mandatory` requirement IDs; every one maps to an implementing child.
Verdict is about the requirement as a whole, not about whether code exists.

| # | Requirement | Child (commit) | Anchor evidence | Verdict |
|---|---|---|---|---|
| 1 | `REQ-core-credentials-001` Live Codex device-auth reconciliation | `bu-ih90b` (`8158691a4`) | `src/butlers/credential_store.py:349-363` strict channel; `src/butlers/core/runtimes/_codex_auth_sync.py:374-510` | **MET**, weak instruments (§2.1) |
| 2 | `REQ-core-daemon-001` Authoritative Codex restore at startup | `bu-ih90b` (`8158691a4`) | `src/butlers/lifecycle.py:254-258`; `src/butlers/daemon.py:1922-2004` | **MET**, wiring untested (§6 C6) |
| 3 | `REQ-core-credentials-002` Asymmetric probe-control capability | `bu-0uqgo.5/.10/.11/.12/.15` | `src/butlers/core/runtime_probe_control/keys.py`, `capability.py`; `src/butlers/cli_auth/sandbox_platform.py` | **MET in code**, adversarial evidence unrunnable (§3, C4) |
| 4 | `REQ-dashboard-model-settings-001` Test uses a runtime probe | `bu-0uqgo.10/.11` (`4227f87c1`, `bc0f3d749`) | `src/butlers/core/runtime_probe_control/coordinator.py`; `src/butlers/api/routers/model_settings.py:1737-1780` | **PARTIAL** — owner-control clause missing (C2) |
| 5 | `REQ-database-security-008` Probe receipt is Switchboard-owned | `bu-0uqgo.5` (`3b88be39c`) | `alembic/versions/core/core_201_runtime_probe_control_receipts.py` | **MET**, two boundary holes (§6 C7, C8) |
| 6 | `REQ-runtime-attention-outbox-001` Durable attention episodes | `bu-0uqgo.1/.2` (`62e8b1a8d`, `a6bdef571`) | `scripts/init-db.sql:4049-4143`, `:4585-4700`; `src/butlers/core/dispatch_outcomes.py:229-294` | **MET** |
| 7 | `REQ-runtime-attention-outbox-002` At-most-once delivery | `bu-0uqgo.3` (`4a723a017`) | `roster/switchboard/tools/runtime_attention/worker.py`, `outbox.py` | **VACUOUS** — worker never wired (C1) |
| 8 | `REQ-runtime-attention-outbox-003` Explicit uncertain reissue | `bu-0uqgo.3/.6` | `scripts/init-db.sql:2966-3044` (advisory lock, state gate, `ON CONFLICT`) | **MET** server-side; unreachable from UI (C3) |
| 9 | `REQ-database-security-007` Outbox least privilege | `bu-0uqgo.1/.2` | `scripts/init-db.sql:3732`, `:3818-3821`, `:3860-3873` | **MET**, carve-out never lifted (C5) |
| 10 | `REQ-model-catalog-001` Dispatch-outcome circuit breaker | `bu-0uqgo.2` (`a6bdef571`) | `src/butlers/core/dispatch_outcomes.py:86-98`, `:233-287` | **MET**; the push it produces is undelivered (C1) |
| 11 | `REQ-model-catalog-002` Canonical identity survives execution | `bu-0uqgo.4` (`8fc35b259`) | `pricing.toml:167-269`; `src/butlers/core/dispatch_outcomes.py:93-97` | **MET**, untested (§6 C10) |
| 12 | `REQ-runtime-opencode-001` Model selection | `bu-0uqgo.4` (`8fc35b259`, corrected by `2314aa481`) | `src/butlers/core/runtimes/opencode.py:57-70`, `:1024-1025` | **MET** (see §7.2) |
| 13 | `REQ-core-notify-027` Confirmed delivery not reclassified | `bu-0uqgo.3` (`4a723a017`) | `roster/switchboard/tools/routing/route.py:687-738`; `deliver.py:130-144` | **MET** for route/deliver; outbox half repaired here (§4) |
| 14 | `REQ-dashboard-model-settings-002` Episode visibility and reissue | `bu-0uqgo.6` (`4575544ce`) | `frontend/src/pages/SettingsModelsPage.tsx:1518-1548`; `src/butlers/api/routers/model_settings.py:596-665` | **UNREACHABLE** from a browser (C3) |
| 15 | `REQ-dashboard-spend-dashboard-001` Fleet-halt visibility | `bu-0uqgo.6` (`4575544ce`) | `frontend/src/pages/SpendPage.tsx:2462-2593`; `src/butlers/api/routers/spend.py:734-772` | **PARTIAL** — banner met, owner-gated observation unreachable (C3) |

### Task-checkbox reconciliation

Tasks 3.3, 3.5 and 3.6b were unchecked but **did ship**; their boxes are corrected in `tasks.md`
by this bead. Evidence:

- **3.3** — coordinator `src/butlers/core/runtime_probe_control/coordinator.py` (bounded deadline
  `:66`, global concurrency `:67`, per-entry de-duplication `:68`/`:332`, receipt committed at
  `:213-225` *before* `_probe` at `:227`); private endpoint `endpoint.py:63`, `:122-128`;
  Switchboard-only wiring `daemon.py:691-714`.
- **3.5** — `keys.py` strict parsers, exact field sets `:100-107`, `[70 s, 5 min]` overlap
  `:97-98`, `:393-395`, no environment or database fallback; runbook
  `docs/operations/runtime-probe-control-keys.md`.
- **3.6b** — Bubblewrap pinned `Dockerfile.base:39` and recorded in the image manifest `:89`;
  shim `scripts/runtime_cli_sandbox_init.c` (`close_range(3, UINT_MAX, 0)` `:45`, post-close audit
  `:53-72`); UID pool `sandbox_platform.py:80-107`; `pass_fds` allowlist `:304`, `:1103-1104`;
  pidfd acquired at `:1117` before payload release at `:1119`.

**3.4 stays unchecked**: everything shipped except its owner-control clause (C2).
**6.3 stays unchecked**: it is criterion 5, and is gated (§5).

## 2. Test-instrument audit (criterion 2)

Several universal-absence claims in this package are carried by source scans rather than by
executed behaviour. A scan is evidence only about what it inspects. Each instrument below is
recorded with its scan roots, its exclusions, and a construct **already present in this
repository** that it would not catch.

### 2.1 `tests/cli/test_runtime_cli_sandbox_completeness.py` — the CLI-bypass scan
Roots: `src/butlers/api/**`, `src/butlers/cli_auth/**`, and the single file
`src/butlers/jobs/secrets_staleness.py` — 135 of `src/butlers`'s ~560 files. Technique is a real
AST walk with import-binding resolution, which is the right shape. Blind spots:

- **`roster/*/api/router.py` is unscanned**, yet `src/butlers/api/app.py:39`, `:646`
  auto-mounts every one of them into the same FastAPI app that holds the signer.
- `__import__("subprocess").run(...)` evades `_dotted_name`. The scan's own blessed file uses that
  idiom: `src/butlers/cli_auth/sandbox_platform.py:798`, `:957`, `:1050`.
- `_DIRECT_CHILD_CALLS` omits `os.execv*`, `os.spawn*`, `pty.spawn`, `loop.subprocess_exec`,
  `anyio.open_process`.

### 2.2 `src/butlers/core/runtime_probe_control/activation.py` — the local-adapter guard
Checks three attribute names on two modules by `hasattr`. A **function-local** import
(`from butlers.core.runtimes.base import get_adapter` inside a route handler) never becomes a
module attribute and so evades it. That is the dominant idiom four lines away in the same package:
`src/butlers/api/routers/cli_auth.py:603`, `:630`; `src/butlers/cli_auth/health.py:151`, `:221`.
The guard's *effect* is behaviourally proven; its *inputs* are evadable.

### 2.3 `tests/core/test_runtime_attention_producer_cutover.py` — the direct-delivery scan
Roots: `inspect.getsource()` over exactly three modules (`spawner`, `dispatch_outcomes`,
`attention_ledger`). All of `roster/`, the rest of `src/`, and every line of
`scripts/init-db.sql` are excluded. Its forbidden-sink tuple omits `deliver` and `notify`, so a
page added as a function-local `from butlers.tools.switchboard.notification.deliver import deliver`
inside the breaker branch passes cleanly — and function-local imports are used throughout this
codebase (`src/butlers/core_tools/_notifications.py:1158`, `:1218`).
The absence claim in `REQ-model-catalog-001` rests on this scan alone. I re-derived it
independently by repo-wide grep and it **does hold at this head** — but it holds by luck of
current code, not because the instrument would catch a regression.

### 2.4 `tests/adapters/test_opencode_adapter.py::test_selected_model_translation_has_one_named_boundary_mapper`
Scans two functions for the substring `canonical_to_execution_model(` and the absence of
`.removeprefix(`. `model_id.split("/", 1)[1]` — which **exists at
`src/butlers/core/spawner_provider.py:77`** — is not blocked. The scan also cannot see the third
execution boundary added later (`coordinator.py:288-294`).

### 2.5 `tests/adapters/test_codex_auth_sync.py` — the mock re-implements the unit under test
`_mock_store` (`:73-140`) re-implements `load_codex_cli_auth` and the CAS writers in the test file,
delegating to `store.load`/`store.load_shared`. Assertions such as
`store.load_shared.assert_not_awaited()` are therefore tautologies over the double. A regression
that reintroduced local-first fallback inside the real `CredentialStore.load_codex_cli_auth` would
leave this 1800-line file green. There is **no** real-Postgres execution of the Codex authority
CAS anywhere in the suite.

### 2.6 String assertions standing in for state assertions
- `tests/config/test_credential_store.py:134-176` proves "value replacement atomically clears
  prior health state" with `assert "last_test_ok = CASE" in sql` against an `AsyncMock`. The
  statement is never executed.
- `tests/api/test_model_runtime_attention.py:161` proves "reissue writes no breaker row" by
  asserting the *SQL string* contains neither `model_dispatch_attempts` nor `notify`.

### 2.7 Instruments that silently do not run
- `tests/config/test_runtime_cli_sandbox_compose.py:30-32` skips when `docker` is absent, taking
  with it the only assertion over the seccomp profile and the
  `apparmor:unconfined`/`systempaths=unconfined`/no-`privileged` policy — while being marked
  `pytest.mark.unit` and shipped in the unit lane, where Docker is incidental.
- `tests/migrations/test_runtime_attention_producer_upgrade.py` carried **no `pytestmark` at
  all**, so it was invisible to `-m db`/`-m integration` selection and *errored* rather than
  skipped without Docker. Repaired by this bead.

**Conclusion for criterion 2.** Source scans do not currently supplement the behaviour tests
evenly: for `REQ-model-catalog-001`'s no-direct-delivery clause, for
`REQ-core-credentials-001`'s no-fallback clause, and for the one-mapper clause of
`REQ-runtime-opencode-001`, a scan is the *only* instrument. Those three are recorded as C9.

## 3. Gate results (criterion 3)

| Gate | Result |
|---|---|
| `openspec validate harden-runtime-auth-and-breaker-attention --strict` | **pass** — "Change ... is valid" |
| `make check-spec-overwrites` | **pass** — "No unfrozen baseline losses across 67 MODIFIED requirement(s) with debt" |
| Focused backend tests for the code changed here | pass (§4) |
| Real-Postgres ACL / concurrency / replay / fencing / reissue-race | **present and real** — `tests/integration/test_runtime_attention_delivery_worker.py`, `tests/integration/test_dispatch_outcome_recorder.py`, `tests/migrations/test_runtime_attention_outbox_migration.py`, `tests/api/test_runtime_probe_control_receipts_db.py` all execute against a live container |
| Adversarial concurrent-child and daemonized-descendant container isolation | **NOT RUN — see C4** |

The spec-overwrite ratchet reports this change's two `dashboard-model-settings` blocks
(`Catalog Verify-All API`, `Hourly Automated Verification Sweep`) as carrying *fewer* frozen
losses than baselined. That is a tightening opportunity, not a failure.

The baseline `openspec/specs/dashboard-spend-dashboard/spec.md:342-343` still describes the
retired `maybe_push_fleet_halt_attention` helper as current. This is ordinary baseline lag under
an unarchived MODIFIED block, not drift: `Fleet-Halt Visibility` does **not** appear in the
overwrite guard's loss list, so archiving this change resolves it correctly. Editing the baseline
by hand here would be exactly the overwrite hazard `AGENTS.md` warns about, so it was left alone.

## 4. Repairs made by this bead

Confined to defects that are unambiguous, low-risk, and independently correct.

1. **`roster/switchboard/tools/runtime_attention/worker.py` — a lost service lease was ignored.**
   `run_once` discarded `renew_service_lease`'s return value, documented at `outbox.py:187` as
   "`False` means it was lost". A worker whose lease had been taken over kept claiming and sending
   under a stale epoch — two delivery services on the wire, the one thing the lease exists to
   prevent. The cycle now stops before the next claim.
2. **Same file — a terminal transition that raised aborted the whole cycle.** `_record` was
   unguarded, so an exception from `mark_sent` propagated out of `run_once` and stranded every
   remaining episode. It is now reduced to a typed counter and a log carrying only the exception
   *class name*, never its message. This does not repair the deeper reclassification hazard (C11).
3. **`tests/migrations/test_runtime_attention_producer_upgrade.py`** — added the
   `db`/`integration`/Docker-skipif markers its sibling carries (§2.7).
4. **Requirement-ID citations on the frontend tests** (task 6.1), which had none:
   `frontend/src/pages/SettingsModelsPage.test.tsx`, `SpendPage.test.tsx`,
   `src/hooks/use-fleet-halt.test.tsx`.
5. **`tasks.md`** — 3.3, 3.5, 3.6b marked shipped.

New tests: `roster/switchboard/tests/test_runtime_attention_worker_cycle.py` (3 cases, cited to
`REQ-runtime-attention-outbox-002`). Both repair tests were confirmed **red against the unfixed
worker** and green after; the third is a control that passes either way.

## 5. Criterion 5 — blocked

Exact deployed-runtime evidence was **not** obtained and must not be. The bead's operational gate
forbids live key provisioning, deployment or restart, controlled breaker/fleet induction,
ambiguous-transport simulation, and resend until the owner authorizes the exact action. No
substitute was accepted: a dashboard `Test = OK` reading is not daemon-routed evidence, and is
explicitly ruled out by the bead.

Two of this document's findings make the point concrete rather than procedural: C1 means a
controlled breaker induction would today produce a durable episode and **no delivery**, and C4
means the sandbox's adversarial containment has never been executed against a real kernel. Running
the handoff matrix before C1 and C4 are resolved would measure a system that cannot pass it.

## 6. Gap register (criterion 6)

Dedupe-ready child candidates, severity-ordered. C1–C4 block the milestone.

| ID | Gap | Evidence | Shape |
|---|---|---|---|
| **C1** | **The delivery worker is never constructed or scheduled**, while producers are live. A breaker opening or ceiling breach creates a durable episode delivered to nobody. | `roster/switchboard/tools/runtime_attention/__init__.py:9-11` still declares itself unwired; no construction site exists repo-wide; producers fire at `src/butlers/core/dispatch_outcomes.py:274`, `:286` | P0 implementation child: wire the worker into Switchboard daemon startup with its lease and cadence |
| **C2** | **Test and Verify-all do not enforce `require_dashboard_owner_control`**, which `REQ-dashboard-model-settings-001` states as a `SHALL` (spec.md:93-97) with its own scenario. | `src/butlers/api/routers/model_settings.py:1010-1013`, `:1735-1740` declare only the DB dependency; `tests/api/test_model_settings_signed_probe_cutover.py:275` asserts the *ungated* behaviour | P0, **must ship with C3** |
| **C3** | **No browser path sends `X-API-Key`**, so every owner-gated surface is unreachable. Unset key → 503; set key → 401. | `frontend/src/api/client.ts:511-550` sets only `Content-Type`/`Accept`; zero non-comment hits in `frontend/src`; `docker-compose.yml:255`, `:848` default the key empty | P0. Fixing C2 alone would break the Test button in the default deployment — one child, not two |
| **C4** | **The adversarial container evidence for task 3.6b never executes.** Peer-stage access, daemonized-descendant survival, inherited-FD closure, and the `ENOENT`-on-signer proof the spec mandates (core-credentials spec.md:293-295) are all unrun. | `tests/cli/test_runtime_cli_sandbox.py:2769-2770` skips unless `BUTLERS_RUN_EXACT_IMAGE_SANDBOX_TEST=1`, which is set in no workflow, Makefile or script | P0 verification child; blocks criterion 3 |
| **C5** | Delivery-evidence columns are ungranted and never written, so the safe-reason chain through constraint, API and UI is permanently unreachable. | `scripts/init-db.sql:3870` omits `delivery_error_class`/`delivery_error_detail`/`notification_ref` from the Switchboard `UPDATE` grant; `outbox.py:317-347` never sets them | P1; the `REQ-database-security-007` carve-out was never lifted after the worker landed |
| **C6** | The daemon-side wiring of `REQ-core-daemon-001` is unasserted: no test proves `lifecycle.run_startup` passes `codex_authority`, and the degraded-evidence half of "authority loss is reported" has no test. | `src/butlers/lifecycle.py:254-258`; `tests/daemon/test_startup_coverage_gaps.py:277` calls `restore_tokens` directly | P1 test child |
| **C7** | The Dashboard's DB principal is the receipt table's owner and is exempt from a non-`FORCE` RLS policy, so it can read and write receipts directly. | `alembic/versions/core/core_201_runtime_probe_control_receipts.py:192`; `src/butlers/api/deps.py:505-507` disables `SET ROLE`; `tests/migrations/test_runtime_probe_control_receipts_migration.py:343` proves the owner *can* read | P1 |
| **C8** | `TRUNCATE` fires no row-level trigger, so the receipt retention bound is bypassable by the same owner principal, reopening every live replay window. | retention trigger is `BEFORE DELETE … FOR EACH ROW`, `core_201:162-167` | P1 |
| **C9** | Three universal-absence claims rest on a source scan alone with named blind spots: no-direct-delivery, no-schema-local-fallback, one-mapper. | §2.1, §2.3, §2.4, §2.5 | P1 test-instrument child |
| **C10** | `REQ-model-catalog-002`'s "canonical pricing/spend/history lookup remains intact" has **no test**: no `opencode-go/*` identifier appears in any pricing, spend, or cost test. | `tests/api/test_pricing.py`, `tests/api/test_spend.py`, `tests/core/test_compute_session_cost_usd.py` | P2 |
| **C11** | The transport budget exceeds every TTL, so a *live* claimant is fenced mid-send: worst case `3×30 + 1 + 5 = 96 s` against `SERVICE_LEASE_TTL_SECONDS = CLAIM_LEASE_SECONDS = STALE_SENDING_SECONDS = 60`. `LEASE_HEARTBEAT_SECONDS = 10` has no reader. A delivered page is then recorded `uncertain` and offered to the operator for reissue — an operator-driven duplicate. | `roster/switchboard/tools/runtime_attention/outbox.py:44-54`; heartbeat unread repo-wide | P1. The constants are marked "contract, not tuning", so this is a spec-level decision, deliberately not patched here |
| **C12** | `src/butlers/api/routers/cli_auth.py:736-737` locates the OpenCode model via `test_command.index("--model")`; the pinned command at `registry.py:166-171` carries a maintenance note telling the next engineer to repin it, and `registry.py:124` already uses the `-m` short form. A shape change raises inside a broad `except`, reporting a *credential failure* to the operator. | executed and reproduced at this head | P2 |

## 7. Findings falsified

Recorded so they are not re-raised.

1. **"A degraded attempts source renders as 'no denials'."** `src/butlers/api/routers/model_settings.py:2015-2019` returns an empty 200 on `UndefinedTableError`. That is **correct**: `docs/api_and_protocols/response-conventions.md:80-84` classifies a pre-migration missing table as legitimately absent, not a degraded source, and the spec scenario is scoped to "network error, non-2xx". Every genuine failure path does surface as unavailable.
2. **"The OpenCode mapper is a no-op, so the requirement is unmet."** `canonical_to_execution_model` is the identity function (`src/butlers/core/runtimes/opencode.py:70`), but the requirement at this head says qualified identifiers "are passed unchanged" — `8fc35b259`'s prefix strip was deliberately reverted by `2314aa481`, which rewrote the delta accordingly. The requirement is **met**. What remains is that the one-mapper property has no behavioural consequence and is enforced only by the scan in §2.4 (C9), and that task 3.2's parameterization is thinner than specified.
3. **"`docs/runtime/model-routing.md` describes retired helpers as current."** It does not — `:272-282` explicitly says both helpers were retired in PR 3742 and that nothing in the repository reads the markers today.
4. **"Reissue is a read-then-write race."** It is not: `scripts/init-db.sql:2994` takes `pg_advisory_xact_lock`, `:3009-3014` re-reads and gates on state, `:3034` has `ON CONFLICT (manual_reissue_of) DO NOTHING` backed by the partial unique index at `:4166-4168`, proven under real concurrency at `tests/integration/test_runtime_attention_delivery_worker.py:434-441`.
5. **"A legacy direct breaker/fleet delivery path survives."** It does not. `butlers.core.model_breaker_attention` and `butlers.core.fleet_halt_attention` no longer exist; `INSERT INTO public.model_dispatch_attempts` has exactly one site repo-wide (`src/butlers/core/dispatch_outcomes.py:93`). The claim holds — but only the scan in §2.3 would notice if it stopped holding.
