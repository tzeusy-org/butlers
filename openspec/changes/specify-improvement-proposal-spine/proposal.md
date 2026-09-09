# Specify unified improvement proposals and gated amendment PRs

## Why

`bu-8cdl1.15` ("One improvement-proposal spine + `propose_amendment`") is not packet-complete:
there is no governing public improvement-proposal contract, and current authority describes two
different, intentional legacy systems (`autonomy-suggestions`'s per-butler `autonomy_suggestions`
table, RFC 0021's confirmed human-gate model; `switchboard-rule-promotion`'s
`rule_promotion_suggestions` table, which preserves a separately approved automatic exception for
clearly-automated suppression rules). A 2026-09-06 bounded shaping pass
(`/home/tze/.local/share/butlers/coordinator-evidence/run11-shaping-20260906/bu-8cdl1.15-shaping.md`)
determined `.15` cannot proceed as implementation and proposed this spec-first prerequisite bead
(`bu-3xqhy`), which this draft delivers.

Three further conflicts make this a specification problem before it can be an implementation one:

1. **`core-skills` already authorizes direct `AGENTS.md` writes**, and the active (unmerged, no
   open PR found) `k3s-deployment-helm-chart` change goes further, adding a `state`-table DB
   fallback so a read-only k3s pod can still persist "runtime agent notes" — which get read back
   and concatenated into the next system prompt. Neither authority currently exists on any
   runtime-facing MCP tool surface (`write_agents_md`/`append_agents_md` are today only used by
   tests and other developer-facing tooling, per a full-repo call-site scan), but both remain
   *latent* direct-write authority that a gated-PR-only identity contract must close off before
   any future tool wires a runtime session to either path.
2. **QA's `_create_qa_pr` (`src/butlers/core/qa/dispatch.py`) is finding-specific implementation**,
   not generic authority: it carries QA statuses (`healing_attempts`), QA labels, and QA prompt
   assumptions. Its clone/worktree/anonymization/whitelist/push/PR primitives are reusable
   substrate; its state machine and authority are not.
3. **Two independent lifecycle vocabularies exist for the same underlying concept** (a system
   proposes something; a human decides whether to accept it, and separately, whether/how to apply
   it) and a dashboard that renders them as two disconnected sections.

This change is specification only. It defines the public schema, state machines, target adapters,
trust boundaries, actor derivation, audit events, dashboard API/UI, retry, concurrency,
partial-effect, compatibility, and rollback semantics for one unified improvement-proposal spine —
and it explicitly reconciles the `core-skills`/k3s conflict and draws the QA-substrate boundary
so neither is a silent authority change. It performs no schema, runtime, Beads, branch, PR,
dashboard, or deployment mutation. It is itself a draft artifact requiring separate owner approval
before any implementation of `bu-8cdl1.15` or its decomposed children may begin.

## What Changes

- Add a new `improvement-proposal-spine` capability defining:
  - the restricted public schema `public.improvement_proposals` /
    `public.improvement_proposal_events` (server-derived proposer, canonical content digest,
    typed opaque evidence refs, independent `review_status` / `application_status` axes,
    fencing `generation`, `prior_value`, `effect_receipt`);
  - `propose_amendment(...)`, a narrow `SECURITY DEFINER`-style append function that derives the
    proposer butler/schema from the calling connection's active runtime role (never from
    client-supplied JSON), computes the content digest server-side, and is the *only* insert path
    into the table (no direct `INSERT` grant to any runtime role);
  - row-level security restricting each runtime role to reading its own proposals, mirroring the
    existing `expected_signals` forced-RLS precedent (`database-security` spec) but applied to
    `SELECT` as well as write;
  - four target-adapter families (DB-transactional for `autonomy_rule` / `ingestion_rule` /
    `model_tier`|`runtime_config`; git-PR for `roster_prompt` / `roster_skill` /
    `roster_manifesto` / `roster_schedule`), each naming its trust, failure, concurrency, and
    rollback semantics;
  - a one-time, idempotent backfill of every legacy `autonomy_suggestions` and
    `switchboard.rule_promotion_suggestions` row into the new spine, keyed by
    `(legacy_system, legacy_schema, legacy_id)` provenance, with legacy tables retained
    unmodified (no drop, no destructive migration) so a partial cutover can roll back to legacy
    readers;
  - an explicit, literal statement that the existing owner-approved Switchboard
    clearly-automated-suppression auto-apply exception (`switchboard-rule-promotion`,
    "Promotion Application") is preserved as a named compatibility adapter and that this draft
    grants **no new auto-adoption** for any target kind;
  - a dashboard API/UI contract (`GET /api/improvement-proposals`,
    `GET /api/improvement-proposals/{id}`, `POST .../reject`, `.../accept-and-apply`,
    `.../accept-and-open-pr`, `.../rollback`) that is content-blind at the list level, exposes the
    restricted payload only in the privileged detail view, and truthfully surfaces degraded
    sources per this repository's fan-out convention
    (`docs/api_and_protocols/response-conventions.md`) rather than a silent empty/all-clear state.
- Add a **`[TARGET-STATE]`** MODIFIED requirement to `core-skills`'s "AGENTS.md Read/Write Access"
  requirement: `write_agents_md`/`append_agents_md` (file path or DB fallback alike) MUST NOT be
  reachable from any runtime-facing MCP tool surface; any future exposure of agent-notes writing
  to a butler runtime session MUST route through `propose_amendment` and the PR target adapter
  instead of a direct call. Developer/deployment tooling callers (migrations, CLI scripts, the
  existing test suite) are unaffected — this constrains only a *runtime session's* reachable
  surface, which today is empty (no MCP tool wires to either function), so the change is
  preventive, not a revocation of exercised behavior.
- No change to `about/legends-and-lore/rfcs/0021-decision-loop-one-tap-approvals-and-decision-memory.md`,
  `pending_actions`, the approvals executor, `autonomy-suggestions`, or
  `switchboard-rule-promotion`'s baseline requirements — all remain authoritative, unmodified, and
  are the exact backfill/dual-write sources this draft's new capability spec cites by name.
- No change to `qa-investigation-dispatch`, `healing-worktree`, or `healing-anonymizer` baseline
  requirements — this draft names their primitives as reusable implementation substrate for a
  future PR adapter and explicitly forbids that adapter from calling `_create_qa_pr` or writing
  `healing_attempts`; it does not touch QA's own state machine, tables, or authority.

## Capabilities

### New Capabilities

- `improvement-proposal-spine`: the public schema, state machines, server-derived actor
  derivation, target adapters, backfill/dual-write contract, retry/concurrency/rollback
  semantics, and dashboard API/UI for one unified improvement-proposal-and-gated-amendment-PR
  system.

### Modified Capabilities

- `core-skills`: adds a `[TARGET-STATE]` boundary constraint to "AGENTS.md Read/Write Access" —
  no runtime-facing MCP tool surface may reach `write_agents_md`/`append_agents_md` directly;
  additive to developer/deployment callers, preventive (not revocation) for runtime sessions.

## Impact

- **Affected future code** (not touched by this draft): a new core migration for
  `public.improvement_proposals` / `public.improvement_proposal_events` plus grants/RLS, a new
  `propose_amendment` SQL function, new dashboard API routes under
  `src/butlers/api/routers/improvement_proposals.py` (or similar), new target-adapter modules
  (DB-transactional and PR-publisher), a generalization pass over
  `src/butlers/core/qa/repo_whitelist.py` / `src/butlers/core/healing/{worktree,anonymizer}.py`
  primitives into a shared, QA-independent module, and the eventual `core-skills`
  runtime-tool-surface audit.
- **Affected RFCs**: none amended by this draft. A future implementation PR should record the
  spine's wire contract as a new RFC (next free number: **0033**, since 0001-0032 are taken) per
  `design.md` D1, rather than amending RFC 0021 (a materially different trust model — see D1).
- **`core-skills` / `k3s-deployment-helm-chart` sequencing (named, not silent)**: this draft's
  `core-skills` delta is authored as a full superset — it carries forward, verbatim, every
  scenario the unarchived `k3s-deployment-helm-chart` change's own `AGENTS.md Read/Write Access`
  MODIFIED block already defines (the DB-fallback-on-read-only-filesystem behavior), and adds
  the new runtime-tool-surface boundary scenario on top. Both changes still modify the *same*
  requirement name against the *same* current baseline, which `check-spec-overwrites`
  (`scripts/check_spec_overwrites.py`) and `openspec archive` treat as an ordinary two-change
  collision, not a conflict this draft can resolve unilaterally by archive order. This is named
  here as the exact prerequisite: whichever of the two changes archives second MUST rebase its
  `core-skills` delta onto the requirement text the other one left in `openspec/specs/` before
  archiving (mechanical, since this draft's DB-fallback scenarios are copied verbatim from
  `k3s-deployment-helm-chart`'s own text — see `design.md` D13). No implementation may treat either
  change as archived out of this order.
- **QA publisher primitives**: remain QA-owned. This draft names the exact primitives (managed
  clone, isolated worktree, path allowlist, minimal-credential environment,
  anonymize/validate_anonymized, GitHub publish-and-reconcile) as substrate a future PR adapter
  reuses by import/generalization, never by calling `_create_qa_pr` or writing `healing_attempts`
  — see `design.md` D7.
- **`bu-24lu6` / `bu-24lu6.8`** (autonomy-suggestions decision-loop closeout) and its active
  `fingerprint_version` OpenSpec change: the backfill this draft specifies is additive and
  read-only against `autonomy_suggestions`; a future implementation must wait for that closeout
  or rebuild the backfill mapping against its final schema. This draft's backfill requirement
  names `fingerprint_version` as an open compatibility question for that future implementation,
  not something decided here.
- No implementation, migration, runtime process, provider/credential access, message or PR
  publication, deployment, or merge is performed or authorized by this draft. Expected tests:
  `+0 ~0 -0`.
