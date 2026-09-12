## Context

This design is grounded at source commit `63de6168fc852fcbf4e8fc24717585f64df0e2a5`.
`docs/plans/system-prompt-authority-decision.md` is the reviewed predecessor evidence map; its
source inspection was refreshed here before drafting. Evidence labels in this document mean:

- **[Observed]** directly present in this source tree or its Git history;
- **[Target]** proposed by this change and inert until separately reviewed, owner-adopted, and
  implemented;
- **[Boundary]** explicitly outside this change's authority.

### Current prompt-source inventory

| Order | [Observed] source and seam | Current behavior | [Target] disposition |
| ---: | --- | --- | --- |
| 1 | `public.system_prompt_history.prompt` via `fetch_system_prompt_override()` | The highest non-blank version replaces the roster base; query failure returns no override. | Historical rows remain history. A new active row may contribute only a literal, delimited owner-operations overlay. |
| 2 | `roster/{name}/CLAUDE.md` via `read_system_prompt()` | Used only when no DB override exists; missing/blank content produces a generated generic prompt. | The roster root is mandatory in `roster_overlay` mode. Existing agents may remain in one-way `precutover_legacy_hold`; target-mode missing/invalid roots block spawn. |
| 3 | Recursive bare references, including `CLAUDE.md -> AGENTS.md -> ../shared/AGENTS.md` | `core/skills.py` resolves them relative to the containing file, confines the result to the roster root, preserves missing/unsafe/cyclic directives, and logs. | Core-skills owns this behavior; an unresolved required identity reference is an invalid roster root, not prompt text passed to a runtime. |
| 4 | HTML `<!-- @include ... -->` references | `core/skills.py` resolves roster-relative safe paths non-recursively and preserves missing/unsafe directives. | Existing contract remains separate. Owner overlay text is literal and never interpreted as either include syntax. |
| 5 | `roster/shared/BUTLER_SKILLS.md`, then `MCP_LOGGING.md` | Appended by `process_system_prompt_base()` to either the disk or DB-selected base. | Appended only to the roster identity segment, in the existing stable order, before the owner overlay. |
| 6 | General timezone/locale/date/time/week/currency/measurement-system instruction | Fetched from general settings and appended first after the processed base; failure omits it. | Enumerated dynamic layer after the optional overlay; content-blind warning on failure. |
| 7 | Situational context preamble | Fetched from `public.user_context`; failure logs and omits it. | Enumerated after general settings; existing availability behavior retained. |
| 8 | Blind-spot disclosure | Appended for declared absent/unmeasurable signals; query failure produces explicit disclosure; a runtime flag may disable it. | Enumerated after situational context; existing fail-closed disclosure contract retained. |
| 9 | Switchboard routing instructions | Appended only for Switchboard, sorted by priority; failure omits them. | Enumerated after blind-spot disclosure and rejected for other agents. |
| 10 | Memory context | Appended when memory is enabled and available; failure omits it. | Enumerated last; existing availability behavior retained. |
| 11 | Runtime adapter | Receives the already composed system prompt. | Every adapter receives the same composed bytes and may not add another system-prompt source. |

### Current writer and authority inventory

| [Observed] path | Authority today | [Target] treatment |
| --- | --- | --- |
| Git edits to `roster/{name}/{CLAUDE.md,AGENTS.md}` and `roster/shared/*.md` | Repository writers can change identity/shared prompt bytes through normal review. | Remains the sole identity/shared-content authority. |
| `write_agents_md()` / `append_agents_md()` in `src/butlers/core/skills.py` | Direct filesystem helpers exist; no production caller or registered MCP tool was found. | Not modified here. Their future runtime/DB-fallback policy remains owned by the two active `AGENTS.md Read/Write Access` deltas. |
| `PUT /api/butlers/{name}/prompt` | Any admitted dashboard caller can append unrestricted prompt text through `credential_shared_pool()`; actor attribution is server-derived but no owner-specific dependency runs. | Becomes a fail-closed owner-only overlay PUT through a dedicated direct-login pool. |
| `GET /api/butlers/{name}/prompt` and `/history` | Any admitted dashboard caller can read raw stored prompt rows through the generic shared pool. | Both become fail-closed owner-only, distinguish roster identity from overlays, and use the dedicated prompt pool. |
| Direct SQL by runtime roles and `connector_writer` | `core_098` granted `SELECT, INSERT, UPDATE, DELETE` on the whole table to every named role. | All DML is revoked. Each mapped runtime role can select only its own agent rows; connectors see none. |
| Direct SQL by the generic dashboard/migration login | The API pool does not `SET ROLE`; its login inherits broad table authority. | The generic API pool cannot select protected text or perform application DML. Migration authority remains operationally separate from the application path. |
| Direct SQL by the proposed prompt writer | Does not exist. | A dedicated `LOGIN NOINHERIT NOBYPASSRLS` role and dedicated pool can select and append overlay rows only. |
| Table ownership | The migration login creates and owns the table. | A dedicated `NOLOGIN NOINHERIT NOBYPASSRLS` role owns the table and is not granted to application roles. |

Travel is now **[Observed] conforming**: PR #4154 added `@../shared/AGENTS.md` as the first line of
`roster/travel/AGENTS.md`. QA remains **[Observed] deliberately different**: PR #3110 recorded its
missing shared include as intentional because QA is an infrastructure staffer. This design keeps
Travel's bytes and QA's `CLAUDE.md`/`AGENTS.md` bytes unchanged.

## Goals / Non-Goals

**Goals:**

- Make roster identity structurally non-replaceable while retaining a useful, explicitly trusted
  owner-operations overlay.
- Give every allowed prompt layer one owner, fixed precedence, explicit separators, and a named
  failure posture.
- Make owner overlay read/write authority fail closed at both HTTP admission and PostgreSQL role/RLS
  boundaries under the pools the product actually uses.
- Preserve history, prevent lost updates, keep audit and persistence atomic, and define a reversible
  staged cutover.
- State the limit honestly: structural provenance does not make contradictory natural-language
  instructions harmless.

**Non-Goals:**

- No implementation, migration, role, credential, roster, prompt, database, deployment, archive,
  or runtime mutation in this change.
- No adoption of the k3s DB-notes fallback or improvement-proposal target state, and no modification
  of `core-skills / AGENTS.md Read/Write Access`.
- No natural-language policy engine, semantic conflict detector, prompt-injection classifier, or
  claim that delimiters force an LLM to privilege identity text.
- No new browser credential transport. A future UI remains bound by the existing owner-control
  contract or a separately approved successor.
- No change to QA prompt bytes and no further Travel repair.

## Decisions

### D1: Closed composition with one structural owner per layer

The final system prompt is assembled in exactly this order:

In `roster_overlay` mode, the final system prompt uses:

1. resolved roster identity (`CLAUDE.md` and recursive bare references);
2. shared `BUTLER_SKILLS.md`, then `MCP_LOGGING.md`;
3. optional active owner-operations overlay;
4. general timezone/locale/date/time/week/currency/measurement-system instructions;
5. situational context;
6. blind-spot disclosure;
7. Switchboard routing instructions, for Switchboard only;
8. memory context.

Present adjacent layers use exactly one blank line between them. Optional absence removes the whole
layer and its separator. The server wraps an active overlay with reserved BEGIN/END markers carrying
only `source=owner_operations`, `trust=trusted_owner_input`, and its integer version. Submitted
overlay text containing either reserved marker is invalid. Overlay text is otherwise literal: bare
or HTML include-looking lines are not resolved.

Every runtime adapter receives the already composed bytes. An adapter may translate transport but
may not resolve roster references, reorder layers, or add a system-prompt source. This closed list
replaces the baseline's inaccurate "no other source" clause without opening a generic extension
point.

Alternative rejected: preserving the DB row as a full base keeps current code small but violates
the owner-approved direction and allows mutable text to erase roster identity. Alternative rejected:
putting the overlay before shared snippets lets later generic text accidentally override the owner's
operational instruction in ordinary model interpretation. The selected order keeps authority
visible, not mechanically enforced.

### D2: Roster identity is an admission invariant, not a mutable fallback

A configured roster agent must have a non-blank `CLAUDE.md` whose required bare-reference graph
resolves completely inside the roster root. A missing file, blank root, missing target, roster
escape, or cycle fails composition before the adapter starts. A database row cannot rescue that
failure. Unknown agent names fail before prompt-store access.

Domain butlers continue to require `CLAUDE.md` to contain only `@AGENTS.md` and `AGENTS.md` to begin
with `@../shared/AGENTS.md`. The current generic `You are the {name} butler.` fallback is retired for
admitted roster agents because it cannot preserve their roster identity or distinguish staffers.

Alternative rejected: retain the generated fallback. It improves availability but silently starts
an agent without its governing identity, which conflicts with Vision Rules 5 and 6 and the selected
direction.

### D3: Staffer shared instructions are explicit governance, not file-shape inference

`staffer-archetype` owns the type-wide rule: each staffer's infrastructure contract explicitly says
whether it composes domain shared instructions, and its capability spec exposes the result. No
implicit default is inferred from another staffer's file layout. A missing declaration is invalid
for a newly added or changed staffer.

QA's approved choice is opt-out. Its future governance-only implementation updates
`roster/qa/MANIFESTO.md` and `staffer-qa` together while keeping QA's existing `CLAUDE.md` and
`AGENTS.md` bytes unchanged. Existing staffer opt-ins are preserved. Travel is a domain butler and
already conforms; this change does not recast it as an exception.

### D4: Overlay API is owner-only, versioned, bounded, and compare-and-swap

All three existing routes retain their paths but change representation and authority:

- `GET /api/butlers/{name}/prompt` returns an owner-only composition projection with the resolved
  roster identity content and digest, the current overlay state (content, active, version, source,
  trust label, timestamp, server-derived actor), the selected composition mode and mode version,
  the ordered layer names, and
  `semantic_enforcement=false`.
- `PUT /api/butlers/{name}/prompt` accepts exactly `overlay` (string), `active` (boolean), and
  `expected_version` (non-negative integer). Unknown fields are rejected.
- `GET /api/butlers/{name}/prompt/history` returns overlay/legacy provenance per version and never
  labels a legacy full replacement as an overlay.
- `PUT /api/butlers/{name}/prompt/mode` appends a separately versioned composition-mode transition;
  it is the only application path that can select `roster_overlay` or the temporary
  `legacy_full_replacement` rollback mode.

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

The mode PUT accepts exactly `mode`, `expected_mode_version`, and
`acknowledge_identity_replacement`. It runs behind the same pre-body owner control and dedicated
pool, takes the same per-agent serialization boundary, and appends to an immutable
`system_prompt_mode_history` relation owned and protected like prompt history. A divergent stale
mode version returns 409; identical state is a no-op. Selecting `legacy_full_replacement` additionally
requires `acknowledge_identity_replacement=true`, an existing legacy head, and an open approved
rollback window. A committed transition emits `butler.prompt_mode_changed`, target equal to the
canonical agent name, and metadata limited to `mode_version`, `from_mode`, `to_mode`,
`overlay_version`, and `roster_digest`. Mode persistence and audit are one transaction.

The rollback-window source is deployment-owned environment configuration
`BUTLERS_PROMPT_LEGACY_ROLLBACK_UNTIL`, parsed as an RFC 3339 UTC timestamp. A separately authorized
deployment may set or shorten it; HTTP requests, prompt/mode rows, MCP tools, runtime sessions, and
generic API mutation paths cannot create or extend it. The mode route reads it server-side. Missing,
malformed, or expired configuration means closed and returns the same fixed content-blind conflict
as any other unavailable rollback. The timestamp itself may appear in owner-only status but not in
runtime prompt text, audit notes, error details, logs, metrics, or traces.

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

The spawner stops preferring the credential shared pool for this table and reads the current overlay
through its agent-local runtime pool after the existing runtime-role setup. If that role is missing
and the pool falls back to the shared login, the RLS/grant boundary yields no overlay; composition
continues roster-only with a content-blind warning. This preserves identity availability without
turning graceful role fallback into cross-agent prompt-read authority.

The repository's bootstrap/finalizer grant reconciliation must encode the same restricted end state
so rerunning it cannot restore `core_098`'s grants. Real-PostgreSQL checks use actual direct-login,
generic API, connector, and runtime-role connections, not role-name mocks.

### D6: Legacy rows are preserved and never silently reinterpreted

The additive schema records `prompt_kind` (`legacy_full_replacement` or
`owner_operations_overlay`), `active`, roster digest, and trust/source provenance. Every pre-existing
row is backfilled as `legacy_full_replacement`; none is automatically active as an overlay. New
overlay rows are append-only and record the roster digest observed at acceptance so history can show
which immutable identity revision they accompanied. The runtime always reads the current roster
bytes; the digest is provenance, not a mechanism for pinning stale identity.

An append-only `system_prompt_mode_history` relation records per-agent selection, mode version,
timestamp, and server-derived actor. It shares the prompt table's non-login owner, direct-login
writer, RLS mapping, and denial of generic/runtime/connector DML. Runtime roles may read only their
own current mode. Existing agents receive an initial `precutover_legacy_hold`; new agents created
after migration start in `roster_overlay`. The hold preserves the compatibility release's selector
until owner review, cannot be selected through the mode API, and cannot be re-entered after the owner
leaves it. The owner-only mode API is the sole application writer after migration. This table makes
rollback authority explicit without granting a second generic operator or SQL mutation path.

Both legacy modes are migration-only compatibility exceptions to roster-rooted identity.
`precutover_legacy_hold` is migration-seeded existing state and needs no owner action because it
prevents the migration itself from changing prompt meaning. `legacy_full_replacement` is a distinct
post-cutover rollback selected explicitly through the bounded mode API while the deployment-owned
window is open. Both apply the compatibility release's pre-cutover DB-base selector and dynamic
suffixes, are labeled identity-replacing in owner projections, and omit owner-operations overlays.
Neither can become the default for a new agent or survive final legacy-mode retirement.

The owner must review each agent's legacy head and explicitly create or activate an overlay through
the owner-only API. Copying useful operations text is an owner action; migration code does not infer
which sentences are identity versus operations.

### D7: Structural provenance does not enforce natural-language precedence

The server guarantees presence, ordering, delimiters, source labels, and writer authority. It does
not guarantee that an LLM will obey roster identity over contradictory overlay text. The dashboard
shows a persistent warning before write/activation and reports `semantic_enforcement=false` in the
API. Tests may prove byte structure and source provenance, but must not claim semantic isolation from
string comparison or one sampled model response.

Overlay content is trusted owner input rather than untrusted user or connector input. No MCP tool,
connector, scheduled task, runtime session, or generic dashboard route may write it. Audit, logs,
metrics, traces, and non-owner errors may contain only agent name, version, active state, byte count,
digest, outcome, and server-derived actor; they never contain overlay or roster text.

### D8: Active core-skills ownership is preserved, not merged here

Two active changes contain full `MODIFIED` blocks for the exact requirement
`core-skills / AGENTS.md Read/Write Access`:

- `k3s-deployment-helm-chart` adds a DB fallback for runtime notes on read-only mounts;
- `specify-improvement-proposal-spine` carries that whole block and adds a target prohibition on
  runtime-facing direct writers.

This change adds the differently named `Recursive Bare File-Reference Resolution` requirement and
does not restate, modify, remove, or resolve `AGENTS.md Read/Write Access`. The two owners must still
serialize archive: after the first archive, rebuild or remove the second block against the refreshed
baseline before its archive. This change must be re-scanned after either archive, but it does not
adopt their candidate semantics.

## Risks / Trade-offs

- **[Risk] Owner text can contradict identity in natural language.** -> Keep roster bytes present,
  expose provenance and a persistent warning, restrict writers, and make no semantic-sandbox claim.
- **[Risk] Mandatory roster validation trades availability for identity integrity.** -> Fail before
  adapter invocation with a content-blind operational error; never start a generic or DB-defined
  impersonation of the roster agent.
- **[Risk] RLS can be accidentally bypassed by ownership or inherited roles.** -> Use a non-login
  owner, `NOINHERIT`/`NOBYPASSRLS` application roles, direct-login writer tests, catalog assertions,
  and bootstrap-rerun tests.
- **[Risk] Staged migration temporarily retains broad historic grants.** -> Do not call the security
  boundary complete until the privilege-cutover stage and its real-PostgreSQL checks pass.
- **[Risk] A rollback to pre-compatible code cannot write after DML revocation.** -> Retain the
  compatibility release and legacy-selection switch through the rollback window; never restore broad
  grants as a routine rollback.
- **[Risk] Source SHA or active delta ownership moves during review.** -> Re-run same-name scans,
  overwrite guards, and semantic/security review on the exact final commit. Any semantic edit
  invalidates the prior review and owner-adoption target.

## Migration Plan

1. **Draft and adoption gate:** validate and independently review this exact spec-only artifact. The
   owner separately adopts the reviewed commit. No later stage is authorized by direction approval
   or by merging this draft alone.
2. **Compatibility release:** add schema-aware code, dedicated-pool support, new response parsing,
   closed composition, legacy selection, and inactive overlay support while current broad grants and
   current legacy runtime selection remain. Provision the direct-login writer credential through an
   independently authorized secret path. No existing row changes meaning.
3. **Additive and privilege migration:** add provenance columns and mode history, backfill every
   current row as `legacy_full_replacement`, seed existing agents into `precutover_legacy_hold`,
   create and transfer to the non-login owner, enable RLS,
   revoke historical table/sequence authority, and activate the direct-login writer. Validate counts,
   digests, catalog state, direct-login behavior, and bootstrap rerun before any overlay is load-bearing.
   Continue legacy selection until an owner reviews each head.
4. **Owner preparation:** through the owner-only route, create inactive or active operations overlays
   as explicitly chosen. The system never copies or activates legacy text automatically.
5. **Behavior cutover:** only after the privilege migration passes, use the owner-gated mode API to
   switch each prepared agent to roster-plus-overlay composition. Verify exact
   layer order, roster digest, Travel conformance, QA opt-out governance, and content-blind evidence.
6. **Rollback window:** retain the compatibility release and owner-gated compare-and-swap mode API.
   It may restore legacy full-replacement selection only with explicit identity-replacement
   acknowledgement, an existing legacy head, and a metadata-only atomic audit record. RLS and writer
   isolation remain active.
7. **Finalize:** only after an approved observation window may a later reviewed change retire legacy
   selection. History remains retained. Archival, deployment, and live activation each require their
   own authority.

Rollback never edits or deletes a historical row, never restores generic/runtime/connector DML, and
never changes QA or Travel prompt bytes. If the dedicated credential or migration is unavailable,
cutover does not begin; the last verified stage remains active.
