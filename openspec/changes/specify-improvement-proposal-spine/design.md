# Design: unified improvement proposals and gated amendment PRs

## Context

`bu-8cdl1.15`'s shaping pass found two shipped, intentional systems answering "how does a butler
propose a standing change to its own behavior, and how does a human decide?" — `autonomy-suggestions`
(RFC 0021's confirmed-human-gate model, per-butler `autonomy_suggestions`) and
`switchboard-rule-promotion` (`switchboard.rule_promotion_suggestions`, which additionally
preserves an owner-approved automatic-apply exception for clearly-automated suppression). Both are
narrowly scoped: one butler-side tool-call pattern, one email/message routing pattern. `.15`'s
outcome asks for something broader — prompts, skills, schedules, model tiers, and manifesto/module
selection, none of which the two existing systems can express, because all of it lives in
git-tracked roster identity files, not a database row a legacy adapter can flip a status column on.

The correct generalization is not "extend `autonomy_suggestions`'s schema to cover git targets" —
its columns (`representative_args`, `resulting_rule_id`) are approval-rule-shaped and its lifecycle
column conflates "was this accepted" with "did the accepted thing take effect," which breaks the
moment "accepted" means "a PR is open, unmerged" rather than "a rule exists." The shaping pass's
own proposed design (separate review/application axes, a target-neutral restricted spine, adapters
per target family) is the right shape; this document grounds it in this repository's actual
existing primitives (RLS/grant conventions, response-envelope conventions, and the QA healing
pipeline's clone/anonymize/publish primitives) rather than re-deriving them from scratch.

## Goals and non-goals

Goals:

- Define one target-neutral proposal spine covering ingestion-rule suggestions, autonomy grants,
  and — newly — prompts, skills, schedules, manifesto/module selection, and model/runtime tuning.
- Separate "was this accepted" from "did it take effect," with UI verbs that state the effect
  (`Accept and apply` vs `Accept and open PR`), not a generic "approve."
- Make the proposer identity, and the accepting/rejecting human identity, both server-derived —
  never trusted from request payloads.
- Define exact trust, failure, concurrency, idempotence, compatibility, and rollback semantics for
  every target-adapter family, named at the level a future implementation can build gate tests
  against without further design work.
- Backfill and dual-write both legacy systems without loss, duplication, or vocabulary drift,
  while leaving them fully intact and authoritative until an explicit future read-cutover.
- Reconcile `core-skills`'s direct-write authority and the active k3s DB-fallback delta so no
  runtime session ever gets a silent, unaudited identity-write path.
- Name QA's reusable primitives and draw the line at `_create_qa_pr`/`healing_attempts` so the
  spine's PR adapter is substrate reuse, not a QA-authority claim.

Non-goals (all preserved verbatim from the bead):

- No runtime, provider, or credential operations; no message or PR publication by any runtime
  session; no configuration change; no implementation, deployment, merge, or activation.
- No new unattended (auto-adopted) authority for any target kind beyond the one existing,
  owner-approved Switchboard clearly-automated exception, preserved as-is.
- No retrospective scheduling or adoption-verification loop — this draft does not add a mechanism
  that later checks whether an applied proposal "worked" or schedules a follow-up review.
- No widening of authority via future schema or capability metadata fields left open for later
  interpretation — every field this draft adds has a stated purpose and a stated boundary.
- No changes to RFC 0021, `pending_actions`, the approvals executor, or either legacy suggestion
  table's baseline requirements.
- No live GitHub publication, database migration, or deployment — this is a draft artifact.

## Decisions

### D1: A new RFC, not an RFC 0021 amendment

RFC 0021's doctrine position is specifically approval-gate strengthening for tool-call execution
("One-tap inline approval buttons... strengthens the per-event approval path"). An improvement
proposal carries no execution authority by itself — accepting a proposal is a distinct human act
from approving a tool call, and for PR targets the "effect" is opening a *reviewable* PR, not
executing anything. Conflating the two RFCs would make a future reader assume proposal acceptance
inherits RFC 0021's approval semantics (auto-dispatch on accept), which is false for every PR
target and true only in a narrow, already-transactional sense for DB targets. This draft does not
create the RFC file; if the artifact is accepted, its future implementation records RFC 0033 (next
free number after 0032) documenting this contract.

### D2: The public schema — one table pair, restricted representation

`public.improvement_proposals` (see Requirement: Restricted Public Schema in the capability spec)
lives in `public`, following this repository's own stated convention (`CLAUDE.md`: "cross-butler
tables in public, each role sees only its schema plus public") — a proposal is inherently
cross-butler-visible to the dashboard/owner, and its target may not even be the proposing butler's
own schema (e.g. a `model_tier` proposal targets shared `runtime_config`). This mirrors the
`notify-confirm-interaction` precedent's D3 rationale for a similar cross-butler routing table,
and the existing `public.healing_attempts` / `public.qa_findings` placement for the same reason
(state visible to the dashboard, produced by many butlers).

Two tables, not one: `improvement_proposals` (current state) and `improvement_proposal_events`
(append-only transition log), exactly mirroring the existing `pending_actions` /
`approval_events` split — a proven shape in this codebase for "current mutable state" +
"immutable audit trail," including an immutability trigger on the events table (no `UPDATE` or
`DELETE`, matching `approval_events`'s existing trigger).

### D3: Server-derived proposer via a narrow append function, not client-supplied identity

`propose_amendment(target_kind, target_ref, payload, evidence_refs)` is the *only* insert path.
Runtime roles receive `REVOKE ALL` on direct table `INSERT`/`UPDATE`/`DELETE` and `GRANT EXECUTE`
on this function only — the same shape this repository already uses for
`public.expected_signals` (`database-security` spec, "Expected-signal producer-owned writes": role
gets `SELECT` broadly, but writes are constrained so "one runtime role [cannot] replace another
role's signal key"). This draft applies the *same mechanism* one step further: the function itself
derives `proposer_butler`/`proposer_schema` from `current_setting`/role introspection on the
connection that is already `SET ROLE`-scoped per the `database-security` spec's "Runtime Role
Enforcement via SET ROLE" — the caller's request body cannot name a different proposer no matter
what it sends, because the function never reads a proposer field from its arguments at all.

`content_digest` is computed **inside** the function, over the canonical (key-sorted) JSON of
`{target_kind, target_ref, payload}` — never accepted as a client-supplied field — so a proposal's
digest is always provably a function of its actual stored content, not an assertion the proposer
could spoof to make a modified payload appear to match an already-reviewed digest.

### D4: Row-level security restricts reads, not only writes

`expected_signals`'s existing RLS is write-only (broad `SELECT`, constrained `INSERT`/`UPDATE`).
A proposal's restricted payload can carry evidence (typed refs only, never raw content — D5) that
is still butler-internal in nature; the bead's outcome explicitly calls for "each runtime may read
only its own proposals." This draft therefore specifies `FORCE ROW LEVEL SECURITY` with a `SELECT`
policy of `proposer_schema = <the connection's own active runtime role's schema>`, evaluated the
same way the existing `producer_role` write policy is (role introspection, not a trusted session
variable a connection could set itself). The privileged dashboard/admin role — the same role that
already has cross-schema visibility for other dashboard aggregation endpoints — is exempted from
this policy (`BYPASSRLS` or an explicit dashboard-role policy branch, whichever this repository's
existing RLS migrations use as precedent; a future implementation PR picks the exact mechanism,
both satisfy this requirement equally).

### D5: Restricted payload — typed opaque evidence, never raw content

Mirroring the `notify-confirm-interaction` precedent (D3's "no privileged authority to isolate")
inverted — a proposal *does* carry sensitive shape, so the restriction runs the other way: `payload`
is schema-validated per `target_kind` (a closed set of known shapes — see D6), never a free-form
diff or raw prompt/session text, and `evidence_refs` is a list of typed opaque references
(`{"kind": "sessions_friction", "id": <uuid>}`, `{"kind": "routing_verdict_log", "id": <uuid>}`)
that point at existing evidence tables (`sessions_friction` per `bu-8cdl1.9`, `routing_verdict_log`
per `switchboard-rule-promotion`) rather than copying their content. This directly satisfies the
bead's "raw proposal bodies, diffs, provider/GitHub errors, prompts, and friction content never
enter audit text, logs, metrics, or cross-butler list responses" and matches this repository's
existing "content-blind" pattern already in flight elsewhere (`project-audit-history-content-blind`,
`project-secret-read-endpoints-content-blind`): a list/summary view never carries raw content; a
privileged detail view, gated the same way those other content-blind changes gate their detail
endpoints, may.

### D6: Four target-adapter families, not one generic writer

| `target_kind` | Adapter | Effect owner (unchanged) |
|---|---|---|
| `autonomy_rule` | DB-transactional | `autonomy-suggestions`'s existing confirm path (`approval_rules` creation) |
| `ingestion_rule` | DB-transactional | `switchboard-rule-promotion`'s existing confirm path (`ingestion_rules` creation) |
| `model_tier` / `runtime_config` | DB-transactional | the existing model/runtime-config transaction (`docs/runtime/model-routing.md`) |
| `roster_prompt` / `roster_skill` / `roster_manifesto` / `roster_schedule` | Git-PR | a new, generalized PR publisher (D7) |

No adapter is a generic "run this JSONB patch against this table" writer — each of the three
DB-transactional kinds calls the *same existing effect-owner function* the legacy confirm path
already calls (D9), wrapped so the proposal's state transition, the target write, prior-value
capture, and the immutable event all commit in one transaction. This is the bead's explicit
requirement ("No generic proposal row may directly update a target table or tracked file") and it
means accepting this draft adds no new mutation authority to any DB table — it adds a second,
gated *caller* of authority that already exists.

### D7: The PR adapter reuses QA's primitives as substrate, never `_create_qa_pr` or `healing_attempts`

`src/butlers/core/qa/dispatch.py::_create_qa_pr` is QA-finding-shaped: it builds a QA-specific PR
body (root cause / fix summary / test coverage sections pulled from
`INVESTIGATION_NOTES.md`), applies QA-specific default labels (`["self-healing", "automated"]`),
and its caller persists results onto `healing_attempts`. `src/butlers/core/qa/repo_whitelist.py`
(`RepoWhitelist`, fail-closed when unloaded/empty), `src/butlers/core/healing/anonymizer.py`
(`anonymize`/`validate_anonymized`, general-purpose — not QA-specific already), and
`src/butlers/core/healing/worktree.py` (isolated worktree creation/cleanup, branch-per-attempt,
stale-worktree reaping) are the pieces this draft names as reusable substrate. A future PR adapter
implementation:

- imports `anonymize`/`validate_anonymized` directly (already general-purpose, no change needed);
- generalizes `RepoWhitelist`'s *shape* (fail-closed load-then-check) into a roster-**path**
  allowlist scoped to `roster/{butler}/{CLAUDE.md,AGENTS.md,skills/**,MANIFESTO.md,schedule.toml}`
  — a different check from QA's cross-repo owner/repo whitelist, since proposal PRs always target
  this same repository's roster tree, never an arbitrary external repo;
- generalizes `create_healing_worktree`/`remove_healing_worktree`'s isolation pattern into a
  sibling helper (a new branch-per-proposal worktree under a new `.proposal-worktrees/` — not
  `.healing-worktrees/`, so QA's stale-worktree reaper never has to reason about proposal branches
  and vice versa);
- writes its own PR body builder, its own default labels (e.g. `["improvement-proposal",
  "owner-review"]`), its own `gh pr create` invocation, and its own result table
  (`improvement_proposals.effect_receipt`, not `healing_attempts`).

This is a substrate-reuse relationship, symmetrical to how `notify-confirm-interaction`'s D1
explicitly declined to reuse `apr1:`'s callback *format* while still following its *shape*: same
kind of tool, deliberately un-shared state and authority.

### D8: Content-digest, base-SHA, and deterministic-branch fencing for PR publication

Recovering the bead's "exact-digest owner-gated PR publication" and "fenced retries" outcomes
concretely:

- `target_ref` for a git-PR-kind proposal includes `base_sha` — the blob SHA of the target file
  captured at proposal-creation time. The `Accept and open PR` act re-reads the current blob SHA
  immediately before writing the commit and fails closed (`application_status='failed'`, a visible
  `base_conflict` reason, **no automatic rebase or patch rewrite**) if it no longer matches — the
  bead's "A base/content conflict becomes a visible `failed`/needs-new-review result, never an
  automatic patch rewrite."
- The branch name is deterministic per *proposal*, not per *attempt*
  (`proposal/<target_kind>/<proposal_id>`), so a retry after a crash or a lost HTTP response first
  queries GitHub for an existing open PR with that exact head branch before attempting a new push
  or a new `gh pr create` — reconciling by head rather than creating a duplicate, exactly the
  bead's "Push-success/response-loss and PR-create-response-loss reconcile by head instead of
  creating duplicates."
- Retry is permitted only when the proposal's `content_digest` and `generation` are unchanged from
  the attempt being retried; any payload edit is disallowed post-creation (D11) so a "changed
  content" retry cannot exist by construction — a genuinely new payload always means a new proposal
  row (D11), never a retried old one.
- The adapter persists an `effect_receipt` marker (`{"phase": "push_attempted", ...}`) **before**
  the push, so a crash between push and PR creation is distinguishable on restart from "never
  attempted" and the reconciliation-by-head path is taken rather than a blind retry from scratch.

### D9: DB-target transactions — atomicity, fencing, and compare-and-set rollback refusal

Accepting a DB-target proposal (`Accept and apply`) is one transaction: `UPDATE
improvement_proposals SET review_status='accepted', application_status='applying' WHERE id=$1 AND
review_status='pending_review' RETURNING generation` (the sole fencing gate — a race between two
simultaneous "Accept and apply" clicks resolves to exactly one winner, mirroring the
`notify-confirm-interaction` precedent's identical `WHERE status='pending'` single-writer-wins
pattern) → capture `prior_value` by reading the target row's current state → call the *existing*
effect-owner function (e.g. `autonomy-suggestions`'s rule-creation path) → on success, update
`application_status='applied'`, `effect_receipt`, write the immutable event, commit. On any
exception, the whole transaction rolls back: `review_status` reverts to its pre-accept value is
**not** correct — a failed apply still means the proposal *was* accepted (the human decision
stands) but the *effect* failed, so the correct behavior is `review_status` remains `accepted`
while `application_status` becomes `failed`, and a **new** `Accept and apply` retry (same digest,
`generation + 1`) is the recovery path, not a re-review.

Rollback (a separate, later act, only for reversible DB targets) is a compare-and-set against
`prior_value`: `UPDATE <target> SET ... = prior_value WHERE <target identity> AND
<current-fingerprint> = <fingerprint captured at apply time> RETURNING *`. Zero rows returned means
the target changed since the proposal applied (a later manual edit, or a different proposal's
apply) — rollback fails closed with an explicit "target changed since apply; rollback refused"
result rather than silently clobbering the newer state. This is the bead's "rollback uses a
compare-and-set against the applied generation and refuses to overwrite later edits" made concrete.

### D10: No new auto-adoption; the one existing exception is a named, unmodified compatibility adapter

The `switchboard-rule-promotion` spec's "Promotion Application (Owner-Confirmed with
Automated-Tier Auto-Apply)" requirement is **not touched by this draft**. Its backfilled/dual-written
row in the new spine carries the *same* semantics through a **compatibility adapter**:
`decided_by='system:rule_promotion_auto_apply'` (the existing marker, unchanged) sets
`review_status='accepted'` and `application_status='applied'` atomically, with the underlying
`ingestion_rules` mint happening exactly as it does today — the new spine observes and mirrors this
event, it does not gate or re-decide it. No other `target_kind` gets an auto-apply path; every
other proposal in the spine, of every kind, requires an explicit owner accept. This draft's own
recommendation is to preserve the exception exactly as-is (matching the shaping evidence's
recommendation); **revoking it is a product-policy alternative this draft explicitly marks as
proposed for owner review, not decided here** (see Open Questions) — preserving already-shipped,
already-approved behavior is not itself a new decision this draft needs owner sign-off to make.

### D11: Immutable payload; a content change is a new review revision via supersession, not an edit

A `pending_review` proposal's `payload`/`target_ref`/`content_digest` are immutable once created —
there is no "edit a pending proposal" operation. When a proposer needs to change a proposal already
under review (e.g. a promotion trigger re-evaluates and wants to update its proposed condition), it
calls `propose_amendment` again with the same `(proposer_schema, target_kind, target_ref)` identity.
A partial unique index on `(proposer_schema, target_kind, target_ref) WHERE review_status =
'pending_review'` enforces at most one live proposal per identity; the new call's insert path
atomically transitions any existing `pending_review` row with that identity to
`review_status='superseded'` (`decided_by='system:proposal_superseded'`) before inserting the new
row at `generation=0`. This is the same shape `switchboard-rule-promotion` already uses for its own
unique-partial-index + supersede-on-new-rule pattern — reused, not reinvented — and it directly
satisfies "changed content creates a new review revision."

### D12: Legacy backfill and dual-write are additive; legacy tables are never dropped or mutated by this migration

The backfill is a one-time, idempotent `INSERT ... ON CONFLICT (legacy_system, legacy_schema,
legacy_id) DO NOTHING` read of every existing `autonomy_suggestions` and
`switchboard.rule_promotion_suggestions` row (mapping table in the capability spec's Backfill
requirement). It is read-only against the legacy tables — no column added, no row updated, no drop.
Dual-write (transactionally coupling each legacy producer's insert to a matching spine insert) is
named as a future-implementation contract this draft specifies the *shape* of (same transaction,
same identity mapping used by backfill) but does not build — `bu-8cdl1.15`'s own decomposition
(design point, preserved from the shaping evidence) places dual-write as future step 2, after
representation (step 1) lands and is verified. This ordering means a partial cutover — backfill
landed, dual-write not yet — is always safely rollback-able to "read from legacy tables only,"
because the legacy tables are still the only rows anything writes during that window.

### D13: `core-skills` boundary is preventive, and its delta is authored as a full superset of the active k3s change's own delta

Today, no MCP tool wires a runtime butler session to `write_agents_md`/`append_agents_md` (a
full-repo call-site scan finds only test callers and the functions' own definitions in
`src/butlers/core/skills.py`). This draft's `core-skills` change is therefore preventive: it closes
a latent authority (the functions exist, are importable, and the active `k3s-deployment-helm-chart`
change extends their DB-fallback reach) before any future tool exposes either path to a runtime
session, rather than revoking an exercised capability.

Because `k3s-deployment-helm-chart` is an active, unarchived change whose own `## MODIFIED
Requirements` block already rewrites the same `AGENTS.md Read/Write Access` requirement (adding the
`state`-table fallback for read-only filesystems), this draft's own block cannot be authored as an
independent, narrower addition the way the `notify-confirm-interaction` precedent kept its three
touched files' `## ADDED`-only blocks disjoint from the sibling `decision-loop-one-tap-approvals`
change (that precedent's proposal.md "Coexistence note" — additive requirement names only, never a
second `## MODIFIED` block on the same requirement). This draft's situation is the harder case that
precedent explicitly avoided: two changes really do need to modify the same requirement. Two changes
modifying the same requirement name against the same baseline is exactly what
`scripts/check_spec_overwrites.py` polices for content loss on archive, and `openspec archive`
itself has no cross-change conflict detection — it resolves whichever change archives against
whatever is currently in `openspec/specs/` at that moment.

This draft resolves it explicitly rather than leaving it for an archive-time surprise: its
`core-skills` delta is authored as a full superset, carrying forward every scenario from
`k3s-deployment-helm-chart`'s own MODIFIED block verbatim (the DB-fallback-on-read-only-filesystem
scenarios) and adding the new runtime-tool-surface boundary scenario on top. This means:

- If this draft archives first, `k3s-deployment-helm-chart`'s own delta becomes redundant with (not
  contradicted by) what is now in `openspec/specs/core-skills/spec.md` — its author rebases its
  block to a no-op or drops it, since every scenario it wanted is already present.
- If `k3s-deployment-helm-chart` archives first, this draft's block, if archived unchanged, would
  still carry forward every scenario correctly (it was authored as a superset of that exact text),
  but its author should re-diff against the just-archived baseline before archiving to confirm
  nothing in `k3s-deployment-helm-chart`'s final merged text drifted from what this draft copied.

Either order is safe from silent content loss; neither is a no-op that this draft can force by
itself. `proposal.md`'s Impact section names this as the exact prerequisite acceptance criterion 2
requires, rather than presenting the sequencing as already resolved.

## Rejected alternatives

- **Extend `autonomy_suggestions`'s schema to cover every target kind** — rejected (Context, D6):
  its columns and lifecycle are approval-rule-shaped; forcing git-PR targets through a
  DB-row-status lifecycle designed for "does an `approval_rules` row exist" conflates acceptance
  with effect in a way that is actively false for a PR (accepted-but-not-yet-merged).
- **A single generic `apply_proposal(target_table, target_id, patch_jsonb)` writer** — rejected
  (D6): grants the spine itself the authority to mutate any table a caller names, which is a
  strictly larger authority surface than "call the four already-existing, already-reviewed
  effect-owner functions under a new gate," and defeats "no generic proposal row may directly
  update a target table or tracked file."
- **Reuse `_create_qa_pr` directly for the PR adapter, parameterizing its QA-specific fields** —
  rejected (D7): would couple the spine's PR authority to `healing_attempts`'s state machine and
  QA's investigation-specific prompt assumptions, exactly the coupling the bead's outcome forbids
  ("QA publisher primitives are implementation substrate, not authority").
- **Trust a client-supplied `proposer` field, validated against a signature** — rejected (D3): adds
  a signing-key provisioning problem this repository's existing `SET ROLE` mechanism already
  solves for free; server-side role introspection has no key to leak or rotate.
- **Auto-expand the one Switchboard auto-apply exception to cover more target kinds "for
  consistency"** — rejected (D10): the bead is explicit that no *new* target auto-adopts; treating
  "we already have one exception" as license to add more would be exactly the silent authority
  widening the bead forbids.
- **Allow editing a `pending_review` proposal's payload in place** — rejected (D11): makes
  "content_digest" mean "digest of *a* version," not "digest of *the* reviewed version," which
  breaks the exact-digest-owner-gated-publication guarantee the moment a reviewer's mental model of
  what they reviewed diverges from what a later edit changed it to.
- **Drop or truncate legacy tables once the spine's representation lands** — rejected (D12): the
  bead's non-goals and acceptance criteria both require legacy tables retained through this move
  for rollback; this draft treats "no destructive migration" as a hard constraint, not an
  optimization to revisit later.

## Test Strategy (future implementation only)

Named seams a future implementation PR's tests must cover (none exist yet; this draft adds no
tests, `+0 ~0 -0`):

- **Migration/grants**: `public.improvement_proposals`/`improvement_proposal_events` table and
  index creation; `REVOKE`/`GRANT EXECUTE` on `propose_amendment`; `FORCE ROW LEVEL SECURITY`
  policy behavior (a runtime role's `SELECT` returns only its own `proposer_schema` rows; the
  dashboard/admin role sees all); the events-table immutability trigger rejects `UPDATE`/`DELETE`.
- **`propose_amendment` unit/contract**: proposer/schema derivation ignores any client-supplied
  proposer field; `content_digest` is a deterministic function of canonical
  `{target_kind, target_ref, payload}`; unique-partial-index supersession on a repeat call for the
  same identity (D11); rejection of an unsupported `target_kind`/malformed `payload` shape.
- **Registration/API**: each dashboard endpoint's request/response shape, authentication
  requirement (401 unauthenticated, matching the existing `dashboard-approvals` pattern), and
  content-blindness (list/summary responses never include raw `payload`/evidence content; detail
  does, under the same privileged auth the endpoint already requires).
- **Concurrency/idempotence**: two simultaneous "Accept and apply" calls for the same proposal
  produce exactly one target effect and one terminal `review_status` transition (D9); a DB-target
  transaction failure leaves `review_status='accepted'`, `application_status='failed'`, and no
  partial target write; a rollback attempt against a target changed since apply is refused via the
  compare-and-set (D9); PR-adapter push/create response loss reconciles by deterministic head
  branch instead of duplicating a PR (D8); a base-SHA conflict at PR-open time fails closed with no
  automatic rewrite (D8).
- **Backfill**: idempotent re-run produces no duplicate rows (`ON CONFLICT DO NOTHING` on the
  legacy-provenance unique constraint); every legacy status maps per the table in the capability
  spec; legacy tables are read-only touched (no row mutated, no column added by the backfill
  migration itself).
- **Compatibility/auto-apply**: the backfilled/dual-written clearly-automated Switchboard exception
  proposal lands as `review_status='accepted'`/`application_status='applied'` with
  `decided_by='system:rule_promotion_auto_apply'` and triggers no new gate; every other backfilled
  or freshly-proposed row of every other kind requires an explicit accept call before
  `application_status` can leave `not_requested`/`queued`.
- **Adapter boundary (privacy/authority)**: a static/dependency assertion that the PR adapter's
  module has no import edge into `src/butlers/core/qa/dispatch.py::_create_qa_pr` or any
  `healing_attempts` write path — mirroring the `notify-confirm-interaction` precedent's equivalent
  static-assertion test for its own authority boundary.
- **UI**: the dashboard's proposal card renders `Accept and apply` for DB-target kinds and
  `Accept and open PR` for git-PR-target kinds (never a generic "Approve" verb); a degraded source
  (e.g. GitHub unreachable during head-reconciliation) surfaces a truthful "unavailable" indicator
  per `docs/api_and_protocols/response-conventions.md`, never a silent empty/all-clear list.

Expected test delta is deliberately not predicted to an exact node count before the contract is
owner-approved and an implementation exists to measure against; this draft's own test delta is
`+0 ~0 -0` (no code changes). No live GitHub publication, database migration, merge, runtime
activation, or deployment is required or authorized by this draft.

## Delivery gates

1. Land this draft only after independent exact-head semantic/security/state-machine review
   returns GO or corrections are applied and re-reviewed.
2. Obtain separate owner approval naming the exact reviewed commit before any implementation of
   `bu-8cdl1.15` or its decomposed children claims this contract as authority.
3. Resolve the `core-skills`/`k3s-deployment-helm-chart` sequencing (proposal.md "Impact") before
   either change archives — whichever archives second rebases its `AGENTS.md Read/Write Access`
   delta onto the requirement text the other left in `openspec/specs/`.
4. Implementation proceeds only under `bu-8cdl1.15`'s already-existing decomposition sequence
   (representation/backfill → transactional producers/adapters while legacy readers remain
   authoritative → dashboard read/review cutover → owner-authenticated DB effect adapters and
   rollback receipts → generalized PR publisher + `propose_amendment` + identity-write cutover),
   each step separately gated and tested per the Test Strategy above.
5. Treat any live GitHub API call, database migration, or deployment as a separate, later-authorized
   act this draft does not perform or authorize.

## Open questions (proposed for owner review — not decided here)

These are the artifact's genuinely owner-scoped items — product/privacy/authority calls, not
ordinary engineering allocation, and this draft deliberately leaves them undecided rather than
picking a default and presenting it as settled:

1. **Whether to preserve or revoke the existing Switchboard clearly-automated auto-apply
   exception** once the unified spine is live (D10). This draft's recommendation is preservation
   (matching the shaping evidence and avoiding an unrequested behavior change), but revocation is a
   legitimate alternative an owner may prefer once all target kinds are visible in one place.
2. **The exact target payload schema and roster-path allowlist for prompt/skill/schedule/manifesto
   amendments** (`design.md` D7's roster-path allowlist sketch is illustrative, not final) —
   the shaping evidence flags this as not-yet-owner-approved, and this draft agrees it needs an
   explicit allowlist review rather than an engineering default, because the blast radius of a
   wrong path (e.g. accidentally allow-listing a credentials or migration path) is a security
   decision, not a convenience one.
3. **Retention/redaction rules for proposal payloads and the exact privileged dashboard detail
   projection** — how long a rejected or superseded proposal's restricted payload is retained, and
   exactly which fields the privileged detail view surfaces beyond what this draft names, needs
   the same owner review this repository already requires for other content-blind/audit-retention
   decisions (see the in-flight `project-audit-history-content-blind`/
   `project-secret-read-endpoints-content-blind` changes for the house pattern).
4. **Reuse of QA's existing GitHub publication credentials/repository registration for the new PR
   adapter's principal** — the shaping evidence is explicit that this is a *new* authority (a
   second principal with PR-creation rights, even if scoped to a path allowlist within the same
   repository) and must be named and approved in the eventual RFC 0033 text, not inferred from
   "QA already has GitHub access."
