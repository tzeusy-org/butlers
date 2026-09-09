# Improvement Proposal Spine

## Purpose

Defines one target-neutral spine through which any butler can propose a standing change to its
own or shared behavior (an autonomy rule, an ingestion rule, a model/runtime tuning value, or a
git-tracked roster identity file such as a prompt, skill, manifesto/module selection, or schedule),
and through which an authenticated human decides whether to accept it and, separately, whether and
how it takes effect. This capability is a prerequisite contract for `bu-8cdl1.15`; no code,
migration, credential, or runtime described here exists yet. It supersedes no existing authority:
`autonomy-suggestions` and `switchboard-rule-promotion` remain the effect owners for their target
tables, and this spine's own only insert path (`propose_amendment`) grants no new mutation
authority to any table.

## ADDED Requirements

### Requirement: Restricted Public Schema

`public.improvement_proposals` SHALL exist as a cross-butler table storing: `id` (UUIDv7),
`proposer_butler` (TEXT, server-derived — see Requirement: Server-Derived Proposer),
`proposer_schema` (TEXT, server-derived), `target_kind` (TEXT, one of `autonomy_rule`,
`ingestion_rule`, `model_tier`, `runtime_config`, `roster_prompt`, `roster_skill`,
`roster_manifesto`, `roster_schedule`), `target_ref` (JSONB, structured target identity —
including `base_sha` for git-PR kinds, see Requirement: Base-SHA Fencing), `content_digest` (TEXT,
sha256 hex, server-computed — see Requirement: Server-Computed Content Digest), `payload` (JSONB,
schema-validated per `target_kind`, never raw diff/prompt/session text), `evidence_refs` (JSONB
array of typed opaque references, e.g. `{"kind": "sessions_friction", "id": <uuid>}`, never raw
evidence content), `review_status` (TEXT, one of `pending_review`, `accepted`, `rejected`,
`superseded`), `application_status` (TEXT, one of `not_requested`, `queued`, `applying`, `applied`,
`pr_opened`, `failed`, `rolled_back`), `generation` (INTEGER, default 0, incremented on a retry of
an already-accepted proposal — never on a content change, which instead supersedes), `prior_value`
(JSONB, nullable, captured before a DB-target apply), `effect_receipt` (JSONB, nullable,
adapter-specific: e.g. `{"rule_id": ...}` or `{"pr_url": ..., "head_sha": ...}`), `legacy_system` /
`legacy_schema` / `legacy_id` (nullable, backfill provenance), `created_at`, `decided_at`
(nullable), `decided_by` (nullable, server-derived at decision time), `applied_at` (nullable).

A companion append-only `public.improvement_proposal_events` table SHALL record every
`review_status`/`application_status` transition atomically with the state change, mirroring the
existing `pending_actions`/`approval_events` split: `id`, `proposal_id` (FK), `event_type`,
`actor` (server-derived), `generation`, `event_metadata` (JSONB, content-blind — see Requirement:
Content-Blind Event Metadata), `created_at`. The events table SHALL carry an immutability trigger
rejecting `UPDATE` and `DELETE`, matching `approval_events`'s existing trigger.

#### Scenario: Schema created via migration

- **WHEN** the future core migration runs
- **THEN** `public.improvement_proposals` and `public.improvement_proposal_events` exist with all
  specified columns
- **AND** a unique constraint exists on `(legacy_system, legacy_schema, legacy_id)` where all three
  are non-null

#### Scenario: Events table rejects mutation

- **WHEN** an `UPDATE` or `DELETE` is attempted against `public.improvement_proposal_events`
- **THEN** the immutability trigger rejects the statement

### Requirement: Server-Derived Proposer

`propose_amendment(target_kind, target_ref, payload, evidence_refs)` SHALL be the only insert path
into `public.improvement_proposals`. Runtime roles SHALL have `INSERT`/`UPDATE`/`DELETE` revoked on
the table directly and `EXECUTE` granted only on this function. The function SHALL derive
`proposer_butler`/`proposer_schema` from the calling connection's active `SET ROLE`-scoped runtime
role (per the `database-security` spec's "Runtime Role Enforcement via SET ROLE"), never from a
caller-supplied argument or request field.

#### Scenario: Proposer cannot be spoofed

- **WHEN** a butler running under `SET ROLE butler_email_rw` calls `propose_amendment` with any
  argument shape, including one that attempts to name a different butler as proposer
- **THEN** the created row's `proposer_butler`/`proposer_schema` reflect `email`, derived from the
  connection's role, regardless of what the call arguments contain

#### Scenario: No direct table write is possible for a runtime role

- **WHEN** a runtime role attempts a direct `INSERT INTO public.improvement_proposals`
- **THEN** the write is rejected by the revoked grant — only `propose_amendment` can create a row

### Requirement: Server-Computed Content Digest

`content_digest` SHALL be computed inside `propose_amendment` as the sha256 hex digest of the
canonical (key-sorted) JSON encoding of `{target_kind, target_ref, payload}`. It SHALL NOT be
accepted as a caller-supplied argument.

#### Scenario: Digest reflects actual stored content

- **WHEN** two `propose_amendment` calls supply byte-identical `target_kind`/`target_ref`/`payload`
- **THEN** both produce the same `content_digest`, regardless of call order or any other argument

#### Scenario: Digest changes with payload

- **WHEN** two `propose_amendment` calls differ in `payload` alone
- **THEN** their `content_digest` values differ

### Requirement: Row-Level Security on Read

`public.improvement_proposals` SHALL have `FORCE ROW LEVEL SECURITY` enabled with a `SELECT`
policy restricting a runtime role to rows where `proposer_schema` equals that role's own schema,
derived the same way `propose_amendment` derives it (role introspection, not a client-settable
session variable). The privileged dashboard/admin role SHALL be exempted from this policy.

#### Scenario: A butler reads only its own proposals

- **WHEN** the `email` butler's runtime role queries `public.improvement_proposals`
- **THEN** only rows with `proposer_schema = 'email'` are returned

#### Scenario: Dashboard reads across all proposers

- **WHEN** the privileged dashboard role queries `public.improvement_proposals`
- **THEN** rows from every `proposer_schema` are returned

### Requirement: Content-Blind Event Metadata

`improvement_proposal_events.event_metadata` SHALL contain only structural fields (event type,
generation, prior/new status values, adapter-reported error class) and SHALL NOT contain raw
`payload` content, evidence content, provider/GitHub error bodies, or session prompt/output text.

#### Scenario: A failed apply event carries no raw target content

- **WHEN** a DB-target apply fails and its `improvement_proposal_events` row is written
- **THEN** `event_metadata` includes the adapter's error class and the target identity, and does
  not include the raw exception text or any row content from the target table

### Requirement: Independent Review and Application State Axes

`review_status` and `application_status` SHALL be independent: acceptance (`review_status
='accepted'`) SHALL NOT imply the effect has occurred, and `application_status` SHALL only advance
past `not_requested` once `review_status='accepted'`.

#### Scenario: Accepted but not yet applied

- **WHEN** a git-PR-kind proposal is accepted via "Accept and open PR" but the PR creation call has
  not yet completed
- **THEN** `review_status='accepted'` and `application_status` is `queued` or `applying`, never
  `applied` or `pr_opened`

#### Scenario: Application cannot precede acceptance

- **WHEN** any request attempts to advance `application_status` on a proposal with
  `review_status='pending_review'`
- **THEN** the request is rejected

### Requirement: Target Adapter Kinds and Effect Ownership

Each `target_kind` SHALL map to exactly one of two adapter families, and no adapter SHALL write
directly to a target table or tracked file outside calling that target's existing effect-owner
function/process:

- **DB-transactional**: `autonomy_rule` (calls `autonomy-suggestions`'s existing rule-creation
  path), `ingestion_rule` (calls `switchboard-rule-promotion`'s existing rule-creation path),
  `model_tier` / `runtime_config` (calls the existing model/runtime-config transaction).
- **Git-PR**: `roster_prompt`, `roster_skill`, `roster_manifesto`, `roster_schedule` (a
  generalized PR publisher — see Requirement: PR Adapter Substrate Boundary).

#### Scenario: DB-target apply calls the existing effect owner

- **WHEN** an `autonomy_rule`-kind proposal is accepted and applied
- **THEN** the same `approval_rules`-creation function `autonomy-suggestions`'s
  `confirm_promotion_suggestion` already calls is invoked, not a generic table write

#### Scenario: A proposal cannot target an unmapped kind

- **WHEN** `propose_amendment` is called with a `target_kind` outside the eight enumerated values
- **THEN** the call is rejected with a structured validation error

### Requirement: DB-Target Atomic Accept-and-Apply

Accepting a DB-target proposal SHALL be one transaction: an atomic conditional transition
(`review_status: pending_review -> accepted` `WHERE review_status = 'pending_review'`, returning
zero rows for a losing racer), a `prior_value` capture of the target's current state, the
effect-owner call, and the event write. On any failure inside that transaction, the whole
transaction SHALL roll back, `review_status` SHALL remain `accepted`, and `application_status`
SHALL become `failed` — the accept decision stands even when the effect fails.

#### Scenario: Concurrent accept-and-apply produces exactly one effect

- **WHEN** two "Accept and apply" requests for the same proposal race
- **THEN** exactly one transitions `review_status` to `accepted` and performs the effect
- **AND** the other observes zero rows affected by its conditional transition and performs no
  effect

#### Scenario: Effect failure leaves no partial target write

- **WHEN** the effect-owner call raises inside the accept-and-apply transaction
- **THEN** the transaction rolls back entirely — no partial write to the target table occurs
- **AND** `review_status` remains `accepted` and `application_status` becomes `failed`

#### Scenario: Retry after failure reuses the same digest

- **WHEN** a failed DB-target proposal's accept-and-apply is retried
- **THEN** the retry uses the same `content_digest` and increments `generation`
- **AND** a payload change is not permitted on the same row (see Requirement: Immutable Payload)

### Requirement: DB-Target Rollback via Compare-and-Set

Rollback of an applied, reversible DB-target proposal SHALL be a compare-and-set update against
`prior_value`, conditioned on the target row's current state matching the fingerprint captured at
apply time. A mismatch SHALL be treated as rollback refusal, not an overwrite.

#### Scenario: Rollback succeeds when target is unchanged since apply

- **WHEN** rollback is requested for an applied proposal whose target row has not changed since
  `applied_at`
- **THEN** the target row is restored to `prior_value` and `application_status` becomes
  `rolled_back`

#### Scenario: Rollback refuses to overwrite a later edit

- **WHEN** rollback is requested for an applied proposal whose target row has been modified (by a
  manual edit or a different proposal's apply) since `applied_at`
- **THEN** the compare-and-set affects zero rows, rollback is refused, and a "target changed since
  apply" result is returned — the target's current state is not overwritten

### Requirement: Base-SHA Fencing for Git-PR Targets

A git-PR-kind proposal's `target_ref` SHALL include `base_sha`, the target file's blob SHA captured
at proposal-creation time. "Accept and open PR" SHALL re-read the current blob SHA immediately
before committing and SHALL fail closed with `application_status='failed'` and a `base_conflict`
reason when it no longer matches `base_sha` — no automatic rebase or patch rewrite SHALL occur.

#### Scenario: Base SHA still matches

- **WHEN** "Accept and open PR" is invoked and the target file's current blob SHA equals the
  proposal's recorded `base_sha`
- **THEN** the commit proceeds

#### Scenario: Base SHA has diverged

- **WHEN** "Accept and open PR" is invoked and the target file's current blob SHA differs from the
  proposal's recorded `base_sha`
- **THEN** the PR is not created, `application_status` becomes `failed` with reason
  `base_conflict`, and no commit, push, or rewrite is attempted

### Requirement: PR-Target Recovery by Deterministic Head Reconciliation

Each git-PR-kind proposal SHALL use a deterministic branch name derived from its `id`
(`proposal/<target_kind>/<proposal_id>`), not from the attempt. Before pushing or calling
`gh pr create`, the adapter SHALL persist an `effect_receipt` marker recording that a push is about
to be attempted. A retry SHALL first query for an existing open PR on that exact deterministic head
branch and reconcile against it rather than push or create unconditionally.

#### Scenario: Push succeeds but the response is lost

- **WHEN** a push succeeds, the process crashes before recording success, and the operation is
  retried
- **THEN** the retry finds the branch already pushed (same content, since payload is immutable —
  see Requirement: Immutable Payload) and proceeds to the PR-creation step without re-pushing
  divergent content

#### Scenario: PR creation succeeds but the response is lost

- **WHEN** `gh pr create` succeeds, the response is lost before being recorded, and the operation
  is retried
- **THEN** the retry queries for an existing PR on the deterministic head branch, finds it, records
  its URL/number in `effect_receipt`, and does not create a duplicate PR

### Requirement: PR Adapter Substrate Boundary

The git-PR adapter SHALL reuse the anonymization gate (`anonymize`/`validate_anonymized`), an
isolated-worktree pattern, and a path-allowlist pattern as implementation substrate. It SHALL NOT
call `src/butlers/core/qa/dispatch.py::_create_qa_pr`, SHALL NOT write to `public.healing_attempts`,
and SHALL use its own worktree directory, branch namespace, and result storage
(`improvement_proposals.effect_receipt`) distinct from QA's `.healing-worktrees/`/
`healing_attempts`. The adapter's path allowlist SHALL be scoped to roster identity paths
(`roster/{butler}/{CLAUDE.md,AGENTS.md,skills/**,MANIFESTO.md,schedule.toml}`-shaped, exact
allowlist left to future implementation per `design.md` Open Question 2) and SHALL fail closed
(deny) for any path outside it, mirroring `RepoWhitelist`'s fail-closed-when-empty behavior.

#### Scenario: PR content passes the anonymization gate before publication

- **WHEN** a git-PR-kind proposal's PR title/body/labels are prepared for `gh pr create`
- **THEN** each is passed through `anonymize()` then `validate_anonymized()`
- **AND** a residual-PII violation blocks PR creation and deletes any already-pushed remote branch,
  exactly as the existing QA anonymization-failure behavior does for its own PRs

#### Scenario: A path outside the allowlist is rejected

- **WHEN** a git-PR-kind proposal's `target_ref` names a path outside the roster-identity allowlist
- **THEN** `propose_amendment` (or the adapter's pre-flight check) rejects it before any worktree,
  branch, or PR is created

#### Scenario: No coupling to QA's state or authority

- **WHEN** the PR adapter is implemented
- **THEN** it has no import or call edge into `_create_qa_pr` or any `healing_attempts` write path
- **AND** a static/dependency assertion test enforces this boundary

### Requirement: Immutable Payload and Supersession on Repeat Proposal

A `pending_review` proposal's `target_kind`/`target_ref`/`payload`/`content_digest` SHALL be
immutable — there is no in-place edit operation. A partial unique index SHALL exist on
`(proposer_schema, target_kind, target_ref) WHERE review_status = 'pending_review'`. A new
`propose_amendment` call matching that identity SHALL atomically transition the existing
`pending_review` row to `review_status='superseded'`
(`decided_by='system:proposal_superseded'`) and insert a new row at `generation=0`.

#### Scenario: A repeat proposal supersedes the pending one

- **WHEN** a butler calls `propose_amendment` for the same `(proposer_schema, target_kind,
  target_ref)` identity as an existing `pending_review` proposal, with different `payload`
- **THEN** the existing row transitions to `superseded`
- **AND** a new row is created at `generation=0` with the new content and its own
  `content_digest`

#### Scenario: No two pending proposals share an identity

- **WHEN** the unique partial index is checked after a supersession
- **THEN** at most one `pending_review` row exists for that `(proposer_schema, target_kind,
  target_ref)` identity

### Requirement: Legacy Backfill Provenance and Idempotence

A one-time backfill SHALL insert one `improvement_proposals` row per existing
`autonomy_suggestions` row and per existing `switchboard.rule_promotion_suggestions` row, setting
`legacy_system`/`legacy_schema`/`legacy_id` and mapping status as follows, guarded by
`ON CONFLICT (legacy_system, legacy_schema, legacy_id) DO NOTHING` for idempotence:

| Legacy status | `review_status` | `application_status` |
|---|---|---|
| `pending` / `pending_review` | `pending_review` | `not_requested` |
| `confirmed` | `accepted` | `applied` |
| `dismissed` | `rejected` | `not_requested` |
| `superseded` | `superseded` | `not_requested` |

The backfill SHALL be read-only against the legacy tables — no legacy row is updated, no column
added, no table dropped.

#### Scenario: Backfill is idempotent

- **WHEN** the backfill migration is run twice
- **THEN** the second run inserts zero additional rows (the unique provenance constraint blocks
  duplicates)

#### Scenario: Legacy tables are unmodified by backfill

- **WHEN** the backfill completes
- **THEN** `autonomy_suggestions` and `switchboard.rule_promotion_suggestions` contain the exact
  same rows, in the exact same state, as before the backfill ran

#### Scenario: Status mapping is applied per the table

- **WHEN** a legacy `autonomy_suggestions` row with `status='confirmed'` is backfilled
- **THEN** the created `improvement_proposals` row has `review_status='accepted'` and
  `application_status='applied'`

### Requirement: Legacy Compatibility Auto-Apply Adapter Preserved Unmodified

The existing `switchboard-rule-promotion` clearly-automated auto-apply exception (its "Promotion
Application (Owner-Confirmed with Automated-Tier Auto-Apply)" requirement) SHALL be preserved
exactly as specified there. Its backfilled/dual-written representation in this spine SHALL carry
`decided_by='system:rule_promotion_auto_apply'`, `review_status='accepted'`,
`application_status='applied'`, mirroring the existing automatic decision without re-gating or
re-deciding it.

#### Scenario: Auto-applied legacy suggestion backfills as already-accepted

- **WHEN** a `switchboard.rule_promotion_suggestions` row with `decided_by
  ='system:rule_promotion_auto_apply'` is backfilled
- **THEN** the created `improvement_proposals` row has `review_status='accepted'`,
  `application_status='applied'`, and `decided_by='system:rule_promotion_auto_apply'`
- **AND** no new gate, confirm, or review step is introduced for it by this spine

### Requirement: No New Auto-Adoption for Any Target Kind

No `target_kind` other than the one existing, unmodified Switchboard clearly-automated exception
(Requirement: Legacy Compatibility Auto-Apply Adapter Preserved Unmodified) SHALL have any
auto-apply, auto-accept, or auto-adoption path. Every other proposal, of every other kind,
including every future dual-written `ingestion_rule` proposal that is not clearly-automated,
SHALL require an explicit authenticated human accept before `application_status` can leave
`not_requested`.

#### Scenario: A new autonomy_rule proposal is never auto-accepted

- **WHEN** an `autonomy_rule`-kind proposal is created via `propose_amendment`
- **THEN** `review_status` starts at `pending_review` and remains so until an explicit accept call,
  regardless of evidence count or pattern frequency

#### Scenario: A route_to ingestion_rule proposal is never auto-accepted

- **WHEN** a dual-written `ingestion_rule`-kind proposal represents a `route_to:` action (not
  clearly-automated)
- **THEN** it requires an explicit accept call, matching `switchboard-rule-promotion`'s existing
  "route_to is never auto-applied" behavior

### Requirement: Dashboard Proposal List API is Content-Blind

The dashboard API SHALL expose `GET /api/improvement-proposals`, cursor-paginated per this
repository's `docs/api_and_protocols/response-conventions.md` convention, accepting `review_status`,
`application_status`, `target_kind`, and `proposer_butler` filters. Each returned summary object
SHALL include `id`, `proposer_butler`, `target_kind`, `target_ref` (structured, safe fields only —
e.g. a file path or tool name, never a diff), a short `content_digest` prefix, `review_status`,
`application_status`, `evidence_refs` (typed refs only), `created_at`, `decided_at`. It SHALL NOT
include `payload`.

#### Scenario: List omits restricted payload

- **WHEN** `GET /api/improvement-proposals?review_status=pending_review` is called
- **THEN** each returned object omits `payload` entirely

#### Scenario: Empty result is honest

- **WHEN** no proposals match the filter
- **THEN** the API returns an empty page with response status 200, not an error

### Requirement: Dashboard Proposal Detail API is Privileged

The dashboard API SHALL expose `GET /api/improvement-proposals/{id}`, requiring authenticated
context, returning every field including `payload` and full `evidence_refs`.

#### Scenario: Detail includes payload under authentication

- **WHEN** `GET /api/improvement-proposals/{id}` is called with authenticated context
- **THEN** the response includes `payload`

#### Scenario: Detail requires authentication

- **WHEN** `GET /api/improvement-proposals/{id}` is called without authentication
- **THEN** the response status is 401

### Requirement: Dashboard Decision and Effect Endpoints

The dashboard API SHALL expose, each requiring authenticated context with the actor derived via
`authenticated_principal()` (never a request field):

- `POST /api/improvement-proposals/{id}/reject` — optional `{"reason": string}` body, transitions
  `review_status` to `rejected`.
- `POST /api/improvement-proposals/{id}/accept-and-apply` — valid only for DB-transactional
  `target_kind`s; performs the Requirement: DB-Target Atomic Accept-and-Apply transaction.
- `POST /api/improvement-proposals/{id}/accept-and-open-pr` — valid only for git-PR `target_kind`s;
  performs the base-SHA-fenced, head-reconciling PR publication.
- `POST /api/improvement-proposals/{id}/rollback` — valid only for an applied, reversible
  DB-transactional proposal; performs the Requirement: DB-Target Rollback via Compare-and-Set.

Calling a DB-only verb on a git-PR-kind proposal, or the PR verb on a DB-transactional-kind
proposal, SHALL be rejected with a structured error naming the mismatch.

#### Scenario: Actor is server-derived on accept

- **WHEN** `POST /api/improvement-proposals/{id}/accept-and-apply` is called by an authenticated
  dashboard user
- **THEN** `decided_by` is set from `authenticated_principal()`, never from the request body

#### Scenario: Verb mismatch is rejected

- **WHEN** `POST /api/improvement-proposals/{id}/accept-and-apply` is called for a
  `roster_prompt`-kind proposal
- **THEN** the request is rejected with a structured error indicating this proposal requires
  "Accept and open PR"

#### Scenario: Rejection requires no further action

- **WHEN** `POST /api/improvement-proposals/{id}/reject` is called on a `pending_review` proposal
- **THEN** `review_status` becomes `rejected`, `application_status` remains `not_requested`, and no
  effect-owner call occurs

### Requirement: Dashboard Source Degradation is Disclosed, Never Hidden

When a target-adapter dependency the list, detail, or effect endpoints rely on is unreachable (a
butler's database pool for a DB-target status check, or GitHub for PR head reconciliation), the
affected response SHALL surface a truthful degraded indicator per this repository's fan-out
convention rather than a silent empty or all-clear result.

#### Scenario: GitHub unreachable during list rendering

- **WHEN** `GET /api/improvement-proposals` is called and a git-PR-kind proposal's live PR-status
  enrichment call to GitHub fails
- **THEN** that proposal's row still renders with its last-known `application_status`, and the
  response includes a degraded-source indicator (e.g. `meta.sources_degraded` or an equivalent
  per-field flag, matching the existing convention's field-naming guidance) rather than omitting
  the row or reporting a false `pr_opened`/`applied` state
