## Context

This design is grounded at source commit `63de6168fc852fcbf4e8fc24717585f64df0e2a5`.
`docs/plans/system-prompt-authority-decision.md` is the reviewed predecessor evidence map; its
source inspection was refreshed here before drafting. Evidence labels in this document mean:

- **[Observed]** directly present at the historical source commit above or its prior Git history;
  these inventories are not assertions about the current runtime;
- **[Target]** the RFC0038-conforming contract after this spec-only rescope; implementation and
  live operation remain separately authorized and unperformed;
- **[Boundary]** explicitly outside this change's authority.

### Historical prompt-source inventory (63de6168)

| Order | [Observed] source and seam | Behavior at historical baseline | [Target] disposition |
| ---: | --- | --- | --- |
| 1 | `public.system_prompt_history.prompt` via `fetch_system_prompt_override()` | The highest non-blank version replaces the roster base; query failure returns no override. | History retains exact provenance; no row replaces a goal system prompt or becomes an automatic runner input. |
| 2 | `roster/{name}/CLAUDE.md` via `read_system_prompt()` | Used only when no DB override exists; missing/blank content produces a generated generic prompt. | Versioned domain guidance, loaded by the server and referenced by briefs; not the lead or child system-prompt root. |
| 3 | Recursive bare references, including `CLAUDE.md -> AGENTS.md -> ../shared/AGENTS.md` | `core/skills.py` resolves them relative to the containing file, confines the result to the roster root, preserves missing/unsafe/cyclic directives, and logs. | Core-skills resolves required guidance references with confinement and cycle/missing/read-failure bounds; failure never supplies partial guidance or a generic identity. |
| 4 | HTML `<!-- @include ... -->` references | `core/skills.py` resolves roster-relative safe paths non-recursively and preserves missing/unsafe directives. | The non-recursive HTML contract stays separate; owner text remains literal and never initiates an include read. |
| 5 | `roster/shared/BUTLER_SKILLS.md`, then `MCP_LOGGING.md` | Appended by `process_system_prompt_base()` to either the disk or DB-selected base. | Remain ordered shared guidance content for declared participants, not automatic goal prompt suffixes. |
| 6 | General timezone/locale/date/time/week/currency/measurement-system instruction | Fetched from general settings and appended first after the processed base; failure omits it. | No automatic goal prompt suffix; any required facts use the admitted brief or stamped domain/context reads under D15. |
| 7 | Situational context preamble | Fetched from `public.user_context`; failure logs and omits it. | Retired as session injection under D18; relevant facts are retrieved through authorized stamped capabilities. |
| 8 | Blind-spot disclosure | Appended for declared absent/unmeasurable signals; query failure produces explicit disclosure; a runtime flag may disable it. | Preserve honest absent/unmeasurable-source and fail-closed disclosure obligations in returned evidence; no automatic prompt suffix or authority upgrade. |
| 9 | Switchboard routing instructions | Appended only for Switchboard, sorted by priority; failure omits them. | No domain-session routing suffix; goal admission and the lead policy own control-plane routing under RFC0038. |
| 10 | Memory context | Appended when memory is enabled and available; failure omits it. | Retrieved through stamped context/domain read capabilities, not automatically appended; private custody remains unreachable. |
| 11 | Runtime adapter | Receives the already composed system prompt. | Receives the runner-composed lead or child input, byte-identical across transports; no extra source or include expansion. |

### Historical writer and authority inventory (63de6168)

| [Observed] path | Authority at historical baseline | [Target] treatment |
| --- | --- | --- |
| Git edits to `roster/{name}/{CLAUDE.md,AGENTS.md}` and `roster/shared/*.md` | Repository writers can change identity/shared prompt bytes through normal review. | Remains reviewed authority for versioned guidance; lead policy and owner profile govern leads, briefs govern children. |
| `write_agents_md()` / `append_agents_md()` in `src/butlers/core/skills.py` | Direct filesystem helpers exist; no production caller or registered MCP tool was found. | Not modified here; AGENTS read/write ownership remains with specify-improvement-proposal-spine, not this delta. |
| `PUT /api/butlers/{name}/prompt` | Any admitted dashboard caller can append unrestricted prompt text through `credential_shared_pool()`; actor attribution is server-derived but no owner-specific dependency runs. | Owner-only operations management retains validation/CAS/atomic audit; it confers no goal-prompt or runtime activation authority. |
| `GET /api/butlers/{name}/prompt` and `/history` | Any admitted dashboard caller can read raw stored prompt rows through the generic shared pool. | Owner-only provenance/history through the dedicated pool; guidance and stored operations are not presented as a complete goal system prompt. |
| Direct SQL by runtime roles and `connector_writer` | `core_098` granted `SELECT, INSERT, UPDATE, DELETE` on the whole table to every named role. | Revoke broad DML; preserve fixed current_user mapping as a database upper bound only, never a goal grant or runner credential. |
| Direct SQL by the generic dashboard/migration login | The API pool does not `SET ROLE`; its login inherits broad table authority. | Generic API has no protected-text/DML authority; migration and backup remain separately authorized operational paths. |
| Direct SQL by the proposed prompt writer | Does not exist. | Future dedicated LOGIN NOINHERIT NOBYPASSRLS writer and pool preserve append-only management; no runner/domain credential reuse. |
| Table ownership | The migration login creates and owns the table. | Future NOLOGIN NOINHERIT NOBYPASSRLS owner is not granted to application roles. |

Travel is **[Observed at 63de6168] conforming**: PR #4154 added `@../shared/AGENTS.md` as the first line of
`roster/travel/AGENTS.md`. QA is **[Observed at 63de6168] deliberately different**: PR #3110 recorded its
missing shared include as intentional because QA is an infrastructure staffer. This design keeps
Travel's bytes and QA's `CLAUDE.md`/`AGENTS.md` bytes unchanged.

## Goals / Non-Goals

**Goals:** make this existing draft conform to adopted RFC0038 D1/D2/D9/D15/D18; retain safe,
versioned guidance loading and QA's exception; preserve owner-management security, history,
least-privilege and future real-PG proof obligations without creating an extra goal prompt source.
Structural provenance never promises model obedience or authentication from text.

**Non-Goals:** no implementation, prompt/roster edits, schema/role/credential mutation, runtime or
DB access, activation, deployment, archive or release; no RFC decision change, new owner decision,
new goal/runtime mode, wire mechanism or authority grant; no change to AGENTS read/write ownership,
QA prompt bytes or Travel's already-merged repair.

## Decisions

### D1: Goal prompt authority and explicit disposition of the old eight layers

RFC0038 D1/D15 governs leads: versioned lead policy at `roster/switchboard/goal-policy/LEAD.md`
and the owner profile, plus fenced goal input, domain/guidance index and on recovery the ledger
summary. D9 governs children: the objective, success conditions, supplied context references,
governing guidance references and expected result schema in their brief. The runner alone launches
these invocations. All other context is retrieved through stamped `context.search`, domain read
capabilities and `guidance.load`; private-custody content remains unreachable. Content does not
change origin, exposure, grants or owner authority.

The historical inventory's eleven target dispositions are the complete old-to-new source map.
Its old eight-layer composition is superseded, not retained as an alternate goal mode: roster and
shared snippets become versioned guidance; operations rows remain owner-managed history/content
without automatic prompt inclusion; settings, signal evidence and memory require authorized
retrieval; situational injection and domain routing suffixes do not survive. Honest blind-spot and
source-availability disclosure remains mandatory in evidence. No generic extension point or
unnamed source can add system instructions.

The server resolves declared guidance before returning it, with its domain/name/version and
provenance; child briefs reference the governing version. Shared `BUTLER_SKILLS.md` and then
`MCP_LOGGING.md` retain their declared order within applicable guidance, not as runner suffixes.
Valid returned text preserves its bytes and separators. Owner operations remain literal, bounded,
versioned content with reserved BEGIN/END marker validation and `source=owner_operations`,
`trust=trusted_owner_input` provenance; neither markers nor this trust label confer grants or
permission to inject it. Adapters receive the same runner-composed bytes and may change transport
only, without filesystem expansion, reordering or additional system sources.

Engineering executors are the separate RFC0038 D18/RFC0015 case: their existing launching guidance
and approved shared snippets can be resolved without situational-context injection, with only the
anonymized payload and isolated shell/worktree contract. This does not exempt staffer triage goals
or re-enable domain reasoning sessions.

### D2: Required guidance resolves safely; no domain prompt fallback

Domain `CLAUDE.md` contains only `@AGENTS.md`; domain `AGENTS.md` begins with
`@../shared/AGENTS.md`. The server resolves each bare reference relative to its containing file,
confined to the canonical roster root. A blank/missing required root, missing/read-failed target,
escape or revisit of a file on the active chain fails with a fixed content-blind category. It
returns no partial guidance, literal unresolved required directive or generated generic identity.
HTML includes retain their separate roster-relative, traversal-safe, non-recursive behavior.
Unknown agent names fail before protected-store access. No adapter or database row rescues an
invalid required guidance graph. A child requiring that guidance cannot start with a fabricated
replacement; a lead's policy/profile identity is never replaced by domain text.

The old `roster_overlay`, `precutover_legacy_hold` and `legacy_full_replacement` selectors are
historical compatibility records, not goal-runtime system-prompt modes. No owner acknowledgement,
recorded selection, deadline or database fallback can make them a D15 exception. See D6 for the
retained history, security and operational rollback obligations.

### D3: Staffer shared instructions are explicit governance, not file-shape inference

`staffer-archetype` owns the type-wide rule: each staffer's infrastructure contract explicitly says
whether its versioned guidance composes domain shared instructions, and its capability spec exposes the result. No
implicit default is inferred from another staffer's file layout. A missing declaration is invalid
for a newly added or changed staffer. This declaration concerns guidance participation, not a
domain-daemon prompt or permission to bypass the lead/child contract.

QA's approved choice is opt-out. Its future governance-only implementation updates
`roster/qa/MANIFESTO.md` and `staffer-qa` together while keeping QA's existing `CLAUDE.md` and
`AGENTS.md` bytes unchanged. Existing staffer opt-ins are preserved. QA triage remains an exposed staffer goal under RFC0038;
its investigation agents remain the isolated D18 engineering executors, not ordinary domain sessions.
Travel is a domain butler and
already conforms; this change does not recast it as an exception.

### D4: Overlay API is owner-only, versioned, bounded, and compare-and-swap

The existing prompt route paths retain their owner-management security obligations. Their projection
is guidance/operations provenance, never the complete goal system prompt or a runner activation API:

- `GET /api/butlers/{name}/prompt` returns an owner-only guidance/operations projection with the resolved
  versioned roster guidance content and digest, the current overlay state (content, active, version, source,
  trust label, timestamp, server-derived actor), historical mode and mode-version provenance clearly labeled non-authorizing for goal sessions,
  the applicable guidance source names, and
  `semantic_enforcement=false`.
- `PUT /api/butlers/{name}/prompt` accepts exactly `overlay` (string), `active` (boolean), and
  `expected_version` (non-negative integer). Unknown fields are rejected.
- `GET /api/butlers/{name}/prompt/history` returns overlay/legacy provenance per version and never
  labels a legacy full replacement as an overlay.
- The old `PUT /api/butlers/{name}/prompt/mode` target is superseded as a goal prompt selector.
  Its historical records retain their provenance and integrity protections; no mode request can
  authorize a legacy goal prompt, a hold re-entry or domain reasoning sessions. No replacement wire
  operation is selected by this amendment.

Owner control runs before body buffering, pool acquisition, roster content reads, or protected
state access. Unconfigured owner control returns 503; a missing or mismatched credential returns
401. `authenticated_principal()` supplies attribution only after admission. Unknown roster names
return 404 before any prompt pool is acquired.

Overlay text is at most 65,536 UTF-8 bytes. An active overlay must be non-blank. A disabled state may
carry an empty overlay. NUL, invalid UTF-8, or either reserved delimiter marker returns a fixed 422
without echoing submitted text. Valid text is stored byte-for-byte.

PUT executes under one transaction and per-agent serialization lock. After validation:

- an exact state match is a 200 no-op at the current version, even for a retry with a stale
  `expected_version`;
- a divergent state with matching current version appends exactly one version and its audit record;
- a divergent stale version returns 409 with the current version but no current or submitted text;
- concurrent divergent writers from one version produce at most one commit;
- concurrent identical writers converge on one commit plus one no-op response;
- any persistence or audit failure rolls back both.

A committed overlay change emits `butler.prompt_overlay_set` with target equal to the canonical
agent name and metadata limited to `version`, `active`, `byte_count`, `digest`, and `roster_digest`.
An exact no-op emits no audit event. No audit note or field contains roster or overlay text.

The historical mode contract accepted exactly `mode`, `expected_mode_version` and
`acknowledge_identity_replacement`, behind the same pre-body owner control, dedicated pool,
per-agent serialization and immutable `system_prompt_mode_history`. Its retained integrity duties
are stale-divergent 409, identical-state no-op, at most one winner and atomic metadata-only audit
(`butler.prompt_mode_changed`: canonical target; `mode_version`, `from_mode`, `to_mode`,
`overlay_version`, `roster_digest` only). These duties protect historical evidence; they do not
permit a mode append to alter a goal prompt. A request to select an obsolete goal mode fails closed
without a write or content disclosure. This amendment does not implement or activate that route.

The old rollback source `BUTLERS_PROMPT_LEGACY_ROLLBACK_UNTIL` was deployment-owned RFC3339 UTC
configuration, closed when missing, malformed or expired; no request, row, MCP tool, runtime or
generic API could create or extend it. Preserve that authority boundary and content-blind evidence
for historical/operational review, including the explicit acknowledgement and existing-legacy-head
requirements of the old mechanism. Even an open old window grants no post-cutover goal-mode
exception. A historical selected/expired state is disclosed to the owner as blocked, never silently
continued, automatically converted into a live overlay or attributed to an owner who did not choose
it. Safe exit from obsolete state must remain possible under separately authorized migration and
owner control; it cannot reopen legacy selection or rewrite history. Deployment/restore authority
remains separate from the spec amendment.

Alternative rejected: `MAX(version)+1` alone. It detects collisions only through a database error
and does not give callers a usable lost-update contract.

### D5: The database enforces the writer and reader boundary independently

A forward migration creates:

- `butlers_dashboard_prompt_rw`: `LOGIN NOINHERIT NOBYPASSRLS`, used by one dedicated asyncpg pool;
- `butlers_system_prompt_history_owner`: `NOLOGIN NOINHERIT NOBYPASSRLS`, owns the table and is not a
  member of or granted to application roles.

The migration revokes `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`, and `TRIGGER` from
`PUBLIC`, every runtime role, `connector_writer`, and the generic API login, plus associated sequence
privileges. The prompt writer receives only table `SELECT, INSERT` and required sequence usage. Rows
are append-only through application paths; no application role receives update/delete/truncate.

The generic dashboard login itself must be `NOSUPERUSER NOBYPASSRLS` and must not own or inherit the
owner roles. Schema migration and backup/restore use a separately provisioned operational principal
that is never supplied to the dashboard process. Without that credential split, grant revocation and
RLS would not independently constrain a generic API pool authenticated as a superuser.

RLS is enabled. A fixed migration-owned mapping from canonical `butler_{name}_rw` current users to
exact `butler_name` values permits each runtime role to select only its own rows. An unknown runtime
role, connector, or generic API login sees zero rows. The prompt writer may select and insert all
agents, and INSERT policy requires `current_user = 'butlers_dashboard_prompt_rw'`. No policy trusts a
caller-supplied agent name without the role check. The design does not depend on the generic API
pool performing `SET ROLE`, and does not select `FORCE ROW LEVEL SECURITY`; backup/restore access is
owned by the separate operational role path.

The old domain-spawner read through an agent-local runtime pool is superseded as a goal-prompt
input path. Retain the fixed role-to-agent mapping as a database upper bound: a mapped runtime role
can see at most its own rows and fallback/shared/unmapped logins see none. This mapping is not a
goal grant, and the runner does not inherit domain credentials or gain cross-agent overlay access.
No unavailable/denied store can replace lead policy, owner profile or a child brief; any separately
authorized read remains capability/grant-bound with content-blind failure evidence.

The repository's bootstrap/finalizer grant reconciliation must encode the same restricted end state
so rerunning it cannot restore `core_098`'s grants. Real-PostgreSQL checks use actual direct-login,
generic API, connector, and runtime-role connections, not role-name mocks.

### D6: Legacy provenance is retained; compatibility is not goal authority

Every pre-existing prompt row retains its exact version, text, timestamp and actor and is labeled
`legacy_full_replacement`, never copied, reinterpreted, deleted or activated as owner operations.
Any future operations row is append-only with explicit source/trust and the guidance digest observed
at acceptance; a digest is provenance, not authority to pin stale guidance or replace a goal prompt.

The old additive schema/mode-history design, per-agent preparation and rollback controls remain
unimplemented historical migration obligations. Their integrity requirements survive: immutable
versions with server attribution, the same non-login owner/direct writer/RLS/sequence restrictions,
no fabricated owner selection for migration-seeded state, no new-agent hold or hold re-entry, no
implicit activation from useful-looking legacy text, and no restoration of broad grants. Existing
mode names are history only in the goal target; they cannot select domain sessions or override D15.
No migration is authorized to seed a new live `precutover_legacy_hold`, activate `roster_overlay`,
or expose `legacy_full_replacement` as an alternate post-cutover goal runtime.

An authenticated owner's explicit review is still required before any future use of legacy
operations text. Independent exact-head security review, separately authorized implementation,
credential/privilege preparation and real-PG proof precede any live storage/API change. Failure at
one stage blocks the next and preserves the prior verified stage and every row; it never reports
partial success. The RFC0038 hard cutover does not run old and new reasoning paths side by side.
Operational rollback/restore is a separately authorized act, with exact prior-version evidence,
acknowledgement and a bounded observation window, not a per-agent goal prompt-mode switch. It never
rewrites history, bypasses expired authority or restores generic/runtime/connector DML. A later
reviewed retirement may remove obsolete selection machinery, retaining its historical rows and
provenance; this spec amendment performs neither activation nor retirement.

### D7: Structural provenance does not enforce natural-language precedence

The server guarantees declared guidance resolution, version/provenance and writer authority. It does
not guarantee model obedience when natural-language content conflicts. The dashboard persistently
warns before an owner-management write, discloses that guidance/operations are not the complete goal
prompt, and reports `semantic_enforcement=false`. Explicit owner confirmation does not activate a
goal prompt source. Tests prove structure, provenance and authorization; string comparison or one
sampled model response cannot prove semantic isolation.

Overlay content is trusted owner input rather than untrusted user or connector input. No MCP tool,
connector, scheduled task, runtime session, or generic dashboard route may write it. Audit, logs,
metrics, traces, and non-owner errors may contain only agent name, version, active state, byte count,
digest, outcome, and server-derived actor; they never contain overlay or roster text.

### D8: Active core-skills ownership is preserved, not merged here

One active change contains a full `MODIFIED` block for the exact requirement
`core-skills / AGENTS.md Read/Write Access` (the former second owner,
`k3s-deployment-helm-chart`, was abandoned and removed on 2026-09-26):

- `specify-improvement-proposal-spine` adds a target prohibition on runtime-facing direct writers.

This change adds the differently named `Recursive Bare File-Reference Resolution` requirement and
does not restate, modify, remove, or resolve `AGENTS.md Read/Write Access`. Any future archive must re-scan current same-name ownership and rebuild against the refreshed
baseline, including the generalist base/spawner blocks. This change does not adopt the separately
owned AGENTS read/write candidate semantics or edit the generalist delta.

## Risks / Trade-offs

- Owner text can contradict guidance: preserve provenance, restricted writers and persistent honest
  warnings; never claim delimiters enforce obedience, authenticate text or widen a goal grant.
- Required guidance can be unavailable: fail its required load/child dependency content-blind;
  never substitute a database-defined identity or a generated agent for the lead/child contract.
- RLS can be bypassed by ownership or inherited credentials: retain non-login ownership,
  NOINHERIT/NOBYPASSRLS roles, direct-login/catalog/sequence and bootstrap-rerun proof obligations.
- Historical inventories can be mistaken for runtime evidence: retain the explicit 63de6168 anchor;
  do not promote their old no-consumer or role observations to current proof.
- A stale whole-requirement delta can overwrite a sibling: exact-head semantic review and same-name
  scans remain mandatory before eventual archive. The only ratchet change is the authorized
  `clause / Interactive response mode / e69f93fa9eaa` append under
  `specify-roster-identity-owner-operations-overlay/butler-base-spec/CLAUDE.md as System Prompt Entry Point`
  in `scripts/spec-overwrite-baseline.json`. No canonical baseline spec or RFC is changed, and no
  global regeneration or matcher/threshold change occurs.

## Migration Plan

This section records future implementation/operational duties, not a parallel prompt-runtime plan.
The current conversion is PENDING exact-head independent review and hosted source/protected gates.
Its four completed draft tasks remain historical; no implementation, activation or archive task is
ticked by this amendment. RFC-conforming text itself requires no new exact-text owner adoption.

1. Independently review the exact conforming spec commit. Any future non-conformance feature
   adoption remains separate; the old direction approval is not live implementation authority.
2. Before separately authorized storage changes, verify schema-aware compatibility, dedicated-pool
   and credential handling, response parsing and failure cleanup without making an old selector a
   second live goal runtime. Provision credentials only through the separately authorized path.
3. Preserve all rows/provenance and verify counts/digests, table ownership, RLS, historic grant
   revocation, direct-login behavior and bootstrap rerun before protected writes become load-bearing.
   Retain historical mode evidence without seeding selectable goal compatibility states.
4. Require authenticated owner review before any reuse of legacy operations text. Never infer,
   copy or activate it automatically; no API write becomes a goal system-prompt selection.
5. Verify the RFC0038 single hard cutover, lead-policy/owner-profile and child-brief boundaries,
   versioned guidance/include behavior, honest source failures, Travel conformance and QA's opt-out.
   Preserve owner attribution/CAS/atomic audit with metadata-only evidence for any authorized writes.
6. A separately authorized operational rollback/restore must retain exact prior-stage evidence,
   bounded approval and least privilege; it cannot reinstate legacy selection inside the goal runtime,
   delete rows, restore broad DML or change QA/Travel prompt bytes. Failure preserves the last
   verified stage and blocks further transition.
7. A later reviewed change may retire obsolete selection machinery after observation, retaining
   history. Eventual sync/archive requires rebuilding all same-name blocks against the then-current
   baseline. Deployment, runtime activation, restore, archive and release retain separate authority.

### Complete mechanism disposition

| Original clause family | Goal-runtime disposition and retained obligation |
| --- | --- |
| D1 eight layers and exact separators | Superseded by lead policy/profile and child brief; preserve deterministic bytes and separators within loaded guidance, source labels and adapter invariance, with no extra system source. |
| D2 roster session identity and generic fallback | Versioned, confined required guidance; reject invalid graph/partial fallback; no domain daemon session composition. |
| D3 shared staffer instructions | Explicit versioned-guidance participation, QA opt-out unchanged; D18 executors are distinct from exposed staffer goals. |
| D4 full prompt/overlay projection | Owner-only guidance/operations provenance; no complete-goal-prompt claim; preserve pre-access auth, CSRF/Origin, bounds, attribution, CAS/no-op/concurrency and atomic audit. |
| D4 mode PUT, acknowledgement and deadline | Historical/operational safeguards only; obsolete goal-mode selection denied even with acknowledgement/open old window; no new route selected. |
| D5 runtime overlay pool and role mapping | No automatic runner/domain overlay input; fixed RLS mapping remains an upper bound, not a grant or runner credential. Preserve writer/owner/generic/sequence/bootstrap boundaries. |
| D6 seeded hold, per-agent cutover and rollback | Preserve exact row and mode provenance, review/no-auto-copy, one-way/no-new-agent historical facts; single RFC hard cutover, separately authorized operational restore, no alternate live goal selector. |
| D7 natural-language precedence | Provenance is not semantic isolation or authority; literal bounded text, allowlisted metadata and honest warning remain. |
| D8 AGENTS read/write and future archive | Separate owner untouched; re-scan/rebuild exact same-name requirements at eventual archive. |
