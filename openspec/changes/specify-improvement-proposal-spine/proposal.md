# Specify unified improvement proposals and gated amendment PRs

## Current fold-in disposition

The complete `core-skills` delta for `AGENTS.md Read/Write Access`
(`REQ-core-skills-004`) has moved to
`generalist-lead-focused-delegation/specs/core-skills/spec.md`. This source is emptied of that
moved delta and remains active, unarchived, for its 20 `improvement-proposal-spine` requirements
and 41 scenarios. Its historical drafting tasks and all outstanding review, owner approval,
implementation, verification and archive gates remain recorded in `tasks.md`. The fold does not
approve the remaining spine, register a tool or activate runtime behavior.

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

1. **Historical `core-skills` authority allowed direct `AGENTS.md` writes** ("runtime agent notes", read
   back into the next system prompt). That authority does not currently exist on any
   runtime-facing MCP tool surface (`write_agents_md`/`append_agents_md` are today only used by
   tests and other developer-facing tooling, per a full-repo call-site scan), but it remains
   *latent* direct-write authority. The General goal-runtime change now owns the full folded
   requirement: helpers remain tooling-only, no goal/sandbox or direct/thin-wrapper MCP write
   path is granted, and any future tracked-roster proposal still requires explicit owner accept
   through its target adapter. D2 guidance-notes `record` remains distinct. (An earlier draft also had to
   reconcile the `k3s-deployment-helm-chart` change's `state`-table DB fallback; that change was
   abandoned and removed, bu-60pwv6.3.)
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
and it draws the QA-substrate boundary. The former `core-skills` direct-write boundary is now
owned by the General fold-in, not a competing delta in this source. It performs no schema, runtime, Beads, branch, PR,
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
- The former **`[TARGET-STATE]`** `core-skills` MODIFIED requirement is folded into the General
  change's full `AGENTS.md Read/Write Access` block. That block retains developer/deployment
  helper semantics, the direct/thin-wrapper runtime MCP prohibition, and the conditional
  owner-accepted `propose_amendment` path for tracked roster-file changes. It also preserves the
  target's goal/sandbox ban and separate D2 guidance-notes authority. No `core-skills` delta
  remains in this source.
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

None remain in this source: the whole `core-skills` block moved to
`generalist-lead-focused-delegation`, with the existing requirement ID and complete eight-name
scenario union. The new `improvement-proposal-spine` capability remains here.

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
- **Folded `core-skills` delta**: General now owns `REQ-core-skills-004`, combining the five
  shared helper/content/workflow scenarios, both former [TARGET-STATE] scenarios and its existing
  goal/sandbox scenario. This source no longer carries or later archives that block. The unarchived
  `k3s-deployment-helm-chart` change was abandoned and removed (bu-60pwv6.3), so no cross-change
  sequencing applies; the DB-fallback text once copied from it was dropped under task 2.5
  (bu-60pwv6.27).
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
