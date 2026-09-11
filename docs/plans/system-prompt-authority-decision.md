# System-prompt authority and shared-include decision

**Status:** Proposed governing-spec amendment packet. This document is read-only research and
does not authorize specification, roster, source, schema, runtime, deployment, archive, ratchet,
or Beads lifecycle changes.

**Source:** `36e683197ab0ab548ddd67a12badbaaba940da04` (`origin/main` and this
branch matched when the evidence was refreshed on 2026-09-12).

**Predecessor research:** `docs/plans/spec-debt-provenance-next-tranche.md`, produced by
`bu-o2yi6k` and merged in PR #4148. That packet established the historical requirement rename
but deliberately left effective prompt provenance and two shared-include surfaces unresolved.

**Owning debt:** `bu-w8uno` still owns unarchived whole-requirement overwrite debt;
`bu-tk618` still owns archived requirements that did not land; `bu-o2yi6k` closed only its two
candidate assessments. This packet does not weaken, close, relabel, or satisfy any of those
closure criteria. In particular, it does not authorize the historical `RENAMED` repair proposed
by `bu-o2yi6k`.

## Outcome

[Observed] The repository currently has two approved but incompatible authority stories:

1. Doctrine and `butler-base-spec` make git-tracked roster content the source of butler identity,
   require `CLAUDE.md -> AGENTS.md -> shared AGENTS.md`, and say no other system-prompt source is
   injected.
2. `dashboard-butler-management` and merged PRs #1700/#2250 make the newest row in
   `public.system_prompt_history` a load-bearing, full replacement for the roster-derived base.

[Observed] This is not merely stale terminology. A non-empty database row means the spawner does
not read the roster `CLAUDE.md` body at all. The row can therefore replace identity content and
omit the `AGENTS.md` chain. Separately, the spawner appends several other prompt layers, some
specified and some not directly covered by a requirement. The literal "no other" clause cannot
describe the current final prompt.

[Observed] The include surfaces have different dispositions:

- `roster/travel/AGENTS.md` is a domain-butler conformance gap. Its first line is not the required
  shared include, its resolved on-disk prompt omits `# Shared Butler Instructions`, and no approved
  exception was found.
- `roster/qa/AGENTS.md` also omits the include, but PR #3110 explicitly records that choice as
  deliberate because QA is a staffer, not a domain butler. The vision distinguishes those role
  types. The implementation is therefore not safely repairable by text similarity; the remaining
  gap is that the staffer exception and opt-in rule are not stated in `staffer-archetype`, QA's
  infrastructure-contract MANIFESTO, or `staffer-qa`.

**Recommendation:** approve the bounded amendment contract below: the roster-derived identity
segment remains present and cannot be replaced wholesale; database content becomes an explicitly delimited owner-operations
overlay rather than a full base replacement; approved dynamic layers are enumerated; bare file
references are resolved in the shared core seam; domain butlers must include shared instructions;
staffers opt in according to their governing infrastructure contract. This retains the dashboard
feature while restoring the roster's identity authority and preserving QA's recorded exception.

**Default if no owner decision is recorded:** retain all current behavior and files unchanged,
leave the contradiction and both include findings explicitly unresolved, and do not release any
spec, prompt, roster, schema, or runtime implementation. Research completion is not semantic
approval.

## Evidence boundary and input identities

The inspection used only source, requirement bodies, Git history, merged-PR metadata, Beads reads,
and targeted existing tests. Code and passing tests corroborate runtime behavior; neither is
treated as normative authority.

| Input at source SHA | Git blob |
| --- | --- |
| `openspec/specs/butler-base-spec/spec.md` | `117871bd7fb264db4bab0a99e54046e07867e7f5` |
| `openspec/specs/core-skills/spec.md` | `db4634935e3501764bf51efd545c661483960fb5` |
| `openspec/specs/core-spawner/spec.md` | `ae09be18e6cc29da9c33f7a0d5e27fff95e87968` |
| `openspec/specs/dashboard-butler-management/spec.md` | `6e4e78bd4989d97060428c7b1eba7b833ad0be7c` |
| `src/butlers/core/skills.py` | `bd6632eccbf3b625fb75580f3cba755027179503` |
| `src/butlers/core/spawner.py` | `d5a4706a6f5262e96e92a4cc31494f083b621f09` |
| `src/butlers/core/spawner_context.py` | `955771ecdca70067d7e57d25e9bc6e40e7ebff3f` |
| `src/butlers/api/routers/butler_management.py` | `849d63163ab8218a0377e48f3644e7357ffc7ae8` |
| `alembic/versions/core/core_098_butler_prompt_history.py` | `0fca0840931545f1d54af6eec87ea2397686d83d` |
| `tests/features/test_skills.py` | `b4b3cac1b89dc3971b6861e68818a3d800946680` |
| `tests/core/test_core_spawner.py` | `28483e36913a055ed23b7b4b8ce99bf179924951` |
| `tests/config/test_roster_claude_md_include.py` | `2ac596543c45b9c0e6fd601e4c858d58c8c0a9ae` |
| `roster/travel/AGENTS.md` | `6937213c24df7a16b7970c77607f2f408bdc293d` |
| `roster/qa/AGENTS.md` | `02edd19060c1d15c74a8f5f08a6610b9728ede77` |
| `roster/shared/AGENTS.md` | `2910759c5c46ef32cb4d2cb1c7ee738c027e0cc3` |
| `openspec/changes/k3s-deployment-helm-chart/specs/core-skills/spec.md` | `f14d6ebd9d4581363ea3ef6670536926341b3de7` |
| `openspec/changes/specify-improvement-proposal-spine/specs/core-skills/spec.md` | `a519cd50b23b4b904bfb7b6a2b1967b6b46b93be` |
| `openspec/changes/specify-improvement-proposal-spine/design.md` | `f844ecc3586d83b8f01ae81763beae68e6f4bc7b` |
| `openspec/changes/specify-improvement-proposal-spine/tasks.md` | `1202663e9384b59c4b16698bc79fb965035629ea` |
| `openspec/specs/staffer-archetype/spec.md` | `d725f07c5ad31c96b5324fc53f9c25369260a48b` |
| `openspec/specs/staffer-qa/spec.md` | `045e6cad5c0d17d9969f25414641934311afbe25` |
| `roster/qa/MANIFESTO.md` | `4860afc4f4aff636e71cb3437db18373e93f2143` |
| `src/butlers/api/owner_control.py` | `9ab67e062cf6818adefe5aab2556e5e984df0150` |
| `src/butlers/api/deps.py` | `1ca02659ff205d246264af05d60cb404f37e810f` |
| `scripts/init-db.sql` | `cdb43646e686c6e100c1c93f9c5c71b5982da358` |

No active OpenSpec delta contains a same-named requirement block for any of these four canonical
requirements:

- `butler-base-spec / CLAUDE.md as System Prompt Entry Point`
- `core-skills / System Prompt Loading from CLAUDE.md`
- `core-spawner / System Prompt Composition`
- `dashboard-butler-management / System Prompt Versioning API`

Two active, unarchived changes modify the separate, same-named
`core-skills / AGENTS.md Read/Write Access` requirement:

- `k3s-deployment-helm-chart` proposes storing runtime notes in a butler-local state row when the
  roster mount is read-only, then merging file and DB notes on read.
- `specify-improvement-proposal-spine` carries the k3s requirement body and scenarios forward,
  then adds `[TARGET-STATE]` clauses forbidding any runtime-facing MCP path to
  `write_agents_md()`/`append_agents_md()` and requiring a future notes proposal to go through
  `propose_amendment` and an accepted target adapter.

[Observed] Both changes report planning complete, but the improvement-spine change's independent
review, owner approval, sequencing, and implementation-release tasks are unchecked. Active delta
text is candidate future authority within its proposed scope, not an adopted baseline or evidence
that its target state is approved or implemented. At the source SHA, the helpers permit direct
file writes, the k3s fallback is not implemented, and no production caller of either write helper
was located.

[Observed] The two changes are also an archive-order collision: each has a whole-body MODIFIED
block for the same requirement. The improvement-spine block is intentionally a textual superset,
but that does not reserve future baseline text or make either archive order self-validating. After
whichever change archives first, the other block must be rebuilt from the refreshed baseline and
only its own still-approved delta reapplied. If improvement-spine archives first, the k3s block is
expected to become redundant and must be rebuilt or removed; if k3s archives first, the
improvement-spine block must be rebuilt before its archive. The same-requirement body search and
`make check-spec-overwrites` must run again after each archive. A future prompt-authority change
must not create a third owner for this requirement; it either avoids the requirement or waits for
this sequencing to resolve. This packet inventories those candidate obligations without adopting
either target state.

## Applicable doctrine and requirements

| Authority | Binding or relevant clause | Disposition before a decision |
| --- | --- | --- |
| `about/heart-and-soul/vision.md`, Rule 5 | Git-based config is the source of truth for butler identity, including personality; database state is for operational tuning. | Preserved doctrine. A full arbitrary prompt replacement is not named as operational tuning and can replace personality. |
| `about/heart-and-soul/vision.md`, agent types | Butlers are domain specialists; staffers are infrastructure specialists governed by infrastructure contracts. | Preserved. This distinction supports QA's recorded exception but does not define a general staffer include rule. |
| `butler-base-spec / CLAUDE.md as System Prompt Entry Point` | `CLAUDE.md` is the base; memory may be appended; no other prompt source is injected. | Internally inconsistent with its own memory clause and with later canonical prompt-layer requirements; contradicted by DB replacement. |
| Same requirement, file-reference scenario | `CLAUDE.md` contains only `@AGENTS.md`, which the runtime resolves, not butler include logic. | Bare-reference shape is preserved across the roster; resolution ownership was superseded in code by approved PR #1292 but never amended in the spec. |
| Same requirement, shared-instruction scenario | A butler's `AGENTS.md` starts with `@../shared/AGENTS.md`. | Preserved for domain butlers; Travel drifts. Applicability to staffers is unresolved in the spec; QA has explicit approved exception history. |
| `core-skills / System Prompt Loading from CLAUDE.md` | `read_system_prompt()` reads `CLAUDE.md`, falling back to a generated default. | Preserved only when no non-empty DB override exists. The function's actual signature and precedence are broader. |
| `core-skills / Include Directive Resolution` | HTML-comment includes are roster-relative, traversal-safe, and non-recursive. | Preserved. It does not specify recursive bare `@file.md` includes, although that behavior is tested and merged. |
| `core-skills / Shared Snippet Appending` | `BUTLER_SKILLS.md` and `MCP_LOGGING.md` append in stable order. | Preserved and applied to either disk or DB base. These are also prompt sources despite the base requirement's literal prohibition. |
| Active k3s `core-skills / AGENTS.md Read/Write Access` delta | On a read-only mount, write/append falls back to local state and read concatenates file plus state. | Proposed, unarchived candidate behavior; not implemented in the observed prompt path and not promoted by this packet. |
| Active improvement-spine delta on the same requirement | No runtime MCP tool directly reaches the write helpers; future runtime notes use a gated `propose_amendment` target adapter. | Explicit `[TARGET-STATE]` with unchecked review/owner/release tasks. It must be sequenced with k3s before either archive; not adopted here. |
| `core-spawner / Memory Context Injection` | Memory context is appended when enabled and available. | Preserved and explicitly approved. |
| `core-spawner / System Prompt Composition` | Roster base, shared snippets, and situational context form the prompt. | Preserved canonical authority for those layers, but it omits DB-base replacement and general timezone/routing layers. |
| `core-spawner / Blind-Spot Preamble Injection` | A typed signal-health layer is inserted after context and before routing. | Preserved and explicitly approved. |
| `dashboard-butler-management / System Prompt Versioning API` | GET/PUT/history endpoints store versioned prompt text in `public.system_prompt_history`. | Preserved API and persistence contract. It does not itself say the newest row replaces roster identity. |

## Observed effective-prompt entry points

The following map is the executed default spawner path at the recorded SHA.

| Order | Source and seam | Selection / failure behavior | Current normative coverage |
| ---: | --- | --- | --- |
| 1a | `public.system_prompt_history.prompt`, fetched by `fetch_system_prompt_override()` | Highest version for the named agent wins when non-empty. Any query failure returns `None`. | API/history are specified; full-base precedence is approved by PR #2250 but absent from current capability requirements and conflicts with Rule 5. |
| 1b | `roster/{name}/CLAUDE.md`, read by `read_system_prompt()` | Used only when 1a is absent/blank/unavailable. | Specified as unconditional base, so current conditional use is unresolved drift. |
| 1c | Generated `You are the {name} butler.` default | Used when neither effective DB text nor non-empty `CLAUDE.md` exists. | Specified in `core-skills`. The wording also does not distinguish staffers. |
| 2 | Bare `@file.md` chain, including `CLAUDE.md -> AGENTS.md -> ../shared/AGENTS.md` | Recursively expanded by `core/skills.py` with cycle and roster-escape protection. | Approved implementation in PR #1292; contradicts the `butler-base-spec` statement that the runtime, not butler logic, resolves the reference. |
| 3 | HTML `<!-- @include ... -->` references | Roster-relative, non-recursive, missing/unsafe reference preserved with warning. | Specified in `core-skills`. |
| 4 | `roster/shared/BUTLER_SKILLS.md`, then `MCP_LOGGING.md` | Appended to either selected base when present/non-empty. | Specified in `core-skills`. |
| 5 | General timezone/locale/date/time/week instruction | Loaded from shared general settings; failure omits it. | [Unknown] No directly governing prompt-composition scenario was located. |
| 6 | Situational context preamble | Loaded from `public.user_context`; ordinary failure omits it. | Specified in `core-spawner / System Prompt Composition`. |
| 7 | Blind-spot preamble | Inserted for absent/unmeasurable declared signals; query failure produces an explicit failure block; runtime-config flag can disable it. | Specified in `core-spawner / Blind-Spot Preamble Injection`. |
| 8 | Switchboard owner routing instructions | Loaded from `routing_instructions` for Switchboard only; failure omits it. | [Unknown] No direct system-prompt composition requirement was located. |
| 9 | Memory context | Loaded when the memory module is enabled; failure omits it. | Specified in both `butler-base-spec` and `core-spawner`. |
| 10 | Runtime adapter | Receives the already composed string as `system_prompt`; Codex combines it with the task prompt for stdin. | Adapter transport is specified in `core-spawner`; it is not the observed bare-reference resolver. |

[Observed] `_compose_system_prompt()` implements the suffix order
`base -> timezone -> situational context -> blind spot -> routing -> memory`. Its docstring's
numbered list omits timezone even though the code inserts it first after the base. That is a
documentation symptom of the same missing single authority map, not independent authorization to
change the order.

## Obligation disposition

| Obligation | Classification | Evidence |
| --- | --- | --- |
| Git roster defines domain-butler identity and personality | Preserved, but violated when a DB base replacement exists | Vision Rule 5 predates PRs #1700/#2250 and has not been amended. |
| Dashboard stores prompt versions and exposes owner-facing CRUD/history | Preserved | Canonical dashboard requirement, migration `core_098`, PR #1700, and API tests. |
| A stored prompt edit affects the next spawned session | Approved behavior, normatively unresolved | `bu-dr03f.1` and PR #2250 intentionally fixed the decorative-editor defect, but did not amend Rule 5 or the base-prompt requirements. |
| A DB prompt is a full replacement for roster identity | Unresolved | Implemented and tested, but not stated by a current requirement and in tension with doctrine. Similarity to `runtime_config` does not establish identity authority. |
| Approved dynamic context may join the final system prompt | Preserved | Memory, situational context, shared snippets, and blind-spot layers have canonical requirements. The blanket "no other" text is therefore superseded in practice but not formally retired. |
| Bare references are resolved by the runtime adapter | Superseded in approved implementation, unresolved in spec | PR #1292 moved recursive expansion into `core/skills.py`; the canonical sentence still names the runtime. |
| Domain-butler `AGENTS.md` begins with the shared include | Preserved | Canonical successor and owner-authored commit `034a54a3` explicitly established it for then-existing domain butlers. |
| Travel may omit the shared include | Unresolved implementation drift, not an approved exception | Commit `76000410` removed the include 14 minutes after `034a54a3` while moving the prompt body, with no exception rationale. Travel is a domain butler. |
| Every staffer must include shared butler instructions | Superseded for QA, unresolved as a general spec rule | Vision distinguishes staffers. PR #3110 explicitly says QA deliberately omits the include because it is an infrastructure staffer. Other staffers currently opt in. |
| QA must receive shared butler instructions | Retired as an assumed obligation; explicit spec wording still required | PR #3110's owner-approved exception is more specific than inference from file shape. Adding the include could import domain-butler rules that conflict with QA investigation responsibilities. |

## History and approval provenance

- [Observed] Commit `1ea0058e40425efaccc867cffc713b8d6cc6d0f0` renamed the base
  requirement to `CLAUDE.md as System Prompt Entry Point`, added the two include-chain scenarios,
  and cited planning item `butlers-d9my.9`. It retained the historical "no other source" clause.
- [Observed] Commit `034a54a329eb61a35a6403752b827ff2744fefdb` added
  `@../shared/AGENTS.md` to the five then-existing domain-butler AGENTS files and explicitly said
  this was required by `butler-base-spec`.
- [Observed] Commit `76000410558350f87504c20cb937a223e1d522d3`, 14 minutes later,
  moved Travel's large `CLAUDE.md` body into `AGENTS.md` and removed the just-added include. Its
  message records no exception or semantic decision.
- [Observed] QA was added by PR #986 after the shared-include commit. Its initial AGENTS file did
  not contain the shared include.
- [Observed] PR #1292 merged as
  `1f9ed74c3714fcfee42d0ebd7b0f218a30841b90`. It deliberately added recursive bare-reference
  expansion to `read_system_prompt()` to make the existing include chain work across runtimes.
- [Observed] PR #1700 merged as
  `371579ae73df2adbc42d7c520945f11915d076e5`, implementing `public.system_prompt_history` and
  prompt CRUD/history from the approved settings change. Its acceptance said prompt versioning
  worked end to end, but the first implementation did not affect spawning.
- [Observed] Closed bead `bu-dr03f.1` classified that editor as decorative. PR #2250 merged as
  `ecb19b5d790e8da07646433e40ea3ea114f9d0ce`, explicitly choosing DB HEAD as the live full base
  and roster `CLAUDE.md` as seed/default. This is positive approval of the runtime behavior, but
  its comparison to `runtime_config` was not a doctrine amendment.
- [Observed] PR #3110 merged as
  `5fa098c63fa85a5fa0f87311dbb05d39b1202eb1`, unifying every roster `CLAUDE.md` onto bare
  `@AGENTS.md`. Its body explicitly records QA's missing shared include as deliberate because QA
  is an infrastructure staffer. The accompanying test enforces only the CLAUDE-to-AGENTS edge; it
  does not check the AGENTS-to-shared edge.
- [Observed] PR #3110 described Travel as already following the CLAUDE-to-AGENTS pattern. It did
  not inspect or approve Travel's missing AGENTS-to-shared edge.

No reviewed artifact located in this history formally amended Vision Rule 5, reconciled the
blanket "no other" clause, or assigned full identity authority to arbitrary database text.

## Shared-include conformance at the source SHA

All 13 roster `CLAUDE.md` files contain only `@AGENTS.md`; the existing contract test passes.
Among the 13 roster agent directories, only Travel and QA fail the separate first-line check for
`@../shared/AGENTS.md`. Direct execution of `read_system_prompt()` confirmed both resolved prompts
omit the `# Shared Butler Instructions` marker.

### Travel

- [Observed] `roster/travel/butler.toml` has no `type = "staffer"`; normal config parsing therefore
  treats it as a domain butler.
- [Observed] It is bound by the canonical domain-butler shared-include scenario.
- [Observed] The removal came from a prompt-body relocation, not a reviewed exception.
- **Disposition:** implementation drift. A later, separately authorized conformance change may
  restore the first line and add a role-aware guard. This packet does not make that edit.

### QA

- [Observed] `roster/qa/butler.toml` explicitly sets `type = "staffer"`.
- [Observed] PR #3110 explicitly approved the absent include as deliberate.
- [Observed] shared AGENTS begins with domain-facing MCP-only and calendar instructions, while QA's
  governing prompt includes infrastructure investigation responsibilities. Blindly prepending the
  shared body could create conflicting instructions.
- **Disposition:** not a text-level conformance repair. QA's `CLAUDE.md` and `AGENTS.md` bytes can
  remain unchanged, but preserving the exception is still a governance change: the staffer-wide
  policy, QA infrastructure contract, and QA capability spec must represent it explicitly after
  owner adoption. Until then, the exception remains approved history but unresolved canonical
  authority.

## Proposed governing amendment contract

This is the recommended semantic choice, not an authored OpenSpec delta. Owner sign-off is
required before any changeset is created because it changes effective runtime behavior and
interprets a non-negotiable doctrine boundary.

### Named authority owners

| Concern | Proposed canonical owner |
| --- | --- |
| Roster-rooted identity and domain-butler composition policy | `openspec/specs/butler-base-spec/spec.md`, sourced from Vision Rules 5 and 6 |
| Cross-staffer prompt-composition default | `openspec/specs/staffer-archetype/spec.md`, because it already owns type-wide staffer roster conventions, the shared daemon/spawner model, and infrastructure-contract requirements |
| QA-specific shared-include choice | `roster/qa/MANIFESTO.md` as QA's doctrine-defined infrastructure contract, projected into an observable scenario in `openspec/specs/staffer-qa/spec.md` |
| Include syntax, recursion, path trust, and shared-file expansion | `openspec/specs/core-skills/spec.md` |
| Effective prompt layer order, availability, and failure posture | `openspec/specs/core-spawner/spec.md` |
| Owner editor API, version history, and overlay presentation | `openspec/specs/dashboard-butler-management/spec.md` |
| Storage and privilege boundary | a new forward core migration for `public.system_prompt_history`; never edit historical `core_098` |

### Proposed behavior

1. The resolved git roster chain is always present as the roster-rooted identity segment for every
   spawned session and cannot be replaced wholesale by a database row. The existing runtime-notes
   capability remains separately governed; absence/blank-file fallback remains explicit.
2. Recursive bare `@file.md` expansion is owned by the shared core skills seam, not by individual
   runtime adapters. HTML-comment include behavior remains separate and non-recursive.
3. A database prompt row may contribute only an explicitly delimited owner-operations overlay. It
   no longer replaces or deletes the roster root. The overlay's trust label and version are
   observable in the dashboard and audit record.
4. The spawner spec enumerates the complete allowed layer order and names the owner of every
   layer. At minimum it covers shared snippets, owner overlay, general settings, situational
   context, blind-spot disclosure, Switchboard routing instructions, and memory context. The old
   "no other sources" clause is replaced by a closed-list rule: no source outside the enumerated
   composition contract may be injected.
5. Domain butlers must begin AGENTS with `@../shared/AGENTS.md`. The type-wide staffer default is
   authored in `staffer-archetype`, not broadened into the domain-butler rule. Each concrete
   staffer's infrastructure contract records its opt-in or opt-out, and its capability spec
   projects the observable result. For QA, the current `CLAUDE.md` and `AGENTS.md` bytes remain
   unchanged, but `roster/qa/MANIFESTO.md` and `staffer-qa` must gain the explicit opt-out after
   owner adoption. Existing staffer opt-ins are not removed by this decision.
6. The dashboard must distinguish the roster-derived identity segment from the mutable operations
   overlay. It must not label an overlay as the complete current system prompt.

### Schema, caller, and trust implications

- **Current schema:** `public.system_prompt_history` stores unrestricted `prompt TEXT`, a version,
  timestamp, and actor. It has no source mode, roster digest, activation state, or trust class.
- **Current caller boundary:** `authenticated_principal()` returns `"owner"` for every request that
  reaches the handler. It is attribution, not authentication or admission. The API relies
  primarily on network isolation; `DASHBOARD_API_KEY` is optional defense in depth. Therefore the
  current route cannot express or test a distinct non-owner request after admission. Caller-
  supplied actor text is ignored, but that does not make the caller authenticated.
- **Current database boundary:** historical migration `core_098` grants `SELECT, INSERT, UPDATE,
  DELETE` on the whole table to all listed runtime roles and `connector_writer`; there is no
  per-agent row isolation in that migration. API-managed `DatabaseManager` pools explicitly do
  not use `SET ROLE`; the prompt route uses the common `credential_shared_pool`. Consequently a
  database policy based only on `authenticated_principal()` or an assumed API session role would
  be vacuous. This is broader integrity authority than an identity-adjacent prompt source should
  inherit.
- **Required fail-closed API admission:** the future write route depends on the existing
  `require_dashboard_owner_control()` boundary before acquiring a prompt pool. With no configured
  owner key it returns 503; with a missing or mismatched key it returns 401; only a matching owner
  key reaches persistence. `authenticated_principal()` remains attribution after admission and is
  never credited as the admission check.
- **Required independently enforceable database boundary:** option C requires a dedicated
  direct-login database principal and dedicated asyncpg pool for prompt writes (proposed name:
  `butlers_dashboard_prompt_rw`, with `NOINHERIT` and `NOBYPASSRLS`). The API must not write
  through the generic no-`SET ROLE`
  `credential_shared_pool`, and the dedicated pool authenticates directly as that principal rather
  than relying on `SET ROLE`. A NOLOGIN table-owner role (proposed name:
  `butlers_system_prompt_history_owner`, also `NOINHERIT` and `NOBYPASSRLS`) owns the table and is
  not granted to application roles.
  A forward migration must explicitly revoke the historical `INSERT`, `UPDATE`, `DELETE`,
  `TRUNCATE`, `REFERENCES`, and `TRIGGER` privileges from `PUBLIC`, every runtime role, and
  `connector_writer`, plus sequence privileges that could support direct inserts. It grants the
  dedicated writer only the append/read privileges it needs; runtime roles retain only the
  minimum per-agent read path. Enabled RLS policies restrict runtime reads to the row identity
  derived from `current_user` through a fixed, fail-closed mapping of canonical
  `butler_{name}_rw` roles to exact `butler_name` values; an unmapped role sees no rows. Insert
  policy admits only `current_user = 'butlers_dashboard_prompt_rw'`. The generic API/migration
  login must be proven unable to perform application DML after table ownership transfer. This
  design does not depend on API-pool `SET ROLE` and does not select `FORCE ROW LEVEL SECURITY`,
  whose backup implications require separate proof.
- **Required forward design:** the same migration encodes overlay/full-replacement provenance and
  preserves every existing row. The implementation also updates the normal bootstrap/finalizer
  grant path so a rerun cannot restore `core_098`'s broad DML. It must provision the new writer
  credential and pool before cutover; without that separately approved authority, option C
  remains blocked and the no-change default applies. Exact ownership, membership, RLS, sequence,
  backup, and migration mechanics receive independent security review before implementation.
- **Trust statement:** overlay text is trusted owner input, not identity authority. Delimiting and
  always retaining roster content improves provenance but is not a hard semantic sandbox: an LLM
  can still interpret contradictory owner text. The dashboard must warn about that boundary, and
  the owner must approve whether arbitrary overlay text remains acceptable.

### Required scenarios for the future delta

- **Roster root with no overlay:** WHEN no active overlay exists, THEN the final prompt contains
  the resolved roster chain unchanged before the approved dynamic layers.
- **Roster root with overlay:** WHEN an active owner overlay exists, THEN the final prompt still
  contains the resolved roster identity and shared content, plus a delimited overlay with its
  version/source metadata; the database text never substitutes for the roster root.
- **Unavailable overlay store:** WHEN the overlay query fails, THEN spawning continues from the
  roster root and records a content-blind operational warning.
- **Closed source list:** WHEN the final prompt is composed, THEN every segment maps to one named
  requirement and no unnamed source is accepted.
- **Stable order:** WHEN every allowed segment is present, THEN their exact order and separators
  match the core-spawner contract across every runtime adapter.
- **Domain-butler include:** WHEN a default-type/domain roster AGENTS file is validated, THEN its
  first line is the shared include and the resolved prompt contains shared instructions.
- **Staffer policy:** WHEN a staffer AGENTS file is validated, THEN `staffer-archetype` supplies the
  type-wide rule and the concrete infrastructure contract plus capability spec record its
  opt-in/opt-out; QA's prompt bytes remain unchanged only after its MANIFESTO and `staffer-qa`
  record the approved opt-out.
- **Fail-closed API admission:** WHEN the owner key is unconfigured, missing, or wrong, THEN the
  write endpoint returns 503/401 as defined by `require_dashboard_owner_control()` and no database
  pool is acquired; a matching key is required before `authenticated_principal()` is used for
  attribution.
- **Database DML denial under the actual pool model:** WHEN tests use the generic dashboard pool
  with its unchanged no-`SET ROLE` connection, the dedicated writer's direct-login pool, and the
  daemon/connector role contexts by which runtime code actually queries PostgreSQL, THEN only the
  dedicated writer can append an overlay; runtime, connector, and generic API roles cannot INSERT,
  UPDATE, DELETE, or TRUNCATE, and runtime SELECT is limited to its own agent rows.
- **Historical grant removal:** WHEN the forward migration completes, THEN catalog inspection
  proves the DML and sequence grants created by `core_098` are absent for every runtime/connector
  role and remain absent after the repository's normal bootstrap/grant reconciliation path.
- **Audit atomicity:** WHEN owner overlay persistence or audit append fails, THEN neither becomes
  partially committed.
- **Existing-row migration:** WHEN the forward migration runs, THEN every historical prompt row is
  preserved with explicit legacy provenance; no legacy row silently becomes a different kind of
  instruction.

### Verification contract

The future spec-authoring leaf must run strict OpenSpec validation, same-requirement overwrite
checks, semantic review, documentation guards, and `git diff --check`. The future implementation
must extend nearest existing tests, not create a parallel guard:

- `tests/features/test_skills.py` for disk/overlay precedence, both include syntaxes, and shared
  expansion;
- `tests/core/test_core_spawner.py` and `tests/core/test_core_spawner_context.py` for complete
  source order, fail behavior, and adapter handoff;
- `tests/api/test_butler_management.py` for caller attribution, overlay semantics, and atomic audit;
  its denial cases must assert that `require_dashboard_owner_control()` returns 503/401 and that
  the prompt pool was never acquired;
- the existing roster include contract test, renamed or extended to classify domain butlers and
  staffers from `butler.toml` rather than a hand-written allowlist;
- a real-Postgres migration/role test that leaves the generic API connection in its actual
  no-`SET ROLE` state, authenticates the dedicated writer pool directly, and exercises runtime/
  connector roles through their existing role setup, covering existing-row preservation, table
  ownership, RLS read scope, writer-only append, generic API-pool denial, historical DML/sequence
  revocation, and bootstrap re-run resistance.

The implementation is cross-cutting core, schema, API, and roster work. It therefore requires
targeted nodes first, collection after topology changes, migration/contract lanes, guards, and the
terminal protected merge-queue run. A passing string-comparison test alone is not authority proof.

### Rollback

- The forward migration must be additive and preserve old rows so reverting application code does
  not lose prompt history. Old code must be able to ignore new provenance columns safely.
- The effective-prompt behavior needs an operator-visible cutover/rollback path that can restore
  legacy full-replacement selection without rewriting history. The exact mechanism belongs in the
  approved design; a silent emergency edit to rows is not acceptable.
- Travel's eventual include change is independently revertible as one roster commit.
- QA's prompt-file bytes are unchanged by the recommended decision. The required
  `staffer-archetype`, QA MANIFESTO, and `staffer-qa` amendments roll back together if the owner
  withdraws the opt-out policy before implementation.

## Alternatives considered

### A. Codify the database row as full identity replacement

This most closely matches current code and preserves the editor without implementation change. It
is not recommended: arbitrary DB text can erase the git roster identity and shared instructions,
the schema grants broad mutation privileges, and no doctrine amendment approves personality as
operational tuning. Choosing it requires an explicit owner-adopted amendment to Vision Rule 5,
not just a capability-spec edit.

### B. Remove database prompt application and make roster the only source

This most strictly follows Rule 5 and has the smallest runtime authority surface. It makes the
current dashboard editor non-load-bearing unless the product is redesigned to produce a reviewed
git change. It is credible, but larger at the UX/workflow boundary and would retire approved
behavior from PR #2250. Choose it if arbitrary runtime overlays are unacceptable even as trusted
owner input.

### C. Roster-derived identity segment plus explicit owner-operations overlay (recommended)

This preserves the useful live editor while preventing DB presence from deleting roster content.
It requires a real spec, schema, security, API, UI, and runtime change, and it cannot make
contradictory natural-language overlays mechanically harmless. Its advantage is honest provenance:
identity, owner operations, and dynamic context each have one named home and observable order.

## Overlap and downstream authority

At the recorded source, 22 open PRs were inspected by exact file list.

- [Observed] PR #4043 (`agent/bu-hz0g0`, head
  `ab8a20ea7dad7bd50e330c29accf3206aef24bb3`) touches
  `src/butlers/core/spawner.py` and `src/butlers/core/spawner_context.py`. Any future runtime work
  from this packet must serialize with it and refresh the composition contract against its landed
  tree.
- [Observed] A registered worktree for `bu-2jtfw.5` has a dirty
  `src/butlers/core/spawner.py`. Current absence of a PR does not release that ownership.
- [Observed] No open PR touches the proposed documentation artifact, the four canonical governing
  specs, core skills, dashboard management route, historical prompt migration, Travel/QA AGENTS,
  or the roster include test.

Those observations are point-in-time evidence, not reservations.

Downstream release sequence:

1. The owner records one semantic choice (A, B, or C) and whether arbitrary trusted overlay text is
   acceptable. Approval of this research PR is not that decision unless the approval explicitly
   names the option and source SHA.
2. A separately owned feature-request amendment leaf authors the exact OpenSpec delta against a
   freshly inspected baseline. If option A is chosen, it first authors the required doctrine
   amendment. The author inventories both active `AGENTS.md Read/Write Access` deltas and does not
   create a third MODIFIED owner. No implementation is released before owner sign-off and reviewed
   spec merge.
3. Project direction/coordinator work may then allocate non-overlapping implementation leaves.
   Runtime work serializes with PR #4043 and any live spawner owner. Schema/security work receives
   independent review. The k3s and improvement-spine changes must use the rebuild-after-first-
   archive sequence above. Travel conformance may be its own leaf. QA prompt files remain
   unchanged under the recommendation, but the staffer-archetype, QA MANIFESTO, and `staffer-qa`
   governance amendments are required before its opt-out is canonical.
4. The historical `RENAMED` repair from `bu-o2yi6k` remains separately owned by `bu-tk618` and must
   continue to cite this unresolved/decided authority state without claiming runtime fidelity.
5. `bu-w8uno`, `bu-tk618`, and `bu-o2yi6k` retain their recorded closure meanings. Merging this
   packet closes only the `bu-e2zyp6` investigation after independent exact-head review.

## Research verification

Evidence run at the source SHA:

- repository shape scan: all five pillars and local navigators present;
- exact requirement-name search: no active same-named delta for the four governing requirements;
- exact active-body comparison for `AGENTS.md Read/Write Access`: two MODIFIED owners; all 12 k3s
  scenario names occur in the 14-scenario improvement-spine block, whose only two additional
  scenarios are labeled `[TARGET-STATE]`; approval/sequencing/release tasks 2.1-2.4 are unchecked;
- source inspection confirmed `require_dashboard_owner_control()` is fail-closed, while
  `authenticated_principal()` is unconditional attribution, API-managed pools disable `SET ROLE`,
  and `core_098` grants `SELECT, INSERT, UPDATE, DELETE` to every listed runtime/connector role;
- direct `read_system_prompt()` evidence: Travel and QA both report
  `first_line_shared=False` and `resolved_shared_marker=False`;
- `uv run --no-sync pytest tests/features/test_skills.py -q --tb=short -n 0`:
  `11 passed`;
- `uv run --no-sync pytest tests/core/test_core_spawner.py::TestSpawnSystemPromptResolution`
  `tests/core/test_core_spawner.py::TestFetchSystemPromptOverride -q --tb=short -n 0`:
  `6 passed`;
- `uv run --no-sync pytest tests/config/test_roster_claude_md_include.py -q --tb=short -n 0`:
  `2 passed`.
- `make test-plan BASE=origin/main`: documentation-only change, no pytest scope selected;
- `make check-guards`: passed, including both spec-debt guards, citation checks, frontend-copy
  inventory stability, and strict validation of 303 OpenSpec items;
- `git diff --check`: passed.

Research test delta: `Tests: +0 ~0 -0`.
