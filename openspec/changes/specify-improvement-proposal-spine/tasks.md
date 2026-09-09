## 1. Draft the contract

- [x] 1.1 Specify the restricted public schema (`public.improvement_proposals` /
  `public.improvement_proposal_events`): columns, independent `review_status`/`application_status`
  axes, `generation` fencing, `prior_value`, `effect_receipt`, legacy provenance columns, and the
  append-only events table with an immutability trigger.
- [x] 1.2 Specify `propose_amendment` as the sole insert path with server-derived
  proposer/schema (role introspection, never a client-supplied field) and server-computed
  `content_digest` (canonical JSON sha256, never client-supplied).
- [x] 1.3 Specify row-level security restricting each runtime role to reading only its own
  proposals, exempting the privileged dashboard/admin role.
- [x] 1.4 Specify content-blind restricted representation: `payload` is schema-validated per
  `target_kind`, `evidence_refs` are typed opaque references, and event metadata never carries raw
  content.
- [x] 1.5 Specify the four target-adapter families (DB-transactional: `autonomy_rule`,
  `ingestion_rule`, `model_tier`/`runtime_config`; git-PR: `roster_prompt`, `roster_skill`,
  `roster_manifesto`, `roster_schedule`) and that each calls its target's existing effect-owner
  function/process rather than writing generically.
- [x] 1.6 Specify DB-target atomic accept-and-apply (single-transaction fenced transition + prior-
  value capture + effect call + event), failure semantics (`review_status` stands, `application_status`
  becomes `failed`), and compare-and-set rollback with refusal on a since-changed target.
- [x] 1.7 Specify git-PR-target base-SHA fencing (fail closed on divergence, no auto-rewrite),
  deterministic per-proposal branch naming, and recovery-by-head-reconciliation for lost
  push/PR-create responses.
- [x] 1.8 Specify the PR adapter's substrate-reuse boundary: anonymization gate, isolated-worktree
  pattern, and path-allowlist pattern as reusable substrate; explicit prohibition on calling
  `_create_qa_pr` or writing `healing_attempts`.
- [x] 1.9 Specify payload immutability and identity-scoped supersession (partial unique index +
  atomic supersede-on-repeat-proposal) as the only path for "changed content."
- [x] 1.10 Specify the legacy backfill (idempotent, read-only against legacy tables, provenance-
  keyed status mapping table) and name dual-write as a future-implementation contract this draft
  shapes but does not build.
- [x] 1.11 Specify that the existing Switchboard clearly-automated auto-apply exception is
  preserved unmodified as a named compatibility adapter, and that no other target kind gets any
  auto-adoption path.
- [x] 1.12 Specify the dashboard API/UI contract: content-blind list, privileged detail, verb-
  specific decision/effect endpoints (reject / accept-and-apply / accept-and-open-PR / rollback)
  with server-derived actor, and truthful degraded-source disclosure.
- [x] 1.13 Draft the `core-skills` MODIFIED delta closing the runtime-MCP-tool-surface gap for
  `write_agents_md`/`append_agents_md`, authored as a full superset of the active
  `k3s-deployment-helm-chart` change's own delta on the same requirement, with the sequencing
  dependency named explicitly in `proposal.md`/`design.md` rather than left implicit.
- [x] 1.14 Confirm no change to RFC 0021, `pending_actions`, the approvals executor,
  `autonomy-suggestions`'s or `switchboard-rule-promotion`'s baseline requirements, or
  `qa-investigation-dispatch`/`healing-worktree`/`healing-anonymizer`'s baseline requirements.
- [x] 1.15 Name the four genuinely owner-scoped open questions (auto-apply exception
  preservation-vs-revocation; exact roster-path allowlist and payload schema; retention/redaction
  and privileged-detail-projection rules; reuse of QA's GitHub credentials for a new principal) as
  proposed-for-owner-review rather than deciding them.

## 2. Approval gates

- [ ] 2.1 Obtain independent exact-head semantic/security/state-machine review of this draft.
- [ ] 2.2 After review passes, obtain separate owner approval naming the exact reviewed artifact.
  Any semantic edit invalidates that review and requires a fresh pass.
- [ ] 2.3 Resolve the `core-skills`/`k3s-deployment-helm-chart` sequencing (`design.md` D13) before
  either change archives.
- [ ] 2.4 Keep any implementation, migration, credential, runtime, or live GitHub/PR call blocked
  until this contract is accepted and its own separate authorities (Open Questions) are satisfied.

## 3. Future implementation after approval (`bu-8cdl1.15` or its approved decomposition)

- [ ] 3.1 Draft and, once code exists, apply RFC 0033 recording this contract — do not amend RFC
  0021.
- [ ] 3.2 Add the `public.improvement_proposals`/`improvement_proposal_events` migration, grants
  (`REVOKE` direct writes, `GRANT EXECUTE` on `propose_amendment`), `FORCE ROW LEVEL SECURITY`
  policy, and the events-table immutability trigger.
- [ ] 3.3 Implement `propose_amendment` with server-derived proposer/schema and server-computed
  `content_digest`.
- [ ] 3.4 Implement the DB-transactional adapters (`autonomy_rule`, `ingestion_rule`,
  `model_tier`/`runtime_config`), each calling its existing effect-owner function inside the fenced
  accept-and-apply transaction, plus the compare-and-set rollback path.
- [ ] 3.5 Generalize `RepoWhitelist`'s fail-closed shape into a roster-path allowlist, generalize
  `create_healing_worktree`/`remove_healing_worktree`'s isolation pattern into a
  `.proposal-worktrees/` sibling, and implement the git-PR adapter (base-SHA fencing, deterministic
  branch, head-reconciliation retry, anonymization gate reuse) as a module with no import edge into
  `_create_qa_pr` or `healing_attempts`.
- [ ] 3.6 Implement the one-time idempotent backfill migration and, per the decomposition sequence,
  the later dual-write coupling for both legacy producers.
- [ ] 3.7 Implement the dashboard API routes and UI section (content-blind list, privileged detail,
  the four decision/effect endpoints, degraded-source disclosure).
- [ ] 3.8 Resolve the Open Questions with the owner before implementing the pieces that depend on
  them (auto-apply exception disposition; exact payload schema/path allowlist; retention/redaction
  rules; the PR adapter's credential/principal).

## 4. Future verification after approval

- [ ] 4.1 Migration/grants tests: schema/index creation, revoked direct writes, RLS SELECT scoping,
  events-table immutability trigger.
- [ ] 4.2 `propose_amendment` unit/contract tests: proposer/schema derivation ignores client input,
  digest determinism, unique-partial-index supersession, unsupported-`target_kind` rejection.
- [ ] 4.3 Real-Postgres concurrency tests: racing accept-and-apply resolves to one effect; failed
  DB-target apply leaves no partial write; rollback compare-and-set refusal after a later edit; PR
  push/create response-loss reconciles by head without duplicating a PR; base-SHA conflict fails
  closed with no auto-rewrite.
- [ ] 4.4 Backfill tests: idempotent re-run, per-table status mapping, zero legacy-row mutation.
- [ ] 4.5 Compatibility tests: the auto-apply exception backfills as already-accepted with no new
  gate; every other kind requires an explicit accept.
- [ ] 4.6 Adapter-boundary test: static/dependency assertion that the PR adapter never imports or
  calls `_create_qa_pr` or a `healing_attempts` write path.
- [ ] 4.7 API/UI tests: content-blindness of list vs. detail, authentication requirements, verb-
  mismatch rejection, server-derived actor on decisions, degraded-source disclosure.
- [ ] 4.8 Run targeted unit/contract/real-Postgres/API tests, repo guards (`make check-guards`),
  strict OpenSpec and spec-overwrite checks, lint/format, a fresh independent exact-head review, and
  terminal hosted CI. Report the implementation PR's actual test delta separately — this draft
  predicts no exact node count.

## 5. Archive only after implementation

- [ ] 5.1 After the separately approved implementation is merged, sync the
  `improvement-proposal-spine` capability and the `core-skills` delta to their `openspec/specs/`
  baselines and archive this change, resolving the `k3s-deployment-helm-chart` sequencing (task
  2.3) as part of that archival. Archival does not authorize deployment, runtime activation, or any
  live GitHub/PR call beyond what the separately approved implementation itself performs.
