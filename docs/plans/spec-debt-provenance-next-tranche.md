# Spec-debt provenance: next bounded tranche

**Status:** Read-only research packet. No specification, source, archive, or ratchet change is
authorized by this document.

**Source:** `3fdec9728433f46009d054dbeca158855091fad7` (`origin/main` and this branch
matched when the evidence below was refreshed on 2026-09-11).

**Owning debt:** `bu-w8uno` (unarchived whole-requirement overwrite debt) and `bu-tk618`
(archived requirements that did not land). This packet assesses only the two identities named
below. It does not narrow either parent's original closure criteria, close either parent, or grant
implementation, archive, merge, deployment, or provider authority.

## Evidence method and current denominators

The repository's current `collect()` functions were imported and run directly against the source
SHA above. The guard CLIs were also run without strict mode and without either baseline-writing
operation.

| Collector | Current findings returned by `collect()` | Frozen JSON ledger | Target result |
| --- | ---: | ---: | --- |
| `scripts/check_spec_overwrites.py` | 201 findings across 68 keys; 0 skipped blocks | 202 findings across 69 keys | exact target clause still returned and exactly matches its frozen identity |
| `scripts/check_archived_requirements_landed.py` | 1041 findings across 660 keys | 1064 findings across 667 keys | exact target missing requirement still returned and exactly matches its frozen identity |

The different current and frozen totals are evidence that unrelated entries have healed or partly
healed since the ledgers were written. They are not permission to refreeze, replace a requested
candidate, or delete any unrelated entry. Both requested identities remain observed at this exact
source.

The normal guard output was:

- `python3 scripts/check_spec_overwrites.py`: no unfrozen losses across 68 current debt keys; it
  separately named one unrelated frozen finding that could be tightened.
- `python3 scripts/check_archived_requirements_landed.py`: every current finding was frozen; it
  separately named seven unrelated keys with fewer findings and reported 667 frozen ledger keys.

### Input blobs

These are Git blob identities at the recorded source SHA, not working-tree hashes.

| Input | Blob |
| --- | --- |
| `scripts/check_spec_overwrites.py` | `0f1728aeac24b960eca464ea214e5a2fe9b7067b` |
| `scripts/spec-overwrite-baseline.json` | `658b225ddb1e6b1ba1d4f38fee5ac3fc0c30eaa6` |
| `openspec/specs/butler-travel/spec.md` | `43b49b27924033f809e2669e0d7581852547ec14` |
| `openspec/changes/add-health-medication-travel-mcp/specs/butler-travel/spec.md` | `985db0e9a08c227999aa53fcac4f0e4d4cbc2e9d` |
| `openspec/changes/archive/proactive-insight-engine/specs/butler-travel/spec.md` | `1d352f9474caa07d82279855b4743c5a237c594c` |
| `scripts/check_archived_requirements_landed.py` | `a1f85f4a3cc5d885b539f61f2d96a9913c756866` |
| `scripts/archived-requirements-baseline.json` | `1a374b649a844f3c56febce86418228c95225248` |
| `openspec/specs/butler-base-spec/spec.md` | `117871bd7fb264db4bab0a99e54046e07867e7f5` |
| `openspec/changes/archive/2026-02-24-alpha-release-mvp/specs/butler-base-spec/spec.md` | `98e8816b4691852da4ad008f9286d4ea73d6a1b4` |
| `tests/config/test_roster_claude_md_include.py` | `2ac596543c45b9c0e6fd601e4c858d58c8c0a9ae` |
| `src/butlers/core/skills.py` | `bd6632eccbf3b625fb75580f3cba755027179503` |

## Assessment A: Travel insight medication access

### Frozen identity and present finding

- Key: `add-health-medication-travel-mcp/butler-travel/Travel Insight Scan Job`
- Kind: `clause`
- Scenario: `Medication prep for travel insights`
- Digest: `3506801af226`
- Current and frozen excerpt: `- **AND** the user has active medications tracked by the health
  butler (queryable vithe \`public\` schema or known from...`

[Observed] The current baseline clause is still present in
`openspec/specs/butler-travel/spec.md`. The active change replaces it with two substantive clauses:

1. Travel obtains the snapshot through its parameterless `health_medication_snapshot` MCP tool,
   routed by Switchboard to Health's `medication_travel_snapshot` tool.
2. Travel must not query the Health schema or import Health implementation code.

The collector reports the one baseline clause as dropped. Reversing the body comparison reports
only those two replacement clauses as absent from the baseline, with digests `c69f719b46d5` and
`716b7d69f0aa`. Every other clause and every scenario name in `Travel Insight Scan Job` is carried
forward. The same collector finds no other debt key under `add-health-medication-travel-mcp`.

### Clause disposition

| Clause or obligation | Classification | Evidence and authority |
| --- | --- | --- |
| Travel evaluates upcoming trips before considering medication preparation | Preserved | Same `WHEN` clause in baseline and active block. |
| Medication existence may be obtained by direct `public`-schema access | Superseded and prohibited | Vision Rule 3 requires MCP-only inter-butler communication through Switchboard. RFC 0010 permits cross-schema SQL only for bounded, deterministic batch aggregation and explicitly excludes on-demand queries. The approved active proposal says the public-schema wording is stale and removes it. |
| Medication existence may be inferred from Travel memory facts | Superseded | The active design establishes Health as authoritative and defines one strict, purpose-specific response. It rejects alternate parsing or data paths; the active scenario requires the Health MCP snapshot. |
| Health owns the underlying medication data | Preserved and strengthened | The active scenario says medications are owned by Health, and the paired Health delta defines Health as the authoritative provider. |
| A medication-preparation candidate is generated, with the existing priority, deduplication, expiry, and trip-duration constraints | Preserved | All six downstream baseline clauses are present in the active block. |
| The active snapshot tool's strict privacy, typed failure, and successful-empty behavior | Added by the approved successor | The active change adds provider and consumer requirements; these obligations are not in the old clause and must not be erased merely to make the text matcher green. |

### History and approval provenance

- [Observed] Commit `a8cd7ebc4c1120bac89ec93b65264ad5a32ec005` synced and archived
  `proactive-insight-engine`, creating the canonical `Travel Insight Scan Job` requirement.
- [Observed] Commit `5b03dd2222d9e6a563970cb3244b69aac8b3da6c` mechanically changed the
  old `shared` wording to `public` and introduced the surviving `vithe` typo. That mass migration
  did not approve direct Health-schema access as a new exception.
- [Observed] The owner decision recorded in closed bead `bu-aj7jz` required a Health MCP
  integration for Travel. PR #3290 merged the spec-driven implementation and active change as
  `c6452bc1586610d2c82e48b8e6c427f79b8ff381`; its terminal `check`, `frontend`, and
  `frontend-e2e` checks were successful. The bead closed specifically against that independently
  reviewed merge.
- [Observed] The active proposal explicitly says it removes stale public-schema access and routes
  the narrow snapshot through Switchboard. Its tasks are all checked, and
  `openspec status --change add-health-medication-travel-mcp --json` reports planning complete and
  complete. `openspec validate add-health-medication-travel-mcp --strict` passes at this source.
- [Observed] PR #4132 later changed the baseline Travel tool/booking contract and updated the same
  active Travel delta's tool inventory. It merged as
  `f2fae0f031836a9f2d77c8004a10313649b93e84` after review and full CI. The current collector
  confirms that update introduced no additional loss under this active change.

The authority to replace the stale clause is therefore already recorded in an approved spec delta
and delivered PR. Working code is corroborating evidence only; it is not being used as the source
of specification authority.

### Cold-start-ready maintenance proposal A

Create one separately owned maintenance leaf under `bu-w8uno` to finish and archive the already
approved `add-health-medication-travel-mcp` change. This is the smallest honest repair: preserving
the stale clause, or padding the replacement until the heuristic recognizes it, would contradict
the approved MCP-only contract.

**Allowed tracked surface:**

- the existing `openspec/changes/add-health-medication-travel-mcp/` artifacts, only as needed to
  rebuild complete current MODIFIED bodies and archive the already-approved change;
- `openspec/specs/butler-health/spec.md` and `openspec/specs/butler-travel/spec.md`, only for the
  normal archive application of that change's two ADDED requirements and two Travel MODIFIED
  requirements;
- the corresponding archived change directory produced by normal `openspec archive`;
- exactly the `3506801af226` finding under the exact key in
  `scripts/spec-overwrite-baseline.json`, deleted only after the post-archive collector proves it
  absent.

Do not touch collector source, tests, other ratchet entries, unrelated active changes, runtime code,
prompts, migrations, or PR #3960 surfaces. Do not use `--update-baseline`.

**Execution and preservation proof:**

1. Refresh the exact source, re-run both collectors, strict validation, open-PR file overlap, and
   same-requirement searches. Stop if another owner has acquired either canonical spec, the active
   change, or either ledger.
2. Rebuild each MODIFIED block from the then-current canonical requirement and reapply only the
   approved Health-MCP changes. This includes the PR #4132 tool-surface additions already carried
   by the active delta.
3. Diff each rebuilt block against the canonical body. The only intentional deletion in
   `Travel Insight Scan Job` must remain the stale direct-schema/memory-fact alternative; all
   other current clauses and scenario names must survive. The paired provider/consumer requirements
   and their privacy/failure clauses must remain present.
4. Archive through the normal OpenSpec workflow. Do not hand-move the directory.
5. Re-run `collect()` and delete only the exact frozen finding after it is no longer returned.
6. Run strict validation, both guard CLIs, their existing targeted test files, applicable docs
   guards, and `git diff --check`. Report `Tests: +0 ~0 -0` unless guard behavior itself changes;
   any such behavior change is outside this packet and must extend the existing test file rather
   than introduce a duplicate guard.

**Rollback:** revert the maintenance commit as one unit. That restores the active change, canonical
spec bodies, and exact frozen entry together. There is no schema, data, provider, or runtime rollback.

## Assessment B: CLAUDE.md system-prompt requirement name

### Frozen identity and present finding

- Key: `2026-02-24-alpha-release-mvp/butler-base-spec/CLAUDE.md as System Prompt`
- Kind: `requirement`
- Scenario: none
- Digest: `aaa13cae4049`
- Excerpt: `CLAUDE.md as System Prompt`

[Observed] The archived alpha change contains that exact requirement name. The canonical
`butler-base-spec` instead contains `CLAUDE.md as System Prompt Entry Point`. Because there is no
recorded `RENAMED` mapping, the archived-requirements collector cannot resolve the predecessor name
and reports it as wholly missing.

### Clause disposition

| Predecessor obligation | Classification | Successor evidence |
| --- | --- | --- |
| `CLAUDE.md` is loaded as the base system prompt for each spawned runtime | Preserved and strengthened | The successor uses `SHALL` and retains the same `System prompt composition` scenario. |
| Memory context is appended after a blank separator | Preserved | Exact scenario clause remains in the successor. |
| No other system-prompt sources are injected | Preserved as canonical text, but implementation consistency is unresolved here | Exact clause remains in the successor. `read_system_prompt()` also documents and implements a DB override, while `dashboard-butler-management` specifies prompt history. That cross-spec/runtime tension is not settled by this historical-name repair. |
| Interactive mode is selected from request context and the five response styles | Preserved with vocabulary maintenance | The successor retains both clauses and updates the example channel from historical `telegram` to `telegram_bot`. |
| `CLAUDE.md` directly defines personality, tools, guidelines, memory taxonomy, and response modes | Superseded by composition | The successor makes `CLAUDE.md` an entry point, adds `CLAUDE.md -> AGENTS.md -> shared AGENTS.md` composition, and moves detailed workflow material to skills under the adjacent `AGENTS.md Content Principles` requirement. |
| Include-chain maintenance and content partitioning | Added by successor | The predecessor had no equivalent scenarios; the successor adds the two explicit include-chain scenarios without dropping the old two. |

The collector's own body heuristic, when manually pointed from predecessor body to successor body,
finds only the rewritten preamble missing; it finds no missing predecessor scenario or scenario
clause. In the reverse direction it finds the stronger successor preamble and the two new
include-chain scenarios. That is consistent with a rename plus strengthening, not a vanished
behavior contract.

### History and approval provenance

- [Observed] Commit `ab7bdc7967f6b9338cfad3f969f303840429e0fd` authored the alpha
  requirement. Commit `5d2452ee87568fbc3897ad8f54d2f04c266ba6e0` synced it to the baseline and
  archived the change.
- [Observed] Owner-authored mainline commit
  `1ea0058e40425efaccc867cffc713b8d6cc6d0f0` changed the exact header to
  `CLAUDE.md as System Prompt Entry Point`, retained both predecessor scenarios, and added the
  explicit AGENTS/skills composition contract. Its commit message identifies the reviewed planning
  item `butlers-d9my.9` and states the intent to codify the content boundary.
- [Observed] PR #1409 later normalized this requirement to strict `SHALL` wording and merged as
  `2e9f03df5b8e8a3a207795f6425e62dfb4c6006d`.
- [Observed] PR #3110 merged the roster-wide `@AGENTS.md` unification as
  `5fa098c63fa85a5fa0f87311dbb05d39b1202eb1`. Closed bead `bu-1mq1d.1` records
  the union-with-nothing-dropped outcome. The existing
  `tests/config/test_roster_claude_md_include.py` directly cites the successor requirement and
  checks each roster `CLAUDE.md` is the bare include. At this source, all roster `CLAUDE.md` files
  matched that bare-include invariant. This check does not assert that every `AGENTS.md` uses the
  same first-line composition shape.
- [Observed] A direct first-line check found that `roster/travel/AGENTS.md` and
  `roster/qa/AGENTS.md` do not begin with the shared include required by the successor's
  `AGENTS.md composes shared instructions` scenario. Whether each is deliberate or drift is an
  unresolved implementation/spec-conformance question outside this historical-name repair.

These records are enough to identify the canonical owner and approved successor name. They do not
prove every current runtime prompt source conforms to every sentence of the requirement; that
separate tension remains explicit above.

### Cold-start-ready maintenance proposal B

Create one separately owned historical-provenance leaf under `bu-tk618`, following the already
merged PR #4051 pattern for absent predecessor names with present, approved successors.

**Exact delta:**

```markdown
## RENAMED Requirements

- FROM: `### Requirement: CLAUDE.md as System Prompt`
- TO: `### Requirement: CLAUDE.md as System Prompt Entry Point`
```

**Allowed tracked surface:**

- a new OpenSpec change directory named for this one historical mapping, containing only the normal
  `.openspec.yaml`, proposal, design, tasks, and
  `specs/butler-base-spec/spec.md` provenance artifacts;
- the corresponding archived directory produced by normal OpenSpec archive;
- exactly the key
  `2026-02-24-alpha-release-mvp/butler-base-spec/CLAUDE.md as System Prompt` in
  `scripts/archived-requirements-baseline.json`, deleted only after the mapping makes its current
  finding disappear.

The canonical `openspec/specs/butler-base-spec/spec.md` body is an asserted no-change file: hash it
before and after archive and require byte identity. Do not use this maintenance to change the
successor body, resolve the DB-override tension, edit prompt code or roster prompts, restore the
archived predecessor body, or delete any other frozen entry.

**Execution and preservation proof:**

1. Refresh source, collector identity, successor header/history, open PRs, and dirty-worktree
   overlap. Stop if another owner has acquired the canonical spec, the new provenance name, or the
   archived-requirements ledger.
2. Author and strictly validate the exact `RENAMED`-only change. Its proposal and design must cite
   the predecessor commits, successor commit, PR #1409, PR #3110, and the unresolved DB-override
   and AGENTS first-line tensions so the mapping cannot be mistaken for a full runtime-fidelity
   verdict.
3. Run both guards before archive, archive normally, and prove the canonical spec blob is unchanged.
4. Re-run `collect()`; require only this exact current finding to disappear from the assessed slice,
   then delete only its exact frozen key by hand. There is intentionally no archived-ledger
   refreeze command.
5. Run strict OpenSpec validation, both guard CLIs,
   `tests/scripts/test_check_archived_requirements_landed.py`, applicable docs guards, and
   `git diff --check`. `Tests: +0 ~0 -0` is expected because guard behavior does not change.

**Rollback:** revert the historical-provenance commit. The mapping and one-key ledger deletion
return together; the canonical spec must remain byte-identical in both directions.

## Current overlap and downstream authority

The overlap check was refreshed after rebasing this research branch to the recorded source SHA.

- [Observed] A GraphQL scan of all 22 open PRs found zero file overlaps with the two collectors,
  two ledgers, two canonical specs, the active medication-travel delta, or a proposed
  `butler-base-spec` provenance delta.
- [Observed] No registered Git worktree had dirty changes in those exact files or directories.
- [Observed] PR #3960 remains open, but its files are conversation identity, connector, routing,
  and related specs/tests. It does not touch either assessed identity or any allowed future file.
  Its six held overwrite findings remain outside this packet.
- [Observed] PR #4043 has conceptual prompt-composition proximity but touches spawner/runtime and
  telemetry files, not the allowed historical-name repair files. PR #4044 has conceptual Health
  privacy proximity but touches medication/memory implementation and migrations, not the active
  medication-travel specs or either ledger. Neither grants this packet authority over its surface.
- [Observed] `bu-w8uno` and `bu-tk618` are still blocked on this research prerequisite, and their
  original broad outcomes remain open. Only the coordinator may mutate their lifecycle or create
  downstream work.

Current absence of a collision is a point-in-time observation, not a reservation. Each maintenance
proposal therefore starts with a fresh overlap and ownership check. The two proposals are also
independent of one another: they use different parent ledgers and different specs, and neither may
be used to close or refreeze the other's debt.

## Research-only close condition

This packet is complete when its own documentation PR is reviewed and merged. That closes only
`bu-o2yi6k` research. Proposal A and Proposal B remain unexecuted until the coordinator creates or
adopts exact bounded work with fresh ownership. All other current and frozen findings remain in
their existing ledgers under the original `bu-w8uno` and `bu-tk618` closure criteria.
