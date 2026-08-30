# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
```

Mutations auto-commit to the shared Dolt server (`dolt.parrot-hen.ts.net:3307`, db `butlers`);
there is no `bd sync` and no SQLite. See "Beads DB Mode" below.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run right-sized quality gates** (if code changed) - Targeted tests during active development; full suite only for final merge-readiness checks
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
   (Bead mutations already auto-committed to Dolt — no `bd sync` step exists.)
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

## Test Scope Policy

### Agent test ladder

**Target:** routine unit and integration lanes should each reach five minutes on
the reference CI runner. This is a staged performance goal, not evidence that
the current suite has met it. Do not meet it by dropping an invariant, wire,
privacy, authorization, retry, idempotency, or migration-outcome test.

1. Start each behavior change with the exact new or affected node, then widen
   to its owning file or package:

   ```bash
   uv run --no-sync pytest path/to/test_file.py::test_name -q --tb=short -n 0
   uv run --no-sync pytest path/to/test_file.py -q --tb=short
   ```

   `-n 0` is intentional for a single node or order-dependent debugging:
   `pyproject.toml` otherwise starts three xdist workers for every command.

2. Before choosing a broader scope, run the planner from the dirty worktree:

   ```bash
   make test-plan BASE=origin/main
   ```

   It prints suggested paths or `ESCALATE` reasons from committed, staged,
   unstaged, and untracked files. It **does not run pytest** and is never test
   evidence or merge-readiness evidence.

3. Include `roster/<butler>/tests/` explicitly for roster work. For a deleted
   or moved test, run collection on its surviving parent scope. For test
   fixtures, imports, module registration, or topology changes, run:

   ```bash
   uv run --no-sync pytest tests/ roster/ --collect-only -q -n 0
   ```

   Collection is additional topology evidence, not a substitute for the
   escalation required below when the change crosses a shared boundary.

4. Migrations, database/shared core, registry/discovery, root `conftest.py`,
   test tooling, `pyproject.toml`, Makefile, CI, and unknown source paths must
   escalate beyond a file-level run. State the relevant real-Postgres,
   contract, API, roster, or CI-shaped lane explicitly; never accept a guessed
   selector result as exhaustive coverage.

5. Do **not** run the broad lanes locally by default. At final merge readiness,
   push the exact head after targeted tests, collection, and hygiene checks,
   then use one terminal hosted CI run as the broad evidence. These
   receipt-producing targets mirror the pytest and coverage portions of CI's
   unit and integration steps when a local reproduction is genuinely needed:

   ```bash
   make test-ci-unit
   make test-ci-integration
   ```

   CI also runs static checks and a separate smoke/release-evidence step, so
   these targets alone are not a full `check`-job claim. Run only one broad
   Docker-backed lane at a time, and only one owner may run a broad local lane
   for an exact SHA from a **clean** worktree. `test-ci-*` records that SHA and
   refuses dirty state; any edit or rebase invalidates its receipt. Reviewers
   reuse only that matching clean receipt and run focused tests for their
   findings; they do not repeat an already-valid broad gate. A targeted PASS
   proves only its named scope; terminal hosted CI is the broad merge evidence,
   even though this repository's branch protection does not technically enforce
   it.

### Scope names are not interchangeable

- `make test-qg` is a local, receipt-producing regression gate. It runs
  `tests/` but omits `roster/`, root DB/migration suites, and E2E; do not call
  it the universal or CI-equivalent full suite.
- `make test-unit` selects only explicitly marked unit tests. It is not the
  routine fast lane: the marker currently selects thousands of cases.
- A command-line `-m` replaces pytest's default marker expression. Restate the
  nightly/bench/perf exclusions when using a custom marker expression, as CI
  does.

### Frontend CI gate order (knip masks build and test)

The `frontend` job in `.github/workflows/ci.yml` runs six steps in order: lint, em-dash copy gate,
query-result coercion gate, **Import graph (knip)**, build, test. knip sits *before* build and test,
so a knip failure marks the job failed with Build and Test **skipped** - the frontend suite never
runs. A local "full frontend suite green" is therefore not evidence the `frontend` job will pass,
and the CI failure will not appear in any test output.

Run `npm run knip` from `frontend/` before pushing. It flags unused exports and duplicate exports.
Two recurring shapes:

- A component with both `export function Foo` and `export default Foo` where every consumer uses the
  named import. Reported twice, as an unused export *and* a duplicate export; deleting the default
  clears both.
- A helper written but never wired up. Treat "unused export" as a question, not a verdict: check the
  bead's acceptance criteria before deleting, because the same finding covers both genuine dead code
  and a helper whose wiring was simply forgotten.

General rule this is an instance of: verify against the CI job's actual steps in `ci.yml`, not
against a remembered list of commands. A verification list assembled from memory omits exactly the
gate nobody remembers.

### Two unarchived OpenSpec changes can silently overwrite each other

`openspec archive` writes the **whole** requirement into the baseline. So when two unarchived
changes each carry a `## MODIFIED Requirements` block for the *same* `### Requirement: X`, and they
were authored against different ancestors, whichever archives **second** deletes everything the
first added. This is not baseline lag (the normal, healthy mid-change state); it is two deltas
racing to overwrite one requirement.

`openspec validate --strict` does **not** detect it. openspec 1.9.0's `findMissingCurrentScenarios`
guard (`dist/core/parsers/requirement-blocks.js:269`, shared by validate and archive) compares
scenario **names** only. A change that keeps every baseline scenario name and rewrites the bodies
inside them validates clean while overwriting. The guard therefore fires on the harmless case (a
rename) and stays silent on the destructive one.

Before archiving anything, grep the other unarchived changes for a same-named requirement block:

```bash
rg -l '^### Requirement: <Name>$' openspec/changes/*/specs/*/spec.md
```

If two hit, archive one, then **rebuild** the other's block against the refreshed baseline before
archiving it. Rebuilding means starting from the new baseline body and re-applying only that
change's own edits, then diffing the result against the baseline to prove nothing else moved.
Archiving also *arms* the bug for any remaining change holding a block on a spec you just rewrote,
so re-run the grep after each archive. Three instances of this had accumulated as of 2026-08-22
(bu-97nlt, PR #3755).

`make check-spec-overwrites` (CI job `spec-overwrite-guard`, `scripts/check_spec_overwrites.py`,
bu-s9uv3) now compares **bodies**: for every unarchived MODIFIED block it walks the live baseline
requirement clause by clause and reports each clause the block would delete on archive. Because the
repo carries ~360 pre-existing losses, `scripts/spec-overwrite-baseline.json` freezes them. The
ratchet is keyed on the **digest of the baseline clause being dropped**, not a count, so the moment
an archive moves a requirement under an unarchived block those losses change identity and the gate
fires. That is the signal, not churn: it is the arming event above. When it fires, rebuild the
block; only re-freeze with `--update-baseline` once you have confirmed the loss is intended.

The gate compares text, so it catches deletions, not contradictions: a block clause that *restates*
a baseline clause more broadly (older wording, wider scope) still covers its characters and reads as
preserved. Rebuilding is still the only way to be sure.

General rule this is an instance of: a validator that passes is only evidence about what it
inspects. Name-level equality was credited with an answer only a body-level diff could give.


<!-- bv-agent-instructions-v1 -->

---

## Beads Workflow Integration

This project uses [beads_viewer](https://github.com/Dicklesworthstone/beads_viewer) for issue tracking. Issues are stored in `.beads/` and tracked in git.

### Beads DB Mode (Dolt server)

This repo uses `bd` v1.0.x backed by the **shared Dolt server** on
`dolt.parrot-hen.ts.net:3307` (database `butlers`), discovered via `.beads/metadata.json`
(`dolt_mode: server`). There is no SQLite database and no `beads-sync` branch.

**Data flow:**
```
bd create/update/close  →  write directly to Dolt; each write auto-commits to Dolt history
bd export -o .beads/issues.export.jsonl  →  refresh the local gitignored JSONL mirror (optional; never commit it)
```

- **No `bd sync`.** That subcommand does not exist in this bd version. Dolt is the
  source of truth and persists every mutation immediately — you do not need a flush
  or sync step to make bead changes durable.
- **Never create `.beads/issues.jsonl`.** On bd 1.0.4 server mode its presence
  triggers a full-file re-import on every write that can wedge bd town-wide. The
  local gitignored mirror lives at `.beads/issues.export.jsonl` (`export.path` in
  `.beads/config.yaml`); `bd export` keeps it fresh. Dolt is the source of truth;
  never commit the mirror.
- **Repair:** if bd misbehaves (e.g. `database "..." not found`), run
  `bd doctor --fix --yes` from the repo root. For Dolt server health/cleanup use
  `gt dolt status` / `gt dolt cleanup`.

### Essential Commands

```bash
# View issues (launches TUI - avoid in automated sessions)
bv

# CLI commands for agents (use these instead)
bd ready              # Show issues ready to work (no blockers)
bd list --status=open # All open issues
bd show <id>          # Full issue details with dependencies
bd create --title="..." --type=task --priority=2
bd update <id> --status=in_progress
bd close <id> --reason="Completed"
bd close <id1> <id2>  # Close multiple issues at once
```

### Workflow Pattern

1. **Start**: Run `bd ready` to find actionable work
2. **Claim**: Use `bd update <id> --status=in_progress`
3. **Work**: Implement the task
4. **Complete**: Use `bd close <id>`

Mutations land in Dolt immediately — no export/sync step is required for durability.

### Worktrees

- All worktrees talk to the same Dolt server, so a bead created in one worktree is
  visible from any other immediately — no manual hydration/import step is needed
  for reads, **once bd has correctly discovered the shared server** (see below).
- **Required setup step for worktrees created via plain `git worktree add`** (not
  `bd worktree create`): copy the machine-local pointer file into the new
  worktree immediately after creation:
  ```bash
  cp .beads/metadata.json <worktree-path>/.beads/
  ```
  `./scripts/setup_worktree.sh` now does this automatically (in addition to
  its existing `frontend/node_modules` symlinking) — run it after creating a
  worktree instead of copying by hand. `.beads/metadata.json` is untracked
  (gitignored, machine-local) and is not carried into a fresh `git worktree
  add` checkout. Without it, bd's git hooks (`post-checkout`/`pre-commit`/etc.,
  invoked automatically on every `git checkout`/`git commit` in the new
  worktree) can fail to find the shared Dolt server pointer and fall back to a
  from-scratch import of the multi-MB `.beads/issues.export.jsonl` — an 8+
  minute operation that contends the shared `:3307` Dolt server (bu-dna8i).
  `bd worktree create` is reported to handle this automatically via
  git-common-dir discovery; plain `git worktree add` (the flow
  beads-orchestration workers actually use) does not.
- Separately, note that `core.hooksPath` and `.beads/config.yaml` are resolved
  by bd via `git rev-parse --git-common-dir`, i.e. **every worktree reads the
  hooks and config.yaml checked out in the main repo root** (`~/gt/butlers`),
  never a worktree-local copy of either file. This means: (a) editing
  `.beads/hooks/*` or `.beads/config.yaml` inside a worktree has no effect
  until the change is merged to `main` and the root checkout is refreshed —
  but (b) once merged and refreshed, the fix applies instantly to every
  existing and future worktree with no per-worktree copying required (unlike
  `metadata.json`, which is genuinely per-worktree local state).
- The `post-checkout`/`post-merge` JSONL→Dolt auto-import bd added in its own
  PR #3730 (GH#3729) is disabled via `export BD_IMPORT_AUTO=false` in
  `.beads/hooks/post-checkout` and `.beads/hooks/post-merge` (bu-dna8i). That
  feature exists for JSONL-in-git sync topologies with no shared Dolt server;
  it does not apply here (the shared server is already the single source of
  truth) and was costing 5-8 minutes per checkout re-importing ~5.6k issues
  row-by-row. `.beads/config.yaml` also sets `import.auto: false` as
  forward-compatible documentation, but **that line is currently a no-op on
  bd v1.0.4** — its YAML config loader does not wire up the `import.auto`
  key (confirmed via `bd config show` provenance: reports `(default)`, not
  `(config.yaml)`, even from a repo root whose own checked-out config.yaml
  sets it). The env var in the hook scripts is the actual, verified fix — it
  is placed outside the `BEADS INTEGRATION` managed markers so `bd doctor
  --fix` hook regeneration preserves it. Do not remove either without
  re-reading bu-dna8i's findings — `no-auto-import` (already `true` in this
  file) is a *different* config key and does not gate this behavior.

### Worktree node_modules

When working on frontend changes in a worktree created by `bd worktree create`, the frontend build toolchain (TypeScript, vitest) fails without `node_modules`. Rather than copy the entire cache (wasting disk space), the setup script symlinks `frontend/node_modules` from the main repo's copy, keeping your worktree in sync automatically when `package.json` changes.

Run this after creating a worktree:
```bash
./scripts/setup_worktree.sh
```

**Verify the symlink actually resolves to a populated directory.** As of 2026-08-22 the main repo's
`frontend/node_modules` on this machine is an EMPTY, root-owned directory, so the script happily
creates a symlink to nothing and the frontend toolchain (tsc, vitest, knip) fails in the worktree
with errors that look like missing source, not missing packages. `test -e` and `ln -s` both report
success on this, which is why nothing catches it. Check the count, not the path:

```bash
ls -A frontend/node_modules | wc -l   # 0 means the symlink is dead; run npm install in the worktree
```

Filed as bu-87osw. Until it is fixed, a plain `npm install` inside the worktree is the working
fallback (gitignored, costs disk per worktree).

The script symlinks `frontend/node_modules` and silently skips if the main repo hasn't run `npm install` yet. It is safe to run multiple times (idempotent).

### Worktree `.venv` must be real, never a symlink (bu-1redj)

`node_modules` is safe to symlink between worktrees. **`.venv` is not.** The venv holds an
editable-install `.pth` (`.venv/lib/python3.12/site-packages/_editable_impl_butlers.pth`) naming one
absolute source root. Symlink a worktree's `.venv` at the main repo's venv and that worktree's
`import butlers` resolves to **main's** `src/`, so a local test run validates main's code while
appearing to validate the branch diff. CI is unaffected (fresh checkout, fresh install) — what the
symlink destroys is local pre-push confidence. Observed 2026-08-24 on the `bu-istke.5` and
`bu-u7iwh` worktrees, and on `bu-erfdj`, whose worktree has since been removed.

Detection — the third line is the one that settles it:
```bash
ls -ld .venv                                                            # symlink or real dir?
cat .venv/lib/python3.12/site-packages/_editable_impl_butlers.pth       # which src/ is wired in?
./.venv/bin/python -c "import butlers; print(butlers.__file__)"         # must print THIS worktree
```

This now runs automatically: the root `conftest.py` refuses to start pytest when `butlers` resolves
outside `<checkout>/src`, naming the offending path and the repair. It hard-fails rather than warns
because the failure mode is false confidence, and a warning in a 40-minute run scrolls away.
`BUTLERS_ALLOW_EXTERNAL_PACKAGE=1` opts out for the rare deliberate case.

Repair, from inside the affected worktree:
```bash
rm .venv && uv sync --dev    # rm on a symlink drops the link only, never the target
```

**Never run `uv sync` / `uv pip` / `pip install` while `.venv` is still a symlink.** It operates on
the *linked* venv and can re-point main's `.pth` at a worktree, breaking main and every other
symlinked worktree at once. Check `ls -ld .venv` before any installer command.

### Key Concepts

- **Dependencies**: Issues can block other issues. `bd ready` shows only unblocked work.
- **Priority**: P0=critical, P1=high, P2=medium, P3=low, P4=backlog (use numbers, not words)
- **Types**: task, bug, feature, epic, question, docs
- **Blocking**: `bd dep add <issue> <depends-on>` to add dependencies

### Session Protocol

**Before ending any session, run this checklist:**

```bash
git status              # Check what changed
git add <files>         # Stage code changes
git commit -m "..."     # Commit code
git push                # Push to remote (bead mutations already in Dolt)
```

### Best Practices

- Check `bd ready` at session start to find available work
- Update status as you work (in_progress → closed)
- Create new issues with `bd create` when you discover tasks
- Use descriptive titles and set appropriate priority/type

<!-- end-bv-agent-instructions -->

---

## Notes to self

- Test-condensation counts: anchor the grep as `^[[:space:]]*(async[[:space:]]+)?def[[:space:]]+test_`. Not for async/indented tests — plain `grep -rc 'def test_'` already matches those (both give 12,322 in `tests/`, per-file diff empty). The anchor's real value is excluding commented-out and in-string matches (it changes `roster/` from 3,957 to 3,956). **Scope is what actually moves the number** — `tests/` alone is 12,322, `tests/ roster/` is 16,278 — so always state the scope beside a count, and keep historical phase figures separate from the current measurement.
- The protected restore-drill overlay must preflight `RESTORE_DRILL_EXECUTOR_PASSWORD_FILE` before `down`; otherwise Compose interpolation can report an arbitrary missing service after lifecycle work begins.
- Infrastructure identity-version provenance is opt-in: producers pass `Observation(identity_version=...)`; on the first higher-version successor they must also pass the explicit `predecessor_fingerprint`. A complete snapshot then stores reciprocal `metadata.identity_payload.predecessor`/`successor` links and the terminal `superseded_by_identity_version_bump` reason. That reason lives in **top-level `metadata.resolution_reason`** — the single home for every resolution path, explicit or supersede (bu-o4i4j); only lineage nests under `identity_payload`. `condition_ledger.RESOLUTION_METADATA_KEYS` (`resolution_reason`, `evidence_closed`) is refused at every producer boundary so the creation-wins resolution merge cannot swallow it. Never infer lineage from opaque fingerprints or rewrite historic rows; without that explicit link (or with an incomplete snapshot), ordinary snapshot-absence semantics remain.
- OAuth token refresh is implemented **more than once per provider**, in files whose class names read alike. Spotify has `SpotifyClient._refresh_access_token` (`connectors/spotify_client.py`) *and* `SpotifyConnector._refresh_access_token` (`connectors/spotify.py`, the polling connector) — fixing one leaves the other untouched, and a review finding naming "the connector" is ambiguous between them. Before closing any token-handling fix, grep the whole tree for `expires_in` / `access_token` extraction sites rather than trusting the reported symbol name.
- A 200 from a token endpoint is **not** a validated payload. The recurring bug shape is `data["access_token"]` plus `int(data.get("expires_in", 3600))`: it accepts `"3600"`, `True`, floats, negatives, and whitespace/non-string tokens, then persists them to a credential store. `spotify_credentials.parse_spotify_token_response` is the reference validator; `modules/calendar.py` and `modules/contacts/sync.py` show the equivalent shape for Google. Validate *before* assigning, or a rejected payload still leaves partially-mutated in-memory state.

- Recovering a file deleted by a bad commit: use `git checkout <deleting-commit>^ -- <paths>`, never copy from a leftover `bu-*/` bd worktree. A bead worktree is pinned at its branch point, so its copies are silently *older* than what was deleted — restoring from one reverts every commit that landed in between. Confirm a recovery with `git diff <deleting-commit>^ -- <paths>` (empty = exact) and blob-match suspect files with `git hash-object` against `git log --format=%H -- <path>` before trusting them.
- Two paths carry outsized blast radius and are easy to delete by an over-broad path glob: `src/butlers/api/app.py` (imported by ~132 test files via `create_app`) and `src/butlers/core_tools/_delegation.py` (imported by `core_tools/_dispatcher.py`, so losing it fails *every* butler daemon at import). Any bulk `git rm`/cleanup touching `src/` or `pr/` should be reviewed with `git show --stat` before pushing — see bu-q0po2.
- `GET /api/secrets/user/{provider}` is a **content-blind** contract (owner decision 2026-08-13): it publishes capability categories from `CAPABILITY_VOCABULARY` (`calendar`/`gmail`/`drive`/`health`/`connectivity`/`other`), never raw OAuth scopes, the persisted `entity_info.type` or `label`, or any audit `note` / probe `message` / failure tail. `_fetch_single_user_secret` returns an internal `_UserCredentialRecord` (mutation routes need `type`/`label`/`failure_tail` for OAuth revocation, guided-rotate gating, and the reauthorize account hint); `_content_blind_detail` is the only bridge from that record to *this endpoint's* DTO and builds it field by field on purpose, so adding a field to the record does NOT publish it here, and must not, without re-running the security review. It is **not** the only path from that record to a client: `POST .../reauthorize` returns the persisted `label` as `account_hint` inside `redirect_url`, and `POST .../probe` returns `failure_tail` (or provider response text) as `TestResult.message`. Both predate the content-blind work and are sanctioned by the current spec; audit them before assuming a value cannot escape. The content-blind treatment has since been extended: `GET /api/secrets/inventory` now returns `InventoryData` built from `_content_blind_summary` / `_content_blind_cli`, whose `UserSecretSummary`, `SystemSecretSummary`, and `CliRuntimeSummary` rows carry capability categories rather than raw scopes and drop `type`, `label`, `last_test_message`, and the probe message; the inventory's `CredentialAuditOutcome` carries only `ts`/`actor`/`action`, never a note. The system and CLI detail endpoints have had it too. Every one of those projections is written field by field on purpose -- adding a field to an internal record does not publish it, and must not, without re-running the security review.
- A test that needs a table from **another butler's migration chain** must build it from `src/butlers/testing/schema_standins.py` (e.g. `CONNECTOR_REGISTRY.ddl(schema="switchboard")`), never a local `CREATE TABLE`. A hand-copied column list passes in isolation forever and goes stale the moment the chain widens: the endpoint's `SELECT` raises, the route returns its DEGRADED envelope, and the test dies far downstream on a missing response key — five of the nine failures on PR #3853 were this. `tests/config/test_schema_standin_parity.py` diffs every declaration against the real chain and refuses new hand-rolled copies; a fixture that genuinely is not a query stand-in (a GRANT target, say) opts out with `# schema-standin-exempt: <why>`.

### Actor attribution is server-derived, never caller-asserted
`authenticated_principal()` in `src/butlers/api/audit_emit.py` is the single named place recording
that Butlers is a single-user deployment whose principal is `"owner"`. Any route that **persists** an
actor (an `updated_by` / `submitted_by` / `added_by` column) or **audits** one (`audit_append`,
`emit_dashboard_audit`) must take it from there. An actor that reaches the handler from request data
is not attribution — the caller can write anything — so an `actor: str = "dashboard"` field on a
request model is a forgery hole *and* a misattribution bug at the same time (bu-4y9ck, bu-6zlqt).
- Removing the wire field outright is a **breaking change** when the model sets `extra="forbid"`:
  older clients start getting 422. Use `IgnoresCallerAssertedActor` (same module) as the base
  instead — a `model_validator(mode="before")` strips the listed wire names before validation, so
  the model exposes no attribute a handler could trust, typo rejection still works, and legacy
  clients are ignored rather than rejected. Override `caller_asserted_actor_fields` when the legacy
  name is not `actor`.
- `tests/api/test_actor_attribution_sweep.py` holds the recorded classification of every API route
  and the mechanical guards that keep new ones honest: no request body may declare an actor field,
  and no route may read one from a query param or header outside two allow-lists. Add to the
  allow-lists only with a recorded justification; the guards assert their route set is non-empty
  first, because a lazy-mount bug would otherwise make them pass vacuously.
- Two look-alikes that are **not** this defect: `X-Butlers-Decision-Actor` on the approvals routes is
  server-*verified* (`_decision_actor_id()` 401s an unauthenticated claim and 403s a mismatched one),
  and `system.py`'s `actor_id` names the external egress *recipient* resolved from a server-side
  registry, not the requester.
- Enumerating routes by hand does not work here. FastAPI mounts included routers lazily, so
  `create_app().routes` holds `_IncludedRouter` objects, not `APIRoute`s — recurse into
  `route.original_router.routes` (2 routes vs 589). The body model is
  `route.body_field.field_info.annotation`; `.type_` no longer exists on pydantic v2 `ModelField`.

### Steam presence events are metadata-only AND routing-skipped
- Steam `status_change` / `online_status` envelopes keep `payload.raw = null` and `control.ingestion_tier = "metadata"` (persistence shape only — a metadata-only ref row still lands in `message_inbox`/`public.ingestion_events`).
- `ingestion_tier` alone does NOT bypass LLM classification or routing — that decision comes from the pre-resolved `triage_decision` sourced from a `scope='global'` `ingestion_rules` row, evaluated by `IngestionPolicyEvaluator` in `roster/switchboard/tools/ingestion/ingest.py::ingest_v1()` before `pipeline.process()` ever runs. Migration `025_switchboard_steam_status_skip.py` seeds a `rule_type='substring'` rule matching the `"steam:status:"` prefix of `external_event_id` (action=`metadata_only`) so status_change specifically skips LLM classification/routing/notify — a bare `source_channel='gaming'` rule would have over-scoped and silenced play/achievement/purchase/friend events too. `_make_ingestion_envelope()` in `ingest.py` surfaces `event.external_event_id` as `raw_key` for the `gaming` channel to make this substring match possible (the wire contract's `event` section is `extra="forbid"`, so there is no separate event-type field to match on directly).
- Play, achievement, library, and friend deltas remain full-tier and fully routable.

### Contributor workflow: PRs only, never push directly to main
All code changes must go through a pull request — never `git push origin main` or
`git push --force origin main`. GitHub branch protection (Settings → Branches →
"Require a pull request before merging") is the authoritative prevention mechanism
and should be kept enabled. Do branch work in a dedicated worktree; keep the repo
root on `main` (see CLAUDE.md "Repo Root Discipline"). See bu-ue37d.

### Session-link privacy guard (session-link-guard)
The `session-link-guard` CI gate (bu-mr5t5, a session-link leak/privacy gate)
blocks agent session URLs in PR titles, bodies, and comments, plus non-trailer
commit text. The sole allowed exception is the exact terminal Git trailer
`Claude-Session: https://claude.ai/code/session_...`, which the owner's Claude
Code setup adds automatically. That URL remains forbidden in every PR surface,
in comments, and anywhere else in a commit message; a "🤖 Generated with ..."
block carrying a session link remains prohibited. A plain `Co-Authored-By:`
trailer without a URL is also fine. Dispatch prompts need not strip the exact
allowed Claude trailer, but must keep PR metadata clean. Tripping the gate still
costs a reviewer an amend + force-push + full CI re-run per PR (a PR-body-only
edit is not re-read by the gate until a fresh `synchronize` event — the workflow
reads the event's body snapshot, and manually re-running the failed job in the
GitHub Actions UI reuses that same stale snapshot rather than fetching the
current body). See bu-ya2cv.

### Core migration optional-schema guard contract
- Core-chain migrations must tolerate fresh/core-only databases where specialist schema tables are absent; cross-schema `ALTER/UPDATE/GRANT` statements should guard with `to_regclass(...)` / information_schema checks instead of assuming `education.*`, `general.*`, etc. always exist.
- `to_regclass('other_schema.tbl')` is NOT a safe existence probe at *runtime* under `SET ROLE` isolation: resolving a schema-qualified name needs USAGE on that schema, so it raises `InsufficientPrivilegeError` instead of returning NULL. Migrations run as an admin role and are unaffected; per-butler daemon code is not. Probe reachability first — `SELECT COALESCE((SELECT has_schema_privilege(oid,'USAGE') FROM pg_namespace WHERE nspname='<schema>'), false)` (subquery, not `EXISTS(...) AND has_schema_privilege(...)`: Postgres does not guarantee AND short-circuits and the privilege call errors on an unknown schema). Without it a broad `except Exception` turns the designed posture into a per-butler traceback at every startup — `owner_bootstrap._seed_owner_telegram_handle` logged ten of them per boot.

### relationship.facts is a multi-valued log store — contradiction detection must allowlist functional predicates
`relationship.facts` is predominantly append-only, multi-valued log data: `activity` and `interaction_*` legitimately carry many active rows per `(entity_id, predicate)` (one entity had 117 active `activity` rows). "Contradiction" = two active rows on the same `(entity_id, predicate)` with differing content is ONLY meaningful for FUNCTIONAL (single-valued) predicates. `run_fact_retraction_curation` (`roster/relationship/jobs/relationship_jobs.py`) gates contradiction detection to `_CONTRADICTION_FUNCTIONAL_PREDICATES` (an allowlist — fail-safe so a new log predicate can never re-flood). The unregistered facts-store predicates are NOT in `relationship.entity_predicate_registry` (that registry is for the kebab-case `entity_facts` RDF store), so cardinality can't be read from there. An over-broad detector once parked ~930 false `memory_forget` approvals overnight (115 real groups × ~8 rows).

### Owner-entity approval carve-out: scoping + cross-schema recognition (RFC 0017 §2.3)
- Owner-entity mutations PARK for approval unless the `src` is in `_OWNER_AUTO_APPLY_SOURCES` (`roster/relationship/tools/relationship_assert_fact.py`) = owner-self/owner-bootstrap (identity self-registration) ∪ `_TRUSTED_INTERNAL_SOURCES` (structured internal derivation; currently just `interaction_sync`). Prose/text-extraction jobs (e.g. `memory_curation` edge promotion) are deliberately NOT trusted — a mis-extracted owner fact is the RFC 0017 incident class. The dashboard API models (`roster/relationship/api/models.py:_reject_trusted_internal_src`) must reject any auto-apply src from HTTP callers; the MCP wrapper hardcodes `src="relationship"`.
- The approval gate's owner detection reads `relationship.entity_facts`, which non-relationship butlers (messenger/home) cannot — schema isolation via `SET ROLE butler_<schema>_rw` (`src/butlers/db.py`). Owner-directed sends from those butlers therefore parked as "unresolvable target". Fix: `public.resolve_owner_triple(predicate, candidates[])` SECURITY DEFINER function (migration `core_145`) does the owner-only triple lookup as its owner; `identity.resolve_owner_channel_via_definer()` calls it as a gate fallback when normal resolution returns None. Preserves the `is_primary` requirement (RFC 0017 §2.1). The channel→predicate mapping + telegram normalization stay in Python to avoid SQL/Python drift.

### Detail-page snapshot baseline (bu-sfeuw.1 / Gate-A)

Ten `*DetailPage.tsx` components are baseline-snapshotted as of 2026-05-10 (branch
`agent/bu-sfeuw.1`). Tests live in `frontend/src/pages/` alongside each page.

**Shell architecture:**

| Page | Shell | `<Page archetype="detail">` | Breadcrumbs owned by |
|------|-------|-----------------------------|----------------------|
| ButlerDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| ContactDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| ConnectorDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| EntityDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| EpisodeDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| FactDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| RuleDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| SessionDetailPage | raw `<div>` | **no** | raw `<Breadcrumbs>` |
| QaPatrolDetailPage | raw `<div>` | **no** | raw `<Breadcrumbs>` |
| QaInvestigationDetailPage | raw `<div>` | **no** | raw `<Breadcrumbs>` |

No page uses a Tier-2 hero (PulseStrip) unless the record has an associated entity
(ContactDetailPage conditionally renders PulseStrip when `entity_id` is set).

**Gate-A changes that invalidate snapshots:**

- Migrating Session / QaPatrol / QaInvestigation from raw `<div>` to `<DetailPage>`:
  invalidates single-H1 count (loading state goes from 0 to 0, loaded state remains 1),
  adds `max-w-5xl` constraint, and changes breadcrumb ownership — updates needed in all
  three `*DetailPage.test.tsx` files' "slot composition baseline" suites.
- Adding a PulseStrip to Session / QaPatrol / QaInvestigation: invalidates the
  "no Tier-2 hero" baseline assertions in those files.
- Changing the `<Page>` HeadingBlock to suppress the `<h1>` during loading: all
  "renders zero H1s in loading state" assertions across all 10 files would need updating.
- Renaming H1 titles (e.g. "Patrol Detail" → something else): invalidates the
  `H1 contains '...'` assertions in QaPatrolDetailPage.test.tsx and
  QaInvestigationDetailPage.test.tsx.

### Runtime timeout propagation contract
- `Spawner._run()` must forward the effective `session_timeout_s` into `runtime.invoke(timeout=...)`, not just wrap the call in outer `asyncio.wait_for(...)`; otherwise adapter-specific inner timeouts can drift from session records and produce misleading mixed timeout behavior (observed in QA self-healing Codex runs).

### Scheduled memory consolidation runtime contract
- The deterministic `memory_consolidation` handler must supply the daemon's registered live `Spawner` but resolve its database pool and embedding engine through the active MemoryModule hook. Calling the embedding helper directly loses custom model/cache lifecycle; using the daemon pool breaks private memory schemas such as Chronicler's `chronicler_mem`. Missing module or Spawner wiring fails closed so the scheduler records the error.

### Empty-response failover contract
- `Spawner` must merge adapter-reported and daemon-captured tool calls before accepting a normal return with no result text. No text plus no confirmed non-command MCP action is an empty-response failure even when token usage exists; same-tier retry is safe only when the merged tool-call list is empty. Command-execution evidence suppresses retry because shell work may have side effects, while a confirmed MCP tool-only completion remains successful.
- OpenCode exit 0 with no result text, no tool calls, no token usage, and empty stderr is rejected earlier by `OpenCodeAdapter` with the same classifier-eligible posture.

### Model catalog timeout authority contract
- `public.model_catalog.session_timeout_s` is the authoritative per-session runtime timeout for catalog-resolved runs; `resolve_model()` returns it, `Spawner` uses it for normal butler sessions, and `DiscretionDispatcher` uses it for discretion-tier direct adapter calls.
- Per-butler `runtime_config` is operational-only (`core_groups`, `max_concurrent`, `max_queued`); model/runtime selection, extra args, and per-session timeout must not be reintroduced there or the `/settings` model catalog stops being authoritative again.
- `[butler.runtime_seed]` is also operational-only after the cleanup pass: keep only concurrency/core-group/registration seed fields there, put runtime adapter type under top-level `[runtime]`, and keep model selection/args/timeouts in the model catalog.

### Finance overview N+1 query optimization pattern
- The subscription_audit function in `roster/finance/tools/overview.py` implements batched query optimization for fetching subscription charge dates: use single LEFT JOIN with GROUP BY instead of per-subscription queries. This pattern should be replicated for any overview/analytics tool that needs to correlate multiple parent entities with their most recent related transactions or events. The key is `COALESCE(MAX(CASE WHEN condition THEN field END), fallback)` to handle entities with no related rows.

### Compose base-image invalidation contract
- `scripts/compose.sh` rebuilds `butlers-base:latest` when the `butlers.base.dockerfile_sha` image label differs from the current `Dockerfile.base` SHA; pinned runtime CLI bumps must happen in `Dockerfile.base` (not live npm `latest` checks), and app-image rebuilds alone are not enough to pick up base-layer tool additions like `gh`.

### Compose MCP listener port reservation contract
- Butler MCP ports `41100-41111` are within Linux's default ephemeral range. Both `butlers-up` and `butlers-up-hotreload` must set `net.ipv4.ip_local_reserved_ports=41100-41111`; otherwise an outbound DB connection can claim a listener port before daemon startup and cause a persistent `port still in use` failure.

### Owner entity bootstrap conflict contract
- `_ensure_owner_entity` in `src/butlers/daemon.py` must first resolve an existing owner via `WHERE 'owner' = ANY(roles)` before attempting insert, and the insert must use `ON CONFLICT DO NOTHING` (no explicit conflict target) so the partial unique index `shared.ix_entities_owner_singleton` cannot raise `UniqueViolationError` during startup.

### Runtime args passthrough contract
- Runtime args are sourced solely from `public.model_catalog.extra_args` — there is no butler.toml fallback. The catalog's `extra_args` array is forwarded verbatim to the adapter as `runtime_args`.
- `Spawner` forwards non-empty runtime args into adapter invocation as `runtime_args`, and `CodexAdapter` appends them to `codex exec` before the `--` prompt delimiter (supports flags like `--config model_reasoning_effort="high"`).

### Runtime config cache invalidation contract
- `RuntimeConfigAccessor.invalidate_cache()` must set `_cache_time` to an always-expired value (for example `float("-inf")`), not `0.0`; `0.0` only invalidates once process uptime exceeds the TTL and can leak stale config on fresh processes/CI workers.

### Finance transaction dedup provenance contract
- In `roster/finance/tools/transactions.py`, composite same-day dedup should only run when there is additional provenance (`account_id` or `source_message_id`); source-less manual rows must remain insertable as distinct transactions to avoid false-positive collapse.

### Recovery workflow accounting contract
- Recovery/session timeout semantics are split: `session_timeout_s` bounds one spawned runtime session, while higher-level healing/QA workflows own any broader investigation deadline.
- Pre-launch gate rejects (cooldown, concurrency cap, circuit breaker, no-model) should be tracked as dispatch decisions, not written as failed `healing_attempts`, or breaker state and operator history become polluted.

### QA circuit breaker reset contract
- QA circuit-breaker state must use the same launched-attempt filter everywhere: count rows with `healing_session_id IS NOT NULL` plus the synthetic dashboard reset sentinel `status = 'manual_reset'`, while still excluding orphaned gate-rejection rows.
- The dashboard summary, `/api/qa/circuit-breaker`, `/api/qa/circuit-breaker/reset`, and `src/butlers/core/qa/dispatch.py::_is_circuit_breaker_tripped` must stay aligned or the UI can report a reset while dispatch still suppresses new QA investigations.

### QA recursion provenance drift
- The intended QA self-recursion barrier depends on `qa_findings.source_session_trigger_source`, but current ingress/discovery paths do not populate it end-to-end: `modules/qa.report_finding` omits `trigger_source`, `core/qa/sources/butler_reports.py` does not store it, and `core/qa/sources/session_records.py`/`log_scanner.py` do not extract it, even though `core/qa/dispatch.py` and `/api/qa/meta-review` already rely on it.

### QA source-type schema contract
- Adding a QA discovery source is a persisted vocabulary change: keep `QaConfig.enabled_sources`, `_KNOWN_SOURCES`, source-emitted `QaFinding.source_type`, and `public.qa_findings.ck_qa_findings_source_type` aligned in the same change, with a migrated-DB regression test that inserts the new `source_type`.

### QA doctrine doc drift
- `about/heart-and-soul/architecture.md` still describes QA as a future staffer, while `about/README.md`, `roster/qa/`, and the live daemon topology treat QA as a current third staffer; when reconciling doctrine, prefer roster + spec state over that stale paragraph until the pillar doc is corrected.

### PR merge from worktree contract
- The exact-base helper is the sole final merge route. Do not use bare REST merge requests, `gh pr merge`, or `--auto`; these bypass its exact-base audit. After a `merged-exact-base` result, delete the head ref separately when needed.

### Exact-base REST merge contract
- GitHub's REST merge endpoint conditionally accepts only the PR head `sha`; it has no atomic expected-base-SHA parameter. A SHA-pinned REST merge can therefore land on a base that advanced after final exact-head revalidation.
- Capture `headRefOid`, the live target-branch ref name, and its live ref SHA (not the PR's potentially stale `baseRefOid`) together during final terminal-green hosted CI / independent-review revalidation. Every hosted check must be terminal green, regardless of branch-protection required-check configuration. Then use `python3 scripts/merge_pr_exact_base.py --pr <n> --expected-head <head> --expected-base-ref <ref> --expected-base <base>` rather than a bare REST request, `gh pr merge`, or `--auto`. The helper keeps SHA pinning, rejects pre-request head/target-ref/base drift without issuing a merge, then re-reads the merged PR's retained `baseRefName` through GraphQL before auditing the resulting squash commit's sole parent and immutable result tree.
- Only `merged-exact-base` (`source_bead_closure_allowed: true`) permits source/review Bead closure. `premerge-head-drift`, `premerge-base-ref-drift`, or `premerge-base-drift` requires a rebase onto current `origin/main` followed by fresh CI and independent exact-head review. `postmerge-base-ref-drift` (including a failed post-merge GraphQL lookup), `postmerge-base-drift`, `postmerge-patch-drift`, and `postmerge-unexpected-squash-parent-shape` are nonzero, already-merged classifications: keep the source Bead open and run the documented post-merge audit/investigation; never portray any as exact-current-base evidence. The last classification records parent evidence but found a non-squash parent shape. For patch proof, the audit must record matching `expected_patch_tree_sha` / `landed_patch_tree_sha`: coupled with the verified sole parent equal to the reviewed base, immutable tree equality proves the same net patch without relying on local Git state.

### Batch merge target-branch health contract
- A pull request's CI can only see its own branch, so a batch that checks each PR and never reads
  main between merges will happily land PR N+1 on a main that PR N already broke. That is bu-vul8u:
  two PRs each numbered a migration `core_204`, both green, and the post-merge "Migration Chain
  Integrity (main)" workflow went red on the merged tree exactly as designed while nothing read it.
- `scripts/merge_pr_exact_base.py` now consumes `scripts/main_health_gate.py` before it sends the
  merge request. `premerge-target-branch-red` (exit `6`) halts the batch; `premerge-target-branch-
  health-unknown` (exit `7`) means wait and repeat. `--acknowledge-target-red <workflow-filename>`
  merges past one named red so the fix for a red main can land; any other red still halts.
- Between merges, run `python3 scripts/main_health_gate.py --tree <scratch worktree>
  --sync-tree-to origin/main --wait-seconds 300`. Use a dedicated scratch worktree, never the repo
  root; the script refuses to reset anything that is not a linked worktree.
- Four absences look identical on the wire and mean different things: path-filter excluded
  (proceed), run not created yet (wait), run in flight (`gh --json conclusion` returns the empty
  string, not null -- wait), and cancelled (UNKNOWN, never green). "No run" is UNKNOWN, not pass.
- Main structurally cannot earn a green CI verdict mid-batch: `ci.yml`'s concurrency group is keyed
  on the branch ref with `cancel-in-progress`, so every push cancels the previous run. The gate
  therefore never asks `ci.yml` for a verdict and computes green locally instead, from guards
  enumerated out of the tree under test rather than a hardcoded list that would go stale the moment
  a new repo-wide guard lands.

### Calendar projection linkage schema contract
- Core `scheduled_tasks` now includes calendar-linkage columns (`timezone`, `start_at`, `end_at`, `until_at`, `display_title`, `calendar_event_id`) with bounds checks and a partial unique index on `calendar_event_id`.

### Required-schema module startup gate
- `ButlerDaemon` now filters `load_all()` results via `_select_startup_modules`: if a module defines required `config_schema` fields and its `[modules.<name>]` section is absent, startup skips that module (info log) instead of config-failing on missing required fields.
- This keeps intentionally omitted modules (for example `contacts` on `messenger`/`switchboard`) out of migrations/startup/tool registration and prevents provider-required warning noise.

### FastMCP test introspection contract
- FastMCP in this repo/toolchain exposes async `get_tool(name)` and may not expose `get_tools()` or private `_tool_manager`; metadata/introspection tests should use public `get_tool` (or a `get_tools` fallback) instead of private manager internals.

### Scheduler job_args JSONB contract
- In scheduler code paths, `job_args` JSONB values can round-trip through asyncpg as JSON strings; writes should serialize dict payloads explicitly, and reads should normalize back to dicts before diffing, validation merges, list responses, or dispatch payload assembly.

### Manifesto-driven design
Each butler has a `MANIFESTO.md` that defines its public identity and value proposition. Features, tools, and UX decisions for a butler should be deeply aligned with its manifesto. The manifesto is the source of truth for *what this butler is for* — CLAUDE.md is *how it behaves*, butler.toml is *what it runs*. When proposing new features or evaluating scope, check the manifesto first.

### Calendar module config reminder
- Calendar configs run through `src/butlers/daemon.py::_validate_module_configs`, which loads the module's `config_schema` and rejects extra/missing fields; `CalendarConfig` requires `provider`, while `calendar_id` is optional and resolved during startup when omitted.

### Calendar projection sync contract
- `CalendarModule._sync_calendar` now materializes unified projection rows: provider deltas upsert into `calendar_events` + `calendar_event_instances`, and internal scheduler/reminder sources refresh into the same tables with deterministic `origin_ref` linkage (`scheduled_tasks.id` / native `calendar_events.id`).
- Projection checkpoints are persisted in `calendar_sync_cursors` (`provider_sync` for provider pulls, `projection` for internal sources), and each sync refresh records action status in `calendar_action_log`.
- `calendar_sync_status` and `calendar_force_sync` now include `projection_freshness` (`last_refreshed_at`, `staleness_ms`, per-source `sync_state=fresh|stale|failed`); projection writes hard-gate on strict `to_regclass(...) IS TRUE` checks so pre-migration DBs/tests safely no-op.
- Dashboard `calendar_force_sync(queue=true)` commands are durable `calendar_action_log` rows: the module worker starts even when normal polling is disabled, leases one `running` command at a time, coalesces a single pending successor, and restart recovery merges/requeues interrupted work without losing full-recovery intent.

### Calendar-native reminder provider mirror contract
- Runtime reminder lifecycle and target resolution are native-only (`calendar_events` with `source_kind='internal_reminders'`); the retired physical `reminders` table is migration history, not a runtime fallback or test fixture.
- The dedicated Butlers provider-calendar mirror stores its durable provider id in `calendar_events.metadata.provider_event_id`. Refreshes update that same event with description/body, location, and RRULE recurrence; active ids participate in orphan protection, and series deletion removes the provider copy before deleting the authoritative local row.

### Calendar workspace contract coverage
- Calendar workspace frontend tests should cover URL-backed view toggles (`view=user|butler`), butler-lane rendering/grouping, and both user/butler create-edit mutation payload shapes.
- `docs/frontend/backend-api-contract.md` calendar section must include mutation endpoints (`/api/calendar/workspace/user-events`, `/api/calendar/workspace/butler-events`), v1 recurrence scope limitation (`series` for provider recurring updates/deletes), and projection/request-id telemetry fields (`projection_freshness`, `request_id`).

### Contacts module sync contract
- The contacts module is expected to run its incremental sync as an internal poll loop, not as a standalone connector (see `docs/modules/contacts_draft.md` §8); the default cadence is an immediate incremental run on startup, recurring polling every 15 minutes, and a forced full refresh every 6 days before the sync token expires.
- Modules load inside `butlers up` via `ButlerDaemon.start()` (`src/butlers/daemon.py:852-931`), so the poller should live in-process; `scripts/dev.sh` already launches `uv run butlers up` and required connector panes, so no extra standalone contacts connector bootstrap is needed.
- Contacts rollout contract: enable `[modules.contacts]` with `provider = "google"` and `sync` defaults (`run_on_startup=true`, `interval_minutes=15`, `full_sync_interval_days=6`) in `roster/general/butler.toml`, `roster/health/butler.toml`, and `roster/relationship/butler.toml`; intentionally exclude `roster/switchboard/butler.toml` (routing plane) and `roster/messenger/butler.toml` (delivery plane).
- The concrete runtime contract lives in `src/butlers/modules/contacts/sync.py::ContactsSyncRuntime`; mode selection is state-driven (`full` when no cursor or stale >=6 days, otherwise `incremental`) and poller trigger surface is `trigger_immediate_sync()`.

### Relationship interaction-sync ACL contract
- `roster/relationship/jobs/relationship_jobs.py::run_interaction_sync_job` reads `switchboard.message_inbox` directly, so `scripts/init-db.sql` must grant `butler_relationship_rw` read-only access to schema `switchboard` plus `SELECT` on its tables (and matching default privileges for future migration-user-created tables).

### Relationship contacts sync trigger API contract
- `POST /api/relationship/contacts/sync` is the manual dashboard/API trigger for contacts sync and dispatches to the relationship butler MCP tool `contacts_sync_now` with args `{"provider":"google","mode":"incremental|full"}`.
- The `mode` query parameter is strict (`incremental` or `full` only), and credential-related MCP failures are surfaced as actionable `400` errors pointing operators to `/api/oauth/google/start` or `/api/oauth/google/credentials`.

### v1 MVP Status (2026-02-09)
All 122 beads closed. 449 tests passing on main. Full implementation complete.

### One-DB runtime topology contract (butlers-1003.5)
- `[butler.db]` is schema-aware: when `name = "butlers"`, `schema` is required (explicit target schema, no implicit fallback).
- `Database` / `DatabaseManager` apply schema-scoped `search_path` (`<schema>,shared,public`; shared pool uses `shared,public`) for one-db pool resolution.
- API startup (`init_db_manager`) treats one-db topology as canonical shared-credentials path (`db=butlers`, schema `public`).
- Daemon migration URL generation includes libpq `options=-csearch_path=...` when a schema is configured so Alembic runs in the intended schema context.

### dev.sh Gmail OAuth rerun contract
- In `dev.sh`, `_has_google_creds()` must check the same credential DB set as the OAuth gate (`_poll_db_for_refresh_token`), plus legacy/override DB names where applicable, so preflight and gate do not disagree.
- Build the Gmail pane startup command at Layer 3 launch time (after OAuth gate), not once during early preflight; otherwise reruns can keep showing the stale "waiting for OAuth" pane even when credentials already exist.
- For pane logs, prefer wrapping the launched command with stdout/stderr tee capture (`_wrap_cmd_for_log`) instead of raw `tmux pipe-pane`, so log files contain process output rather than interactive shell prompt/control-sequence noise.

### dev.sh OAuth shared-store contract
- `dev.sh` OAuth preflight (`_has_google_creds`) and Layer 2 gate (`_poll_db_for_refresh_token`) query `public.contact_info` for the owner contact's `google_oauth_refresh` row (one-db mode, schema `public` by default, overridable via `BUTLER_SHARED_DB_NAME`/`BUTLER_SHARED_DB_SCHEMA`).

### Dev debug access contract
- In repo-root dev workflows, use `docker logs` as the primary debugging surface for compose services rather than the local `logs/` folder, and build `psql` commands from `.env.dev`; `POSTGRES_DB` may be unset there, with compose/scripts defaulting the database name to `butlers`.

### butlers-dev path split contract
- The dev dashboard and API are served on different Tailscale path prefixes: `/butlers-dev/` is the Vite frontend, while live JSON APIs are under `/butlers-dev-api/api/...`; probing `/butlers-dev/api/...` returns the frontend HTML fallback, not backend JSON.

### Compose live-log inspection contract
- For the `butlers-dev` Docker compose stack, authoritative live run logs are inside the containers under `/app/logs/...` (for example `/app/logs/butlers/*.log` and `/app/logs/<run>/butlers/up/output.log`); the repo worktree's `logs/` tree can lag or reflect a different local run and should not be treated as the live source of truth.

### Compose UI restart-policy gap
- In `docker-compose.yml`, `dashboard-api` and `frontend-dev` do not set `restart: unless-stopped`, while `butlers-up` and the connectors do. After a host reboot or external stop, the UI/API pair can remain down with `ExitCode=137` even though switchboard/connectors recover, leaving Tailscale `/butlers-dev` and `/butlers-dev-api` mapped to dead localhost ports until `./scripts/compose.sh` (or targeted `docker compose up -d`) is rerun.

### QA/self-healing PR label contract
- QA and self-healing dispatch default to GitHub labels `self-healing` and `automated`; on `Tzeusy/butlers`, missing repo labels make otherwise successful investigations fail at PR creation with `gh_pr_create_failed: could not add label: '<label>' not found`, leaving `healing_attempts.status = 'failed'` even when the agent produced a valid commit.

### QA helper workspace contract
- QA investigation Codex runs now launch from `<worktree>/.tmp/qa-agent/` with a local `AGENTS.md` override that explicitly disables `bd`/generic session-close workflow instructions; the helper dir must keep symlinks to repo roots like `src/`, `tests/`, `roster/`, `frontend/`, `pyproject.toml`, and `uv.lock` so repo-relative `uv run pytest` / `ruff check src/ tests/` still work.

### QA synthetic validation contract
- `POST /api/qa/dev/synthetic-findings` is an operator-only dev/staging hook gated by `QA_ALLOW_SYNTHETIC_FINDINGS=true`; it persists a placeholder `qa_patrols` row (`status='suppressed'`) plus a queued `qa_findings` row (`dispatch_queued=TRUE`) so the next scheduled patrol validates the normal rehydrate/triage/dispatch path even when `force_patrol` is unavailable out-of-process.

### Google OAuth credential storage split
- App credentials (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_SCOPES`) live in `butler_secrets` via `CredentialStore`.
- Refresh token lives exclusively in `public.contact_info` on the owner contact (type `google_oauth_refresh`, `secured=true`). No butler_secrets fallback exists.
- No runtime env-var fallback; all credential resolution is DB-only.
- `dev.sh` gate and runtime code both read from `public.contact_info` so shell gating and runtime behavior cannot drift.
- `scripts/compose.sh`/`oauth-gate` only proves the refresh token row exists; a revoked/expired token still lets the stack start, then Google-backed connectors/modules log `invalid_grant`/`Token has been expired or revoked` until the account is reauthorized with forced consent.

### Google `public.google_accounts` is ONE shared row per account across all 4 connectors
- Drive/Calendar/Gmail/Health each run their own account-sync loop that filters `WHERE status='active'` AND a qualifying scope in `granted_scopes` (`google_drive.py:1767`, `google_calendar.py:1833`, `gmail.py:3285`). A single account row backs all of them, so narrowing scopes or flipping `status` in one connector's flow cascades and silently drops the others at their next ~15-min sync (offline in the ingestion console).
- Re-auth must request **incremental authorization** (`include_granted_scopes=true`, set on both authorize builders in `oauth.py`): the request stays minimal (esp. health, which needs a restricted-scope subset) while Google returns a token covering the union of all prior grants — the callback persists `token_data["scope"]` verbatim, so a non-incremental request *replaces* and narrows. The legacy `_widen_scopes()` only fired when both `scope_set` and `account_hint` were passed, which the Calendar/Drive/Gmail UI flows don't.
- A single connector's credential error must NOT flip the shared `status` to `revoked` unless it's a genuine `invalid_grant` — only `GoogleHealthTokenRevokedError` gates `_mark_account_revoked` now; transient/scope-local failures stay connector-local (per-account `auth_error` flag only).

### WhatsApp module bridge DSN contract
- The in-process `modules.whatsapp` bridge runs inside `butlers up` and receives the daemon `Database` object, not connector env vars; `_get_db_dsn` must build `WA_BRIDGE_DSN` from `Database` connection fields when no explicit DSN attr exists. The standalone `connector-whatsapp-user` still uses its separate env-based DSN helper.

### WhatsApp pair_required is a startup-readiness-exempt waiting state (bu-7sh43)
- `BridgeSubprocessManager`'s `pair_required` handling in `_startup_poll_loop`/`_poll_status` (`src/butlers/connectors/bridge_manager.py`) is unconditional — NOT gated behind `startup_allow_degraded` — because a bridge sitting in `pair_required` is legitimately waiting for a human QR scan, not a startup failure; it must satisfy `_startup_ready_event` immediately on every boot path so the ordinary `startup_timeout_s` (60s for the connector) never tears the bridge down mid-pairing. `is_awaiting_pairing` (True only for this state) is the property callers must check — it is never escalated to `is_invalidated_session` (bu-5ocmh's classifier only fires from the disconnected/connecting/link-dead branches).
- `WhatsAppUserClientConnector._sse_event_loop` must check `is_awaiting_pairing` before its generic `is_degraded -> stop the loop` branch: an awaiting-pairing bridge idles (re-checks every `_SSE_PAIRING_IDLE_INTERVAL_S`) instead of breaking, because breaking triggers `connector.start()`'s `finally: await self.stop()`, which tears down the very bridge the user is mid-scan against. Only genuinely terminal degraded reasons (pairing-timeout rc=1, session-invalidated rc=2, unreachable) should still break the loop.
- Because `start()` can now return with the bridge still in `pair_required` (before the user has scanned), the `endpoint_identity` resolution that only fires when it is still the `"whatsapp:pending"` placeholder (first-time setup, no `whatsapp_phone` in owner entity_info yet) can no longer resolve on that first attempt — the bridge has no phone yet. `_maybe_resolve_pending_endpoint_identity()` makes this idempotent and re-callable; `_sse_event_loop` retries it on every non-degraded pass so it self-heals the moment the bridge actually reports `connected`, instead of leaving the connector stuck tagging ingestion under the placeholder identity for the rest of the process lifetime.

### Chronicler day-close prose/provenance contract
- `chronicler_day_close` should keep raw `source_ref`/connector IDs internal: `chronicler_day_close_bundle` returns citations for cache provenance/staleness, but the Telegram prose should cite only human-readable sources by default.
- Day-close summaries should use the owner timezone from General settings; pass that IANA timezone into `chronicler_day_close_bundle` so the bundle includes local display timestamps and local day boundaries.

### compose.sh dev DB role-grant contract
- `scripts/compose.sh` can clear Tailscale/OAuth gates and still fail at `butlers-up` if the target DB user lacks runtime role membership/ACLs; the signature is repeated `InsufficientPrivilegeError: permission denied for schema public` plus per-butler failures like `permission denied for schema <butler>` or `permission denied to set role "butler_<name>_rw"`.
- Fix the target dev DB before retrying by ensuring the connecting user has the expected role grants from `scripts/init-db.sql` (or equivalent grants done as a sufficiently privileged Postgres role); otherwise the switchboard health endpoint never comes up and all connectors remain blocked behind `butlers-up`.
- On PostgreSQL 16+, `pg_has_role(user, role, 'MEMBER')` is not enough to guarantee `SET ROLE role` works; the `pg_auth_members` row also needs `set_option = true`. `scripts/init-db.sql` should re-grant runtime roles with explicit `WITH SET TRUE`/`WITH INHERIT TRUE` so reruns repair older memberships instead of skipping them.

### init-db privileged bootstrap contract
- `scripts/init-db.sql` is the single privileged bootstrap entrypoint: it must create managed schemas/runtime roles when missing, grant role membership to the migration user (default `butlers`), grant runtime DB/schema ACLs, and set `ALTER DEFAULT PRIVILEGES FOR ROLE <migration user>` so later Alembic runs by `butlers` do not require a post-migration privileged grant pass.
- The script intentionally grants broad DML defaults on `public` objects created by the migration user to all runtime roles (`butler_*_rw`, `butler_qa_rw`, `connector_writer`) as the operational tradeoff that keeps the bootstrap to one privileged step; rerun it when the managed schema/role surface changes. Stored-function body drift is separately reported, never fatal: `butlers.core.stored_function_drift` emits a one-shot dashboard-api startup WARNING and exposes `GET /api/system/stored-functions`; its whitespace-only normalization remains literal-sensitive, exposes only short digests rather than bodies, and treats `not_deployed` as an ordinary pre-installer state, not a failure.
- Changing a **string literal inside a stored function body** is not the same problem as renaming a schema object, and Alembic cannot solve it. `runtime_attention_admin.upgrade_producers_v2()` emits the body of `public.runtime_attention_plant_legacy_debounce_marker()`, and `core_199` invokes that upgrader exactly once — so a literal edited only inside the upgrader reaches fresh bootstraps and no existing database. The migration role also cannot rewrite the body itself: the function is owned by the NOLOGIN `runtime_attention_outbox_owner` and `core_198`'s own catalog proof asserts the migration role is **not** a member. The convergence vehicle is `runtime_attention_admin.finalize_interface()`, which init-db.sql's trailing `DO` block calls on every rerun of an installed database. Define the body once in a bootstrap-owned installer (`install_legacy_debounce_marker()`) that both the one-shot upgrader and the finalizer call, so the two paths cannot drift (bu-95gq7).
- Order matters in that finalizer: adopt the body **after** the `ALTER FUNCTION ... RENAME`/`ALTER ... OWNER` steps. `CREATE OR REPLACE` earlier would create a *second* function under the new name while `pg_trigger.tgfoid` still pointed at the old OID — convergence that silently did nothing, and it would also defeat bu-kww1r's "function does not exist" regression test.
- Never converge such literals with a row backfill: it corrects history and drifts again on the very next insert while looking like it worked. Assert on a row planted **after** the change. Historical `public.audit_log` rows keep the old vocabulary forever (`runtime_attention_cutover_fence` / `blocked_old_binary`), so any query filtering on actor must tolerate both.

### Runtime-attention producer concurrency: do not lock in the Python caller
- `INSERT ... ON CONFLICT DO NOTHING` **waits** for a conflicting *uncommitted* insert and re-checks after it commits — it does not skip past it. Verified empirically 2026-08-24 against `public.append_runtime_attention_fleet_halt` with its own advisory lock deleted: the second caller still blocked and still got the winner's episode from the re-SELECT. So `ON CONFLICT (key) DO NOTHING RETURNING id` + `IF NULL THEN re-SELECT` is already a complete once-per-key guarantee; the producers' own `pg_advisory_xact_lock` is belt-and-braces on top of it.
- Never add a **second** lock on the same key in `butlers.core.dispatch_outcomes`. Advisory xact locks are re-entrant within a transaction, so a caller-side acquisition adds no exclusion — it only widens the critical section over the dispatch-attempt INSERT and the producer's month-wide `count(*)` evidence query, on the deny path that fires for *every* spawn while the fleet is halted. Removed in bu-86t7r; bu-jxelx (#3822) existed only because that duplicate key was a second, independently evaluated month expression that could name a different month across a UTC rollover. Keep the month named once, by the producer. The breaker path is different and its lock **is** load-bearing: its decision spans `get_breaker_state` then the producer, two statements Python issues itself.
- Producer activation cannot be straddled by an in-flight denial, whatever Python locks: `runtime_attention_admin.upgrade_producers_v2()` writes `producer_activated_at` in the same transaction as `CREATE TRIGGER ... BEFORE INSERT ON public.model_dispatch_attempts`, and that SHARE ROW EXCLUSIVE conflicts with every inserter's ROW EXCLUSIVE. Any denial row that exists before activation is committed before activation; any denial after it stamps `ts` after activation.

### Fencing a `public` table to one runtime role
- Grants alone cannot do it. `scripts/init-db.sql` both `GRANT`s on **all** existing `public` tables and sets `ALTER DEFAULT PRIVILEGES ... ON TABLES` for every runtime role, so any per-role `REVOKE` a migration performs is silently re-widened the next time init-db runs. Use `ENABLE ROW LEVEL SECURITY` with a policy keyed on `current_user = '<role>'` on top of the revoke/grant pass: a policy is not a grant, so an init-db rerun cannot undo it.
- Do **not** add `FORCE ROW LEVEL SECURITY` to reach the table **owner** without checking the backup path. `pg_dump` sets `row_security = off`, and in that mode PostgreSQL **raises** (`query would be affected by row-level security policy for table ...`) rather than silently filtering, for any reader without `BYPASSRLS`. `deploy/backup/pg_dump.sh` dumps as `POSTGRES_USER` (default `butlers`) — the migration user, which `init-db.sql` pins `NOSUPERUSER` — and it runs under `set -o pipefail`, so ONE forced table aborts the entire nightly dump. Verified empirically 2026-08-22: `public.user_context`, `public.runtime_attention_outbox`, and `public.runtime_attention_delivery_lease` (all FORCEd by init-db.sql, owned by dedicated NOLOGIN owners) already raise for the migration user under `row_security = off`. Owner-fencing needs a mechanism that is not in the backup path.

### `deploy/backup/pg_dump.sh` cannot dump as the migration user (observed 2026-08-22, unfixed)
- Reproduced against a fresh testcontainer bootstrapped with the real `scripts/init-db.sql` + core chain, running pg_dump 17 as the migration user: the dump aborts before writing a byte, at the `LOCK TABLE` it issues over every visible relation. First `ERROR: permission denied for schema restore_drill_executor_admin`; excluding the three admin schemas, then `ERROR: permission denied for table restore_drill_results`. These are the deliberate trusted-bootstrap boundaries (`core_196`, `core_198`, dnd-generation), which `init-db.sql` revokes from the migration role on purpose — so the boundary and the backup script are in direct conflict. `pg_dump.sh` passes no `--exclude-schema`/`--exclude-table`, and `set -o pipefail` plus the cleanup trap mean a failed run leaves NO file, so the symptom is a silently absent backup rather than a partial one. Not fixed here; verify before trusting any restore drill.

### Reading a secret file safely
- `os.open(path, O_RDONLY | O_NOFOLLOW)` **blocks indefinitely** if the path is a FIFO — `O_NOFOLLOW` only rejects symlinks, and the regular-file check cannot run until `open` returns. Add `O_NONBLOCK` (inert for regular files) so a FIFO planted at a secret mount path fails fast instead of parking startup; then `os.fstat` + `stat.S_ISREG` on the descriptor. A test that plants a FIFO is the only thing that catches this — it manifests as a hung pytest run with no output, not a failure.
- File mode `0400` is a final property worth asserting, but it is **not** isolation from a child process running as the same identity; say so wherever the check is made so nobody reads it as a containment boundary.

### Pytest xdist/testcontainers concurrency contract
- `conftest.py` now serializes `testcontainers` `DockerClient.run()` calls across xdist workers and caps `pytest_xdist_auto_num_workers()` to `3` by default (override with `PYTEST_XDIST_AUTO_WORKERS`) so CI commands that pass `-n auto` do not overwhelm Docker-backed Postgres fixtures and time out during container startup.

### Code Layout
- `src/butlers/core/` — state.py, scheduler.py, sessions.py, spawner.py, telemetry.py, telemetry_spans.py
- `src/butlers/modules/` — base.py (ABC), registry.py, telegram.py, email.py
- `src/butlers/tools/` — switchboard.py, general.py, relationship.py, health.py, heartbeat.py
- `src/butlers/` — config.py, db.py, daemon.py, migrations.py, cli.py
- `alembic/versions/{core,mailbox}/` — shared migrations (core infra + modules)
- `roster/{switchboard,general,relationship,health}/migrations/` — butler-specific migrations
- `roster/{switchboard,general,relationship,health,heartbeat}/` — butler config dirs

### Test Layout
- Shared/cross-cutting tests in `tests/`
- Butler-specific tool tests colocated in `roster/<name>/tests/`
  - `roster/general/tests/test_tools.py`
  - `roster/health/tests/test_tools.py`
  - `roster/relationship/tests/test_tools.py`, `test_contact_info.py`
  - `roster/switchboard/tests/test_tools.py`
- `pyproject.toml` testpaths: `["tests", "roster"]`
- Uses `--import-mode=importlib` to avoid module-name collisions across butler test dirs

### Agent test-plan contract
- `make test-plan` is a dirty-worktree-aware **plan only**: it unions branch, staged, unstaged, and untracked paths, and prints either existing pytest paths or an `ESCALATE` reason. It never runs pytest and cannot be cited as green evidence.
- Its broad roots are derived from pytest `testpaths` (`tests/` and `roster/`). Unknown, shared-core, migration, test-tooling, CI, and configuration paths fail closed to escalation; a deleted test file widens to its existing parent for a subsequent collect-only run.

### Test Patterns
- All DB tests use `testcontainers.postgres.PostgresContainer` with `asyncpg.create_pool()`
- Tables created via direct SQL from migration files (not Alembic runner)
- When tests create `sessions` manually, keep schema aligned with `core_003`+ columns (`model`, `success`, `error`, `input_tokens`, `output_tokens`, `parent_session_id`) to avoid `UndefinedColumnError` in `core.sessions` queries.
- Integration test modules that create asyncpg pools in async fixtures must align asyncio loop scope under xdist (`@pytest.mark.asyncio(loop_scope="session")` on async test classes/modules) to avoid cross-loop `RuntimeError: ... Future ... attached to a different loop`.
- `roster/health/tests/test_tools.py` currently reproduces the same asyncpg cross-loop failure on `main` under pytest-xdist (`uv run pytest roster/health/tests/test_tools.py::test_measurement_log_weight -n auto --maxfail=1`), so refinery scoped runs that select `roster/health/tests/` need baseline-failure triage rather than assuming an MR regression.
- Shared test doubles for spawner behavior live in `src/butlers/testing/shared_fixtures.py`; root `conftest.py` is their sole global pytest registration layer for both `tests/` and `roster/`. Nested and tier-specific conftests must not re-register those project-wide fixtures, but may define fixtures, hooks, and helpers scoped to their tree.
- CLI tests use Click's `CliRunner`
- Telemetry tests use `InMemorySpanExporter`
- Root `conftest.py` patches `testcontainers` teardown (`DockerContainer.stop`) with bounded retries for known transient Docker API teardown races (notably "did not receive an exit event") under `pytest-xdist`; non-transient errors must still raise.

### Memory System Architecture
Memory is a **common module** (`[modules.memory]`) enabled per butler, not a dedicated shared role/service. Memory tables (`episodes`, `facts`, `rules`, plus provenance/audit tables) live in each hosting butler's DB and memory tools are registered on that butler's MCP server. Uses pgvector + local MiniLM-L6 embeddings (384d). Dashboard remains available at `/memory` (aggregated via API fanout) and `/butlers/:name/memory` (scoped).

### Memory API fanout contract
- `src/butlers/api/routers/memory.py` must not require `db.pool("memory")`; `/api/memory/*` reads fan out across available butler DB pools and aggregate results.
- Pools without memory tables should be skipped gracefully so no-dedicated-memory deployments return zero/empty payloads (or 404 for ID lookups) instead of 503.
- Unscoped `/api/memory/reembed/pending` must aggregate over memory-capable schemas and skip non-memory schemas such as `chronicler`; `chronicler.episodes` is not a memory table and has no `embedding` column.

### General timezone settings contract
- Shared dashboard-level defaults live in `public.state` under key `settings.general`; `/api/settings/general` is the owner-facing surface, and `Spawner` injects the resulting general-settings block into every butler system prompt via the shared credential pool when available (falling back to local pool only when shared access is absent).
- The current shared payload is `{timezone, language, date_format, time_format, week_starts_on, currency}` with implicit defaults `UTC`, `en-US`, `YYYY-mm-dd`, `HH:MM`, `Monday`, `USD`; `measurement_system` is fixed response-only `metric` and is not persisted as a user-editable field.

### Fact provenance write contract
- `src/butlers/modules/memory/storage.py::store_fact` now auto-fills `source_butler` from runtime context and creates/reuses a canonical `episodes` row for the current runtime session when `source_episode_id` is omitted.
- Any direct fact writer that bypasses `store_fact` (for example bulk SQL insert paths or scheduled jobs without runtime context) must either pass `source_butler` explicitly or call `resolve_write_provenance(...)` before inserting, or the entity facts UI will show blank provenance/session columns.

### Memory OpenSpec alignment contract
- `openspec/changes/memory-system/specs/*` now aligns to target-state module semantics: per-butler memory module integration, tenant-bounded operations by default, canonical fact soft-delete state `retracted` (legacy `forgotten` alias only), required `memory_events` audit stream, deterministic tokenizer-based `memory_context` budgeting/tie-breakers, consolidation terminal states (`consolidated|failed|dead_letter`) with retry metadata, and explicit `anti_pattern` rule maturity.

### Migration naming/path convention
Alembic revisions are chain-prefixed (`core_*`, `mem_*`, `sw_*`) rather than bare numeric IDs. Butler-specific migrations resolve from `roster/<butler>/migrations/` via `butlers.migrations._resolve_chain_dir()` (not legacy `butlers/<name>/migrations/` paths).
- Core chain baseline is consolidated into `alembic/versions/core/core_001_target_state_baseline.py` (`revision = "core_001"`); legacy incremental core revisions (`001_create_core_tables.py` through `011_apply_schema_acl_for_runtime_roles.py`) were removed.
- Core migration coverage is centralized in `tests/config/test_migrations.py`; do not reintroduce per-step core files under `tests/migrations/` for pre-baseline revision IDs.
- Within a chain, set `branch_labels` only on the branch root revision (e.g. `rel_001`); repeating the same label on later revisions causes Alembic duplicate-branch errors.
- Do not leave stray migration files in chain directories: even if chain tests only assert expected filenames, Alembic will still load every `*.py` in the versions path and fail on duplicate `revision` IDs.
- Switchboard migrations already include `sw_005` as the latest linear revision; new switchboard revisions must continue from `sw_005` (for example `sw_006`) to avoid multi-head failures during `switchboard@head` upgrades.
- Table renames preserve existing index names; when rewriting a table in-place (rename old + create new), new index names must not collide with indexes still attached to the renamed backup table.
- Rollback is **not** uniformly available across the core chain: `core_196` (restore-drill executor) and `core_198` (runtime-attention outbox) install trusted-bootstrap boundaries whose downgrade is bootstrap-only and may refuse outright, and later revisions can retain cutover state that makes `core_198`'s rollback permanently unavailable. A test that rolls back one old migration must therefore **bound its upgrade to the revision it owns** — `command.upgrade(cfg, f"core@{mod.revision}")`, or `create_migrated_test_db(..., revisions={"core": "core_NNN"})` — never `core@head` followed by a downgrade into the 130s-170s. Migrating to head first drags those boundaries into an unrelated rollback and fails with `core_198 downgrade requires trusted bootstrap rollback interface` (bu-2rqrl). `tests/config/test_bounded_revision_downgrade_guard.py` is an AST guard (no DB) that fails any test whose resolved upgrade target sits at or above a boundary while its `command.downgrade` target sits below one. It **derives** the boundary set from the migration sources — a revision counts as a boundary when its `downgrade()` refusal message names both a rollback direction and a privileged authority — so a future `core_2NN` boundary is picked up with no edit. Two shapes are exempt on purpose: a downgrade inside `pytest.raises` (a test *about* the refusal) and a config built on `migration_bootstrap_db_url()` (the trusted rollback interface the boundary asks for). Non-literal targets (`f"core@{mod.revision}"`, `revisions={"core": mod.revision}`) are unreadable and therefore unguarded, not assumed to be head (bu-elwgq).
- Adding a migration moves the chain head, which used to **break head-pinned test assertions** (twice in two days, at `core_200` and `core_201`). Those assertions now derive the head: use `butlers.testing.migration.assert_at_chain_head(connection, schema, chain=...)`, or compare against `butlers.migrations.get_chain_head(chain)` where the test already has the version list. `tests/config/test_migration_chain_head.py::test_no_alembic_version_assertion_hardcodes_a_revision_literal` is an AST guard (no DB) that fails any comparison between an `alembic_version` read and a literal revision id — it is AST-based because these asserts put the `SELECT` and the literal on **different lines**, so a line-scoped grep under-counts them (it measured 2 where 10 existed, including 4 in the `approvals` chain nobody had noticed). A test that pins an older revision on purpose (a refused upgrade, a bounded partial upgrade) keeps its literal and declares it with a `# pinned-revision: <why>` comment on or above the assertion; the reason is mandatory and must sit on the marker line, since a bare marker would let the guard be silenced without anyone saying which reading the literal has. See bu-4sgl8.
- `tests/migrations/test_runtime_attention_outbox_migration.py::test_core_chain_serializes_global_runtime_attention_{install,downgrade_and_reapply}_across_processes` deliberately contend on a **global cross-process lock**. They can fail spuriously if another pytest run touches the same Postgres concurrently, so they must not run in parallel with a second full-suite run — targeted migration runs need the same serialization as a full gate.
- `src/butlers/migrations.py::_build_alembic_config` must escape `%` as `%%` when setting `sqlalchemy.url` on Alembic `Config`; otherwise percent-encoded libpq query params (for example `options=-csearch_path%3D...`) raise `configparser` interpolation errors.
- `create_migrated_test_db()` hands back the **ordinary migration login**, which `runtime_attention_admin.finalize_interface()` strips of every privilege on `public.runtime_attention_outbox` (and the table is FORCE RLS with policies only for its owner and `butler_switchboard_rw`). A migration test that needs to read an episode it just produced must open a second connection on `migration_bootstrap_db_url(container, db_name)` — the container's superuser login, which bypasses RLS. Keep the db name in its own module-scoped fixture so both URLs can be built from it (bu-guxz8).
- Comparing `timestamptz` to a `DATE` variable (`ts >= v_month`) silently promotes the date through the **session `TimeZone`**, not UTC. In `scripts/init-db.sql`'s runtime-attention producers every month expression is deliberately UTC-explicit; write the bound as `ts >= (v_month::timestamp AT TIME ZONE 'UTC')` so a non-UTC session cannot shift the window by the UTC offset (bu-guxz8).
- Pre-merge freshness check (bu-hmdqz.8): the duplicate-revision guard (`tests/config/test_migration_chain_head.py`) only fires against the tree it runs on — it cannot see a same-numbered revision landing on `main` from a sibling PR that merged after this branch's CI last ran. PRs #3125 and #3127 both minted `core_164` off the same `core_163` base and both merged green because neither PR's CI re-checked against the other's result. Before merging any PR touching a migration root (`alembic/versions/**`, `src/butlers/modules/*/migrations/**`, or `roster/*/migrations/**`), fetch `origin/main` and re-run `tests/config/test_migration_chain_head.py` (and the relevant chain's migration tests) against the merge result, not just the PR's own stale CI run. The `Migration Chain Integrity (main)` workflow now re-runs that guard on every merged `main` push touching those roots; keep the pre-merge check to catch a collision before it reaches `main`. Note that `origin/main` is not sufficient when the colliding revision is on a **sibling branch that has not merged yet**: observed 2026-08-21, `main` ended at `core_198` while open PR #3742 already carried `core_199_runtime_attention_producer_v2.py`, so a parallel worker cutting from `main` correctly concluded `core_199` was free. When several branches are open at once, take the next revision number from `git ls-remote --heads origin` / `git ls-tree -r --name-only origin/<branch> -- alembic/versions/core/` across every live branch, not from the local worktree, and re-verify at handoff since a sibling may land in between.

### Memory migration baseline contract
- `src/butlers/modules/memory/migrations/` is a single baseline chain file: `001_memory_baseline.py` (`revision=mem_001`, `branch_labels=('memory',)`, `down_revision=None`).
- Legacy incremental memory revisions (`001_create_episodes.py` through `007_fix_rules_missing_columns.py`) are intentionally removed; no prior revision compatibility is preserved for this rewrite.

### Known Warnings (not bugs)
- 2 RuntimeWarnings in CLI tests from monkeypatched `asyncio.run` — unawaited coroutines in test mocking

### FastMCP API drift test failures (current baseline)
- `make test-qg` currently fails in this branch with tests assuming legacy FastMCP internals: `tests/modules/test_module_approvals.py::TestApproveAction::test_approve_tool_rejects_spoofed_actor_kwarg` accesses `mcp._tool_manager`, and `tests/daemon/test_daemon.py::TestNotifyTool::test_notify_tool_description_and_schema_contract` calls `runtime_mcp.get_tools()`.
- Current FastMCP surface in this env exposes `get_tool` but not `_tool_manager`/`get_tools`; update tests to use supported introspection APIs before treating these as product regressions.

### Testcontainers xdist teardown flake
- `make test-qg` can intermittently fail during DB-backed test teardown with Docker API 500 errors while removing/killing `postgres:16` testcontainers (`did not receive an exit event`); tracked in `butlers-e6b`.

### Testcontainers startup timeout under contention
- Root `conftest.py` patches `testcontainers.core.docker_client.DockerClient.__init__` with bounded retry for transient startup timeouts from API-version negotiation (`Error while fetching server API version ... Read timed out`) before container launch.
- Controlled contention probe results on 2026-02-13 (48 workers, docker CLI churn): `docker.from_env(version=\"auto\")` failed 136/1200 calls (11.33%) at `timeout=0.05` and 0/1200 at `timeout=0.1`, indicating a host-load-sensitive daemon response-time class rather than a teardown lifecycle race.
- Triage rule: startup timeout errors happen before container start and should be mitigated with bounded init retries and reduced host contention; teardown races happen during `container.remove()` and are handled by teardown retry logic.

### Quality Gates
```bash
uv run ruff check src/ tests/ roster/ conftest.py
uv run ruff format --check src/ tests/ roster/ conftest.py
make test-qg
```

### Calendar workspace audit
- Frontend now exposes a first-class `/butlers/calendar` route (`frontend/src/router.tsx`) and sidebar navigation entry (`frontend/src/components/layout/Sidebar.tsx`).
- `frontend/src/pages/CalendarWorkspacePage.tsx` provides the initial dual-view shell: query-persisted `view=user|butler` plus `range=month|week|day|list` + `anchor` controls, a primary calendar canvas, and a right-side source/lane panel.
- Calendar workspace reads are wired via `frontend/src/hooks/use-calendar-workspace.ts` to `GET /api/calendar/workspace` and `GET /api/calendar/workspace/meta`; TypeScript contracts live in `frontend/src/api/types.ts` and client bindings in `frontend/src/api/client.ts`.

### Parallel Test Command
- Default quality-gate pytest scope uses `pytest-xdist` (`-n auto`) via `make test-qg`.
- Serial fallback/debug path remains available via `make test-qg-serial`.
- `make test-qg-parallel` is an explicit alias to the same parallel default.
- All three run pytest through `scripts/pytest_gate.py run --tee` and end on `pytest_gate.py verdict`
  (bu-ecizp), so every gate run leaves a `## pytest-gate exit=N` receipt under `.tmp/test-logs/` and
  the verdict's exit status is the target's. `--tee` mirrors the log to the terminal live, so the
  targets are still watchable.

### Testing cadence policy
- For bugfixes/features under active development or investigation, default to targeted `pytest` runs to keep loops fast and context lean.
- Run full-suite tests when branch changes are finalized and you need a pre-merge readiness signal.

### Dashboard health endpoint alias contract
- `src/butlers/api/app.py` must expose both `GET /api/health` and `GET /health` with the same `{"status":"ok"}` payload so direct infra probes and `/api`-prefixed clients both work.

### Switchboard message_inbox test partition contract
- Switchboard integration fixtures that create partitioned `message_inbox` tables must provision partitions dynamically for `now()` (and typically the next month) via a helper, not hard-coded calendar months; otherwise conformance tests start failing once wall-clock time moves past the fixed partition range.

### Health meals API facts contract
- `GET /api/health/meals` in `roster/health/api/router.py` must query `facts` with `predicate = ANY(meal predicates)`, `scope = 'health'`, and `validity = 'active'`, applying `since/until` filters to `valid_at`.
- Response mapping remains backward compatible (`id`, `type`, `description`, `nutrition`, `eaten_at`, `notes`, `created_at`), where `nutrition` is derived from fact metadata (`estimated_calories` + `macros.{protein_g,carbs_g,fat_g}`) and is `null` when those fields are absent.

### Approvals CAS/idempotency contract
- `src/butlers/modules/approvals/module.py` decision paths (`_approve_action`, `_reject_action`, `_expire_stale_actions`) must use compare-and-set SQL writes (`... WHERE status='pending'`) so concurrent decision attempts cannot overwrite each other.
- Approval expiry is a decision boundary, not just background cleanup: approve and defer/extension paths must reject and mark a still-`pending` action expired when `expires_at < now`, so a missed sweep cannot make an expired action executable or extendable.
- `src/butlers/modules/approvals/executor.py::execute_approved_action` is idempotent per `action_id`: it serializes execution with a process-local per-action lock, replays stored `execution_result` when status is already `executed`, and only performs the terminal write when status is still `approved`.

### Calendar recurrence normalization contract
- `_normalize_recurrence()` in `src/butlers/modules/calendar.py` must reject any rule containing `\\n` or `\\r` to prevent iCalendar CRLF/newline injection.
- `FREQ` presence and `DTSTART`/`DTEND` exclusion checks should be case-insensitive (`rule.upper()`), so lowercase property names cannot bypass validation.

### Calendar recurring write contract
- `CalendarEventCreate` and `CalendarEventUpdate` validate/normalize `recurrence_rule` via `_normalize_recurrence_rule`; invalid RRULEs must raise clear `ValueError`s before provider calls.
- Recurring writes with naive datetime boundaries require explicit `timezone`; omit timezone only when datetime boundaries already carry tzinfo.
- `calendar_update_event` is series-only for recurrence in v1 (`recurrence_scope="series"`); non-series scope values must be rejected at validation time.

### Switchboard Classification Contract
- `classify_message()` returns decomposition entries (`list[{"butler","prompt"}]`), not a bare butler string. Callers must normalize both legacy string and list formats before routing.
- When `butler_registry` is empty, `classify_message()` auto-discovers butlers from `roster/` (see `roster/switchboard/tools/routing/classify.py`) before composing the "Available butlers" prompt.
- Classification uses `list_butlers(..., routable_only=True)` so stale/quarantined targets are excluded from planner prompt context by default.

### Switchboard Codex tool-call parsing contract
- `src/butlers/core/runtimes/codex.py` must normalize nested Codex MCP tool-call payloads (`item.type="mcp_tool_call"` with `call`/`tool_call` sub-objects) so `route_to_butler` name + arguments are preserved in `tool_calls`; otherwise switchboard can mis-detect "no route_to_butler tools" and incorrectly fall back to `general`.

### Switchboard no-tool fallback routing contract
- In `src/butlers/modules/pipeline.py`, when LLM output includes no recognized `route_to_butler` calls, fallback routing should first attempt unambiguous target inference from CC summary text patterns like `routed to <butler>` (restricted to currently available butlers) before defaulting to `general`.

### Switchboard correct_route inbox lookup contract
- `roster/switchboard/tools/routing/correct_route.py` should pass explicit timestamp bounds (`window_start`, `window_end`) as separate bind parameters when filtering `message_inbox.received_at`; avoid expressions like `$2 ± interval '1 day'` on untyped bind params because asyncpg can infer `interval` and trigger `UndefinedFunctionError` (`timestamptz >= interval`).

### Runtime tool-call capture contract
- `src/butlers/core/spawner.py` augments adapter-parsed `tool_calls` with daemon-observed MCP executions captured via `src/butlers/core/tool_call_capture.py`, keyed by `runtime_session_id`.
- Switchboard MCP URLs include `runtime_session_id=<session_uuid>` query params so daemon request middleware (`_McpRuntimeSessionGuard` in `src/butlers/daemon.py`) can bind incoming tool invocations to the active runtime session and capture ground-truth tool calls for fallback decisions.
- `_McpRuntimeSessionGuard` should proxy unknown attributes to the wrapped ASGI app (for example `.routes`) so middleware layering remains transparent to startup/tests that introspect the combined MCP app.
- Daemon-side wrappers (`_SpanWrappingMCP` and `_ToolCallLoggingMCP`) must capture tool-call outcomes (`success`/`error`/`module_disabled`) plus result/error payloads in `tool_call_capture` so session `tool_calls` preserve execution outcomes, not just invocation names/inputs.
- Spawner merge logic should preserve failed-then-retried attempts with identical inputs in chronological order; avoid signature-only dedupe that collapses retry history.

### route.execute trace continuity contract
- Non-messenger `route.execute` background processing (`route.process`) should continue the incoming distributed `trace_context` when present, so switchboard dispatch and target butler work appear under one trace in observability backends.
- Keep `request_id` attributes on both accept (`butler.tool.route.execute`) and process (`route.process`) spans, and retain the process-span `SpanLink` to the accept span for explicit async-boundary correlation.

### notify + memory_store_fact tool metadata contract
- `notify` tool metadata (description + parameter schema) should explicitly document required/optional fields, include a valid JSON example, constrain `channel`/`intent` enums, and describe `request_context` required keys (`request_id`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`) plus `source_thread_identity` for telegram reply/react.
- `memory_store_fact` tool metadata should explicitly document required/optional fields, include a valid JSON example, keep `permanence` as enum (`permanent|stable|standard|volatile|ephemeral`), and state that `tags` must be a JSON array of strings (not a comma-separated string).

### Contacts-identity model contracts

#### Owner contact bootstrap drift
- Legacy `src/butlers/daemon.py::_ensure_owner_entity_and_contact` inserted `public.contacts(name='Owner')` with `ON CONFLICT DO NOTHING`; after `core_016` dropped `ix_contacts_owner_singleton`, repeated startup could create duplicate `Owner` contacts.
- Current startup contract is entity-only bootstrap (`_ensure_owner_entity`) with no automatic `public.contacts` insert/delete side effects.

#### Identity schema (shared)
- `public.contacts` and `public.contact_info` are the canonical identity store in PostgreSQL; all channel-to-person resolution goes through the `public` schema.
- `public.contacts.roles` (text[]) encodes contact relationship: `owner` marks the single human operator. A partial unique index (`ix_contacts_owner_singleton`) enforces owner singleton.
- `public.contact_info` links channel identifiers to contacts: `(type, value)` UNIQUE constraint guarantees at most one contact per channel identifier.
- `contact_info.secured = true` marks credential entries (e.g. `type='telegram_bot_token'`); secured entries are filtered from default read paths.

#### Owner bootstrap
- `_ensure_owner_contact(pool)` in `src/butlers/daemon.py` bootstraps the owner contact row on every daemon startup (idempotent via `ON CONFLICT DO NOTHING`).

#### Identity resolution (ingress / Switchboard)
- `resolve_contact_by_channel(pool, channel_type, channel_value)` in `src/butlers/identity.py` is the canonical reverse-lookup: maps `(type, value)` → `ResolvedContact` (contact_id, name, roles, entity_id).
- Unknown senders: `create_temp_contact(pool, channel_type, channel_value)` creates a temp contact with `metadata.needs_disambiguation = true`; owner is notified once per new unknown sender (idempotent via `butler_state` KV: `identity:unknown_notified:{type}:{value}`).
- `build_identity_preamble(resolved, channel)` formats the preamble injected before every routing prompt; `contact_id`, `entity_id`, and `sender_roles` are persisted to `routing_log`.
- Switchboard identity injection pipeline lives in `roster/switchboard/tools/identity/inject.py`.

#### notify() contact resolution
- `notify()` supports three-tier recipient resolution: (1) `contact_id` UUID → `public.contact_info WHERE contact_id=X AND type=channel` (primary preferred), (2) explicit `recipient` string (backwards-compatible), (3) owner contact's channel identifier (default/scheduled sends).
- `_resolve_contact_channel_identifier(contact_id, channel)` in `src/butlers/daemon.py` handles path (1).
- `resolve_owner_contact_info(pool, info_type)` in `src/butlers/credential_store.py` handles path (3); returns `None` if no matching `contact_info` entry exists (no `butler_secrets` fallback).
- `notify()` returns `{status: pending_missing_identifier, ...}` when `contact_id` is provided but no matching `contact_info` entry exists for the requested channel; owner is notified.
- Scheduled prompts that are not replying to ingress context should use `intent="send"` (not `intent="reply"`).

#### Approval gate: role-based gating
- The approval gate uses role-based target resolution, not tool-name prefix heuristics (`user_*`/`bot_*` prefixes were removed in the h9fs epic).
- Gate resolution order: (1) extract `(channel_type, channel_value)` or `contact_id` from tool args → (2) resolve to `ResolvedContact` → (3) auto-approve if `owner` role, check standing rules otherwise.
- Tool-name-based prefixes in `approval_rules.tool_name` were renamed to plain tool names via Alembic migration (h9fs.7).

### Switchboard registry liveness/compat contract
- `butler_registry` includes liveness + compatibility metadata: `eligibility_state`, `liveness_ttl_seconds`, quarantine fields, `route_contract_min/max`, and `capabilities`.
- `resolve_routing_target()` in `roster/switchboard/tools/registry/registry.py` is the canonical gate for route eligibility: it reconciles TTL staleness, enforces stale/quarantine policy overrides, and validates route contract/capability requirements.
- Eligibility transitions are audited in `butler_registry_eligibility_log`; stale transitions (`ttl_expired`) and recovery transitions (`health_restored`/`re_registered`) should remain traceable in tests.

### Switchboard telemetry/correlation contract
- `roster/switchboard/tools/routing/telemetry.py` is the canonical `butlers.switchboard.*` metrics surface with low-cardinality attribute normalization (`source`, `destination_butler`, `outcome`, `lifecycle_state`, `error_class`, `policy_tier`, `fanout_mode`, `model_family`, `prompt_version`, `schema_version` only).
- `MessagePipeline.process()` emits the root trace span `butlers.switchboard.message` and persists `request_id` alongside lifecycle payloads in `message_inbox.classification` / `message_inbox.routing_results` (`{"request_id": ..., "payload"/"results"/"error": ...}`) for log-trace-persistence reconstruction.

### Switchboard eligibility sweep schedule contract
- `roster/switchboard/butler.toml` schedules `eligibility-sweep` as a job-dispatch entry (`dispatch_mode = "job"`, `job_name = "eligibility_sweep"`) rather than a prompt-based schedule.

### Butler detail schedule serialization contract
- `GET /api/butlers/{name}` serializes `config.schedules` through `ScheduleEntry`; `ScheduleEntry.prompt` must remain nullable because `dispatch_mode="job"` schedules intentionally omit prompt text.

### Notifications DB fallback contract
- `src/butlers/api/routers/notifications.py` should degrade gracefully when the switchboard DB pool is unavailable: `GET /api/notifications` and `GET /api/butlers/{name}/notifications` return empty paginated payloads, and `GET /api/notifications/stats` returns zeroed stats instead of propagating a `KeyError`/404.
- Notifications list serialization must normalize `metadata` to object-or-null without raising on non-mapping JSON values (for example array/string/scalar rows); unsupported metadata shapes should coerce to `null` instead of returning 400/500.

### Memory Writing Tool Contract
- `src/butlers/modules/memory/storage.py` write APIs return UUIDs (`store_episode`, `store_fact`, `store_rule`); MCP wrappers in `src/butlers/modules/memory/tools/writing.py` are responsible for shaping tool responses (`id`, `expires_at`, `superseded_id`) and must pass `embedding_engine` in the current positional order.

### Memory embedding progress-bar contract
- `src/butlers/modules/memory/embedding.py` must call `SentenceTransformer.encode(..., show_progress_bar=False)` for both single and batch embedding paths; otherwise `sentence-transformers` enables `tqdm` "Batches" output at INFO/DEBUG log levels, causing noisy interleaved logs.

### DB SSL config contract
- `src/butlers/db.py` now parses `sslmode` from `DATABASE_URL` and `POSTGRES_SSLMODE`; parsed mode is forwarded to both `asyncpg.connect()` (provisioning) and `asyncpg.create_pool()` (runtime).
- Dashboard DB setup in `src/butlers/api/deps.py` and `src/butlers/api/db.py` reuses the same env parser and forwards the same SSL mode to API pools, keeping daemon/API behavior aligned.
- When SSL mode is unset (`None`), DB connect/pool creation retries once with `ssl="disable"` if asyncpg fails during STARTTLS negotiation with `ConnectionError: unexpected connection_lost() call` (covers servers/proxies that drop SSLRequest instead of replying `S/N`).

### Telegram DB contract
- Module lifecycle receives the `Database` wrapper (not a raw pool). Telegram message-inbox logging should acquire connections via `db.pool.acquire()`, with optional backward compatibility for pool-like objects.

### Telegram ingress dedupe contract
- `src/butlers/modules/telegram.py::_store_message_inbox_entry` must persist inbound rows with deterministic Telegram dedupe keys and `ON CONFLICT (dedupe_key)` upsert semantics.
- `TelegramModule.process_update()` should treat non-insert (`decision=deduped`) ingress persistence results as replayed updates and short-circuit before pipeline routing.

### Telegram history thread-key contract
- Connector/runtime request context may carry Telegram `source_thread_identity` as either `<chat_id>` or `<chat_id>:<message_id>` (reply-target form); realtime history loading in `src/butlers/modules/pipeline.py::_load_realtime_history` must normalize/group by numeric chat id for `source_channel="telegram"` so message-scoped identities do not collapse history to a single row.

### HTTP client logging contract
- CLI logging config (`src/butlers/cli.py::_configure_logging`) sets `httpx` and `httpcore` logger levels to `WARNING` to prevent request-URL token leakage (notably Telegram bot tokens in `/bot<token>/...` paths).

### Telegram reaction lifecycle contract
- `TelegramModule.process_update()` now sends lifecycle reactions for inbound message processing: starts with `:eye`, ends with `:done` when all routed targets ack, and ends with `:space invader` on any routed-target failure.
- `RoutingResult` includes `routed_targets`, `acked_targets`, and `failed_targets`; decomposition callers should populate these so Telegram can hold `:eye` until aggregate completion.
- Per-message reaction state must not grow unbounded: terminal messages should prune `_processing_lifecycle`/`_reaction_locks`, and duplicate-update idempotence should be preserved via the bounded `_terminal_reactions` cache (`TERMINAL_REACTION_CACHE_SIZE`).
- `src/butlers/modules/telegram.py::_update_reaction` treats `httpx.HTTPStatusError` 400 responses from `setMessageReaction` as expected/non-fatal when Telegram indicates reaction unsupported/unavailable; for terminal failure (`:space invader` internal alias -> 👾) it should warn-and-skip rather than emit stack traces.

### Telegram getUpdates conflict contract
- `src/butlers/connectors/telegram_bot.py::_get_updates` must treat Telegram `HTTP 409 Conflict` responses as recoverable polling conflicts: record source API status `conflict`, emit warning-level diagnostics with parsed Telegram `description`, and return `[]` instead of raising.
- `src/butlers/modules/telegram.py::_get_updates` should likewise treat `HTTP 409 Conflict` as non-fatal and return `[]` with a warning so ingress/tool callers avoid repeated unhandled stack traces during webhook/poller contention.

### Frontend test harness
- Frontend route/component tests run with Vitest (`frontend/package.json` has `npm test` -> `vitest run`).
- Colocate tests as `frontend/src/**/*.test.tsx` (example: `frontend/src/pages/ButlersPage.test.tsx`).

### Memory browser episode expansion contract
- `frontend/src/components/memory/MemoryBrowser.tsx` episodes rows expose an explicit `Expand`/`Collapse` control that reveals a full-content detail row (`Episode Content`) while keeping the main cell preview truncated.
- Regression coverage lives in `frontend/src/components/memory/MemoryBrowser.test.tsx` and asserts collapsed-by-default, expand-to-read, and collapse-again behavior.

### Frontend docs source-of-truth contract
- `docs/frontend/` is the canonical, implementation-grounded frontend spec set (`purpose-and-single-pane.md`, `information-architecture.md`, `feature-inventory.md`, `data-access-and-refresh.md`).
- `docs/FRONTEND_PROJECT_PLAN.md` is historical/aspirational context; update `docs/frontend/` when routes, tabs, feature coverage, or data-refresh/write behavior changes.
- `docs/frontend/backend-api-contract.md` is the target-state backend API contract required by the frontend; keep endpoint/query/payload definitions authoritative and up to date.

### Command palette trigger contract
- Dashboard command palette opening is event-driven via `frontend/src/lib/command-palette.ts` (`OPEN_COMMAND_PALETTE_EVENT = "open-search"`).
- Global hotkeys (`frontend/src/hooks/use-keyboard-shortcuts.ts`) and the header search icon (`frontend/src/components/layout/PageHeader.tsx`, with `Cmd/Ctrl+K` hover hint) must dispatch the shared open event.
- `frontend/src/components/layout/CommandPalette.tsx` should listen for that shared event and focus its search input when opening.

### Frontend single-pane contract updates (2026-02-14)
- `/issues` is now a first-class frontend surface (route + sidebar) backed by `useIssues`; Overview includes `IssuesPanel` alongside failed notifications.
- Overview KPI cards are wired: `Sessions Today` is sourced via `/api/sessions` with `since=<local-midnight ISO>` and `Est. Cost Today` via `/api/costs/summary?period=today`.
- Butler detail Overview cost card must show selected-butler daily cost (`by_butler[butlerName]`) plus global-share context, not global total as the primary value.
- Notification feed rows should expose drill-through links to session and trace detail when `session_id` / `trace_id` are present.
- Keyboard quick-nav includes `g` sequences: `o,b,s,t,r,n,i,a,m,c,h`.
- Butler detail tab validation must include health-only tabs so `?tab=health` deep-links resolve on `/butlers/health`.
- `/settings` now provides browser-local controls for theme, default live-refresh behavior (used by Sessions/Timeline), and clearing command-palette recent-search history.
- Frontend router must set `createBrowserRouter(..., { basename: import.meta.env.BASE_URL })` (sanitized) so `dev.sh` subpath deployments (`--base /butlers/`) behave consistently for direct loads and in-app links (for example `/butlers/secrets`), while root-origin paths like `/secrets` correctly 404 under split Tailscale path mapping.
- Contacts sync UI contract: dashboard contacts surface includes a header `Sync From Google` action that calls `POST /api/relationship/contacts/sync?mode=incremental`, shows in-flight (`Syncing...`) + toast success/error feedback, and refreshes contacts data after success. Router exposes both `/contacts` and `/butlers/contacts` to the same page.

### Session tool-call rendering contract
- `frontend/src/components/sessions/SessionDetailDrawer.tsx` must normalize tool-call records before rendering: tool names can appear as `name|tool|tool_name` (including nested `call`/`tool_call`/`toolCall`/`function` objects), arguments can appear as `input|args|arguments|parameters`, and result payloads can appear as `result|output|response`.
- When normalized arguments/results are absent, render a fallback raw payload block so `Tool Calls (N)` never appears empty for unknown record shapes.
- For legacy unnamed rows, `SessionDetailDrawer` should infer fallback tool labels from session result summaries like ``MCP tools called: - `tool_name(...)` `` so UI labels remain informative even when stored call records lack `name`.
- `src/butlers/core/runtimes/codex.py::_extract_tool_call` and `_looks_like_tool_call_event` must treat nested `tool` objects like other containers (`function`/`call`/`tool_call`/`toolCall`) when extracting tool name + arguments, preventing name loss for this Codex event shape.

### Quality-gate command contract
- `make test-qg` is a local `tests/` regression gate and runs with xdist parallelization (`-n auto`); it is not CI-equivalent because it omits `roster/`, root DB/migration suites, and E2E.
- `make test-qg-serial` is the documented serial fallback for debugging order-dependent behavior.
- Both route through `scripts/pytest_gate.py` (see "Parallel Test Command"); read the printed
  `PASS`/`FAILED`/`UNKNOWN` verdict line, not the presence or absence of `FAILED` in the log.

### No local gate command matches CI's scope — check which one produced a number

Verified 2026-08-22. The commands called "the full gate" are each narrower than CI's `check` job,
on different axes. Treating any of them as equivalent to CI is how a green local run precedes a red
required check.

| what | actual scope |
| --- | --- |
| `make test-qg` (called "full-scope" above) | `pytest tests/` minus `test_db.py`, `test_migrations.py`, `tests/e2e` |
| CLAUDE.md low-context gate | `pytest tests/ --ignore=tests/e2e` |
| CI `check`, unit lane (`ci.yml:154`) | `pytest tests/ roster/ --ignore=tests/e2e -m "not integration and not e2e and not nightly and not bench and not perf"` |
| CI `check`, integration lane (`ci.yml:266`) | `pytest tests/ roster/ -m "integration and not nightly and not bench and not perf" -n auto --dist loadfile` |

**`tests/` does not collect `roster/`.** They are sibling top-level directories, so any local command
rooted at `tests/` runs *nothing* under `roster/<butler>/tests/`. A change whose tests live there can
show a fully green "full gate" with its own tests never executed. Run `tests/ roster/` whenever the
diff touches a roster butler. `make test-qg` additionally skips the DB and migration suites, so it is
the wrong gate for any migration work.

`make lint` is likewise `ruff check src/ tests/` only — it omits `roster/` and `conftest.py`, which the
CLAUDE.md gate does cover.

**Skip-count baselines are per-scope and are NOT interchangeable:**

| command | baseline (2026-08-22) |
| --- | --- |
| `pytest tests/ --ignore=tests/e2e` | ~13.9k passed, **21 skipped** |
| `pytest roster/` | ~4044 passed, **0 skipped**, 1 xfailed |
| `pytest tests/ roster/` (unfiltered) | ~17.9k passed, **113 skipped**, 1 xfailed |

Quoting the 21-skipped baseline at a `tests/ roster/` run makes a correct result look like a
regression. Before treating a skip count as evidence of anything, confirm which command produced it.

**The 21 → 113 gap is `tests/e2e`, not `roster/`.** Measured 2026-08-22: `roster/` on its own
contributes **zero** skips, so the ~92 extra skips in the unfiltered run come from the `tests/e2e`
directory that `--ignore=tests/e2e` excludes and the bare `tests/ roster/` form collects. Anyone
reconciling 21 against 113 will otherwise go hunting through `roster/` and find nothing. (The 21 and
0 figures come from one branch and the 113 from another; no branch involved added a skip, so this is
strongly indicated by three measurements rather than proven by one controlled run.)

Practical consequence: `pytest tests/ --ignore=tests/e2e` **plus** `pytest roster/` covers CI's
`check` scope between them, and the second takes ~6 minutes. When a change touches no `roster/`
files, that pair is far cheaper than re-running the whole ~25-minute unfiltered gate to reach the
same coverage — and it matters when the full gate holds a serialized lock other agents are queued on.

Also: `-q` prints no test names. An exit-0 `-q` run proves the suite passed; it does NOT prove the
tests you just wrote were collected rather than skipped. Confirm new files separately with `-v` or
`-q -rs`.

General rule this is an instance of: a gate is only evidence about what it collects, and "full" in a
command's name is not a claim about scope.

### Pytest benchmark snapshot (butlers-vrs, 2026-02-13)
- Unit-scope serial benchmark (`.venv/bin/pytest tests/ -m unit ...`) measured `114.87s` wall (`1854 passed, 358 deselected`).
- Unit-scope parallel benchmark (`.venv/bin/pytest tests/ -m unit ... -n 4`) measured `56.12s` wall (`1854 passed`), ~51% faster than the unit serial run.
- Full required gate `make test-qg` completed in this worktree at `129.15s` wall (`2211 passed, 1 skipped`), but intermittent Docker teardown flakes remain possible on DB-backed scopes (see `butlers-kle`).

### Calendar OAuth init contract
- In `src/butlers/modules/calendar.py`, `_GoogleProvider.__init__` should validate `_GoogleOAuthCredentials.from_env()` before creating an owned `httpx.AsyncClient` so credential errors cannot leak unclosed clients.
- `_GoogleOAuthClient.get_access_token()` should enforce token non-null invariants with explicit asserts rather than returning a fallback empty string.

### Calendar payload parsing error contract
- In `src/butlers/modules/calendar.py`, provider payload/data validation helpers (`_parse_google_datetime`, `_parse_google_event_boundary`, `_google_event_to_calendar_event`) raise `ValueError` for malformed event content; reserve `CalendarAuthError`/subclasses for auth/request transport failures.

### Calendar read tools contract
- `CalendarModule.register_tools()` now exposes `calendar_list_events` and `calendar_get_event`; both must call the active `CalendarProvider` abstraction (not provider-specific helpers directly).
- Tool responses are normalized as `{provider, calendar_id, ...}` with event payload keys `event_id`, `title`, `start_at`, `end_at`, `timezone`, `description`, `location`, `attendees`, `recurrence_rule`, and `color_id`.
- Optional `calendar_id` overrides must be stripped/non-empty and must not mutate the module's default configured `calendar_id`.

### Calendar roster rollout contract
- `roster/general/butler.toml`, `roster/health/butler.toml`, and `roster/relationship/butler.toml` must each declare `[modules.calendar]` with provider `google`, explicit shared Butler calendar `calendar_id` values (not `primary`), and default conflict policy `suggest`.
- `roster/general/CLAUDE.md`, `roster/health/CLAUDE.md`, and `roster/relationship/CLAUDE.md` must document calendar tool usage, shared Butler calendar assumption, default conflict behavior (`suggest`), and that attendee invites are out of v1 scope.

### Calendar conflict preflight contract
- Calendar conflict policy is `suggest|fail|allow_overlap` at tool/config boundaries; legacy config values (`allow`, `reject`) normalize to `allow_overlap`, `fail`.
- `calendar_create_event` always runs conflict preflight; `calendar_update_event` runs conflict preflight only when the start/end window changes.
- Conflict outcomes return machine-readable `conflicts` and `suggested_slots` (`suggest` policy), while `allow_overlap` currently writes through and includes conflicts in the success payload.

### Calendar overlap approval contract
- For overlap conflicts with `conflict_policy="allow_overlap"` and `conflicts.require_approval_for_overlap=true`, `calendar_create_event` / `calendar_update_event` must return `status="approval_required"` before provider writes and queue a `pending_actions` row with executable `tool_name` + serialized `tool_args`.
- Queued calendar overlap actions include `approval_action_id`; replay calls with that id should only bypass re-queue when the corresponding pending action is in `approved` state for the same tool.
- If approvals storage is unavailable (for example approvals module disabled or `pending_actions` table missing), overlap overrides must return `status="approval_unavailable"` plus explicit fallback guidance instead of writing.

### Approvals executor fallback contract
- `ButlerDaemon._apply_approval_gates()` should wire approvals execution with a fallback to registered MCP tool handlers when a `tool_name` is not present in gated originals, so module-queued pending actions for non-gated tools can execute after approval.

### Direct approval producer replay contract
- A producer that calls `park_pending_action()` outside the normal MCP approval gate must persist a declared owner, registered tool name, and exact keyword args, then have the owning daemon validate that handler signature at startup. If no safe command can be replayed (especially for secret-bearing requests), reject before parking with a redacted audit signal; never repair historic rows by guessing a replacement command or arguments.

### Beads coordinator handoff guardrail
- Some worker runs can finish with branch pushed but bead still `in_progress` (no PR/bead transition). Coordinator should detect `agent/<id>` ahead of `main` with no PR and normalize by creating a PR and marking the bead `blocked` with `pr-review` + `external_ref`.

### Beads backend contract (Dolt server)
- `bd` v1.0.x is backed by the shared Dolt server (`dolt.parrot-hen.ts.net:3307`, db `butlers`, `.beads/metadata.json` `dolt_mode: server`). No SQLite DB, no `.beads/beads.db`, no `beads-sync` branch, no `bd sync` subcommand.
- Mutations (`bd create/update/close`) write directly to Dolt and auto-commit to its history immediately — durable without any export/sync step.
- The local gitignored JSONL mirror is `.beads/issues.export.jsonl` (`export.path` in `.beads/config.yaml`); refresh it with `bd export -o .beads/issues.export.jsonl`. Dolt is the source of truth; never commit the mirror. NEVER create `.beads/issues.jsonl` (it triggers a wedging full-reimport loop on writes; see the bd 1.0.4 note below).
- All worktrees share the one Dolt server, so a bead created anywhere is visible everywhere immediately — no hydration/import step needed.

### Beads PR-review strip guardrail
- Before a reviewer worker strips `.beads/` drift from a PR branch, persist any new coordinator-side bead mutations first; restoring `.beads/` from `origin/main` only affects the JSONL mirror (Dolt remains source of truth), but keep the mirror consistent to avoid confusing diffs.

### Pre-existing test failure (tests/daemon/test_module_state.py)
- `tests/daemon/test_module_state.py::TestInitModuleRuntimeStates::test_failed_module_persists_disabled_to_store` is failing on main as of 2026-02-20. CI runs `mergeStateStatus: UNSTABLE` for PRs unrelated to daemon module state. This is a pre-existing failure not introduced by credential_store or butler_secrets PRs.

### CredentialStore service (src/butlers/credential_store.py)
- Lives at `src/butlers/credential_store.py`. Backed by `butler_secrets` table (migration `core_008`).
- Uses `TYPE_CHECKING` guard to import `asyncpg.Pool` (avoids runtime dependency, keeps type safety).
- `resolve(key, env_fallback=True)`: DB-first, then `os.environ.get(key)`, skips empty string env values.
- `list_secrets()` returns only DB-stored secrets (env-only secrets are not listed). `is_set=True` always for any DB row (table enforces `secret_value NOT NULL`).
- Thread-safe: each operation independently calls `pool.acquire()`; never shares connections across concurrent calls.
- Gmail connector DB bootstrap must read OAuth keys via `CredentialStore`/`load_google_credentials` (`butler_secrets`), not legacy `google_oauth_credentials`; optional Pub/Sub token lookup failures must not null-out already resolved OAuth creds.

### Beads worktree write guardrail
- All `bd` writes go to the shared Dolt server regardless of which worktree you run them from, so bead state is consistent across worktrees without `--no-db` gymnastics.
- `bd worktree create` may append per-worktree paths to repo `.gitignore`; strip those incidental lines before committing to avoid unrelated drift on `main`.
- The `.beads/issues.export.jsonl` mirror is gitignored; never commit or force-add it. Dolt is source of truth, so if the mirror ever appears in a branch diff, drop it before merging and let `bd export` regenerate it locally.

### Beads dependency timestamp guardrail
- In no-daemon worktree flows (`BEADS_NO_DAEMON=1`), `bd dep add` currently serializes new dependency records with `created_at="0001-01-01T00:00:00Z"` instead of wall-clock time; treat this as tooling debt (tracked in `butlers-865`) rather than a per-bead data-model change.

### Beads PR-review `external_ref` uniqueness contract
- Beads enforces global uniqueness for `issues.external_ref`; a dedicated `pr-review-task` bead cannot reuse the same `gh-pr:<number>` already attached to the original implementation bead.
- For split original/review-bead workflows, keep `external_ref` on the original bead and store PR metadata (`PR URL`, `PR NUMBER`, original bead id) in review-bead notes/labels, then dispatch reviewer workers with explicit PR context.

### Beads PR-review dependency-direction guardrail
- If the original implementation bead must be blocked by a dedicated PR-review bead, do not create the review bead with `--deps discovered-from:<original>` because that pre-wires the reverse dependency and causes a cycle when adding `<original> depends-on <review>`.
- Preferred flow: create the review bead without `discovered-from`, then add `bd dep add <original> <review>` so review completion unblocks the original bead.

### Beads merge-blocker dedupe guardrail
- Before creating a new `Resolve merge blockers for PR #<n>` bead from a blocked `pr-review-task`, check for an existing open blocker bead tied to the same PR/original issue and reuse it by wiring dependencies instead of creating duplicates.

### Beads merge-blocker completion guardrail
- Merge-blocker worker runs can leave the blocker bead `in_progress` after successfully unblocking/merging a PR; coordinator should normalize by closing the blocker bead and, when applicable, closing related `pr-review`/original beads for merged PRs.

### PR merge + worktree cleanup guardrail
- After the exact-base helper reports `merged-exact-base`, verify the merge via `gh pr view --json state,mergedAt` before deciding blocked vs merged, then remove the checked-out worktree and delete the local branch separately.

### Beads lint template contract
- `bd lint` enforces section headers in issue descriptions, not only structured fields.
- For `task` issues include `## Acceptance Criteria` in `description`; for `epic` issues include `## Success Criteria`.
- For `bug` issues created with `--validate`, include `## Acceptance Criteria` in `description` (the separate `--acceptance` flag alone is not sufficient).

### Decision-bead convention (bu-ckkpz.1, epic bu-ckkpz "Owner Decision Desk")
Owner-attention decisions are marked by the `decision` label. Legacy title
text ("DECISION REQUIRED (owner)", "[OWNER-GATED]", "OWNER:",
"ARCHITECTURAL DECISION") is readable context, not a runtime classifier; the
strict linter flags an open, non-epic legacy-shaped bead that lacks the label.
Any bead that asks the owner to choose among options (not just "do this task")
should follow this machine-checkable convention, built entirely on native `bd`
fields — no new issue type and no bespoke deadline format:

1. **Label** — add the `decision` label: `bd create ... --label decision` /
   `bd update <id> --labels decision`. This is the marker the decision-review
   runtime, linter, and dashboard query use (`bd list --label decision`).
2. **Structured options** — set `metadata.decision.options` (a non-empty list
   of distinct, non-blank strings) and `metadata.decision.default` (one
   string that exactly matches an entry in `options` — the fallback applied
   if the owner does not respond by the deadline), e.g.:
   ```bash
   bd create "DECISION REQUIRED (owner): re-enable the api-haiku lane?" \
     --type task --label decision --due 2026-07-25 \
     --metadata '{"decision": {"options": ["A: re-enable now", "B: keep disabled", "C: descope"], "default": "B: keep disabled"}}' \
     --description "Context: ...\n\nOption A: ...\nOption B: ...\nOption C: ..."
   ```
3. **Deadline** — set bd's native `due_at` via `--due` (e.g. `--due 2026-07-25`,
   `--due +2w`). Do not invent a second, text-only deadline field; `due_at` is
   already filterable (`bd list --due-before ...`, `bd list --overdue`).
4. `description` stays free-form prose (context, rationale, links) — it is
   not required to restate the options verbatim; `metadata.decision` is the
   single structured source of truth consumers should parse.

**Do not reuse bd's built-in `issue_type: decision`.** That type is a
pre-existing, unrelated ADR-style "decision already made" template — `bd
create --type decision --validate` demands `## Decision` / `## Rationale` /
`## Alternatives Considered`, which describes a decision that has already
happened, not one awaiting the owner. Owner-decision beads keep their normal
`issue_type` (usually `task`) and use the `decision` *label* instead.

**Linter:** `scripts/lint_decision_beads.py` checks that every `decision`-labeled
bead carries the four properties above (run via `make lint-decision-beads` or
directly with `python3 scripts/lint_decision_beads.py [issue-id...]`). It
reads live via `bd` (or an offline `--issues-json-file` snapshot for
tests/CI), so it is a manual/local check, not part of `make check` or CI —
GitHub Actions cannot reach the local Dolt server backing `bd`.
`--issues-json-file` accepts either a plain JSON array/object or
newline-delimited JSON (the `bd export` format).

**Non-vacuous mode (bu-hmdqz.6):** by default the linter only checks beads
that already carry the `decision` label, so against a queue where nothing
has adopted it yet, it discovers zero rows and reports a vacuous "clean"
pass. `--check-unlabeled-markers` (`make lint-decision-beads-strict`) widens
discovery to also flag open, non-epic beads whose titles match a legacy
decision marker but lack the label — those then fail the existing "missing
'decision' label" check. `src/butlers/jobs/decision_review.py`'s weekly
digest job (`run_decision_review_digest`) runs this mode automatically
against the mounted `issues.export.jsonl`, delivering a low-priority
attention-ledger-recorded nudge when it finds unmigrated beads.

**Known consumer:** `src/butlers/jobs/decision_review.py` (the weekly
decision-review digest + P1/deploy escalation cron) classifies open, non-epic
decision beads solely by the `decision` label. It never falls back to title
text; its strict lint path separately nudges legacy-shaped unlabeled beads to
migrate.

### Relationship `important_dates` column contract
- Relationship schema stores date kind in `important_dates.label` (not `important_dates.date_type`).
- API queries touching birthdays/upcoming dates should use `label` consistently to avoid `UndefinedColumnError` on production schema.

### Relationship groups API schema-compat contract
- `roster/relationship/api/router.py` group reads (`list_groups`, `get_group`) must introspect `groups` columns via `information_schema.columns` before composing SELECTs.
- For deployments where `groups.description` and/or `groups.updated_at` are absent, project fallback expressions (`NULL::text AS description`, `g.created_at AS updated_at`) so responses keep the `Group` model shape and avoid `UndefinedColumnError`.

### Switchboard MCP routing contract
- `roster/switchboard/tools/routing/route.py::_call_butler_tool` calls butler endpoints via `fastmcp.Client` and should return `CallToolResult.data` when present.
- If a target returns `Unknown tool` for a routing tool name, routing retries `trigger` with mapped args (`prompt` from `prompt`/`message`, optional `context`).

### Route/notify envelope contract
- `roster/switchboard/tools/routing/contracts.py` exports `NotifyDeliveryV1`, `NotifyRequestV1`, and `parse_notify_request`; daemon messenger `route.execute` validation depends on these for `notify.v1` payload parsing.
- `RouteInputV1.context` must accept either string or mapping payloads (`str | dict | None`) because messenger `route.execute` carries structured `input.context.notify_request` objects.
- Messenger `route.execute` must reject `notify_request.origin_butler` when it does not match routed `request_context.source_sender_identity` (deterministic `validation_error`) before any channel send/reply side effects.

### Base notify and module-tool naming contract
- `docs/roles/base_butler.md` defines `notify` as a versioned envelope surface (`notify.v1` request, `notify_response.v1` response) with required `origin_butler`; reply intents require request-context targeting fields.
- Messenger delivery transport is route-wrapped: Switchboard dispatches `route.v1` to Messenger `route.execute` with `notify.v1` in `input.context.notify_request`; Messenger returns `route_response.v1` and should place normalized delivery output in `result.notify_response`.
- `notify_response.v1` uses the same canonical execution error classes as route executors (`validation_error`, `target_unavailable`, `timeout`, `overload_rejected`, `internal_error`); local admission overflow maps to `overload_rejected`.
- Messenger `route.execute` MUST include normalized `notify_response` in error paths when `input.context.notify_request` is missing or invalid, ensuring consistent error reporting contract (route-level error + notify-level error payload).
- `docs/roles/base_butler.md` does not define channel-facing tool naming/ownership as a base requirement; that policy is role-specific.
- `docs/roles/switchboard_butler.md` owns the channel-facing tool surface policy: outbound delivery send/reply tools are messenger-only, ingress connectors remain Switchboard-owned, and non-messenger butlers must use `notify.v1`.
- `docs/roles/switchboard_butler.md` explicitly overrides base `notify` semantics so Switchboard is the notify control-plane termination point (not a self-routed notify caller).
- `roster/switchboard/tools/routing/contracts.py` is the canonical parser surface for routed notify termination: `parse_notify_request()` validates `notify.v1`, and `RouteInputV1.context` must accept both string context and object context (for messenger `input.context.notify_request` payloads).

### Route/notify contract parsing alignment
- `src/butlers/daemon.py` imports `parse_notify_request` from `butlers.tools.switchboard.routing.contracts` at module import time; keep that parser exported in `roster/switchboard/tools/routing/contracts.py`.
- `RouteInputV1.context` must accept structured objects (`dict`) in addition to text so Messenger `route.execute` can receive `input.context.notify_request` payloads.

### Notify react message normalization contract
- `src/butlers/daemon.py::notify` must normalize omitted `message` to `""` before building `notify_request.delivery` so `intent="react"` payloads remain valid through downstream `notify.v1` validation paths that require a string-typed `delivery.message`.

### Spawner trigger-source/failure contract
- Core daemon `trigger` MCP tool should dispatch with `trigger_source="trigger"` (not `trigger_tool`) to stay aligned with `core.sessions` validation.
- `src/butlers/core/sessions.py` canonical trigger-source allowlist includes `route` because daemon `route.execute` background and recovery flows dispatch `spawner.trigger(..., trigger_source="route")`.
- `src/butlers/core/spawner.py::_run` should initialize duration timing before `session_create()` so early failures preserve original errors instead of masking with timer variable errors.
- `src/butlers/core/spawner.py::trigger` should fail fast when `trigger_source=="trigger"` and the per-butler lock is already held, preventing runtime self-invocation deadlocks (`trigger` tool calling back into the same spawner while a session is active).
- `src/butlers/core/runtimes/codex.py::CodexAdapter.invoke` must raise on non-zero CLI exit codes (instead of returning `"Error: ..."` as normal output) so spawner/session rows persist `success=false` and dashboard status matches runtime failures.
- `src/butlers/core/spawner.py::_build_env` includes host `PATH` as a minimal runtime baseline before declared credentials so spawned CLIs can resolve shebang dependencies (for example `/usr/bin/env node`) without hardcoded machine-specific node paths.

### Spawner system prompt composition contract
- `src/butlers/core/spawner.py::_compose_system_prompt` is the canonical composition path: runtime receives raw `CLAUDE.md` system prompt when memory context is unavailable, and appends memory context as a double-newline suffix when available.
- `tests/core/test_core_spawner.py::TestFullFlow` should patch `fetch_memory_context` for deterministic assertions so local memory module/tool availability cannot change expected `system_prompt` text.

### Memory session hook ownership contract
- `core.memory_hooks` context and episode-store dispatch must use a paired runtime keyed by the invoking butler/schema, never a last-started process-global closure; registration/unregistration is identity-safe so replacing or stopping one daemon cannot remove another daemon's active memory runtime (including Chronicler's `chronicler_mem` pool).

### Sessions summary contract
- `src/butlers/daemon.py` core MCP registration should include `sessions_summary`; dashboard cost fan-out relies on declared tool metadata and will log `"Tool 'sessions_summary' not listed"` warnings if not advertised.
- `src/butlers/core/sessions.py::sessions_summary` response payload should include `period`, and unsupported periods must raise `ValueError` with an `"Invalid period ..."` message.

### Liveness reporter 404 contract
- `src/butlers/daemon.py::_liveness_reporter_loop` must treat heartbeat endpoint `404 Not Found` as persistent misconfiguration (wrong host/port/path), log a single warning, and stop the reporter loop instead of retrying indefinitely with traceback spam.
- Regression coverage lives in `tests/daemon/test_liveness_reporter.py::test_404_disables_reporter_without_retries`.

### Switchboard heartbeat auto-registration contract
- `roster/switchboard/api/router.py::receive_heartbeat` should attempt roster-driven self-registration (`roster/<butler>/butler.toml`) when a heartbeat arrives for a butler missing from `butler_registry`, then re-check registry and continue normal heartbeat state handling.
- Unknown names with no roster config must still return `404`, preserving the signal for truly invalid targets.

### MCP client lifecycle hotspot
- `roster/switchboard/tools/routing/route.py::_call_butler_tool` currently opens a new `fastmcp.Client` (`async with`) per routed tool call, which can generate high `/sse` + `ListToolsRequest` log volume under heartbeat fanout.
- `src/butlers/core/spawner.py` memory hooks (`fetch_memory_context`, `store_session_episode`) also create one-off Memory MCP clients per call; this is another source of SSE session churn.

### MCP SSE disconnect guard contract
- `src/butlers/daemon.py::_McpSseDisconnectGuard` wraps the FastMCP SSE ASGI app and suppresses expected `starlette.requests.ClientDisconnect` only for `POST .../messages` requests.
- The guard logs a concise DEBUG line with butler/path/session context and attempts a lightweight empty `202` response when possible; non-`/messages` disconnects and non-disconnect exceptions must still bubble.
### Telegram inbox logging contract
- `TelegramModule.process_update()` should log inbound payloads via `db.pool.acquire()` when DB is available and pass the returned `message_inbox_id` into `pipeline.process(...)`.
- Keep Telegram `pipeline.process` tool args aligned with tests (`source`, `source_channel`, `source_identity`, `source_tool`, `chat_id`, `source_id`); additional metadata should not be forced into this call path without updating tests/contracts.

### Route.execute authn/authz contract
- `src/butlers/daemon.py` `route.execute` enforces `request_context.source_endpoint_identity` against `ButlerConfig.trusted_route_callers` (default: `("switchboard",)`) before any spawner trigger or delivery adapter call.
- Unauthorized callers receive a deterministic `validation_error` response with `retryable=false`; no side effects occur.
- `[butler.security].trusted_route_callers` in `butler.toml` overrides the default; empty list rejects all callers.
- Regression tests in `tests/daemon/test_route_execute_authz.py` cover unauthenticated/unauthorized rejection, custom config, and authorized pass-through.

### Core tool registration contract
- `src/butlers/daemon.py` exports `CORE_TOOL_NAMES` as the canonical core-tool set (including `notify`); registration tests should assert against this set to prevent drift between `_register_core_tools()` behavior and expected tool coverage.
  Adding any new core tool therefore requires adding its name to the matching frozenset in `daemon.py` (`UNIVERSAL_/MESSENGER_/DOMAIN_CORE_TOOL_NAMES`) -- `tests/daemon/test_daemon.py::test_all_core_tools_registered` asserts set *equality*, so a tool registered but not listed fails there and nowhere near the code you changed.
- MCP tool-call logging is centralized in `src/butlers/daemon.py`: `_register_core_tools()` registers through `_ToolCallLoggingMCP(module_name="core")`, and module tools log through `_SpanWrappingMCP` before module-enabled gating/span execution.
- Canonical call log format is `MCP tool called (butler=%s module=%s tool=%s)`; keep this stable for log parsing/observability.

### Switchboard ingress dedupe contract
- `MessagePipeline` enforces canonical ingress dedupe when `enable_ingress_dedupe=True` (wired on for Switchboard in `src/butlers/daemon.py::_wire_pipelines`).
- Dedupe keys are channel-aware: Telegram uses `<endpoint_identity>:update:<update_id>`, Email uses `<endpoint_identity>:message_id:<Message-ID>`, API/MCP use `<endpoint_identity>:idempotency:<caller-key>` when present, else `<endpoint_identity>:payload_hash:<sha256>:window:<5-minute-bucket>`.
- Ingress decisions log as `"Ingress dedupe decision"` with `ingress_decision=accepted|deduped`; deduped replays map to the existing canonical `request_id` and short-circuit routing.

### Approvals product-contract docs alignment
- `docs/modules/approval.md` is now a product-level contract (not just current behavior) and includes explicit guardrails for single-human approver model, idempotent decision/execution semantics, immutable approval-event auditing, data redaction/retention, risk-tier policy precedence, and friction-minimizing operator UX.
- Frontend docs now explicitly track approvals as target-state single-pane integration: planned IA routes in `docs/frontend/information-architecture.md`, current gap in `docs/frontend/feature-inventory.md`, target data-access guidance in `docs/frontend/data-access-and-refresh.md`, and target API endpoints in `docs/frontend/backend-api-contract.md`.

### Approvals immutable event-log contract
- Approvals migrations include `approvals_002` with append-only `approval_events` and a trigger (`trg_approval_events_immutable`) that rejects `UPDATE`/`DELETE`; event rows must be written via inserts only.
- Canonical approval event types are `action_queued`, `action_auto_approved`, `action_approved`, `action_rejected`, `action_expired`, `action_execution_succeeded`, `action_execution_failed`, `rule_created`, and `rule_revoked`.

### Approvals risk-tier + precedence runtime contract
- `src/butlers/config.py::ApprovalConfig` now includes `default_risk_tier` plus per-tool `GatedToolConfig.risk_tier`; `parse_approval_config` validates both against `ApprovalRiskTier` (`low|medium|high|critical`).
- Standing rule matching precedence is deterministic in `src/butlers/modules/approvals/rules.py` (`constraint_specificity_desc`, `bounded_scope_desc`, `created_at_desc`, `rule_id_asc`); gate responses include `risk_tier` and `rule_precedence`.
- High-risk tiers (`high`, `critical`) enforce constrained standing rules in `src/butlers/modules/approvals/module.py`: at least one exact/pattern arg constraint and bounded scope (`expires_at` or `max_uses`); `create_rule_from_action` and approve+create-rule paths auto-bound high-risk rules with `max_uses=1`.

### Beads concurrent-state reconciliation guardrail
- In multi-worker coordinator runs, stale worker commits of the `.beads/issues.export.jsonl` mirror can look like they resurrect previously normalized bead state in diffs; trust Dolt (the source of truth) over the mirror, and regenerate it with `bd export` rather than reverting bead status from a stale JSONL.
- After each coordinator cycle, re-run a PR-state normalization pass (`blocked` + `pr-review` / `pr-review-task`) before dispatching more workers, rather than assuming prior status updates remained authoritative.

### Dev bootstrap connector env-file contract
- `dev.sh` connectors window runs three connector processes: Telegram bot, Telegram user-client, and Gmail.
- Each connector pane may source a local-only env file under `secrets/connectors/` (`telegram_bot`, `telegram_user_client`, `gmail`) using `set -a` so values only affect that pane process.
- Connector endpoint identity is auto-resolved at startup (telegram bot: `getMe`, telegram user: `get_me()`, gmail: `google_accounts.email`, discord: `/users/@me`). No manual identity env var needed. Cursor state is DB-backed (no file path env var needed).

### Dev script location + process-clear contract
- Canonical bootstrap implementation now lives at `scripts/dev.sh`; repository-root `dev.sh` is a compatibility shim that delegates to `scripts/dev.sh`.
- `scripts/clear-processes.sh` is the canonical pre-bootstrap cleanup helper: by default it targets listeners on `POSTGRES_PORT` (`54320`), `FRONTEND_PORT` (`41173`), and `DASHBOARD_PORT` (`41200`), with explicit override via `EXPECTED_PORTS`.

### Telemetry span concurrency guardrail
- `src/butlers/core/telemetry.py::tool_span` decorator usage is unsafe if per-invocation span/token state is stored on the decorator instance (`self._span`, `self._token`): concurrent calls to one decorated async handler can trigger OpenTelemetry `Failed to detach context` / `Token ... created in a different Context`.
- Repro pattern: concurrent `await asyncio.gather(...)` calls to a single `@tool_span(...)`-decorated function fail; per-call context-manager usage (`with tool_span(...)`) does not.
- Track holistic fix in `butlers-978`, including both decorator state isolation and concurrent-session `_active_session_context` parent-lineage hardening.

### Dev bootstrap tailscale+pipefail guardrail
- `dev.sh::_tailscale_serve_check` should prefer modern Tailscale CLI syntax (`tailscale serve --yes --bg --https=443 http://localhost:41200`) with legacy positional fallback (`https:443 ...`) for older CLI versions.
- `dev.sh` split routing defaults are `TAILSCALE_DASHBOARD_PATH_PREFIX=/butlers` (Vite frontend) and `TAILSCALE_API_PATH_PREFIX=/butlers-api` (dashboard API); non-root path routing uses `tailscale serve --set-path <prefix> ...`.
- Dashboard mapping should proxy to `http://localhost:${FRONTEND_PORT}${TAILSCALE_DASHBOARD_PATH_PREFIX}` (not bare frontend root) so prefix paths are preserved end-to-end and Vite `--base` assets avoid redirect loops under tailscale path routing.
- Frontend dev port is configurable via `FRONTEND_PORT` (default `41173`) and should be kept aligned with tailscale dashboard target and the Vite startup command (`--port ... --strictPort`).
- `docker/Dockerfile` is the dev-suite image target for `dev.sh`: include `tmux`, `postgresql-client`, Docker CLI + compose plugin, tailscale CLI, Node.js, and global runtime CLIs (`@openai/codex`, `@anthropic-ai/claude-code`, `opencode-ai`) so `dev.sh` can run in-container when host sockets are mounted.
- Do not discard `tailscale serve` stderr in `dev.sh`; surfaced output is needed to diagnose operator/permission failures (for example `Access denied: serve config denied` and `sudo tailscale set --operator=$USER` remediation).
- In `dev.sh` with `set -o pipefail`, avoid `grep ... | wc -l || echo 0` inside command substitutions; on no-match this can produce `0\n0` and break integer comparisons.

### Scheduler native-dispatch contract
- `ButlerDaemon._dispatch_scheduled_task()` is the scheduler dispatch hook used by both the background scheduler loop and MCP `tick` tool; deterministic schedules can bypass runtime/LLM calls here.
- Switchboard `schedule:eligibility-sweep` is natively dispatched via the roster job loader (`_load_switchboard_eligibility_sweep_job`) and executes against the switchboard DB pool directly; non-native schedules still fall back to `spawner.trigger`.
- `ScheduleConfig` now carries `mode` (`session` default, `job` for deterministic/native execution); config loading must reject unknown `[[butler.schedule]].mode` values.
- Switchboard deterministic schedules (`connector-stats-hourly-rollup`, `connector-stats-daily-rollup`, `connector-stats-pruning`, `eligibility-sweep`) should be declared with `mode = "job"` in `roster/switchboard/butler.toml` so scheduler dispatch bypasses LLM sessions.
- `ButlerDaemon._dispatch_scheduled_task()` resolves schedule mode from `self.config.schedules`; `mode="job"` schedules use `_load_switchboard_schedule_jobs()` handlers and fail fast when no handler is registered (no fallback `spawner.trigger` call).

### Issues aggregation contract
- `src/butlers/api/routers/issues.py` aggregates reachability checks plus grouped `dashboard_audit_log` failures.
- Audit groups are keyed by normalized first-line error message and expose `occurrences`, `first_seen_at`, `last_seen_at`, and distinct `butlers`.
- `GET /api/issues` is ordered by recency (`last_seen_at` desc), not severity-first; schedule-related groups (`operation=session` + `trigger_source` like `schedule:%`) are classified as `critical` `scheduled_task_failure:*`, all other audit groups are `warning` `audit_error_group:*`.

### Audit log degraded-read contract
- `GET /api/audit-log` must treat `asyncpg.exceptions.UndefinedTableError` on `dashboard_audit_log` as an empty page (`data=[]`, `total=0`) rather than a 500, because the dashboard can come up against an unmigrated or offline switchboard schema.

### State API JSON-shape contract
- `src/butlers/api/models/state.py::StateEntry.value` and `StateSetRequest.value` are typed `Any` (widened from `dict[str, Any]` in PR #205); scalar/array/null JSON rows in `state.value` are now serialized correctly.
- Keep list/get state endpoint value-shape contracts aligned with the full JSON domain accepted by the underlying state storage.
- asyncpg decodes JSONB columns directly to native Python types; no secondary `json.loads` fallback is needed in the router.
- The asyncpg JSONB codec also encodes write parameters: pass dicts/lists directly to `$N::jsonb` placeholders. Wrapping with `json.dumps(...)` double-encodes and stores a JSONB string scalar instead of an object. With `metadata = COALESCE(metadata, '{}'::jsonb) || $N::jsonb`, this corruption is destructive — PostgreSQL coerces both operands to arrays and concatenates, leaving e.g. `metadata = [<orig_dict>, "<stringified-patch>"]` and breaking later reads (Pydantic `dict` validation fails). Audit any router/SQL site that combines `json.dumps(value)` with `$N::jsonb`.

### Connector credential resolution pattern (CredentialStore)
- Connectors are standalone processes and need their own short-lived asyncpg pool (min_size=1, max_size=2, command_timeout=5) gated on `DATABASE_URL` or `POSTGRES_HOST` being set.
- `TelegramBotConnectorConfig` and `TelegramUserClientConnectorConfig` are Python **dataclasses** (not Pydantic models); use `dataclasses.replace(config, field=value)` for partial updates — `model_copy()` is Pydantic-only.
- `GmailConnectorConfig` is a Pydantic `BaseModel` with `frozen=True`; use `config.model_copy(update={...})` for partial updates.
- Pydantic v2 auto-coerces `str` to `pathlib.Path` for `Path`-typed fields, but prefer explicit `Path(cursor_path_str)` at construction sites to satisfy static type checkers and remove `type: ignore` suppressions.
- `bd close` from any worktree persists directly to the shared Dolt server; verify with `bd show <id> --json` if in doubt (no `beads-sync` branch re-close step exists anymore).

### Secrets shared-target contract
- `src/butlers/api/routers/secrets.py` treats `/api/butlers/shared/secrets` as a reserved target that resolves via `DatabaseManager.credential_shared_pool()` (not `db.pool("shared")`), returning 503 with `"Shared credential database is not available"` when unset.
- `frontend/src/pages/SecretsPage.tsx` must include a first-class `shared` selector target (via `buildSecretsTargets`) so users can manage shared secrets directly, with per-butler entries representing local override stores.
- `frontend/src/hooks/use-secrets.ts::useSecrets` is responsible for effective-read fallback in the Secrets page: for non-`shared` targets it merges `listSecrets(<butler>)` with `listSecrets("shared")`, preserving local rows on key collisions and marking shared-only rows as `source="shared"` so UI status badges show inherited shared values instead of `Missing (null)`.
- `frontend/src/pages/SecretsPage.tsx` no longer includes a dedicated "Configure App Credentials" form card; Google app credentials are managed through generic secrets rows (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`) and the OAuth section focuses on status/connect/delete actions.
- `frontend/src/hooks/use-secrets-inventory.ts` must derive user provider slugs from the backend provider catalog plus aliases, not from the first underscore segment of `entity_info.type`; live rows include `home_assistant_token` -> `homeassistant` and `telegram_user_session` -> `telegram_bot`.
- `src/butlers/api/routers/oauth.py::_get_scopes()` uses the fixed `_DEFAULT_SCOPES` set for `/api/oauth/google/start`; `GOOGLE_OAUTH_SCOPES` is no longer a runtime override input.
- Fixed OAuth scopes now include People-related scopes in addition to Gmail/Calendar: `contacts`, `contacts.readonly`, `contacts.other.readonly`, and `directory.readonly`.
- Runtime authentication is handled by CLI-level OAuth tokens (device-code flow via the dashboard `/settings` page), not API keys. The spawner only includes `PATH` plus declared `[butler.env]` vars and module credential vars in the runtime environment.

### One-DB multi-schema migration planning contract
- `docs/operations/one-db-multi-schema-migration.md` is the authoritative plan for epic `butlers-1003`: target topology (cross-butler tables in `public` + per-butler schemas), role/ACL model, phased cutover + rollback, parity/isolation gates, and child-issue decomposition.
- `docs/architecture/system-architecture.md` remains current-state for deployed topology and includes a transition note linking to the migration plan.
- `docs/operations/one-db-data-migration-runbook.md` is the executable command/checklist reference for staging dry-runs, parity signoff, and rollback validation.
- `docs/operations/migration-rewrite-reset-runbook.md` is the step-by-step operator procedure for local/dev/staging destructive reset rehearsal, including safety prechecks and required SQL validation evidence.

### Telegram connector DB-first startup contract
- `run_telegram_bot_connector()` and `run_telegram_user_client_connector()` must not hard-fail on missing credential env vars when DB credentials are available; if `from_env()` fails only due missing creds and DB lookup succeeded, build config from required non-credential env vars plus DB-resolved secrets.
- Endpoint identity is auto-resolved at startup via API calls (not env vars). Only `SWITCHBOARD_MCP_URL` is required as a non-credential env var.
- Regression coverage lives in `tests/connectors/test_telegram_bot_connector.py::test_run_telegram_bot_connector_uses_db_token_when_env_missing` and `tests/connectors/test_telegram_user_client.py::test_run_telegram_user_client_connector_uses_db_credentials_when_env_missing`.

### OAuth/dev messaging DB-first contract
- User-facing OAuth guidance (dev bootstrap, startup guards, OAuth callback responses) should default to dashboard + shared `butler_secrets` persistence and avoid recommending `GMAIL_*`/manual env fallback as the normal path.
- `docs/runbooks/connector_operations.md` should not advertise removed `GMAIL_*` aliases; troubleshooting should direct operators to rerun OAuth/bootstrap so credentials persist in DB.

### Legacy-compat cleanup hotspots (dev runtime)
- Runtime source currently does not read `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` or deprecated `GMAIL_*` credential aliases directly; these names mainly remain in `dev.sh`, docs, and tests.
- Active compatibility hotspots to evaluate first for removal: `dev.sh::_has_google_creds`, `src/butlers/modules/calendar.py::_resolve_credentials` fallback path, `src/butlers/google_credentials.py` legacy asyncpg/table helpers, `roster/switchboard/tools/notification/deliver.py` legacy positional-arg shim, and `src/butlers/api/routers/butlers.py` legacy module-status list parsing.

### Gmail connector shared-schema credential lookup contract
- `src/butlers/connectors/gmail.py::_resolve_gmail_credentials_from_db` must perform layered DB-first lookup across local (`CONNECTOR_BUTLER_DB_NAME` + optional `CONNECTOR_BUTLER_DB_SCHEMA`) and shared (`BUTLER_SHARED_DB_NAME` + `BUTLER_SHARED_DB_SCHEMA`, default `public`) contexts.
- Each lookup pool must apply schema-scoped `server_settings={"search_path": ...}` (via `schema_search_path`) so `butler_secrets` resolves correctly in one-db/shared-schema topologies; otherwise DB-only startup cannot resolve credentials and will fail.
- Regression coverage lives in `tests/test_gmail_connector.py::TestResolveGmailCredentialsFromDb::test_uses_shared_schema_fallback_with_schema_scoped_search_path`.

### Gmail connector DB-first startup contract
- `src/butlers/connectors/gmail.py::run_gmail_connector` is DB-only for Google OAuth credentials: it must require credentials from `butler_secrets` and must not fall back to credential env vars.
- `GmailConnectorConfig.from_env(...)` accepts DB-injected credentials as explicit args and reads only non-secret runtime env config.
- Regression coverage lives in `tests/test_gmail_connector.py::TestRunGmailConnectorStartup`.

### Gmail connector error-detail logging contract
- `src/butlers/connectors/gmail.py::_format_google_error` is the canonical parser for Google API/OAuth JSON error payloads; keep logs compact (`code/status/reason/message` or `error/error_description`) and avoid dumping full payloads.
- `GmailConnectorRuntime._fetch_history_changes()` must log parsed Google details for `history.list` 404 cursor resets and for other non-2xx `history.list` responses before `raise_for_status()`.
- `GmailConnectorRuntime._get_access_token()` must log parsed OAuth error details on non-2xx token refresh responses (for example `invalid_grant`) before raising.

### Butler runtime/model pinning contract
- Runtime adapter selection is read from top-level `[runtime].type` in each `roster/*/butler.toml` (defaults to `"claude"` when omitted).
- Runtime model selection is read from `[butler.runtime].model` (defaults to `src/butlers/config.py::DEFAULT_MODEL` when omitted).
- Codex runtime system instructions are loaded from per-butler `AGENTS.md` (via `src/butlers/core/runtimes/codex.py::parse_system_prompt_file`), not `CLAUDE.md`.
- `CodexAdapter.invoke()` must call `codex exec --json --full-auto` (non-interactive mode). Top-level `codex --full-auto` requires a TTY and should not be used by the spawner subprocess path.
- Codex CLI no longer supports `--instructions`; butler/system prompt content must be embedded into the `exec` initial prompt payload, and MCP endpoints should be passed via `-c mcp_servers.<name>.url="..."`.
- `CodexAdapter.invoke()` must insert a `--` option delimiter before the positional prompt argument so user prompts beginning with `-`/`--` are not parsed as Codex CLI flags.
- `CodexAdapter.invoke()` must forward configured model via CLI `--model <id>` when `model` is non-empty, so roster model pins (for example `gpt-5.3-codex-spark`) are actually enforced at launch time.
- Codex spawn failure now drives `last_test_ok` on the `cli-auth/codex` credential row. When the Codex CLI exits non-zero with a refresh-token-reuse error (`_looks_like_auth_refresh_failure`), `CodexAdapter._schedule_record_test_result` writes `last_test_ok=false` → `_derive_state` returns `'failing'` and the secrets passport banner turns red. A successful spawn always clears the field (`last_test_ok=true`) even without a token rotation, so the banner self-heals after re-auth.

### Butler MCP debug surface contract
- Butler detail now includes an always-available `MCP` tab (`frontend/src/pages/ButlerDetailPage.tsx`) for per-butler debug tool calls.
- Dashboard API exposes per-butler MCP debug endpoints in `src/butlers/api/routers/butlers.py`: `GET /api/butlers/{name}/mcp/tools` (normalized `name`/`description`/`input_schema`) and `POST /api/butlers/{name}/mcp/call` (tool name + arguments passthrough with parsed `result`, `raw_text`, `is_error`).
- Frontend contracts are typed in `frontend/src/api/types.ts` (`ButlerMcpTool`, `ButlerMcpToolCallRequest`, `ButlerMcpToolCallResponse`) and wired through `frontend/src/api/client.ts` + `frontend/src/api/index.ts`.

### Runtime MCP transport rollout contract
- Butler daemons now expose dual MCP transports via `_build_mcp_http_app()` in `src/butlers/daemon.py`: streamable HTTP at `/mcp` and legacy SSE compatibility at `/sse` + `/messages`.
- Spawner runtime sessions use canonical streamable MCP URLs from `src/butlers/core/mcp_urls.py::runtime_mcp_url()` (`http://localhost:<port>/mcp`) and `src/butlers/core/spawner.py` should not regress to hardcoded `/sse`.
- `src/butlers/core/runtimes/claude_code.py` resolves transport with `resolve_runtime_mcp_transport()`: default `http` for `/mcp`, explicit/URL-inferred `sse` for legacy endpoints.
- Connector ingest clients are still SSE-based (`SWITCHBOARD_MCP_URL=.../sse`) and are intentionally out of scope for spawner runtime transport cutover.
- Operator cutover/fallback procedure is documented in `docs/operations/spawner-streamable-http-rollout.md`; keep this runbook aligned with transport behavior and rollback guidance.

### Butler runtime concurrency baseline
- All current roster butlers (`switchboard`, `general`, `relationship`, `health`, `messenger`) should explicitly set `[butler.runtime].max_concurrent_sessions = 3` in their `roster/*/butler.toml` to avoid unintended fallback to the serial default (`1`) for scheduled/tool-trigger workloads.

### CRM backfill pipeline (contacts module) patterns
- `src/butlers/modules/contacts/backfill.py` implements the apply_contact callback wired into `ContactsSyncEngine` at startup. Three-class design: `ContactBackfillResolver` (identity matching pipeline), `ContactBackfillWriter` (table mapping/upsert), `ContactBackfillEngine` (orchestrates resolver→writer→activity feed).
- Identity resolution order: source_link > email > phone > name (single match) > ambiguous_name (skip auto-merge).
- Conflict policy: provenance tracked in `contacts.metadata` JSONB under `sources.contacts.{provider}.{field}`. Source wins only if field is provenance-owned; locally-edited fields (no provenance) are preserved.
- `ON CONFLICT DO NOTHING` without a conflict target is valid PostgreSQL syntax. The production CRM tables (`contact_info`, `addresses`, `important_dates`) lack composite unique constraints — adding those would require separate schema migration beads.
- `upsert_source_link` accepts `local_id: uuid.UUID | None`; returns early without creating a link when `local_id is None` (tombstone with no known local contact).
- Activity feed event types: `contact_synced`, `contact_sync_updated`, `contact_sync_conflict`, `contact_sync_deleted_source`.
- Tests use `pytestmark = pytest.mark.integration` with `provisioned_postgres_pool` fixture and create all CRM tables inline in the `crm_pool` fixture.

### Contacts migration cross-schema FK contract
- `src/butlers/modules/contacts/migrations/001_contacts_sync_tables.py` must create `contacts_source_links.local_contact_id` without an inline FK and add `contacts_source_links_local_contact_id_fkey` only when `contacts` exists in the current schema (`to_regclass(format('%I.contacts', current_schema()))`).
- This guard keeps module migration `contacts_001` safe for schemas that enable contacts but do not own CRM `contacts` (for example `general` and `health`).
- `tests/config/test_schema_matrix_migrations.py` `CHAIN_TABLES` must include `contacts` module tables so one-db schema-matrix runs exercise contacts migrations across all enabled schemas.

### Calendar workspace projection baseline contract
- Core migration `core_005` adds app-native calendar projection tables in each migrated schema: `calendar_sources`, `calendar_events`, `calendar_event_instances`, `calendar_sync_cursors`, and `calendar_action_log`.
- Range-window queries are supported by GiST indexes on `tstzrange(starts_at, ends_at, '[)')` for both events and instances; source lookups use `(source_id, starts_at)` indexes.
- Deterministic source linkage/idempotency guarantees are enforced by `UNIQUE (source_id, origin_ref)` on `calendar_events`, `UNIQUE (event_id, origin_instance_ref)` on `calendar_event_instances`, and `UNIQUE (idempotency_key)` on `calendar_action_log`.
- Scheduler calendar-linkage migration is linearized as `core_006` (`down_revision="core_005"`), so `tests/config/test_migrations.py::CORE_HEAD_REVISION` should track `core_006`.
- `tests/config/test_schema_matrix_migrations.py::CORE_TABLES` must include the calendar projection tables.

### Calendar workspace API contract
- Dashboard API now exposes `/api/calendar/workspace` (range query), `/api/calendar/workspace/meta` (capabilities + connected sources + writable calendars + lane definitions), and `/api/calendar/workspace/sync` (global or source-targeted sync trigger).
- `POST /api/calendar/workspace/sync` delegates to each target butler MCP `calendar_force_sync`; source-targeted provider rows pass `{"calendar_id": <calendar_id>}`, while internal-source rows call with `{}`.
- Workspace read payload shape is `ApiResponse[CalendarWorkspaceReadResponse]` with `data.entries` (normalized `UnifiedCalendarEntry[]`), `data.source_freshness`, and `data.lanes`.

### Calendar workspace mutation contract
- Dashboard workspace mutation routes live in `src/butlers/api/routers/calendar_workspace.py`: `POST /api/calendar/workspace/user-events` (`create|update|delete`) and `POST /api/calendar/workspace/butler-events` (`create|update|delete|toggle`), with request envelope `{butler_name, action, request_id?, payload}`.
- Mutation routes proxy to MCP tools and must return projection freshness metadata (`projection_version`, `staleness_ms`, `projection_freshness`) by using tool-returned freshness or falling back to `calendar_sync_status`.
- Calendar module mutation idempotency uses `calendar_action_log.idempotency_key` keyed by action + `request_id`; repeat requests should replay stored applied/noop results instead of re-executing side effects.
- Butler-event MCP tools are `calendar_create_butler_event`, `calendar_update_butler_event`, `calendar_delete_butler_event`, and `calendar_toggle_butler_event`; high-impact delete/toggle operations integrate with approval enqueueing and set `_approval_bypass=True` on queued replays.

### knip runs before build and test — do not write code to appease it
- The frontend gate order is `lint, lint:emdash, lint:query-coercion, knip, build, test`. Because
  knip gates *before* the tests, an "unused export" complaint arrives with no test pressure behind
  it, and the cheapest way to silence it is to import the export somewhere it is not needed. That is
  dead logic written to satisfy a linter, and it ships untested.
- Check `ignoreExportsUsedInFile: true` before reacting: an export consumed only inside its own
  module is already exempt, so the complaint is often about a *different* symbol than the one you
  are looking at. Delete the unused export, or leave it and fix the real consumer — never manufacture
  a cross-file import (and the branch that comes with it) to turn the gate green.

### The core `trigger` MCP tool is synchronous and returns session evidence
- `trigger` **awaits the spawned session to completion** and returns
  `{output, success, error, duration_ms, session_id}`. Neither the name nor the call site suggests
  this. It is what makes server-owned outcome settlement possible without inventing a new MCP tool:
  a caller can persist a terminal status and a `session_id` from the return value alone, instead of
  polling or wiring a callback.

### Frontend dialog test contract
- Radix `Dialog` content renders through a portal (`document.body`), so jsdom tests for dialog controls should query `document` (not only the mounted container) and use the native input value setter + `input` event dispatch for controlled text inputs.

### Telegram connector rate-limit polling contract
- `src/butlers/connectors/telegram_bot.py::_get_updates` must treat Telegram `HTTP 429` as recoverable for polling: record `rate_limited` source API status/error metrics, log a warning, honor `Retry-After` (header first, then `result.parameters.retry_after`), and return `[]` instead of raising.

### Switchboard route_to_butler lineage fallback contract
- `route_to_butler` can run in a different MCP/ASGI task than `MessagePipeline.process()`, so `_routing_ctx_var` may be empty at tool-call time; switchboard now falls back to runtime-session-bound routing lineage.
- `Spawner._run()` captures pipeline routing context and stores it keyed by `runtime_session_id` for the lifetime of the runtime session; `route_to_butler` reads it via `get_current_runtime_session_routing_context()` and restores `source_channel`, `source_sender_identity`, and `source_thread_identity` when task-local context is missing.

### Tool input-shape metadata contract
- `memory_store_fact.tags` and `memory_search.types` metadata must explicitly describe list-only JSON input shapes (not plain strings), with concrete valid/invalid examples (`tags=["x"]`, `types=["fact"]`, invalid `types="facts"`).
- `memory_search.types` should be modeled as `list[Literal["episode","fact","rule"]] | None` and `memory_search.mode` as `Literal["hybrid","semantic","keyword"]` so MCP schemas expose enforceable enums.
- `notify.request_context` metadata must explicitly say it requires an object/dict value (not JSON strings or quoted placeholders), because placeholder examples in skills/docs can cause repeated runtime validation failures.

### Switchboard message-triage delegation contract
- `src/butlers/modules/pipeline.py::_build_routing_prompt` should keep the ingestion preamble minimal and explicitly instruct: `Please use the /message-triage skill ...`.
- Routing/safety behavior details (untrusted-input handling, `<user_message>` wrapping, fallback to `general`, and mandatory `route_to_butler` call) are maintained in `roster/switchboard/.agents/skills/message-triage/SKILL.md` under `Execution Contract`.

### Skill-first routed-content contract
- Route-processing context assembly in `src/butlers/daemon.py` is centralized in `_build_route_runtime_context()` and should reference skills (`/routed-message-safety`, `/butler-notifications`) instead of duplicating long inline safety/notify preambles in both hot-path and recovery flows.
- Shared skill `roster/shared/skills/routed-message-safety/SKILL.md` must be symlinked into each `roster/*/.agents/skills/` so any routed target butler can follow the same fenced-content handling contract.

### Refinery patrol hook activation behavior
- In this rig state, `gt hook`/`gt mol status` can show a hooked `mol-refinery-patrol` bead while also reporting `No molecule attached`; `gt mol attach` rejects hooked wisps because it requires a pinned bead.
- `gt patrol new` creates and hooks a fresh patrol wisp, but the hook output may still report `No molecule attached`; use `gt mq list <rig>` as operational queue truth and continue processing merge-ready MRs.
- Current refinery patrol wisps are root-only molecules: `bd mol show <wisp-id>` reports `Steps: 1` with just the patrol root, and `bd mol current <wisp-id>` shows `0/0 steps complete`; do not block on missing child-step beads before running the patrol loop.
- If `gt prime`/`gt mail check --inject` hang in this rig, check `gt dolt status` first; when the Dolt server is down, `gt` can wedge in auto-start retries, and an explicit `gt dolt start` restores normal command responsiveness.

### Health owner entity resolution contract
- Post-`core_016`, owner-role resolution must not query `public.contacts.roles`; that column is gone. Health meal logging and other owner lookups should resolve the owner via `public.entities.roles` (or the shared owner-entity helper path) and degrade gracefully when no owner entity exists.

### Notifications API startup degradation contract
- `src/butlers/api/routers/notifications.py` must treat a missing switchboard `notifications` table the same as an unavailable switchboard pool: `GET /api/notifications` and `GET /api/butlers/{name}/notifications` return an empty paginated payload, and `GET /api/notifications/stats` returns zeroed stats instead of bubbling a 500 before switchboard migrations have run.

### Group-size discretion bypass: participant_count-alone is not a safe gate
- `DiscretionEvaluator.evaluate()` (`src/butlers/connectors/discretion.py`) supports a `group_size_bypass_max`/`participant_count`/`chat_type` bypass so small groups skip LLM filtering. Any bypass keyed on `participant_count` must also gate on `chat_type in {"group", "supergroup"}` (an allow-list) — DMs conventionally report `participant_count=2` (RFC 0013 Dunbar-eligibility bookkeeping) and Telegram genuinely resolves participant counts for broadcast `"channel"` chats too, so a plain `participant_count <= threshold` check silently bypasses discretion for every DM and small channel. A `!= "private"` deny-list is not enough — use the allow-list.
- `whatsapp-bridge`'s Go event handler dispatches whatsmeow events serially on one goroutine — never make a blocking network call (e.g. `client.GetGroupInfo`) synchronously inside a message handler; it stalls delivery of every subsequent WhatsApp event, not just the affected chat. `internal/events.GroupInfoCache` is the pattern: return the best cached value immediately, refresh in a background goroutine (deduplicated per JID), negative-cache failures briefly. `whatsapp-bridge/` has no Go CI job (no build/vet/test/gofmt check) — verify Go changes locally (`go build && go vet && go test -race && gofmt -l .`) since CI won't. See PRs #3697, #3701.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->

## Notes to self

### Module migration schema overrides
CLI migration entrypoints (`butlers db migrate`, including compose's `migrations`
service) must route each module chain through the same schema override logic as
daemon startup. For memory modules, honor `[modules.memory].memory_schema` (for
example chronicler's `chronicler_mem`) instead of blindly using the owning
butler schema, or memory-chain migrations can hit domain tables with incompatible
columns.

### Google Health API (health.googleapis.com/v4) token + scope contract
Two non-obvious rules, both verified live 2026-07-08 (root cause of the "granted health but probe says 403 scope-not-granted" secrets-page bug):
1. **The Health API rejects full-scope access tokens** with `403 DISALLOWED_OAUTH_SCOPES` — any access token carrying non-health scopes (the unified refresh token covers calendar/gmail/drive/contacts/…) fails EVERY v4 call regardless of granted scopes. Callers must mint a health-only token by passing `scope=<the three googlehealth .readonly URLs>` in the refresh-token exchange (the connector does this in `google_health.py`; the secrets probe does it in `secrets_v2.py::_mint_health_access_token`). A 403 from v4 therefore does NOT mean "scope not granted" unless the token was down-scoped first.
2. **Google reports scope VARIANTS**: token/callback `scope` fields may list the broader non-`.readonly` URL (e.g. `googlehealth.sleep`) for an account already holding the wider grant. All granted-scope checks must match by FAMILY (`google_account_registry.py::google_health_scope_family` / frontend `client.ts::googleHealthScopeFamily`), never by exact URL — exact matching reads a fully-granted account as unscoped (that made tzeuse@'s revoke a silent no-op and showed "grant health" on a granted account). Requesting scopes still uses the `.readonly` URLs.
Related plumbing facts: `public.entity_info` `google_oauth_refresh` values are PLAINTEXT (`secured=true` is a marker, not encryption); app creds live in `public.butler_secrets` under `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`.

### Egress audit operation naming convention
Dashboard egress catalog (`GET /api/system/egress`) is powered by `switchboard.dashboard_audit_log`. Four operation strings represent outbound external calls — each must be emitted at the actual call site using `write_audit_entry` (daemon) or `emit_dashboard_audit` (API layer):
- `llm_api_call` — emitted by `src/butlers/core/spawner.py` after every LLM session (success and error paths); `request_summary` carries `provider`, `model`, `session_id`, `input_tokens`, `output_tokens`.
- `telegram_send` — emitted by `TelegramModule._send_message()` in `src/butlers/modules/telegram.py`; `request_summary` carries `chat_id`, `text_length`.
- `google_calendar_write` — emitted by `CalendarModule._emit_calendar_audit()` (called from `calendar_create_event`, `calendar_update_event`, `calendar_delete_event` tools in `src/butlers/modules/calendar.py`); `request_summary` carries `action` (create/update/delete), `event_title`, `calendar_id`.
- `gmail_send` — emitted by `EmailModule._send_email()` in `src/butlers/modules/email.py`; `request_summary` carries `to`, `subject`.

Modules receive the audit pool via `Module.wire_audit_pool(pool)` — a post-startup hook called in `lifecycle.py` step 10a-ii after the audit pool is created. The base class default is a no-op; only modules that emit egress audit entries need to override it.

- `about/craft-and-care/` is the canonical fifth project-shape pillar for repository engineering standards; keep testing, verification, review, observability, interface/dependency, security, and performance guidance there instead of scattering new standards across ad hoc docs.
- Memory entity merge tombstones the source with `metadata.merged_into`, excludes it from entity list/search/`entity_resolve`, and re-links `public.contacts.entity_id` from source to target.
- Relationship entity active-surface queries should use the shared archived/tombstone/deleted filter: exclude `metadata.archived = true`, legacy `metadata.archived_at`, `metadata.tombstone = true`, `metadata.deleted_at`, and `metadata.merged_into`.
- Memory entity merge only unions the source entity's `aliases` onto the target; it does not automatically add the source `canonical_name` as a target alias, so old-name lookups only keep working if that string already exists in aliases or another resolver path still matches.
- Rule-promotion verdict identity is an email sender for `email` and the exact connector endpoint for opaque channels. New opaque promotions use `source_endpoint` with `{"endpoint_identity": "..."}`; provenance-linked legacy opaque `sender_address` rows are evaluated compatibly without rewriting their audit data, and the newest same-priority legacy confirmation wins.
- Entity-dedup curation treats a matching ordered pair with a `pending`, `approved`, `rejected`, or `abandoned` `memory_entity_merge`/legacy `entity_merge` row as existing lifecycle state. Retention exempts rejected/abandoned ordered merge decisions (including legacy null-key rows), so only expired actions may resurface; curation must never retry or mutate an approved or abandoned historical merge.
- Witness patrol wisps created by `gt patrol new` / `gt patrol report` are `hooked` (not `pinned`), so `gt mol attach <wisp> mol-witness-patrol` fails with "not pinned". Run patrol steps directly and roll cycles with `gt patrol report`.
- `gt patrol report --steps` for witness cycles expects the canonical patrol step keys (`inbox-check`, `process-cleanups`, `check-refinery`, `survey-workers`, `check-timer-gates`, `check-swarm-completion`, `patrol-cleanup`, `context-check`, `loop-or-exit`); custom labels are recorded as `SKIP`.
- `gt hook --json` may expose the current hooked wisp under `pinned_bead`; rely on each issue `status` field (`hooked` vs `pinned`) for truth.
- `gt polecat list` can temporarily show stale `working` state after Dolt disturbances; verify with `gt hook show <agent> --json` and `bd show <issue> --json` before taking cleanup action.
- `gt polecat stale` / `gt polecat check-recovery` can lag just-recovered sessions; before intervening, confirm live session truth with `gt polecat status <rig>/<name> --json`.
- Witness loop step command to resolve `role_type: witness` agent bead can return zero results in this rig; if no witness agent bead exists, skip `gt mol step await-signal` and continue patrol roll with `gt patrol report` while flagging the missing bead.
- For polecat hook inspection, use full agent paths like `gt hook show butlers/polecats/<name> --json`; shorthand `butlers/<name>` may incorrectly report `status":"empty"`.
- `gt patrol report` can rotate the hooked patrol wisp without updating `/home/tze/gt/butlers/witness/state.json`; treat the hooked patrol bead and polecat agent beads as the source of truth for current-cycle state.
- Finance transaction ingestion is split: `POST /api/finance/transactions/bulk` writes facts directly via `roster/finance/tools/facts.py::bulk_record_transactions`, while the finance module/MCP `bulk_record_transactions` routes through `roster/finance/tools/transactions.py::record_transaction` and then mirrors to facts. Their dedupe semantics differ; the direct facts bulk path still dedupes `source_message_id` per predicate and hashes signed amounts, so opposite-sign imports of the same event can persist as both `transaction_debit` and `transaction_credit`.
- Finance transaction retries can leave `finance.transactions` soft-deleted duplicates while their mirrored `finance.facts` rows remain `validity='active'`; cleanup/reconciliation should retract active transaction facts that exactly match a deleted ledger row on `merchant`, `amount`, `currency`, `posted_at`, and `direction`.
- `GET /api/memory/entities/{entity_id}` now accepts `facts_offset` / `facts_limit`; the response includes `recent_facts_total`, `recent_facts_offset`, `recent_facts_limit`, `recent_facts_has_more`, and each fact row may carry `session_id` resolved via its `source_episode_id`.
- `frontend/src/components/settings/QASettingsCard.tsx` must avoid mirroring `useQaRepoConfig()` data into local state via `useEffect`; the current frontend lint config treats `react-hooks/set-state-in-effect` as a hard error, so the repo URL field should stay query-backed until a local draft exists.
- In the current frontend toolchain, Recharts `Tooltip` formatter callbacks should accept `value: string | number | undefined`; narrower signatures can fail `npm run build` under TypeScript even when the plotted data is numeric.
- `src/butlers/core/runtimes/codex.py` should stage isolated per-invocation `HOME` roots under `~/.codex/.tmp` when a real home directory exists; current `codex-cli` warns and can fail when `codex_home` is placed under `/tmp`.
- `src/butlers/core/qa/dispatch.py::_create_qa_pr` also depends on the GitHub CLI after a successful agent session; `Dockerfile.base` must continue to ship `gh` alongside `git` and `uv` or QA investigations can finish cleanly but fail before raising a PR with `FileNotFoundError: 'gh'`.
- This repo's local beads database currently has no Dolt remote named `origin`; `bd dolt push` fails with `remote 'origin' not found`, but local `bd create/update/dep add` writes still persist in `.beads/` via Dolt.
- The `butlers` Dolt database lives on the shared GT/Gastown Dolt server at `dolt.parrot-hen.ts.net:3307`. The literal hostname `gastown` may not resolve from agent shells; use the verified remote endpoint in `.beads/config.yaml`/metadata (`dolt.host: dolt.parrot-hen.ts.net`, `dolt.port: 3307`) and verify with `bd dolt show`. If `bd` reports `database "butlers" not found`, it is pointed at an auto-started local Dolt; fix by killing the PID in `.beads/dolt-server.pid` and writing `3307` to `.beads/dolt-server.port`. Do NOT run `bd doctor --fix --yes` for this — it overwrites the port file with a fresh local server and starts from an empty DB.
- `src/butlers/modules/qa/__init__.py::_handle_report_finding` currently trusts caller-supplied `fingerprint` and `severity`; QA dedup and dispatch autonomy depend on canonicalizing or validating those fields at the QA boundary rather than treating report payloads as authoritative.
- `src/butlers/core/qa/sources/log_scanner.py` currently spends `max_entries_per_scan` budget before `_should_include_entry(...)` filtering and scans oldest-first across deterministically ordered files, so noisy benign logs can starve real error discovery unless the scanner budget/traversal logic is hardened.
- QA investigation PR pushes cannot rely on an SSH `origin` inside the sandbox: use `GH_TOKEN` with `gh auth setup-git` and push over `https://github.com/<owner>/<repo>.git` so PR creation/follow-up works without SSH agent state.
- QA PR review follow-up retry timing is tracked separately from review polling: `healing_attempts.last_follow_up_at` plus `follow_up_count` implement exponential backoff; `last_review_check_at` remains the review-scan timestamp only.
- Shared worktree creation now supports an explicit `base_ref`; QA investigations should fetch `origin/main` first and branch from `origin/main` rather than assuming local `main` is current.
- Live finance schemas at `finance_006` use `merchant_mappings.raw_pattern`, `normalized_merchant`, `learned_from_count`, and `source`; finance runtime code must not query or upsert legacy `merchant`, `merchant_pattern`, or `sample_count` columns or uncategorized transaction ingestion will fail with `UndefinedColumnError`.
- `src/butlers/connectors/spotify.py` must prefer resolved human-readable `context_name` values for playlist/album/artist contexts in both `normalized_text` and `payload.raw.context_name`; raw URI suffixes are fallback-only because downstream entity extraction will otherwise store Spotify IDs as canonical names.
- `tests/config/test_migrations.py` should derive the current core Alembic head from `alembic/versions/core/` instead of pinning a `CORE_HEAD_REVISION` constant; new core migrations land often enough that static head expectations rot immediately.
- Fresh `bd worktree create` checkouts ship without `node_modules`; frontend workers must run `cd frontend && npm install` before `tsc`/`eslint`/`vitest` or commands fail with module-resolution errors.
- `npx tsc --noEmit -p .` does not traverse project references — use `npx tsc -b` (matches `npm run build` in CI) to surface TS errors in test files; many errors are invisible to the `-p` form and only fail in CI.
- Branch protection may classify `frontend` as non-blocking, but that is insufficient for final merge: `frontend`, `frontend-e2e`, and `check` must all be terminal green before the exact-base helper is invoked. Do not use automatic merge to bypass this gate.
- Frontend test files mocking `localStorage.getItem` should type the backing store as `Record<string, string | null>` (not `Record<string, string>`); otherwise `vi.fn` infers the mock signature too narrowly and `mockImplementation((k) => k === "..." ? "x" : null)` fails `tsc -b`.
- Partial TanStack Query mocks in test files must use `as unknown as ReturnType<typeof useFoo>` (existing convention in `ConnectorDetailPage.test.tsx`, `SecretsTable.test.tsx`); `UseQueryResult<T, Error>` is a discriminated union that requires fields like `isPending`, `isLoadingError` that mocks omit, so a direct cast fails.
- `.claude/skills/butler-qa-pr-review/scripts/_github.py::fetch_required_checks` currently treats `gh pr checks --required` exit code 1 as fatal when a PR has no branch-protection required checks configured, so `validate_pr_review.py` can crash even though `statusCheckRollup` still exposes actual CI results.
- `src/butlers/connectors/whatsapp_user_client.py::_get_bridge_db_dsn()` currently hands the Go bridge a bare `butlers` DSN with no schema search_path; in live dev that means the real linked-device state restores from `public.whatsmeow_*` tables while `messenger.whatsapp_sessions` stays empty, so bridge session bookkeeping and whatsmeow device restore can silently diverge.
- WhatsApp bridge protocol freshness is owned by the pinned `go.mau.fi/whatsmeow` module: a live `Client outdated (405)` loop calls for updating `whatsapp-bridge/go.mod` and its resolved transitive modules, then verifying the real connector connects without a startup timeout or restart; it is not a QR re-pair condition.
- `curriculum/` is the generated prerequisite-learning surface for this repo; keep it concept-first under the training-curriculum contract, with `curriculum/research-ledger.md` preserving the required 3-pass discovery audit.
- Dunbar decay scoring treats connector-provenance LLM extraction facts as mention/context facts unless `extra_metadata.source == "interaction_sync"`; `email`, `interview`, and `calendar_event` interactions are intentionally downweighted to `0.2` so one-off transactional work/onboarding exchanges do not masquerade as inner-circle relationships.
- Relationship interaction facts are canonically stored under `subject='entity:{entity_id}'`; low-level `interaction_log`/`interaction_list` still accept legacy contact UUIDs and resolve them to `contacts.entity_id` before writing/querying.
- Memory entity Dunbar enrichment must aggregate duplicate contacts linked to the same `entity_id` by keeping the highest `dunbar_score`; otherwise a zero-score duplicate contact can overwrite the real scored contact and make `/entities` show tier 1500 while `/relationship/dunbar/ranking` shows an inner tier.
- Draft RFC 0014 proposes Chronicler as the retrospective Time Butler; until accepted/synced, treat its future-source compatibility note as proposed guidance, not an enforced rule.
- Google Health OAuth scopes live per-account in `public.google_accounts.granted_scopes`; the static `GOOGLE_OAUTH_SCOPES` app secret can lag. A connector state of `degraded` with `error_message='account_not_linked'` means OAuth scopes are granted but the Google/Fitbit Health account has not been linked, so no wellness ingestion will occur yet.
- Google account-level reauthorization links must pass an explicit all-optional `scope_set` (`gmail,calendar,contacts,drive,health`; `base` is implicit) when preserving restricted/optional scopes; omitting it triggers the backend default (`base+gmail+calendar+contacts+drive`) which intentionally excludes Google Health and can overwrite `granted_scopes` without `googlehealth.*`.
- Chronicler projection adapters whose evidence is mirrored across multiple butler schemas (e.g. `CalendarCompletedAdapter`) MUST derive `source_ref` from the upstream identifier (`calendar:{origin_instance_ref}`), not from the per-schema row id, so the persistent `(source_name, source_ref)` upsert dedups the cross-schema fan-out automatically. The in-run `seen_origin` set is just an efficiency hedge.
- Chronicler `health.steps` and `health.heart_rate` are point-event-only sources: register them in source contracts and schedules, but do not add D1 lane-taxonomy mappings unless they start emitting episodes.
- `Spawner.trigger(...)` propagates `ingestion_event_id` end-to-end to `session_create`; routing handlers in `src/butlers/core_tools/_routing.py` must pass `ingestion_event_id=route_request_id` because the switchboard ingest writes the same UUID7 as both `request_id` and `public.ingestion_events.id`. Without it, every route session stores `ingestion_event_id=NULL` and chronicler titles fall through to `Conversation via unknown channel`.
- Ingestion redesign closure must include live visual parity evidence, not just spec/task completion: compare `/ingestion` routes against the design reference (the Dispatch spec `openspec/specs/dashboard-design-language/spec.md` + `docs/redesigns/ingestion-handoff.md`; the prototype bundle graduated out of `pr/`), verify the old `Ingestion Events` card/table and page-level tab shell are absent, and keep screenshot/report artifacts before archiving.
- Telegram user-client conversation-history batches must preserve `sender.participants` / `owner_sender_id` through `message_inbox.raw_payload`; durable-buffer routing must derive the non-owner sender and pass `source_id` so identity resolution can anchor downstream memory facts to the contact instead of the owner.
- `frontend/src/components/chronicles/MapWidgetInner.tsx` must guard `map.addSource(...)` / `map.addLayer(...)` behind `map.isStyleLoaded()` (or `map.once('load', ...)`); calling them synchronously after `new maplibreGl.Map(...)` throws `Style is not done loading` and renders the user-visible `Failed to load the map. Try again` fallback even when valid trail data exists.
- Frontend links under the `/butlers-dev` mount must respect the routing surface: React Router `Link to` values should stay app-internal (for example `/butlers/lifestyle`) because `createBrowserRouter(..., { basename })` prefixes them; raw `<a href>` targets still need an explicit `import.meta.env.BASE_URL` prefix if they must leave React Router navigation.
- Dev compose runs against an external Postgres capped at `max_connections=200`; keep `BUTLERS_DB_POOL_*` and `BUTLERS_API_DB_POOL_*` defaults conservative in `docker-compose.yml` or late-starting butlers such as `travel` can fail after other services consume regular connection slots.
- Core Alembic migration revisions must stay globally unique and linear; `tests/config/test_migration_contract.py` now asserts duplicate `revision` IDs fail before compose migrations do.
- Alembic migrations that rewrite enum-like `TEXT` values guarded by `CHECK` constraints must drop/replace the old constraint before writing new values; otherwise live upgrades fail even if fresh-schema tests pass.
- The relationship butler has `[modules.memory]`, so `relationship.facts` and `relationship.predicate_registry` are memory-module bare tables in that schema. New relationship-domain triple-store work must use `relationship.entity_facts` plus `relationship.entity_predicate_registry`, and keep tests proving migrations tolerate existing memory-shaped tables.
- Entity-redesign routing lives in `frontend/src/router-config.tsx`, not `frontend/src/router.tsx`: `/entities`, `/entities/hop`, `/entities/columns`, `/entities/concentration`, `/entities/social-map`, and `/contacts -> /entities?has=contact` are mounted there. `router.tsx` only owns redirect helper components. Cmd/Ctrl+K opens the shipped `EntityFinder`; the legacy `CommandPalette` remains mounted but uses a different event.

### Backup strategy (bu-t102m)
- Strategy chosen: filesystem pg_dump cron. Simplest defensible approach for an owner-sovereign, single-instance system. No new external dependencies (no Minio/S3, no WAL archiving).
- Implementation: `deploy/backup/pg_dump.sh` runs `pg_dump | gzip` to a timestamped `.sql.gz` file in `$BACKUP_DIR` (default `/backups`). Prunes files older than `$BACKUP_RETAIN_DAYS` (default 14 days).
- Docker integration: `backup-cron` sidecar (postgres:17-alpine, crond) in `docker-compose.yml` writes to the `butlers_backups` named volume. Default schedule: 02:00 UTC daily (`BACKUP_CRON=0 2 * * *`). `dashboard-api` mounts the same volume read-only at `/backups` and reads it via `BUTLERS_BACKUP_DIR=/backups`.
- API: `GET /api/system/backups` delegates DB-free artifact and run-receipt scanning to `read_backup_facts_from_dir()` in `src/butlers/core/backup_facts.py`. The system router composes the API payload and overlays the DB-backed restore-drill result. The reader scans `BUTLERS_BACKUP_DIR` for `butlers_*.sql.gz` files sorted by mtime and returns the most recent file's mtime + size as `last_backup_at` / `last_backup_size_bytes`. It returns `backup_source_reachable=false` when `BUTLERS_BACKUP_DIR` is unset or the directory does not exist (unconfigured deployment, not an error).
- The `BackupTile` frontend component already handles all three states: loading, unreachable/unconfigured, and reachable with history. No frontend changes were needed.
- Manual test: after `docker compose up`, wait for first cron fire (or run `docker compose exec backup-cron /backup/pg_dump.sh` directly), then `GET /api/system/backups` should return `backup_source_reachable=true` with a non-null `last_backup_at`.
- `SpotifySessionAdapter._project_row` falls through to `track_names` when both `context_name` and `context_uri` are NULL — the underlying `connectors.spotify_listening_sessions.track_names` JSONB column is already populated; do not skip it just because no playlist/album context was attached.
- `bd worktree create <path> --branch X` creates branch X from the current HEAD, not from `origin/main`. New code worktrees must `git reset --hard origin/main` after creation; review worktrees must `git fetch origin agent/<original> && git reset --hard origin/agent/<original>` so reviewers see the actual PR HEAD.
- `bd close <id>` rejects beads that still have an open `blocks` downstream. For coordinator-driven closure where the original-vs-review pair is already done, pass `--force` rather than closing in dependency order (e.g. closing a review bead whose parent still references it).
- `bd create "title" --description="..." --json` fails when the description contains literal newlines or unescaped control characters — jq can't parse the JSON output. Recover the new ID via `bd list --json | jq -r '.[] | select(.title | startswith("...")) | .id'` and continue.
- PR reviewers must run the FULL CI-equivalent gate set (`pytest`, `npm run lint`, `npm run build`, `vitest`, frontend-e2e) — not just the gates listed in the bead's original scope. Pre-existing main breakage often extends beyond the bead, and each fix can unmask the next layer (we discovered ~7 additional failures across 5 review rounds on a 2-issue scoped bead).
- Cache-freshness markers using `time.monotonic()` must initialize to `float("-inf")`, not `0.0`. Fresh CI runners report `time.monotonic()` < typical TTLs (e.g. <900 s), so a `0.0` init makes `monotonic() - last_loaded < ttl` accidentally `True` on the first call → cache is "fresh" but empty. Bug pattern lives in `src/butlers/connectors/gmail_policy.py::GmailPolicyEvaluator` after the round-3 fix.
- `app.dependency_overrides[get_singleton]` only intercepts FastAPI `Depends(get_singleton)` lookups. A wrapper function that calls `get_singleton()` as a plain function call does NOT honor the override. Either keep `Depends(get_singleton)` in the endpoint and override `get_singleton` in tests that don't init the singleton, or accept that wrappers break overrides — don't introduce wrappers thinking they preserve dep-override semantics.
- SQL predicates used to filter rows in repository methods (e.g. `WHERE 'owner' = ANY(roles)`) break unit-test mocks that return canned rows regardless of the SQL. If the production code relies on the predicate to exclude wrong rows, tests with permissive mocks will see those wrong rows pass through. Read all needed columns from the row and filter in Python so mock-returned rows can be intentionally non-owner and the code still rejects them.
- `butlers-dev-dashboard-api-hotreload-1` does NOT auto-reload Python: it bind-mounts `~/gt/butlers/src` but runs `uv run butlers dashboard` without a file watcher. After merging backend changes and pulling main, `docker restart butlers-dev-dashboard-api-hotreload-1` is required (frontend Vite container does hot-reload on its own).
- Ingestion timeline status semantics: skip-triaged events (`triage_decision='skip'`) are stored in `public.ingestion_events` with DB `status='ingested'`; the unified timeline SELECT (`_UNION_COLUMN_SPEC` in `src/butlers/core/ingestion_events.py`) derives the display-only status `'skipped'` for them. Replay/state transitions still match on the DB value `'ingested'` — never write `'skipped'` to the table or match on it in UPDATEs.
- `public.butler_secrets` (shared credential pool) has two shape contracts that must stay aligned: fresh-table bootstrap (`_SECRETS_TABLE_DDL` in `src/butlers/credential_store.py`) and the core Alembic chain. Core-chain schema discovery can exclude `'public'` (as in core_106), so an added per-butler column also needs an explicit public migration. Normal `ensure_secrets_schema` startup may create an absent table/index but must not `ALTER` an existing table: `pg_dump` holds an incompatible relation lock and startup must remain responsive. Existing-table convergence belongs to the migration; fresh-shape parity and the backup-lock seam are pinned in `tests/migrations/test_shared_pool_test_state_migration.py` and `test_shared_pool_startup_lock.py`.
- In except blocks around credential/table queries, never string-match "does not exist" to mean table-missing — a missing COLUMN (schema drift) matches too and silently empties the result. Catch `asyncpg.exceptions.UndefinedTableError` for the silent path; everything else logs a warning (pattern in `_fetch_system_secrets`, `src/butlers/api/routers/secrets_v2.py`).
- `gh` (pr create/checks/merge) intermittently returns transient `HTTP 401: Requires authentication` even when `gh auth status` is healthy; also `gh pr checks` exits nonzero (8) while checks are pending. CI poll loops must tolerate both (retry on empty/401 output; don't gate the loop on exit code).
- Background subagent workers reliably go dormant while waiting on long CI (`check` jobs ≈ 25 min) even when told to poll in-foreground. Coordinators should arm their own fallback CI watcher with a short grace period and complete the merge/close/cleanup tail themselves if the PR is still open when checks finish; duplicate-merge attempts fail harmlessly.
- bd v1.0.4 in Dolt server mode has an unconditional JSONL re-import: `maybeAutoImportJSONL` (cmd/bd/auto_import_upgrade.go, run in main.go PersistentPreRun for every non-read-only command) UPSERTs every row of `.beads/issues.jsonl` into Dolt before the command, with no empty-DB guard on the server-mode path. If that file exists it can wedge bd town-wide (writes fail `begin write tx: context canceled`; reads still work). Durable rule: NEVER create `.beads/issues.jsonl` (no `bd export -o` to it, no symlink); the export path is `.beads/issues.export.jsonl`, set via `export.path` in `.beads/config.yaml`, and bd auto-export keeps it fresh. The upstream fix (GH#3955) is not in a safe release yet (v1.0.5 is gated), so keep this constraint until bd is upgraded.
- OpenSpec spec-amendment beads MUST end with sync+archive (apply deltas to `openspec/specs/`, check tasks.md, `mv` change to `openspec/changes/archive/YYYY-MM-DD-<name>`), not just merge the change directory. Verified failure mode (memory redesign, 2026-06-13): change `redesign-memory-house-ledger` merged un-applied, so a later reconciler (bu-d9im5) read the OLD spec's MUSTs as binding and re-justified UI the redesign had retired (tier cards) — drift caused by the spec machinery itself. Closeout PR #2208 pattern: sync deltas, archive, plus a code-level FE→BE wiring audit (the `superseded_by` field was returned by the API but read under a wrong name behind a stale "gated off" comment — both beads "done", affordance dead; grep the exact field name across types.ts/pages when a backend bead unblocks a gated frontend affordance).
- `butlers-redesign-prompt` handles both bundled and bundle-less redesigns (most pages have no bundle — shipped bundles are deleted; slug map lives at `.claude/skills/butlers-redesign-prompt/references/bundle-registry.md`). For a bundle-less redesign (e.g. `health`, 2026-06-20), originate from the Dispatch spec (`openspec/specs/dashboard-design-language/spec.md`) + the skill's `references/dispatch-kit/` and treat the live pages + that language as the "bundle". Run the UI-maturity QC sweep FIRST (`/butler-relentless-jarvis-pursuit` → `subskills/ui-maturity-audit/`; formerly the standalone `butlers-ui-maturity-audit` skill) — it often reverses the framing (the health surface was already full-CRUD real, not a skin; real gaps were IA, orphaned-but-built endpoints like `/api/health/measurements/trend`/`/latest`/`/sources` with zero FE consumers, and unsurfaced insight). All Dispatch primitives (Eyebrow/Display/KpiStrip/AttentionList/BriefingStatus/Voice/ButlerMark/StateDot/Section) and tokens already exist in `frontend/src/` (`index.css:1-290`), so redesign work is mostly composition, not net-new UI infra. The redesign Act-2 handoff (`/project-direction`, a `th-projects` subskill) → OpenSpec change → READY beads triggers the autonomous fleet; commit+push the brief+changeset to origin/main first so workers see the spec.
- OpenSpec `## MODIFIED Requirements`: the `### Requirement: <name>` header MUST match the existing `openspec/specs/<cap>/spec.md` header VERBATIM — the CLI matches by exact text. Do NOT add `[TARGET-STATE]` (or any tag) to a MODIFIED header; it breaks the match so apply/`validate --strict` create a parallel requirement instead of modifying. `[TARGET-STATE]` is ADDED-only. Quote the real prior literal when restating a value (e.g. an existing cron `0 7 15 * * *`), and ground every new universal/negative invariant against the pre-existing path it binds (an auto-refresh carve-out must NAME the 30s rule it overrides). Cross-butler insight reads: host the reader on `butler_switchboard_rw` (holds SELECT on `public.insight_candidates` per `core_010`), never the health/per-butler role (INSERT-only) — avoids a grant migration and preserves schema isolation.
- **A `## MODIFIED` block must reproduce EVERY scenario name the baseline still carries** — a MODIFIED requirement replaces the whole block, so `openspec validate --strict` fails with `MODIFIED "<req>" omits scenario(s) the current spec still has`. Matching is by scenario NAME only; bodies are never compared (`dist/core/validation/validator.js:514-531`, `dist/core/parsers/requirement-blocks.js:269-291` `findMissingCurrentScenarios`). Two consequences that have each cost a review cycle. (1) **Carried-over scenarios look like accidental duplication and are not** — a delta legitimately repeats scenarios already archived in `openspec/specs/`, including ones unrelated to the change's surface. Deleting them does not de-duplicate; archive would DROP them from the baseline. Observed 2026-08-22 on PR #3742: a high-risk review flagged ten such scenarios as "resurrected", and the coordinator confirmed the facts without checking the rule; the counterfactual took one command (copy `openspec/` to a scratch dir, delete them, validate) and produced six errors naming the six affected requirements. (2) **A baseline scenario heading CAN be renamed** — by two changes ARCHIVED in order: change A carries `## REMOVED Requirements` for the whole requirement, change B carries `## ADDED Requirements` with the corrected heading. Both may be authored, validated and shipped together; only the archive order is constrained, and archiving B first aborts harmlessly (`ADDED failed for header ... - already exists`, no files changed). (An earlier version of this note claimed renaming was impossible; it is not.) Prefer scenario headings that name the GUARANTEE, not the MECHANISM — mechanisms get replaced and a rename costs four things:
  - Never leave the gap open: between A's archive and B's, an unarchived `## MODIFIED` block naming that requirement passes `openspec validate --strict` and then hard-aborts at archive — validate never warns.
  - `ADDED` appends, so the restored requirement lands at the END of the requirements section.
  - Every unarchived `## MODIFIED` block for that requirement must be repointed.
  - `scripts/spec-overwrite-baseline.json` keys a frozen loss on `(kind, scenario, digest)`, so a rename orphans every entry under the old name — edit the `scenario` field on exactly those records, never `--update-baseline` (it re-freezes the whole repo and swallows unrelated regressions).
  - Full procedure: `.claude/skills/doctrine/subskills/spec-and-spine/references/renaming-a-baseline-scenario.md`.
- Background subagents spawned via the Agent tool deliver their final report to "main" only as a SUMMARY line in the idle/teammate notification — the full body does NOT arrive (even a single large SendMessage truncates to its summary). To get full reports reliably: have writer-capable agents (general-purpose) WRITE the report to a file under `.tmp/` (gitignored) and ping main with "written, N lines"; read-only Explore agents can't write, so instruct them to SendMessage the body in explicit `(1/2)/(2/2)` chunks.
- `bd create --graph <plan.json>` (bd v1.0.4) CREATES the nodes but SILENTLY DROPS the `deps` and `parent` fields — verified 2026-06-20 filing the calendar-UX roadmap epic (`bu-l3k0zg`): all 26 nodes created, but every child came back with `dependencies=null` and `parent=null`, so everything showed READY. The graph JSON schema is `{"nodes":[{"key","title","type","priority","description","parent","deps":["blocks:<key>"]}]}` (dep dir: `blocks:X` = this node blocks X = X blocked-by this). Since graph ignores deps/parent, wire them AFTER create from the returned `ids` map: `bd dep <blocker> --blocks <blocked> --no-cycle-check` (then `bd dep cycles`) for edges, and `bd update <child> --parent <epic>` for hierarchy. NOTE: parent-child shows up in a child's `blocked_by` list (dependency_type `parent-child`) but does NOT gate readiness — only real `blocks` edges keep a bead out of `bd ready`. Filing READY beads here auto-triggers the fleet, so gate big/risky children behind real prerequisite beads (design/decision beads, prefs, held epics) rather than leaving them ready.
- Watching PR CI to merge: the three jobs are `frontend` (~4 min: tsc+vitest), `frontend-e2e` (~3 min), and `check` (~23 min: backend pytest + testcontainers). `check` is always the long pole, so a single `gh pr checks --watch` (or a Bash poll) will time out at least once on it; budget ~25 min. Poll the `gh pr checks <#>` STATE COLUMN (`pending`/`pass`/`fail`), NOT `gh pr view --json statusCheckRollup` — the latter returns `.conclusion` as an EMPTY STRING (not null) for in-flight checks, so jq `(.conclusion // .status)` falls through to "" and a naive `grep null` guard exits early thinking it's done. `frontend`, `frontend-e2e`, and `check` must all be terminal green before final exact-head/base revalidation and the helper-only squash route. main is unprotected and these checks are not required by branch protection, but that does not relax this gate. The chronicler editorial endpoints (`/api/chronicler/briefing|attention|kpi`) already accept arbitrary `?date=&tz=` deterministically (no LLM, no cross-schema) via `editorial.compose_briefing_payload`; date navigation is a pure-frontend concern. `lay-and-land/frontend.md` still mis-documents Chronicles as `archetype="workspace"` (it is editorial) — tracked in bu-26j38.
- `openspec validate --strict` scans only the FIRST LINE of each requirement paragraph for `SHALL`/`MUST` — a requirement whose normative verb lands on a wrapped second line fails with "must contain SHALL or MUST" even though the text has it. Word requirement openers so SHALL/MUST sits before the first line break. Also: promoting a `[TARGET-STATE]` requirement to concrete in a delta = `## REMOVED` (old tagged header, with a `**Reason**:`) + `## ADDED` (new untagged requirement); nothing in the corpus uses `## RENAMED`. Debug parser output with `openspec change show <id> --json --deltas-only`.
- `bd create --parent <epic>` (v1.0.4) assigns dotted child ids (`bu-xxxxx.1`, `.2`, …) and wires parent-child at create time — no post-hoc `bd update --parent` pass needed (unlike `--graph`, which stays broken). Parent-child still does NOT gate readiness; children must `bd dep add <child> --blocked-by <gate>` to stay out of `bd ready`. Gate-bead pattern for fleet-safe planning: title it "GATE (owner-only): … do not work this bead", assign it to tze, and have all implementation children blocked-by it; releasing = owner closes the gate.
- Verifying tests in a git worktree (which has no `.venv` of its own): do NOT symlink the root `.venv` and run `uv run --no-sync` — `uv run` resolves to the ROOT venv via the shell env, which (a) may be broken (root `import butlers` can fail outright — editable-install drift) and (b) imports `butlers` from whatever branch the fleet left the root checkout on (often NOT `main`; the root frequently sits on a `codex/*` feature branch mid-session). For a trustworthy run, `uv sync` INSIDE the worktree (builds a real env editable-installed against THAT worktree's `src/`) then invoke `env -u VIRTUAL_ENV ./.venv/bin/python -m pytest …` directly. Per-PR CI (clean `uv sync` env in Actions) is the authoritative gate regardless, so local worktree runs are a pre-check, not the merge gate. Related: in a LONG session `origin/main` advances continuously (active fleet) — branches cut early go stale; before merge, rebase onto fresh `origin/main` (real conflict surface = files BOTH main and your branch touched; compute via `git merge-base HEAD origin/main`, never a hardcoded base SHA, since the worktree's base may already be several fleet commits behind).
- Live UI prototyping next to butlers-dev (pattern used for the entities Plex, 2026-07-02): run a second Vite from a worktree with `npx vite --port 45(x)73 --base /butlers-<name>/` and expose it via `tailscale serve --bg --set-path /butlers-<name> http://localhost:45x73/butlers-<name>`; the frontend MUST be started with `VITE_API_URL=/butlers-dev-api/api` because the default `/api` base escapes tailscale path mounts. Hitting the Vite port directly on 127.0.0.1 will 404 every API call (the API path only resolves through the tailscale mount) — that's expected, not a regression; verify through `https://tzeusy.parrot-hen.ts.net/butlers-<name>/…`. Cleanup: kill the vite pid, `tailscale serve --set-path /butlers-<name> off`, remove the worktree. Also: when a PR re-maps top-level routes (e.g. `/entities` became the Plex and the index moved to `/entities/index`), grep `frontend/tests/e2e/` for `goto("/route"` and URL-assertion regexes — the e2e job runs stubbed specs that pin redirect destinations verbatim, and it fails only in CI (unit vitest won't catch it).
- `bd lint`'s "Missing: ## Acceptance Criteria" (and the epic-level "## Success Criteria") is satisfied by the DEDICATED field — `bd update <id> --acceptance "$(cat <<'A' … A)"` — no need to embed a `## Acceptance Criteria` section in the description. `bd update` also has `--design`/`--design-file` and `--append-notes` (append, vs `--notes` which replaces).
- Live prototyping with BACKEND changes (extends the 2026-07-02 Plex pattern; used for the halo endpoint, 2026-07-04): the butlers-dev API serves main's code, so a worktree branch's new endpoints 404 on `/butlers-dev-api`. Run a second dashboard API from the worktree: `env -u VIRTUAL_ENV uv run butlers dashboard --host 127.0.0.1 --port 41999` with `POSTGRES_{HOST,PORT,USER,PASSWORD,SSLMODE}` copied from `docker inspect butlers-dev-dashboard-api-hotreload-1 --format '{{json .Config.Env}}'`, mount it via `tailscale serve --bg --set-path /butlers-<name>-api http://localhost:41999`, and start the worktree Vite with `VITE_API_URL=/butlers-<name>-api/api`. Two gotchas: (1) despite the container's "hotreload" name, `butlers dashboard` runs uvicorn WITHOUT reload — restart the local process after backend edits, and `docker restart butlers-dev-dashboard-api-hotreload-1` after merging backend changes to main or the live API keeps serving old code; (2) never `pkill -f` a pattern that appears in your own shell's command string (it kills your own session, exit 144) — kill by listening port instead: `kill $(ss -ltnp | grep :41999 | grep -oP 'pid=\K[0-9]+')`.
- Chronicler "no data on /chronicles" triage order (verified 2026-07-05): (1) `chronicler.source_adapter_state` (adapter registry + read_surface docs) and `chronicler.scheduled_tasks.last_result->>'rows_projected'` — the 16 projector jobs usually run healthy but project 0 because the UPSTREAM surface is empty, so (2) check feeders directly: `health.facts` predicates (google-health connector 403s on ALL data types until OAuth is scope-widened → no sleep/steps/HR ever), `connectors.home_assistant_history` (connector may fail `ConnectionError http://butlers-up:41100/sse`), `connectors.owntracks_points` (significant-motion mode ⇒ 1–78 pts/day; raw_payload has conn/batt but NO ssid unless enabled in the app). Also: KPI (`editorial.py _compute_kpi`) uses raw `category_for` WITHOUT the `lane_for_activity` layer filter/`union_seconds` the aggregate endpoint applies, so intent-layer all-day calendar rows leak into KPI lanes (bu-whhll.1). The "Work" lane = butler LLM sessions + `focus_inferred` (fires only on ≥45min butler sessions / calendar titles matching focus|deep work|pomodoro — dry since 2026-04-24); owner employment has no signal source or category (epic bu-whhll, gated bu-xe3ow).
- Token/cost accounting contract (PR #2926): runtime adapters MUST report `usage.input_tokens` as the UNCACHED bucket only, with `cache_read_input_tokens`/`cache_creation_input_tokens` separate (contract doc in `runtimes/base.py`; vendors differ — Claude CLI natively excludes cache from input, OpenAI/Codex `prompt_tokens` INCLUDES it and must be subtracted, OpenCode reports `tokens.cache.{read,write}`). Pricing (`core/pricing.py`) bills a cache bucket at the model's cached/creation rate, falling back to the FULL input rate when unset — never $0; cost lookup is an exact model-id match: a missing entry returns `None` and remains unpriced, while an explicitly configured zero-rate entry (for example, subscription or local) is a known `$0` result. New catalog models need a `pricing.toml` entry rather than silently reading as free (checked by a repo-default-toml regression test in `tests/api/test_pricing.py`). Adding a column to `sessions` fans out via one unqualified `ALTER` in the core chain (per-schema search_path); mocked-pool fixtures for `sessions_summary`/board/ledger shapes live in `tests/api/test_spend.py::_mock_db_pool`, `tests/api/test_butlers_board.py` (fake pool), `tests/core/test_model_routing_quota.py`, `tests/jobs/test_spend_jobs.py` — a new SELECT column breaks them one file at a time, grep `total_input_tokens` in tests/ to catch all. Integration tests calling `session_complete` on a raw asyncpg pool need `init=register_jsonb_codec` (from `butlers.db`) or jsonb params raise "expected str, got list".
- `model_catalog_defaults.toml` bootstrap seeding is DEAD on a truly fresh install: `core_004_model_and_tokens.py::_load_seed_entries()` filters `complexity_tier` against `_COMPLEXITY_TIERS`, a LEGACY-vocab tuple (`trivial/medium/high/extra_high/discretion/self_healing`), but the toml file's actual entries all use the CANONICAL post-core_093 vocab (`cheap/workhorse/reasoning/specialty/local/legacy`) — none match, so `_load_seed_entries()` returns `[]` and a fresh-bootstrap `model_catalog` seeds ZERO rows from the toml (verified empirically 2026-07-05 via `create_migrated_test_db(chains=["core"])` + `SELECT * FROM model_catalog` — only rows inserted by later data-migrations like core_157 exist). Editing the toml has no effect on any environment (fresh or existing) until this filter is fixed; a real fix needs a new migration re-seeding from the current toml with the canonical-vocab filter, run AFTER the tier-rename chain. Do not assume adding a toml entry does anything — verify via an actual migrated-DB query, or seed live rows directly in a data migration instead (see core_157_api_runtime_discretion_classification.py for the pattern).
- Discretion/classification catalog tiers were flipped onto a new `runtime_type='api'` adapter (bu-qvnce.12, `core/runtimes/api.py`) via `core_157`: two `model_catalog` rows (`api-haiku-cheap` priority 30, `api-haiku-specialty` priority 20) outrank the prior top entries (`gpt-5.4-mini` 25, `discretion-qwen3.5-9b` 10) so `resolve_model` picks them by default post-migration. Same-tier failover (`next_same_tier_candidate`, wired in `spawner._run()`) covers spawner-path failures (classification); `DiscretionEvaluator`'s existing fail-open/fail-closed-by-weight semantics cover discretion-path failures (no spawner failover there — discretion calls bypass the spawner entirely).
- Deployments ledger (`public.deployments`, core_163, bu-9r3hd.2): `butlers up` runs ALL configured butler daemons in ONE process (`cli.py::_start_all`), so the ledger write happens exactly ONCE per boot in `_start_all` (via `_record_deployment_boot`) using the first-started daemon's pool — never inside `ButlerDaemon.start()`/`lifecycle.py::run_startup`, which runs once per butler and would produce N duplicate rows per actual deploy. `migration_head` is read from that one daemon's own schema `alembic_version` table (conventionally `switchboard`, since `_PRIORITY_BUTLERS` starts it first) — it's a representative snapshot for the `/system` page, NOT a cross-schema drift proof (butler-specific chains can legitimately diverge); the real hourly alembic-head vs per-schema DB-revision vs deployed-SHA comparison is bu-9r3hd.1's job and should read this table rather than re-deriving it. `GIT_SHA` is threaded as a Docker build arg (`Dockerfile` ARG+ENV, `scripts/compose.sh` passes `--build-arg GIT_SHA=$(git rev-parse HEAD)`) so it's baked into the image and available via `os.environ` at runtime with no compose `environment:` plumbing needed. `result` is only `success`/`failed` in this slice (no `in_progress`) — bu-9r3hd.3's `butlers deploy` verb is what will eventually own real phased start/verify-health/finish timing.
- Provider-managed Spotify `butler_secrets` rows are excluded from proactive secrets lifecycle notifications in both per-butler and shared stores: one-hour access-token expiry is routine, while actionable auth failure comes from connector refresh/status. Keep the backend exclusion aligned with the frontend provider-managed filter.
- `owntracks.ssid_presence` stores `_source_cursor = {watermark, uuid}` in checkpoint carryover because `watermark_id` is BIGINT and OwnTracks IDs are UUIDs. Page via `(ts, id)` using `idx_owntracks_points_ts_id`. A timestamp-only, malformed, or mismatched cursor triggers bounded replay from retained evidence with carryover rebuilt; the first successful page marks the upgrade complete. The adapter-local `run()` transaction takes a source-keyed advisory lock and keeps mapping tombstones, episode/link upserts, carryover/cursor, source-active state, and the relational checkpoint on one Chronicler connection; when source and write pools are the same, source/owner reads reuse that connection so lock waiters cannot exhaust the pool. Failures roll back before best-effort failed-run metadata is recorded separately, and cancellation propagates after transaction/connection cleanup.
- `memory_ann_observability` monitors only local HNSW tables (`episodes`, `facts`, `rules`) through the memory module's own pool, so private schemas such as `chronicler_mem` remain isolated; catalog IVFFlat measurement is a separate concern. Exact recall is allowed only below both a 2,000-row estimate and 1,024-page cap, with local lock/statement timeouts; otherwise report explicit degraded/no-data health and never run vacuum, reindex, or maintenance automatically.
- Dashboard currentness contract: audit groups and QA patrol failures are current only in closed `[now-window, now]` intervals (exclude future clock-skew), and failed-notification queries plus their Notifications drill-down links must preserve one captured `since`/`until` pair. Completed QA dispatches are bounded `Now` activity, not active attention.
- Messenger tracking retirement (bu-g6v9v.1): `msg_003` retires the unwired `delivery_requests`/attempts/receipts/dead-letter stack and its MCP/REST/frontend surfaces. It takes transaction-scoped `ACCESS EXCLUSIVE` locks across every existing legacy table before checking or dropping it, so retained or concurrently committed rows fail closed; downgrade recreates the exact empty `msg_002` compatibility schema and never restores data. The live path remains trusted Switchboard `route.execute` → approval/pending action → Messenger native Telegram/email/WhatsApp adapter → outcome and attention ledger. Do not reintroduce a Messenger tracking health, queue, retry, or receipt surface without a separately designed admission path that actually owns those lifecycle records.
- `npm run lint:query-coercion` is CI-enforced in the `frontend` job with a PER-FILE baseline count (`query-coercion-baseline.json`): any net-new `?? []`/`?? 0` on a query's `.data`/`.meta` fails CI even when eslint/tsc/vitest are all green locally (bit PR #3601 — worker ran every gate except this one). Fix order: reuse an already-guarded upstream value > add an `isError` guard at the site > bump the baseline (only with justification that the query is isError-guarded at render). Run it locally before pushing any frontend page change.
- Racing PUTs from UI flows (e.g. keyboard-reorder move vs Escape-restore firing concurrently against the same row): the accepted repo fix is TanStack Query mutation `scope: { id: "<flow-scoped-id>" }` — `MutationCache.canRun` gates `mutationFn` dispatch until the prior same-scope mutation settles and `runNext` resumes in add-order, so the last-`mutate()`d write is guaranteed last-applied server-side (verified against `@tanstack/react-query` 5.90 internals, PR #3601). Tests for such races must use per-call deferred promises resolved at response time — a mock that applies state at request-send time structurally cannot observe out-of-order arrival.
- The beads-pr-reviewer-worker skill's Phase 2 may REMOVE the implementation worker's worktree (`bd worktree remove`) and check the PR branch out into the reviewer's own worktree instead. Before dispatching a corrector "into the worker's worktree", always run `git worktree list` and target wherever `agent/<id>` actually lives now (bit two correction cycles, 2026-07-26).
- Parallel-lane migration collisions (two open PRs both taking core_NNN off the same base): merge the first, then the second rebases onto new main, `git mv`s to core_N+1, points `down_revision` at the merged revision, resolves the chain-head literal conflict in tests/migrations/test_purge_confidential_pii_memory_catalog_migration.py to core_N+1, and pushes with `--force-with-lease` (the one sanctioned force variant, rebase-only). Renumber BEFORE review so the reviewed head is final.
- Home Assistant recorder statistics use `src/butlers/connectors/home_assistant_statistics.py::HAStatisticsClient` and the current WebSocket command `recorder/statistics_during_period`. The module reuses its connected command sender; jobs/dashboard callers use short-lived credentialed connections. Energy consumption must aggregate per-period `change`, never cumulative `sum`, and provider errors must remain bounded/sanitized.
- Dashboard chat Stop is message-scoped, not conversation-scoped: propagate the immutable dashboard user-message id through ingress, route inbox, recovery, and `Spawner`, and only render cancellation after the durable control row confirms it. Route-inbox workers must use fenced processing leases so a stale recovery cannot overwrite a newer worker's result. A lost lease must be explicit to `Spawner` finalization: cancel the local runtime but leave its dashboard session/turn unresolved for recovery to mark `ambiguous`, never as a synthetic failure. `ambiguous` forbids replay/retry but still records Stop intent and attempts cancellation of every known active session.
- `dashboard_turn_claim_external_action` is only a reservation for dashboard bug-report/dead-letter terminal effects; without an idempotent receipt/outbox reconciler, a crash can leave `external_action_in_progress` unresolved. Do not claim full terminal-lane recovery until the dedicated reconciliation work is complete (bu-s3qvp).
- Messenger `route.execute` inline approvals must materialize one registered native delivery command before gating and reuse it for both immediate execution and deferred replay; executable `tool_args` contain only handler kwargs. Email replies require authoritative `request_context.source_thread_identity` (never substitute `request_id`), and channel runtime policies such as WhatsApp `send_enabled` must be shared by both paths. Retry may expose only allowlisted validation classifications; raw handler/provider errors stay in logs and audit records.
- Timeline partial-source contract: retain the generic `meta.degraded_sources` source-level signal and add `meta.degraded_butlers` only for named failed session fan-out pools; the frontend defaults the additive list for rolling deploys. When Timeline history is unpinned, commit the current rows and cursor before fetching older data so a failed page remains visible and retries the identical cursor rather than implying the end of history.
- GPT-5.6 Spend prices intentionally use OpenAI Standard API-equivalent metered <=272K rates even when the runtime is subscription-covered; pricing lookup is exact, so live `gpt-5.6-luna-high`/`-xhigh` catalog IDs need their own entries. The dashboard's heuristic deliberately assumes every request is <=272K and keeps these entries flat; do not add long-context tiers without an explicit policy change.
- Calendar workspace sync must preserve the raw `query_calendar_sources` fan-out ledger, then select a dashboard-only canonical owner by enabled state, calendar `core` capability, and freshness. `POST /api/calendar/workspace/sync` sends one owner-wide `calendar_force_sync(queue=true)` command per canonical owner and returns HTTP 202; CalendarModule persists/serializes the command in `calendar_action_log`, with `running` recovery/coalescing so browser timeouts never imply provider work was cancelled.
- Entity-dedup curation uses `relationship:entity-dedup:<source>:<target>` as `pending_actions.deduplication_key`; `approvals_013` uniquely protects `pending`/`approved`/`rejected`/`abandoned` rows, preserves NULL historic rows, and permits expired pairs to resurface. It converges the former divergent `approvals_012` development schema without rewriting rows. Select the surviving target by `(created_at, id) ASC`.
- Rule-promotion sender classification is channel-authoritative: only `source_channel='email'` may parse a whole email; opaque endpoint IDs (including `@`) stay `source_endpoint`. Confirmation/auto-apply and the production trigger share an identity advisory transaction lock, and the trigger reloads rules inside it.
- Connector heartbeat `status.state` is a closed wire vocabulary: `healthy`, `degraded`, or `error`. A connector that has not completed its first transport attempt must report `degraded` with detail such as `transport=starting`, never emit a fourth `starting` state that Switchboard rejects.
- Chronicler archive coverage is provenance-scoped: only `day_close_success`, admitted `day_close_cache`, and active `episode_activity`/`episode_evidence` origins establish `earliest_date`, an exact readable day, or `recent_days`; retained `legacy_unverified`, intent, and tombstoned evidence never do. `chronicler_024` classifies historic rows, while a successful day-close promotes an existing legacy row to `day_close_success` without rewriting another authoritative origin.
- Live Codex auth synchronization may use a `CredentialStore` shared/public authority only when it is supplied explicitly; never infer it from a schema-local or connector cursor pool. Bind post-operation rotations to the launch snapshot with a shared-store CAS, and declare one bounded `session_timeout_overhead_s` allowance for reconciliation, prewarm, and refresh-lock waits so they do not consume the provider execution timeout.
- Runtime adapters receive a caller-owned restricted environment. Any invocation-local variable (especially Codex's temporary `HOME`) must be installed in a private subprocess copy; otherwise same-tier failover can inherit stale or deleted runtime state.
- Spec-trace-check ID format: requirement IDs must be bare `REQ-{spec-name}-NNN` with no suffixes like `(modified)` — those are not valid IDs and cause parse errors. Multi-paragraph normative text (bullet lists, tables between paragraphs) also breaks the parser's contiguous-ID-line detection; fold into a single paragraph.
- Education manifesto v1 content-sourcing boundary amendment is tracked in `openspec/changes/source-grounded-education/` (signed off 2026-08-21). Until that changeset is applied, the live manifesto still says "not a content sourcing agent." Agents implementing source-grounded-education tasks should follow the changeset's amended language, not the current manifesto text.
- Calendar prep rail commitment enrichment (`openspec/changes/meeting-prep-commitment-context/`) is blocked on commitment-lifecycle tasks 3-4 (commitment helper module, `bu-n1evl`). The prep job can query `public.owner_conditions` directly with metadata filters, but should use `list_entity_commitments()` when available to inherit confidence threshold and validation.
- Canonical DND mutation authority is bootstrap-only: `scripts/init-db.sql` owns the fixed cluster-superuser installer/finalizer, while `core_197` may only catalog-validate that exact interface or invoke its no-argument installer. The final state transfers `public.user_context`, guard, audit, policies, and private definer to `dnd_generation_owner` (NOLOGIN/NOINHERIT/NOBYPASSRLS), enables and forces RLS, and uses a SECURITY INVOKER active-role gateway plus a private SECURITY DEFINER active-`SET ROLE` recheck. Never restore generic DND upserts, raw-DND audit fields, or migration-role authority; real-PostgreSQL role/catalog proofs remain mandatory before runtime enablement.
- For `condition_ledger.resolve_condition()`, PostgreSQL JSONB `||` is right-biased: creation-wins resolution evidence must merge as `resolution_metadata || existing_metadata`, not the reverse. Keep the resolver in the reconciler's existing `table:source` advisory-lock domain and preserve snapshot-driven `identity_payload` successor provenance when extending `_resolve_episode()`.
- The full backend gate (`uv run pytest tests/ --ignore=tests/e2e`) takes roughly **40 minutes** on this repo — the hosted `check` job consistently reports 40-45m. A run sitting at 85-90% for several minutes is healthy, not hung: the early percentages are fast unit tests and the wall-clock is dominated by the integration/testcontainers tail, so percent-complete is a poor progress proxy on this suite (observed: 34% in 2.5 minutes, then the remainder taking the bulk of ~40 minutes). Confirm liveness with `pgrep -f "pytest tests/ --ignore=tests/e2e"` and `tail -5` on the log before declaring a stall; killing and restarting it costs another 40 minutes. This is why the Test Scope Policy above insists on targeted runs during development and the full gate only at merge-readiness.
- When launching that gate in the background from an agent harness, put each shell step on its own line or separate them with `;`. A collapsed one-line `eval` silently swallows `wait $BGPID` / `echo "EXIT=$?"` as *arguments to the preceding `echo`*, so the run completes but the agent never receives a completion signal and appears to hang. Poll the log file directly rather than depending on the wrapper's exit echo.
- A foreground `pytest` invocation started from an agent tool call can be **signal-killed with the tool call's process group** when that call hits its harness timeout (observed 2026-08-22: exit 144 = 128+16/SIGUSR1 at 53% of the backend gate). The tell is that the log ends mid-progress-line with `pytest_sessionfinish` raising `OSError: cannot send (already closed?)` once per xdist worker, and contains no `F`, no `FAILURES` section, and no summary line — that is the xdist workers losing their controller, not a red suite. Do not read it as a pass or a fail; there is no verdict. Launch the gate fully detached instead (`setsid nohup .venv/bin/python -m pytest ... </dev/null >LOG 2>&1 &`) so it reparents to PID 1 and no group signal can reach it, then poll the log with short separate calls.
### Pytest-gate verdict contract

- **Positive terminator rule:** **A pytest log with no summary line is UNKNOWN, never a pass**
  (bu-5hp74). The truncation above is byte-for-byte indistinguishable from a run still in flight and
  it *greps clean*: no `FAILED`, no `N failed`, nothing to find. Any reading rule of the form "no
  failure line, therefore green" credits a killed run as a passing one. Require a **positive
  terminator** instead: a summary line, or the process exit status.
- **Gate plumbing:** `scripts/pytest_gate.py` is that rule in code — `run [--log PATH] [--tee]
  [--detach] [--] <pytest args>` launches pytest in its own session (group signals cannot reach it)
  and has the *child* append a `## pytest-gate exit=N` sentinel, so the receipt survives the runner
  being killed; `verdict LOG` classifies and exits `0` PASS / `1` FAILED / `2` UNKNOWN, so a shell
  `&&` chain fails closed. `make test-qg`/`test-qg-serial` are wired through it (bu-ecizp), so this
  applies to every gate run and not just the CLAUDE.md snippet; `--tee` mirrors the growing log to
  stdout by *following the file* rather than piping pytest through the caller, because a pipe would
  hand pytest a broken one the moment the caller died — the exact death the receipt exists to
  survive.
- **Exit-code classification:** Sentinel outranks summary line; only exit 0 is a pass, and 3, 4, 5
  and 128+N all mean the suite rendered no verdict.
- **xdist interaction and serial fallback caveat:** **Exit 2 is the interrupted run, and under
  xdist `--maxfail` makes an ordinary test failure exit 2** (the controller raises `Interrupted`; a
  serial run would raise `Failed` and exit 1 -- `addopts` carries `-n 3`, while `make test-qg-serial`
  explicitly supplies `-n 0`; both `QG_PYTEST_ARGS` and the CLAUDE.md snippet pass `--maxfail=1`),
  so it is decided against the log's last summary line: counts reporting `failed`/`error` are
  FAILED, no summary or a clean count stays UNKNOWN (bu-17myd). The summary may only take exit 2
  *down* to FAILED, never *up* to PASS — a Ctrl-C partway through a green run prints a clean count
  and still never reached the rest of the suite. No other nonzero status consults the summary: those
  report that no verdict exists, and counts printed before that cannot contradict them.
- **The `pytest_sessionfinish` crash itself cannot usefully be hardened from this repo** (bu-5hp74, reproduced: `setsid .venv/bin/python -m pytest . -n 2 --dist loadfile -q &` then `kill -USR1` the controller). Two reasons, both checked rather than assumed. First, xdist ships `remote.py` to each worker via `gateway.remote_exec(xdist.remote)`, which executes the module *source* under a synthetic name; the worker's `WorkerInteractor` is therefore a different class object from the imported `xdist.remote.WorkerInteractor`, and monkeypatching the latter from a conftest or `-p` plugin is a verified no-op (both were tried; byte-identical logs). Second, even a successful patch via the `pytest_xdist_getremotemodule` hook would not restore a summary line, because the summary is written by the *controller*, which is the process that died. It would only delete the `cannot send (already closed?)` line, which is currently the only evidence distinguishing a killed run from a hung one. Harden the *reader*, not the teardown.

- **Do not run two full backend gates concurrently on this machine.** They starve the Docker daemon and the second one reports `ERROR` at *setup* of testcontainers-backed tests, which reads like a broken suite but is pure contention (observed 2026-08-22: `13727 passed, 21 skipped, 10 errors in 25:02` where the baseline pass count was 13737 — the 10 errors are exactly the missing passes). The signature is unmistakable: every traceback bottoms out in `urllib3`/`requests` against `UnixHTTPConnectionPool(host='localhost', port=None)` — that is `/var/run/docker.sock` — with `ReadTimeout (read timeout=60)`, the failures cluster in one testcontainers-heavy file (here all 10 in `tests/config/test_init_db_bootstrap.py`), and there is **no `FAILURES` section and no `N failed`** because nothing got far enough to assert. Triage it with `sed -n '/short test summary/,$p' LOG | grep '^ERROR' | sed 's/::.*//' | sort | uniq -c`: errors confined to one or two container-heavy files, plus `passed + errors == baseline`, means infrastructure, not code. Serialize the gates and re-run, or trust the hosted `check` job, which runs on an uncontended runner.

- **A baseline spec that contradicts the code is NOT drift while its OpenSpec change is open.** `openspec/` is delta-based: proposals live in `openspec/changes/<change>/specs/<capability>/spec.md` as `## ADDED` / `## MODIFIED` / `## REMOVED` blocks, and `openspec archive` is what rewrites the baselines under `openspec/specs/`. So a baseline lagging an in-flight PR is the *normal* mid-change state, not a defect — before filing spec drift or blocking a merge on it, grep `openspec/changes/` for a staged delta that already covers it (observed 2026-08-22: a fleet-halt requirement flagged as drift was already retracted verbatim in the open change's `## MODIFIED` block). Two corollaries: hand-editing a baseline while a change is open risks colliding at archive, so only do it for a requirement that change does not touch; and never "refresh" a superseded baseline requirement cosmetically — repointing a module name without correcting the THEN clauses makes a stale guarantee look freshly verified, which is worse than leaving it visibly stale.
- **`cmd 2>&1 > file` does NOT capture stderr** — it points stderr at the *terminal* and only stdout at the file. Redirection is evaluated left to right, so the correct form is `cmd > file 2>&1`. This bites hardest with tools that report on stderr (`openspec validate` among them): a before/after comparison written the wrong way diffs two empty files and returns a confident, entirely vacuous "identical". Same shape as the killed-gate-with-no-summary trap above — absence of output read as a clean result. When a check's value depends on its output, assert the output is non-empty before trusting what it says.

### CI is advisory: `main` is not branch-protected

`gh api repos/Tzeusy/butlers/branches/main/protection` returns 404 "Branch not protected", and
`ci.yml` contains no `continue-on-error` anywhere. So no check in `ci.yml` actually blocks a merge --
a red `check` will not stop `gh pr merge`. Waiting for green is a discipline, not an enforced gate.
Do not describe a CI job as "required" or "blocking"; when adding a new job, "advisory like every
other check here" is the accurate phrasing.

### CI's `check` job runs named make targets, NOT `make check`

`.github/workflows/ci.yml` invokes individual targets (`make check-for-update-joins`, an explicit
`uv run pytest tests/ roster/ ...` step, a smoke step) rather than `make check` wholesale.
**Adding a target to the `check` aggregate in the Makefile does NOT make it run in CI** -- that needs
a dedicated job. Verify by reading the job's `run:` steps, never by reading the Makefile.

Scope table, because the four "run the tests" incantations differ and are easy to conflate:

| what | actual scope |
| --- | --- |
| `make test-qg` | `pytest tests/` minus `test_db.py`, `test_migrations.py`, `tests/e2e` |
| CLAUDE.md low-context gate | `pytest tests/ --ignore=tests/e2e` |
| CI `check` unit lane | `pytest tests/ roster/ --ignore=tests/e2e -m "not integration and not e2e and not nightly and not bench and not perf"` |
| CI `check` integration lane | `pytest tests/ roster/ -m "integration and not nightly and not bench and not perf" -n auto --dist loadfile` |

`make lint` is only `ruff check src/ tests/` -- it omits `roster/` and `conftest.py`. The 21-vs-113
skip-count gap between gate runs is `tests/e2e`, not `roster/`. **Exit 0 is the only acceptance
criterion; no skip count is pass/fail.**

### Judge a background run on its `.exit` file, in its own worktree

Long runs must be detached (`nohup sh -c '<cmd> >"$LOG" 2>&1; echo $? > "$LOG.exit"' &`) -- the
agent-tool foreground cap kills at exactly 10m00s and a foreground `timeout` also applies (SIGTERM,
exit 143). A detached run survives the death of the agent that launched it.

When several agents run gates concurrently, the session task directory accumulates finished gate
output from *other* agents' runs. Reading those harvests someone else's verdict and misattributes it.
**Judge strictly on the `.exit` file inside the worktree you are attesting**, never on a task-output
file. Also beware timing: "no such file" answers "had it launched by the instant I looked", not
"did it ever launch" -- compare timestamps before concluding a run never started.

### `git merge-tree` probes mergeability without moving HEAD

`git merge-tree --write-tree --name-only origin/main <branch>` reports conflicts without checking
anything out, so it respects Repo Root Discipline. Exit 0 plus a tree hash means clean; on a clean
merge the file list is empty. Use it to check two live branches against each other before merging.

Ordering that avoids rework: rebase onto current `main` **before** running the gate. Division of
labour -- the local gate attests *the worker's change*; PR CI attests *the merged tree*.
`gh pr merge --squash --delete-branch` fails while a worktree still holds the branch; remove the
worktree first.

### `check_spec_overwrites.py` cannot see baseline hand-edits

The gate (`scripts/check_spec_overwrites.py`, landed 2026-08-23) walks `## MODIFIED Requirements`
blocks under `openspec/changes/**` and compares them to the live baseline body. It never inspects
`openspec/specs/**` for direct modification. So editing a live baseline by hand passes the gate --
not because the edit is safe, but because it is out of scope. Never cite a green run of this gate as
evidence that a baseline edit is sound; the two do not overlap.

Corollary for reviewers: a branch that changes wire shape and has NO folder under `openspec/changes/`
is the shape to be suspicious of. Contract movement belongs in a delta; the baseline moves only at
`openspec archive`.

### Secrets: CLI `label` is an alias for the `description` column

`_fetch_single_cli_secret` (`src/butlers/api/routers/secrets_v2.py`) builds `CliRuntimeDetail(...,
label=row["description"], ...)`. CLI `label` is not an independent field. This matters for Option C
content-blindness arguments: publishing CLI `label` publishes exactly the operator-authored
`description` column that the dashboard-api baseline already permits, so it is not a widening of the
leak surface. Note the collision with USER rows, where the baseline explicitly FORBIDS publishing the
persisted `entity_info.label` -- same word, different provenance, opposite rule. Check which surface
you are on before reasoning about `label`.

### `jq 'select(.conclusion != null)'` miscounts GitHub check steps

Steps that have not finished carry `conclusion: ""`, not `conclusion: null`. Filtering on `!= null`
therefore counts pending steps as done and reports a job as fully finished while it is still running.
Filter on the bucket/status field, or list conclusions directly and read them.

### A raised exception poisons the whole Postgres transaction -- "catch and continue" needs a SAVEPOINT

Postgres aborts the entire transaction on any raised exception, so catching an error from a call made
inside a transaction and continuing does **not** save the surrounding writes -- they are already lost,
and the code merely looks correct. In asyncpg the savepoint is a nested `connection.transaction()`:
opened while an outer transaction is active it sets `_nested = True` and issues `SAVEPOINT` / `ROLLBACK
TO` rather than `BEGIN` / `ROLLBACK`, so the outer transaction survives the inner raise.

This bit `record_dispatch_attempt` (bu-j65gq): the v2 attention producers are called inside the
attempt-insert transaction so the two commit together, but they raise `42501` unless
`current_setting('role')` is a canonical `butler_*_rw`, and outside hardened posture `db.py` fails open
with no `SET ROLE` at all. The producer's refusal took the attempt row with it.

Two things to copy when applying this pattern:

- **Absorb one error class, not all of them.** The fix catches only
  `asyncpg.InsufficientPrivilegeError`; every other producer failure still propagates, so a row whose
  edge failed for a real reason still rolls back with it. A blanket `except Exception` around the
  savepoint converts genuine failures into silent skips, which is a worse bug than the one being fixed.
- **Prove the commit from a separate pool acquisition.** Reading the row back on the same connection
  inside the same transaction shows intra-transaction visibility, not durability. The post-fix tests
  re-acquire before asserting.

The repo-wide sweep for other instances (bu-74pxv) found none: `record_dispatch_attempt` is the only
call site of this shape. Enumerate by the structural property, not by name -- grep the migration SQL
for `SECURITY DEFINER`, keep the definitions that raise `42501` (directly or through a gate helper),
then find every call site of each. Exactly four runtime functions raise `42501` on a role check:
`public.append_runtime_attention_model_breaker`, `public.append_runtime_attention_fleet_halt`,
`public.dashboard_turn_require_role` (reached from eleven `public.dashboard_turn_*` wrappers), and
`public.runtime_attention_active_switchboard_role` (an RLS predicate, never called from Python).
Every `dashboard_turn_*` call goes through `src/butlers/core/dashboard_turns.py`, and all but three
are pool-scoped, so a refusal aborts only its own implicit transaction. The three that do sit inside
`conn.transaction()` are in `_routing.py`'s durable dashboard acceptance, where the gated call *is*
the transaction's purpose: `claim_target` is the gate itself, and the later `mark_route_enqueued` /
`mark_terminal` gates are provably unreachable because `claim_target` already proved the same role.
A savepoint there would enqueue route work without a durable turn claim. Being inside a transaction
is necessary but not sufficient -- only a *best-effort* call attached to a primary write wants one.
### Two ways a poll loop reports "everything passed" when it actually saw nothing

Both of these bit the same CI-watch loop in one session, and both fail in the same direction: no data
is read as good news.

**`jq -e` on empty stdin exits 0.** `gh pr checks "$p" --json bucket | jq -e 'all(.bucket!="pending")'`
looks like a settled-ness test, but if the `gh` call produces no output, jq runs the filter zero times
and exits 0, so the loop concludes every check passed. Never let a predicate stand in for both "the
data says yes" and "there is no data". Count first, in the shell, and treat zero as not-settled:

```sh
n=$(printf '%s' "$s" | jq 'if type=="array" then length else 0 end' 2>/dev/null)
case "$n" in ''|*[!0-9]*) n=0 ;; esac
[ "$n" -eq 0 ] && { settled=n; echo "PR#$p: <no data this tick>"; continue; }
```

**The shell is zsh, and zsh does not word-split unquoted `$VAR`.** `PRS="3762 3763 3764"; for p in
$PRS` iterates ONCE with `p` set to the whole string, so `gh pr checks "3762 3763 3764"` fails and
returns nothing, which the trap above then launders into "all settled". Use a literal list (`for p in
3762 3763 3764`) or a real array (`prs=(3762 3763 3764); for p in "${prs[@]}"`). An earlier version of
the same loop worked purely because it happened to use a literal list.

The diagnosis only became possible after the empty-data branch printed a visible marker instead of
being swallowed: the marker read `PR#3762 3763 3764 3765: <no data>`, which named the word-splitting
bug directly. When a poll loop can see nothing, make it SAY it saw nothing.

### `gh pr merge --delete-branch` abandons BOTH branches when a worktree holds the local one

If a linked worktree still has the branch checked out, `gh pr merge --squash --delete-branch` merges
the PR, then fails the local delete with `cannot delete branch 'X' used by worktree at ...` -- and that
failure aborts the whole cleanup step, so the REMOTE branch is left behind too. The error names only
the local branch, which reads as if the remote half succeeded. It did not.

Observed side by side in one session: a PR whose worktree was removed before merging had its remote
branch deleted; a PR merged with the worktree still attached left `agent/<id>` on origin.

Remove the worktree first, then merge:

```sh
git worktree remove --force .worktrees/parallel-agents/<id>
gh pr merge <n> --squash --delete-branch
git branch -D agent/<id> 2>/dev/null   # usually already gone
```

If you merged in the wrong order, clean up explicitly and verify, since `--delete-branch` reported no
error for the remote: `git push origin --delete agent/<id>` then
`git ls-remote --heads origin agent/<id> | wc -l` should print 0.

### `find` here is bfs, not findutils: GNU relative timestamps fail AND exit 0

`find` on this machine resolves to **bfs**, which rejects GNU findutils' relative timestamp syntax:

```
find . -newermt '-30 minutes'
bfs: error: Invalid timestamp.  Supported timestamp formats are ISO 8601-like
```

Two things make this dangerous rather than merely annoying. The error goes to **stderr**, so the
common `2>/dev/null` idiom hides it completely. And bfs exits **0** on it, so `|| echo failed` and
`rc=$?` both report success. The result is a command that prints nothing and looks like a confident
negative answer: "no files changed in the last 30 minutes".

That misfired as a liveness probe for a dispatched worker. The empty output was read as "this worker
has not written a file in 45 minutes, it may be stalled", when the worker was in fact writing files
every few minutes and had pytest running. The probe never ran at all.

Use an ISO 8601 timestamp, which bfs accepts and findutils also accepts:

```sh
CUTOFF=$(date -Iseconds -d '30 minutes ago')   # 2026-08-23T14:29:36+08:00
find src tests -type f -newermt "$CUTOFF" -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort -r
```

For worker liveness specifically, prefer evidence that cannot silently return empty: `ls -lt` on the
directory (bfs is not involved), or `ps -eo pid,etime,args | grep <worktree path>`, which shows the
command and how long it has been running. Local time here is UTC+8, so `ls` timestamps and `date -u`
disagree by 8 hours; compare like with like.

### `tests/api/` is not DB-free, and an `--ignore` list built from belief will not make it so

`tests/api/` mixes mocked unit tests with real-Postgres integration tests. The integration ones are
marked `pytest.mark.integration` and gated only on `shutil.which("docker")` -- and docker IS on PATH
on this machine, so they **run** rather than skip. At least six live there today, all suffixed
`_db.py`: `test_issues_condition_ledger_db.py`, `test_relationship_entities_concentration_db.py`,
`test_relationship_entities_search_db.py`, `test_contacts_search_db.py`,
`test_qa_cases_session_doors_db.py`, `test_relationship_queue_dismissed_suppression_db.py`.

So `pytest tests/api/ --ignore=<a few files>` is not a DB-free scope unless every remaining file has
been checked. An agent that does not hold the serialized DB slot can collide with the holder without
either side seeing an error -- the damage shows up as an unrelated-looking failure in the OTHER
agent's run.

The only safe narrow scope is an explicit file list, each verified to have no `asyncpg`, `docker`, or
`create_migrated_test_db` reference:

```sh
grep -lE 'asyncpg|docker|create_migrated_test_db' tests/api/*.py   # exclude every hit
```

Corollary for the whole repo: the DB-slot rule is **structural, not path-based**. If a test touches a
real database it needs the slot, whatever the file is called and whatever directory it sits in.

### DDL durability is a different hazard from role refusal, and a SAVEPOINT does not fix it

Two failure modes look alike inside `async with conn.transaction():` and need opposite fixes.

*Role refusal* (a `SECURITY DEFINER` producer raising 42501) poisons the whole transaction, so
catch-and-continue needs a **savepoint** -- in asyncpg, a nested `connection.transaction()`.

*DDL durability* is not solved by a savepoint at all. A partition or table created inside a
transaction is **dropped by the rollback**, savepoint or not. If the surrounding transaction can
fail for any reason, the object never becomes durable and every later insert for that key fails in
a tight loop. The call has to leave the transaction entirely:

```python
await pool.execute("SELECT switchboard_message_inbox_ensure_partition($1)", received_at)  # on the POOL
async with conn.transaction():
    ...                                                                  # dedupe, advisory lock
```

Correct references already in the tree: `roster/switchboard/tools/ingestion/ingest.py` (carries a
long comment diagnosing exactly this) and `src/butlers/connectors/filtered_event_buffer.py`.

Testing it requires care, because the obvious test cannot fail. Reading the object back **on the
same connection** passes against the buggy code -- intra-transaction visibility is not durability.
Force the enclosing transaction to roll back, then verify on a **separate pool acquisition**:

```python
async with pool.acquire() as verify_conn:
    assert await verify_conn.fetchval(_PARTITION_ATTACHED_SQL, partition_name) is True
```

### `openspec validate` needs an explicit target

The bare form fails with `Nothing to validate` rather than validating everything, which reads like
a tooling error and invites the wrong workaround. Pass the change name:

```sh
uv run openspec validate <change-name> --strict
```

Remember what it does *not* prove: `openspec validate` and `scripts/check_spec_overwrites.py` read
no Python, so neither is evidence about an implementation, and `check_spec_overwrites.py` never
inspects `openspec/specs/**`.

### Two-dot `git diff origin/main` on an agent branch invents deletions

Reviewing a worker branch with `git diff origin/main` compares two *tips*, so every commit that
landed on `main` after the branch forked shows up as a **deletion by the branch**. On an
append-only file like this one that reads as sabotage: a branch forked at `2fb816d6e` showed
`AGENTS.md | 43 ------` purely because `main` had moved to `f9698f219`.

Before believing a diff, ask what it was positioned to tell you. To see what a branch actually did:

```sh
git show --stat --format= HEAD          # this commit only
git diff --stat origin/main...HEAD      # three dots: branch-side changes since the fork
git merge-base HEAD origin/main         # confirm how stale the fork point is
```

`git merge-tree --write-tree --name-only origin/main HEAD` still answers mergeability without
moving HEAD, and a clean probe here is a second signal the "deletion" is an artifact.

### The DB-touch grep is conservative in only one direction

`grep -lE 'asyncpg|docker|create_migrated_test_db' tests/api/*.py` is the right way to pick files
that must hold the serialized DB slot, but it over-matches: `tests/api/test_audit_log.py` hits it
while being fully mocked, because it imports `asyncpg.exceptions.UndefinedTableError` for an
`pytest.raises` and mentions asyncpg in docstrings. Excluding a false positive only costs
parallelism, so keep excluding on the grep — but when you need to *run* a matched file without the
slot, confirm by reading the hits rather than by the filename or the module name alone. The rule
stays structural: a real database connection needs the slot; an imported exception class does not.

### `public.audit_log` has three readers, and the wire chokepoint is the model

`GET /api/audit-log`, `GET /api/audit-log/{id}`, **and** `GET /api/issues/{key}/occurrences`
(`routers/issues.py::list_issue_occurrences`, which returns `PaginatedResponse[AuditLogEntry]`) all
publish rows from the same table. A per-route projection fix silently misses the occurrences
drill-down -- which is plausibly how credential free text survived three sibling content-blindness
fixes. Enforce on `AuditLogEntry` in `src/butlers/api/models/audit.py`; that covers all three
readers, direct construction, and any future one.

Two related traps in that area:

- The bead and spec text say `/api/audit`; the **real router prefix is `/api/audit-log`**, so
  grepping for the former finds nothing.
- `audit_grouping.py` renames the column mid-pipeline: `_AUDIT_NORMALIZED_CTE` aliases `ts AS
  created_at`, then `_OCCURRENCES_SELECT` re-aliases back to the `AuditLogEntry` shape. Grepping for
  `ts` in that file misleads.
- The `target` column is **never normalised on write**, so any predicate over it must accept the
  long-scope spellings (`user:`/`system:`/`cli:`) that `normalize_key_param` tolerates, not just
  `u:`/`s:`/`c:`.

### `secrets_v2.py` names its internal vs public detail types the opposite way per lane

`src/butlers/api/routers/secrets_v2.py` has three credential lanes, and the `...SecretDetail` /
`...CredentialDetail` naming does **not** mean the same thing in each:

| Lane | Internal (never on the wire) | Public payload | Projector |
| --- | --- | --- | --- |
| user | `_UserCredentialRecord` | `UserSecretDetail` | `_content_blind_detail` |
| system | `SystemSecretDetail` | `SystemCredentialDetail` | `_content_blind_system_detail` |
| cli | `CliRuntimeDetail` | `CliCredentialDetail` | `_content_blind_cli_detail` |

So `SystemSecretDetail` is internal but `UserSecretDetail` is public. A census that treats
`...SecretDetail` as "the internal type" flags `response_model=ApiResponse[UserSecretDetail]` on
`get_user_credential` and `rotate_user_credential` as unfixed leaks. They are not — both already
return `_content_blind_detail(...)`, and "fixing" the annotation breaks correct code.

Read the projector's **return type** (`def _content_blind_*(record: X) -> Y`) to learn which side of
the wire a name is on. The `-> Y` is the public one, every time; the name is not evidence.

### Screen a test file for the DB slot by what it *calls*, not what it imports

The serialized full-backend slot is for files that open a real connection. Confirm with

```sh
grep -nE 'docker|create_migrated_test_db|mark\.integration|asyncpg\.(connect|create_pool)' <file>
```

Across `tests/api/test_secrets*.py` this scores 19 files at 0 and exactly one — the
`_schema_drift_db.py` suffix — at 8. A bare `import asyncpg` for an exception class scores 0 and
correctly stays out of the slot. Feed pytest the surviving files as an **explicit path list**; an
`--ignore` list built from belief is not a DB-free scope.

### Never put the Claude session URL in a PR body here, and an edit will not clear it

`scripts/session_link_guard.py` (CI job `session-link-guard`, bu-mr5t5) fails a PR when a
`https://claude.ai/code/session_...` link appears in the PR **title, body, review comments, or
non-trailer commit text**. The *only* permitted place is an exact
`Claude-Session: https://claude.ai/code/session_...` line inside a terminal git commit-trailer block.

This directly contradicts the generic Claude Code instruction to end PR bodies with that URL. **The
repo rule wins**: commit trailer yes, PR body no.

Two follow-on traps once it has fired:

- `.github/workflows/ci.yml` uses a bare `pull_request:` trigger, which defaults to
  `opened, synchronize, reopened` — **not `edited`**. `gh pr edit --body-file` fixes the PR but
  fires nothing.
- `gh run rerun` replays the *same event payload*, so the job re-reads the stale body and fails
  again.

What actually works: `gh pr close` + `gh pr reopen` (a `reopened` event carries a fresh payload), or
push another commit (`synchronize`). Note `concurrency.cancel-in-progress: true` means either one
cancels the in-flight run and restarts every job, so expect to pay for `check` again.

Verify the fix locally before spending that:

```sh
gh pr view <N> --json title -q .title > /tmp/t.txt
gh pr view <N> --json body  -q .body  > /tmp/b.txt
echo '[]' > /tmp/rc.json
python3 scripts/session_link_guard.py --pr-title-file /tmp/t.txt --pr-body-file /tmp/b.txt \
  --commit-range "$(git rev-parse origin/main)..$(git rev-parse origin/<branch>)" \
  --review-comments-file /tmp/rc.json
```

Never point that scanner at a checked-out file tree — its self-match safety depends on only ever
seeing PR/commit/comment metadata.

### A quiet worktree is not evidence a dispatched worker died

Before re-dispatching onto an existing agent worktree, note what the obvious checks actually prove:

- `ListAgents` enumerates **peer sessions**, not your own in-process subagents. An empty listing is
  not evidence your worker is gone.
- `git log origin/main..HEAD` empty + `git status --short` empty means the worker has not *committed*
  yet. A worker that is mid-edit and has not yet saved, or that is between edits, looks identical to
  a worker that never started.

The authoritative check is the task list itself (`/tasks`, or the background-task IDs in a goal
check-in), and `TaskStop <worker-name>` before touching the tree.

Getting this wrong is expensive in a specific way: `git reset --hard origin/main` on a live worker's
worktree silently discards its tracked work-in-progress but leaves untracked files, so the worker
keeps running, re-creates edits under the new agent, and you end up with two agents committing to one
branch plus two competing `openspec/changes/<name>/` directories for the same bead. This happened on
`agent/bu-uqipv`; the second worker caught it only because files changed under it mid-lint.

When two agents have in fact overlapped, do not resolve it by trusting either side. Material from the
stopped agent is neither authoritative nor worthless: re-verify its factual claims against the code
yourself, and diff its abandoned openspec delta against the surviving one before deleting it —
"redundant" is the assumption most likely to be wrong.

A corollary that bites separately: **never run a test gate against a worktree a live worker owns.**
A suite executed while the worker is mid-edit imports a half-written module and fails in ways that
have nothing to do with the code under review. This produced 8 "failures" on `agent/bu-uqipv` that
became 12 passes minutes later, with no fix in between. Before reading any gate result as a verdict
on a branch, confirm the tree is settled: `git status --porcelain | wc -l` must be `0`, and it must
still be `0` when the run ends. A failure observed on an unsettled tree is not evidence of a
regression, and re-running is diagnosis, not denial.

### An absence assertion is the test most likely to be vacuous — mutate before believing it

Owner Option C work produces tests that assert material is **absent** from a payload. That is the
right shape (never reproduce the material to compare against it), but it is also the shape that
passes when the fixture never planted anything, when the query returned nothing, and when the fix is
not wired up at all. A green run is exactly what a vacuous suite reports.

Before trusting one, neutralise the fix — e.g. edit the CTE predicate so the branch can never fire —
re-run the module, restore from a byte copy, and confirm `git status` is clean and the suite green
again. On `agent/bu-uqipv` this turned "12 passed" into evidence: **8 failed, 4 passed** with the fix
removed, and the failure output reproduced the original bug exactly.

Expect some tests to pass under mutation *correctly* — they guard properties the mutation does not
disturb. Check each one rather than assuming it is dead weight: the audit occurrences sentinel there
survives because `bu-ove06`'s row-level withholding already blanks the rows and the drill-down never
echoes the group title, so it pins a different property than the fix under test. Also give every
absence test a positive companion (`meta.total == 1`, a planted sentinel it *does* find) so it cannot
pass by returning nothing.

Related trap when auditing what leaked: a composed display field can carry the text even when the
obvious field does not. `audit_grouping.py:435` builds `Issue.type` as
`audit_error_group:{_slug(error_message)}`, so the summary rides out in the *type label*; the
similar-looking `group_key` (:394) is a `sha256[:16]` digest and never was a vector. Read the
construction, not the assertion diff.

### `scripts/emit_worker_report.py` deadlocks any worker that must not push

The script hard-requires a `Branch-Pushed: yes` header for every `completed-*` status. Under the
beads-coordinator protocol the coordinator is the sole mutation authority and dispatched workers are
forbidden to push, so a worker following its contract can never produce a valid report: the script
rejects it, the worker has no other reporting channel wired up, and it goes **silent** rather than
erroring visibly. From the coordinator's side this is indistinguishable from a worker that hung.

Until that is fixed (`bu-1ajs6`), dispatch prompts must tell workers to skip the script and report
via `SendMessage` instead. Four workers were lost to this before it was diagnosed, each one looking
like a different incident.

### httpx logs every request URL at INFO, so a query parameter is a log leak

`httpx/_client.py:117` binds `logger = logging.getLogger("httpx")`, and `_send_single_request`
(:1025) emits `'HTTP Request: %s %s "%s %d %s"'` with `request.url` — the **full** URL, query string
included — at `INFO`. Your own code never has to log anything for the value to reach a handler.

This matters for any route that carries an identifier in the query string under a promise that it
stays out of logs. `GET /_control/runtime-probe/v1/readiness?kid=<kid>` is the live example: an
absence-sentinel test caught the `kid` in `caplog` on first run, from the library, not from us.

The reference fix is in `src/butlers/core/runtime_probe_control/client.py` — a `logging.Filter` on
the `httpx` logger whose predicate names the private path:

```python
def filter(self, record: logging.LogRecord) -> bool:
    return READINESS_PATH not in record.getMessage()
```

`record.getMessage()` renders the %-args, so it sees the interpolated URL. A filter on the logger
runs **before propagation**, so no handler anywhere sees the record — unlike a handler-level filter.
Naming a specific private path keeps the blast radius to one route; do not filter the `httpx` logger
broadly or drop its level, which would blind anyone debugging an unrelated request.

Prefer fixing this over relaxing the sentinel test: the test was right.

### `pg_dump` as the shared migration login dumps nothing, not less

`pg_dump` takes `LOCK TABLE ... IN ACCESS SHARE MODE` over *every* relation in scope before it
emits a single byte. One `permission denied` in that lock sweep aborts the whole run, so a role
that cannot read one fenced table produces **zero output** — not a smaller dump. The dashboard
then reports "no backup," which reads like a cron failure and sends you looking in the wrong
place. Three separate fence mechanisms in `scripts/init-db.sql` can do this, and each is
enumerable as the dump role before you guess:

```sql
has_schema_privilege(current_user, nspname, 'USAGE')          -- revoked USAGE
has_table_privilege(current_user, oid, 'SELECT')              -- revoked SELECT
relrowsecurity AND (relforcerowsecurity OR relowner <> current_user)  -- forced RLS
```

Derive the exclusion list from those three predicates, not from whichever table happened to
appear in the last error message — `pg_dump` only ever names the first one it hits, so fixing by
symptom takes as many runs as there are fenced objects.

Do **not** reach for `--enable-row-security` to get past the RLS fence. It does not bypass RLS; it
dumps *the rows the role can see*, producing a backup that looks complete and is not. Excluding a
fenced object is the correct answer rather than a workaround because the fence encodes ownership:
the object's owner role is the natural home of its own export path, and widening the general
backup login to reach it dissolves the fence permanently for the sake of one nightly job.

Related trap in the same file: the shebang is `#!/bin/sh`, and `set -o pipefail` is **not POSIX**.
`dash -c 'set -o pipefail'` → `dash: 1: set: Illegal option -o pipefail`, i.e. the script dies on
line 1 under Debian/Ubuntu `/bin/sh`. It only ever worked because the backup sidecar is Alpine
(`ash`). Any `#!/bin/sh` script here that needs pipeline failure detection should record the
left-hand exit status in a status file instead.

### A check can only answer the question it is positioned to ask

`GET /api/system/backups` was credited with "are backups healthy" while it only
ever measured the newest artifact's age. `deploy/backup/pg_dump.sh` refuses to
publish a bad dump, so a failed run leaves the directory byte-identical to what
it was before — freshness stays green for the full 36h window and the first
alarm arrives late, as staleness of an unrelated file, with no reason attached
(bu-xrqyu, the same shape as bu-e1410 one layer down).

The fix pattern, when a producer's failure destroys nothing: have the run record
its own outcome, and read that instead of inferring it from the artifact.

- Write the receipt from the `EXIT` trap, not from the success path and not from
  each enumerated failure branch. A trap fires on every exit, including the
  `set -e` abort nobody listed, so no route out of the script can skip it.
- Absence must be its own value. `deploy/backup/pg_dump.sh` writes
  `BACKUP_DIR/last_run.json`; a missing or unparseable receipt surfaces as
  `last_run.result == "unknown"`, never folded into `"success"` — an older
  deployment and a first run both produce no evidence, and that is not evidence
  of a passing run.
- Keep the receipt on a path that survives what it reports: a file in the
  directory the dashboard already reads, not a database row the Alpine backup
  sidecar would have to connect out for. A signal that dies with the database
  cannot report a database failure.
- Anything reading such a receipt back is a boundary: the `reason` field is a
  fixed vocabulary in both the script and
  `src/butlers/core/backup_facts.py` (`_BACKUP_RUN_REASONS`), and an
  unrecognized value is reported as unrecognized rather than rendered verbatim
  off a mounted volume. `tests/scripts/test_pg_dump_run_sentinel.py` pins the
  two ends together by parsing the real script's output with the real reader.

Host BusyBox is not Alpine BusyBox: Ubuntu's `busybox find` has no `-delete`, so
running this script end-to-end under `busybox ash` locally fails in the prune
step on a build difference, not on the script. Parse-check under `busybox ash -n`
locally; execute in the real `postgres:17-alpine` image (that path is docker-
gated in `tests/scripts/test_pg_dump_backup.py`).

### A dispatched worker's shell starts in the repo root, not in its worktree

Agent tooling inherits the *session's* working directory, and this session's is the main repo root
(`/home/tze/GitHub/butlers`) — not the `WORKTREE_PATH` the worker was handed. A worker that writes
its first file without an explicit `cd` creates it in the root instead of the branch, which is how
`src/butlers/oauth_token_payload.py` briefly appeared on `main`'s worktree during bu-n8gvq. The
file is untracked, so nothing fails loudly; it just silently is not part of the branch, and the
commit that "adds" it adds nothing.

Prefix every command with an absolute `cd <worktree>`, and before handing back, check the root:

```bash
git -C /home/tze/GitHub/butlers status --porcelain   # must be empty
git -C /home/tze/GitHub/butlers branch --show-current # must be main
```

Dispatch prompts should say this outright — it is not discoverable from inside the worker, because
a relative path resolves fine and the file lands somewhere plausible.


### Orphaned testcontainers (do not write an age-based reaper)

`docker ps --filter ancestor=pgvector/pgvector:pg17` showing many-day-old containers is a **leak**,
not normal: DB tests start one pgvector container per pytest *process* (addopts carry `-n 3 --dist
loadfile`), and a run SIGKILLed mid-flight never reaches teardown.

A run that *reaches* teardown leaks nothing — verified, not assumed: a DB-backed suite run to
completion left the host container count at 12 before and 12 after. The leak is confined to killed
runs, which is the case a process cannot handle on its own, and is why the fix belongs in a sidecar
rather than in teardown. Do not generalise that count delta into a leak detector, though: concurrent
sessions legitimately raise the count mid-run, and a count that returns to baseline can still hide a
leak a peer's Ryuk cleaned up meanwhile. The rule below counts nothing, so it stays correct under
concurrency.

**Ryuk is enabled for local runs and you should not "re-enable" it or replace it.** There is no
`~/.testcontainers.properties` and no `TESTCONTAINERS_RYUK_DISABLED` in the shell, so
`config.ryuk_disabled` falls through to its `False` default; every local run starts
`testcontainers-ryuk-<SESSION_ID>`, which holds a TCP socket and reaps by session-id label when the
socket drops (a SIGKILL closes it via the kernel). Note the exception before you grep and conclude
otherwise: CI **does** set `TESTCONTAINERS_RYUK_DISABLED: "true"` (`.github/workflows/ci.yml` in the
`check` job's smoke and integration steps, and `nightly.yml`). That is fine there and cannot leak
here, because those jobs are `runs-on: ubuntu-latest` — throwaway VMs. The `gha-runner-*` containers
on this box belong to a different project, not to butlers CI.

Ryuk's one real gap: it runs with `auto_remove=True` (`testcontainers/core/container.py:348`) and
`Reaper.delete_instance()` (:326) is defined but **called from nowhere** in testcontainers-python
4.14.2 or in this repo. A Ryuk that dies before its containers do vanishes without a trace or a
second chance, and the pgvector containers it guarded (`AutoRemove=false`, `RestartPolicy=no`)
survive indefinitely. Why Ryuk died in a given case is not recoverable after the fact, for exactly
that reason — do not expect to find out from logs.

**Never key cleanup on age.** The oldest orphan can be a live investigation: on this box
`codex-pr3708-acl-repro-11668` runs the *same pgvector image* and is older than four of the leaks.
Ownership is exact instead. A container is provably unowned only if **all** hold:

1. `org.testcontainers=true` **and** a non-empty `org.testcontainers.session-id` — only the library
   stamps these; a hand-run container carries `{}` labels and is excluded by this predicate alone.
2. No `com.docker.compose.*` label (spares `butlers-dev-*`, `property_agent-postgres-1`,
   `gha-runner-*`).
3. No `dev.butlers.keep` label — the documented human pin, honoured for any value.
4. Name matches Docker's generator shape `^[a-z]+(_[a-z]+){1,2}$`; digits and hyphens never occur
   there, so a human-authored name reads as human on its face.
5. **No running `testcontainers-ryuk-<session-id>` for that session id.** Load-bearing: a live pytest
   session always has a live Ryuk, so a missing Ryuk is positive evidence the owner is dead rather
   than an inference from elapsed time.
6. Older than `--min-age-hours` (default 4, past the ~40 min full gate). Backstop only — it covers
   the one case where (5) can lie, which is a run launched with `TESTCONTAINERS_RYUK_DISABLED=true`.
   That is not hypothetical: it is what CI does, so anyone copying the CI env into a local run
   defeats (5) and leaves (6) as the only guard.

Sweep with `python3 scripts/reap_orphaned_testcontainers.py` (report-only; `--reap` to remove,
`--json` for per-container reasons). Safe for an agent to run unattended: the predicates are
conjunctive and every failure mode — missing label, unparseable timestamp, a `docker` call that
errors — resolves to "not reapable", so the script's way of being wrong is to leave an orphan
running, never to kill a live one. Pin anything you want kept:
`docker run --name my-repro --label dev.butlers.keep=<bead> ...`. Full rationale:
`docs/testing/orphaned-testcontainers.md` (bu-3zu5l).
### An unconfigured optional connector must park, not crashloop

Credentials the owner supplies at *runtime* through the dashboard (DB-stored OAuth) are absent on
every fresh deploy, so raising out of `start()` turns "not set up yet" into an infinite Docker
restart loop that burns a container slot and makes a genuinely broken connector indistinguishable
from an unconfigured one. The fleet convention is a **sentinel endpoint identity plus a degraded
heartbeat**, not an exit: `google_health:degraded` (`_ensure_degraded_heartbeat_running`),
`steam:no_accounts`, `spotify:unconfigured`, and the Gmail/Calendar/Drive managers' `no qualifying
accounts found at startup. Running in idle/degraded mode`. Keep `_endpoint_identity` itself empty
while parked so no envelope or checkpoint is attributed to the sentinel; only the metrics/policy/
heartbeat *labels* use it.

Two boundaries that are easy to get wrong in both directions (bu-5m67e):

- Do **not** widen the carve-out to all credential errors. Only "never connected" is non-fatal —
  give it its own exception subclass (`SpotifyCredentialsUnconfiguredError`) so every
  post-configuration fault keeps the base class and stays loud (`error` state, ERROR log).
  Swallowing the class, or exiting 0 quietly, is the same defect pointed the other way.
- Env-var-driven connectors (telegram, discord, whatsapp, activitywatch) validate in
  `Config.from_env()` and are a *deployment* misconfiguration, not an unconfigured account —
  that crash is correct and out of scope for this pattern.
- The four fast CI guards each have a much narrower predicate than their names suggest — do not send anyone chasing hits outside it. (1) `scripts/check-no-em-dashes.py` globs ONLY `about/heart-and-soul/**/*.md`, `about/lay-and-land/**/*.md`, `about/craft-and-care/**/*.md`, `roster/*/MANIFESTO.md`, `roster/*/AGENTS.md` (`DEFAULT_GLOBS`, :56). No Python, TypeScript, SQL, `docs/**`, or this root `AGENTS.md` is scanned; `frontend/src/**` already carries dozens of em dashes in comments. (2) `scripts/session_link_guard.py` fails CI on any `claude.ai/code/session_...` URL in a PR title, PR body, or review comment; the sole exemption is an exact `Claude-Session: <url>` line in a terminal commit-trailer block, case-sensitive, and it does NOT extend to PR surfaces — so the default "end PR bodies with the session link" habit fails CI every time. Always `grep -c "claude.ai" <body-file>` before `gh pr create`; the after-the-fact fix is `gh pr edit <n> --body-file <f>` plus a guard re-run. (3) `spec-overwrite-guard` (`check_spec_overwrites.py`) inspects `openspec/changes/` ONLY and reads `## MODIFIED` sections only — a green run says NOTHING about a direct edit to an `openspec/specs/**` baseline, and a `## REMOVED` block deleting a whole baseline requirement is invisible to it. Nothing verifies that a remove/add pair restores what it removed; diff the two blocks yourself. (4) `cited-requirements-guard` (`scripts/check_cited_requirements_resolve.py`, bu-lpwjc) reads REQ ids out of TEST files only (`tests/**` plus `roster/*/tests/**`) and only in the qualified `REQ-<capability>-<NNN>` shape — `src/`, `roster/` implementation modules, and change-local shorthand like `REQ-005` are all outside it.
- Direct edits to `openspec/specs/**` baselines are de facto repo practice, not a violation: 11 of the 12 most recent commits touching `openspec/specs/` on main did NOT arrive via `openspec archive`. Treat a "never hand-edit a baseline" rule as a default to justify departing from (drift correction with the diff read line-by-line is a good reason; silent scope expansion is not), rather than a hard gate. Also: no CI job runs `openspec validate` at all — `make check` does not call it, and only the two guards above touch specs. Spec validity is enforced by authors. The npm package is `@fission-ai/openspec` (`npx openspec@1.9.0` fails `notarget`); use the installed global `openspec` binary. Debug parser output with `openspec change show <id> --json --deltas-only`.
- A `check_spec_overwrites.py` "MODIFIED ... has no baseline requirement to overwrite" note is NOT benign — verified 2026-08-23, all four then-current instances abort `openspec archive`. Two distinct mechanisms: "target spec does not exist; only ADDED requirements are allowed for new specs" (the whole spec file is absent), and a missing-scenario abort on a DIFFERENT spec that fires before archive reaches the flagged block (over-determined — fixing the flagged block alone does not help).
- `gh pr checks <n>` can report a STALE rollup conclusion over an in-flight rerun, showing `fail` for a job that is actually re-running. Confirm with `gh run view <run-id> --json status,conclusion` before diagnosing a failure; this misled a coordinator three times in one session. Related and separate: `gh pr checks` exits nonzero (8) while checks are merely pending, and `gh` intermittently returns transient `HTTP 401: Requires authentication` even when `gh auth status` is healthy — poll loops must tolerate both.
- `git merge-tree --write-tree` reporting a clean merge does NOT mean CI will pass: GitHub tests the MERGE RESULT, so a branch that is textually conflict-free can still fail on semantic drift from main. Rebase onto fresh `origin/main` before trusting any local test run as evidence for merge.
- There are two schema sources and they drift: `scripts/init-db.sql` (fresh-bootstrap) and `alembic/versions/core/` (incremental). `public.audit_log` is created by alembic ONLY — init-db.sql merely references it. Worse, at least six tests hand-roll their own `audit_log` DDL inline; adding a column via migration therefore breaks them one file at a time with `asyncpg.exceptions.UndefinedColumnError`, which endpoints usually convert to a 503 so the test fails on the WRONG symptom (an unrelated `Expected regex` mismatch). When mirroring a migration into such a fixture, copy the CHECK constraint too, not just the column — the column alone leaves the fixture LAXER than production and the round-trip then accepts values the real table rejects. Note that some of those fixtures deliberately model PRE-migration schemas (`test_backfill_dashboard_audit_log.py`, `test_audit_log_failed_outcome_backfill_migration.py`, `test_audit_log_metadata_repair_migration.py`); do not "fix" those.
- No agent in this repo can run a frontend test: `frontend/node_modules` in the repo root is an EMPTY directory (no `.bin/vitest`) and worktrees have none at all, while owner decision bu-87osw forbids `npm install`/`npm ci` and `scripts/setup_worktree.sh`. Consequence: any change touching `frontend/src/**` that shifts rendered DOM will fail the `frontend` job on a stale vitest snapshot and CANNOT be fixed by the fleet — the snapshot cannot be regenerated. Hand-editing a `.snap` is not a substitute when the diff spans more than a literal (removed text fragment + changed `gridTemplateColumns` string + removed cells inside one serialized full-page DOM). Escalate to the owner rather than burning cycles.
- Patching testcontainers teardown (`conftest.py`): patch exactly ONE layer. `DockerContainer.stop` must be assigned once — a second assignment silently shadows the first, and a "retry" wrapper around an already-retrying inner call multiplies attempts. Retry `container.remove()`, not `stop()`; the transient failures are 404 "no such container"/"removal of container ... is already in progress" (409) and read timeouts, so gating the retry on HTTP 500 guarantees the tolerance can never fire — a check credited with an answer it was never positioned to give. Swallow the final failure with a `RuntimeWarning` rather than failing the session on teardown, but fail fast on non-transient errors. Match markers anywhere in the exception chain including docker-py's `explanation`. Measured: the full backend gate is ~21m31s uncontended (the documented ~40min figure assumes contention).
- The single most common defect found across a long fleet session (~85 instances) has one shape: **a check credited with an answer it was never positioned to give.** Canonical forms — a retry predicate gated on a status code its own tolerated errors never carry; a test name advertising coverage of a guard whose regex was swapped to something else; a hand-rolled fixture laxer than the production table it stands in for; a snapshot recorded against mock data asserting a sentence the production path never renders (i.e. an artifact of the very bug being fixed); a "same input produces same fingerprint" test fed the identical string and calling it "equivalent". When reviewing, ask of every assertion: could this have failed for the reason its name claims?
- CI throughput ceiling: ~8 concurrent runs saturate the runners and `check` alone is 20-40 min, so holding 7 PRs open at once is self-inflicted congestion. Sequence merges that append to the same file (root `AGENTS.md` EOF especially) rather than racing them, and verify after each merge that the note actually survived onto main.
- **`openspec validate --strict` with no target is a NO-OP** — it prints "Nothing to validate" and exits 0. Verified 2026-08-23. The working forms are `openspec validate <change-id> --strict` (one change) and `openspec validate --changes --strict` (all of them). This matters beyond typing: dozens of `tasks.md` checkboxes across the corpus are ticked `[x] Run openspec validate --strict on the changed specs`, and any that were run in the bare form validated nothing. Treat a green tick on that line as unverified. Even the working form is blind to two classes that surface only at `openspec archive`: (a) a `## MODIFIED` block whose target baseline spec FILE does not exist ("only ADDED requirements are allowed for new specs") — both such changes validated clean while aborting at archive; (b) baseline requirements lacking RFC-2119 prose in a spec the change does not touch, because archive revalidates each REBUILT spec in full. So a strict-validation failure count is a floor, not the true count.
- `openspec archive` MUTATES the tree, so reproduce archive failures in a scratch copy (`cp -r openspec /tmp/.../scratch/`), never in a worktree. **Being in `openspec/changes/archive/` does NOT mean a change's requirements are in `openspec/specs/`.** Many "archives" were hand `git mv`s with an archive-sounding commit message: `22fcd4e42` moved nine deltas as 13 pure renames and wrote exactly 2 lines to `openspec/specs/` (two cosmetic header fixes). Measured 2026-08-23 over all 146 archived changes carrying ADDED requirements: **104 applied, 31 partial, 11 fully unapplied, 498 requirements missing in total** — worst offenders `education-butler` (87/87), `2026-05-19-redesign-ingestion-dispatch-console` (44/44), `2026-04-25-whatsapp-connector` (40/40), `2026-06-12-entity-v3-lifecycle-and-depth` (43/44), `2026-02-24-alpha-release-mvp` (121/476). `scripts/check_archived_requirements_landed.py` now detects this (bu-966by, CI job `archived-requirements-guard`): it asserts every archived `## ADDED` requirement exists by name in `openspec/specs/<capability>/spec.md` and that each `## MODIFIED` block's content reached the baseline, reporting **per requirement** so a half-applied archive cannot look green. A later change can legitimately excuse an absence, but the two hatches differ: a `## RENAMED` block counts from ANY change, archived or not (it only redirects the baseline lookup, so an absent new name still fires), while a `## REMOVED` block counts only from an ARCHIVED change — a removal is an unconditional skip, so honouring a pending one lets an abandoned proposal mute a real gap with no ratchet entry and no JSON diff for a reviewer to see. Its own count is 488 missing ADDED across 44 changes (1070 units across 669 requirements once unapplied MODIFIED content is included) — lower than the 498 above because 23 are excused by a recorded `## REMOVED`. `scripts/archived-requirements-baseline.json` freezes that debt for bu-tk618; entries come out **by hand** and there is deliberately no `--update-baseline` flag. Nothing else detects it: `check_spec_overwrites.py` inspects `openspec/changes/**` only, and `openspec validate --changes --strict` passes on a change whose 44 requirements are all missing from the baseline, because it validates delta syntax, not application. To check one capability, extract the `### Requirement:` headers under `## ADDED` from the archived delta and grep each in `openspec/specs/<capability>/spec.md`; `git log -- openspec/specs/<capability>` returning empty means the capability never landed at all and any later `## MODIFIED` block against it is a victim, not the bug. A capability file EXISTING proves nothing — the four "landed" capabilities of that 2026-05-19 change pre-existed from unrelated manual sync commits and carry none of its 44 requirements.
- The archive-time validation of a REBUILT spec has two severities and only one blocks. RFC-2119: a requirement with **no prose at all** (header straight to `#### Scenario:`) is a hard `✗ Requirement "<name>" must contain SHALL or MUST`; a requirement whose prose merely lacks the keyword is a non-blocking `⚠ should contain`. Measured 2026-08-24 on `connector-gmail`: 18 requirements, 15 warnings, 3 errors — only the three prose-less ones abort, so a long ⚠ list above the ✗ lines is noise. Scenarios: a requirement with **zero scenarios** aborts with `✗ Requirement must have at least one scenario`, and **that message does not name the requirement** — find it by grepping the rebuilt spec for a `### Requirement:` header with no `####` heading of ANY kind before the next `###`. Grepping for a missing `#### Scenario:` specifically OVER-REPORTS: the counter accepts any `####` child, so `entity-identity`'s "Entities table in public schema" — whose only child is `#### Schema` — validates `✓` and is a false positive. Never silence the real error by adding a non-scenario heading. Archive also stops at the FIRST failing spec, so fixing one spec can reveal a second (observed: `connector-gmail` masked `module-email` entirely). Repair path is a change of its own whose `## MODIFIED` blocks carry every existing scenario verbatim and insert the prose; adding a scenario to a scenario-less requirement is legal because the missing-scenario rule only forbids DROPPING baseline scenario names. Confirm with `check_spec_overwrites.py`: a verbatim carry leaves the "MODIFIED requirement(s) with debt" count unchanged.
- **`openspec validate --specs --strict` DOES detect the archive-blocking baseline defects** — only `--changes` is blind to them. Use `openspec validate --specs --strict --json` and filter `items[].issues[].level == "ERROR"`; the human summary over-reports badly, because `--strict` marks a spec failed for WARNING-only issues (measured 2026-08-24: **36 specs "failed", but only 13 carried a hard ERROR**, 50 errors in total). Per-spec form is `openspec validate <spec> --type spec --strict`, but one bulk `--json` run beats 182 invocations. Two traps in the ERROR set itself. (1) **The scenario-count check accepts ANY `####` heading, not just `#### Scenario:`** — appending a bare `#### Notes` to a scenario-less requirement clears the hard error outright, leaving only the RFC-2119 warning. So `entity-identity`'s schema requirement, which has a `#### Schema` block and zero scenarios, archives cleanly; grepping for "no `#### Scenario:`" over-reports, and the shape that actually blocks is "no `####` heading of any kind". Never exploit this to silence the error — a heading that is not a scenario leaves the requirement untested. (2) A failed archive writes nothing (`Aborted. No files were changed.`), so ONE scratch copy can probe many specs in sequence: generate a change per spec whose `## MODIFIED` block reproduces one existing requirement byte-for-byte, archive each, and a non-zero exit proves that spec is unarchivable by any change that touches it.
- **Nothing used to check that a REQ id cited by a test resolves to anything.** Measured 2026-08-24 (bu-lpwjc): 39 distinct ids cited across the test tree, **30 resolve only to a change that has not archived yet**, 8 to `openspec/specs/`, and `REQ-cli-runtime-auth-003` (tests/dashboard/test_briefing.py) to nothing at all — there is no `cli-runtime-auth` capability. Note how few ids the baseline carries: only **10** `ID: REQ-` lines exist in all of `openspec/specs/`, so "the capability spec exists" says nothing about whether a given id does. `scripts/check_cited_requirements_resolve.py` (CI job `cited-requirements-guard`) closes this. Its one non-obvious rule: `openspec/changes/archive/**` is **not** a definition source, only `openspec/specs/**` and *unarchived* changes are. Reading `openspec/changes/**` flat would make it permanently blind to `openspec archive --skip-specs` (delta moves under `archive/`, baseline gains nothing, citation still "resolves") and would hide a half-applied archive. Pending-only citations pass but are printed grouped by the change they lean on — that list is what breaks if a change is dropped. Ratchet is `scripts/cited-requirements-baseline.json`, keyed per `(test file, id)` so freezing one file does not license the same id elsewhere; no `--update-baseline` flag on purpose.
- A third category of orphan `## MODIFIED` block exists beyond "genuinely new" and "lost rename": **pending-parent lineage**, where the target capability is created by an unarchived SIBLING change. The block is correctly labelled MODIFIED and archives cleanly once the parent lands; relabelling it ADDED provably aborts with `ADDED failed for header ... - already exists` in one archive order or the other. Since bu-9w5eu, `collect()` resolves such a block against its sibling's `## ADDED` block, so these no longer produce a note and ARE body-compared; before that fix a lookup miss skipped the comparison entirely, forgoing the check the gate exists to perform. A note now means no baseline AND no sibling ADDs it. Where two unarchived changes ADD one requirement, the block is compared against every candidate (only one can ever archive, and which is not encoded), and the note names the rivals. Do not "fix" a pending-parent block to silence the note; fix the archive order.
- `openspec archive`'s incomplete-task gate is **silently absent** for a change whose `tasks.md` uses `### N. Title` headings instead of `- [ ]` checkboxes. Verified 2026-08-24: archiving `generalize-owner-condition-ledger` printed `Task status: 13/17 tasks` and warned about 4 incomplete tasks, while `commitment-lifecycle` — whose `tasks.md` is fully populated with numbered heading sections and per-task `Acceptance:` bullets — printed `Task status: No tasks` and archived with no prompt at all. The heading style is not rare in this corpus, so "archived without a task warning" is not evidence the tasks were done. Related: `--skip-specs` archives a change WITHOUT applying its deltas to the baseline, which is almost never what you want when probing an archive — it exits 0 and proves nothing. `scripts/check_countable_tasks.py` (CI job `countable-tasks-guard`, `make check-countable-tasks`) now fails on any unarchived change whose `tasks.md` would print `Task status: No tasks` — heading-only or absent — mirroring OpenSpec 1.9.0's `TASK_LINE_PATTERN` exactly (`^\s*[-*]\s*\[([\sxX])\]`, so `*` bullets, `-[x]` and a tab inside the box all count, while `> - [ ]` and a mid-line box do not). `--include-archived` prints the read-only archived tally: 5 of 176 archived changes went in reporting `No tasks`, all of them missing `tasks.md` entirely. The four heading-style active files were converted to numbered `- [ ]` lines under their existing headings, acceptance bullets untouched (bu-h7igs).
- `switchboard.connector_registry` has **two independent producers** and the row shape alone does not tell you which one wrote it: the `connector.heartbeat` MCP tool (`roster/switchboard/tools/connector/heartbeat.py`) writes one row per executable connector process, and `cursor_store.save_cursor` (`src/butlers/connectors/cursor_store.py`) upserts one row per persisted checkpoint on the same `(connector_type, endpoint_identity)` PK. Checkpoint rows never heartbeat, so `derive_liveness(NULL)` reported them OFFLINE and they were counted as connectors. Since sw_031 the role is **persisted, not inferred**: `operational_role` (`runtime_instance` | `checkpoint` | `unknown`, CHECK-constrained) plus `parent_endpoint_identity`, with `butlers.connectors.registry_roles` as the shared vocabulary. Rules when touching this table: only `runtime_instance` rows carry liveness/health authority; heartbeat promotion is one-way and unconditional, `save_cursor` stamps `checkpoint` on INSERT only (its `ON CONFLICT` branch must never write the role, or a re-cursored runtime instance would be demoted); never derive the role from an identity string — only the human-facing *label* may parse the identity suffix; unclaimed rows stay `unknown` and must surface as a named `unclassified` state, never guessed into healthy or offline. Any new writer to `connector_registry` has to declare its role explicitly, and any new consumer must filter by role before rolling anything up.
- Compose has two mount idioms for deployment-provisioned files and picking the wrong one is a boot-time footgun. Real credentials go in top-level `secrets:` (service block adds `mode: 0400`); non-credential material goes in `configs:` with an absolute `target:`. The `docker-compose.restore-drill.yml` precedent uses `${VAR:?...}`, which makes `docker compose up` fail hard on an unprovisioned machine — correct for an opt-in overlay, wrong for `docker-compose.yml` itself, where it converts the canonical launcher into a two-stage procedure for everyone. The pattern that keeps a single-stage launcher AND fails closed is `${VAR:-./deploy/<thing>-unprovisioned.json}` pointing at a TRACKED inert placeholder that the real parser rejects: the stack boots, the feature stays unavailable. Two corollaries. (1) "This service never receives the private key" is assertable as `"secrets" not in service` — a much stronger test than checking that a particular source name is absent, since it also catches a future unrelated secret. (2) Adding a second `secrets:`/`configs:`/`depends_on:` key to a service block that already has one is silently swallowed by YAML last-key-wins, and every compose test using `yaml.safe_load` will read the surviving key and pass — so a compose edit is not verified until a duplicate-key scan runs over each service mapping. This bit a mutation check: an injected `depends_on: butlers-up` cycle went undetected until the edit was merged into the EXISTING `depends_on` block.
- **`ruff` cannot see a duplicate module-level name when the first definition is used in between.** F811 fires only on redefinition of an *unused* name, and a merge of two branches that each added the same helper to one file always has uses in between, so `ruff check` stays green while the later definition silently shadows the earlier one for every caller (bu-ayrbg: a security guard handed a `str` instead of `list[str]` iterated its characters and returned `[]` for every input). Per-PR CI is blind to it too, since each branch is green alone. `scripts/check_duplicate_toplevel_names.py` (CI job `duplicate-name-guard`, `make check-duplicate-names`) is the gate: it reads `ast.Module.body` only, so `if TYPE_CHECKING:` / `try: import` / version-gated rebinding is out of scope by construction, `@overload` stubs and `_` are exempt, and imports are not counted. Its ratchet ships empty and has no `--update-baseline` flag.
- `roster/finance/tools/facts.py::_TRANSACTION_PREDICATES` **must stay a `list`**: `list_distinct_merchants` interpolates it with `!r` into `ARRAY{...}::text[]`, and a tuple's repr renders `ARRAY(...)`, a Postgres syntax error that fails 9 tests in `roster/finance/tests/test_facts.py::TestListDistinctMerchants`.
- **A pre-merge union gate must prove the merge actually applied before it judges the result.** A local "merge main + PR, then run every repo-wide guard" script reported UNION-OK for a merge that never happened: the branch was `agent/fix-secrets-probe-state` and it was invoked with `fix-secrets-probe-state`, so `git fetch origin <branch>` failed with `couldn't find remote ref`, the silenced `git merge` failed, no conflict files appeared, and all six guards then ran against plain `origin/main` and passed. A gate that cannot distinguish "the PR is clean" from "the PR was never applied" is not a gate. Hard-fail on fetch failure, hard-fail when the merge produces no change against the base, and print the SHAs actually merged so the OK line carries its own evidence.
- **A CONFLICTING PR runs zero CI, so its rollup is frozen at the pre-conflict head and still reads green.** When main moves under an open PR, GitHub marks it CONFLICTING and cancels/skips new runs — `gh pr view --json statusCheckRollup` keeps returning the last full 11/11 SUCCESS from the head before the conflict. A settle-monitor that only scans for failures called such a PR SETTLED-GREEN mid-rebase. Never settle a PR that is not `MERGEABLE`, and separately flag `n < 11` as SETTLED-INCOMPLETE: an **absent** required check is invisible to a fail-scan, because there is no non-SUCCESS conclusion to find. (The 11 baseline: `check`, `frontend`, `frontend-e2e`, `em-dash-guard`, `session-link-guard`, `spec-overwrite-guard`, `archived-requirements-guard`, `countable-tasks-guard`, `frontend-copy-inventory-guard`, `cited-requirements-guard`, `duplicate-name-guard`.)
- **A change to a generator invalidates every PR queued behind it.** #3854 changed `scripts/extract-frontend-copy.py` itself, so every open branch carrying an inventory generated by the OLD generator was green on its own CI and wrong after merge. Resolving a conflict in `about/lay-and-land/frontend-copy-inventory.md` by taking either side is only a way to get the rebase to *complete* — the resolution is to finish the rebase, **re-run the current generator**, and commit its output. The same reasoning applies to any generated artefact whose producer is itself under review: a PR green before the producer landed proves nothing about the artefact it will produce after.
- **`merge_pr_exact_base.py` compares TREE SHAs, so `patch_identity_matches: false` is EXPECTED whenever main moved between merges** and is not by itself evidence of drift. The real verdict is `git patch-id --stable` equality between the PR's diff and the landed squash's diff; equal ids mean diff-identical regardless of the tree comparison. It also requires the full argument set (`--pr --expected-head --expected-base --expected-base-ref`) and the FULL 40-char SHAs — a bare `merge_pr_exact_base.py <N>` errors out.
- **During a `git merge --no-commit`, use `git diff` (worktree vs index), never `git status --porcelain`**, to ask whether a regenerated artefact drifted: merged files are already *staged*, so `status --porcelain` reports them as changed whether or not the generator moved anything, and a drift check built on it fires on every merge.
- **`out=$(cmd | head -1); rc=$?` captures `head`'s exit status, not `cmd`'s.** This made all four `pytest_gate.py verdict` classifications appear to return 0 while probing the gate's own PASS/FAILED/UNKNOWN contract. Re-measure without the pipe (or set `pipefail` and read `PIPESTATUS`) before believing an exit code taken through a pipeline — same defect class as everything else here: a check credited with an answer it was never positioned to give.
- **A red test that fails on a missing patch target is not a behavioural demonstration.** Reverting the production files and re-running a PR's new tests against the old code is the right falsification, but the resulting reds must be read individually: `assert True is False` is genuine evidence the old code was wrong, while `AttributeError: <module> does not have the attribute '<new_helper>'` only says the test patches a symbol that does not exist yet. The second is a legitimate test of a new code path and no evidence at all about the old defect. Isolate each red before counting it.
- **A forced-quiet green needs a control run that goes red.** `tests/modules/test_insight_engine.py` fails open in a way that is invisible: core_160's owner quiet-hours default lives in `public.approvals_policy`, and without that row the table does not exist, so `get_approvals_policy_quiet_hours` swallows `UndefinedTableError` at `src/butlers/core/approvals_policy.py:227`, returns `None`, and `is_policy_quiet_now` reports all 24 hours awake — the quiet-hours branch of `delivery_cycle` is unreachable and a cycle omitting `now=` cannot observe suppression however loud the wall clock gets. Proving a fix here needs BOTH halves: green with the window widened to 23 of 24 hours AND red when the pinned instant is moved *inside* the window. Widened-window-green alone is worthless, because a window that never fires is also green.
- **Testcontainers setup ERRORs (not FAILUREs) mean Docker contention, not a broken branch.** Two full quality gates running at once produce `UnixHTTPConnectionPool(host='localhost'): Read timed out. (read timeout=60)` at fixture setup, most often in `tests/config/test_stored_function_body_drift.py`. The tell is the split — 0 FAILED / N ERROR — plus the wall clock: the same suite re-run uncontended finished 13 minutes faster (21:47 vs 34:35). Record the red as observed, then re-run alone before attributing it to the code under test.
- Conversation-decomposition model output is selection data, not dispatch authority: ordinary concepts always enter the target through `route.v1`/`route.execute`, with the authoritative `conceptual_message` under `input.context`; inferred calendar proposals remain the explicit code-owned direct-tool exception. Give every concept a target-visible `subrequest_id`/`segment_id`, and scope successful-session dedupe by `(request_id, subrequest_id)` so two concepts for one butler cannot collapse.
- WhatsApp unknown-person names must be neutral and unique without deriving from phone/JID/LID material (a random UUID suffix is the current convention); isolate reservation failure to that speaker after the strict bulk lookup succeeds. Treat connector lifecycle observability as content-blind too: endpoint resolution, startup, credential resolution, checkpoint load/save, and backfill logs carry booleans/counts/failure class only, never endpoint/checkpoint values or raw exceptions.
- Home Assistant transport readiness is stronger than WebSocket authentication: health stays degraded and REST fallback remains available until every required `subscribe_events` acknowledgement succeeds. Keep reconnect ownership to one task and await it during shutdown.
- Home Assistant's broad `source_channel=home_assistant` skip is for ordinary noisy events, not deterministic wellness measurements. Classify the wellness carve-out before that policy and advance the shared checkpoint only after the wellness submission succeeds.
- Home Assistant weight history recovery must query `/api/history/period` with explicit significance/initial-state flags, retain historical attributes, maintain a per-entity cursor, deduplicate same timestamps, log failures content-blind, and await its polling task on shutdown.
- CI's `session-link-guard` job (`.github/workflows/ci.yml`) reads `github.event.pull_request.title`/`.body`, which is a snapshot frozen at the triggering event, not a live fetch. Editing a PR's body via `gh pr edit` does NOT make a rerun of that job (even `gh run rerun --job <id>`) see the new text — it re-executes against the same frozen payload and fails again on the already-fixed content. To get a fresh check with the corrected body, close and reopen the PR (or push a new commit) to fire a new `pull_request` event.
- `run_device_health_check` (`src/butlers/jobs/home.py`) previously flagged `button.*`, `conversation.*`, `tts.*`, IR/RF blaster (`infrared.*`/`radio_frequency.*`) entities, and Zigbee2MQTT per-gesture dimmer "action" sensors (`sensor.*_action_{brightness_delta,color_temperature_delta,rate,step_size,transition_time}`) as offline — these HA domains have no persistent value and legitimately rest at `unknown` between interactions, so this was ~half of a real alert's "critical" list as false positives (see `is_steady_state_unknown`). It also re-sent the full issue list on every scheduled run with no memory of prior alerts; `select_due_issues` now throttles repeat notifications via state-store key `home:health_check:last_alerted` and `home:thresholds:realert` (default 24h) — the job's returned counts still reflect true current state, only the Telegram push/memory-fact writes are throttled. Cadence is every 4h (`roster/home/butler.toml`, `0 */4 * * *`).
- Codex device auth creates `.codex/log/codex-login.log` before writing `.codex/auth.json`. In the Dashboard CLI-auth stage policy, make that log root an optional, private, disposable scratch root only: the auth artifact remains required and strictly read/persisted. A log without auth, or a log plus any undeclared sibling, must fail closed; log bytes never cross the persistence boundary.
