# Durable Approval Recurrence Repair Plan

**Goal:** Stop opaque connector identities from repeatedly generating unusable
standing-rule promotion prompts, and stop a rejected or still-unexecuted entity
merge approval from being re-created by weekly curation.

**Architecture:** Model a connector endpoint as a first-class, exact-match
ingestion rule condition. New promotion suggestions select `sender_address` only
for real email identities and otherwise propose `source_endpoint`; the ingestion
envelope carries that endpoint from intake through policy evaluation. A narrow,
provenance-gated compatibility path makes already-promoted legacy opaque
`sender_address` rows effective without rewriting their audit history. Entity
deduplication treats a current pending, approved, or rejected action for the
same ordered pair as durable curation state; it neither changes that action nor
re-executes it.

**Tech stack:** Python 3.12, asyncio, asyncpg, Alembic, FastAPI/Pydantic,
React/TypeScript, Vitest, pytest, OpenSpec Markdown.

## Constraints

- Work only on `fix/durable-approval-recurrence` in its dedicated worktree.
- Do not modify live approval rows, perform entity merges, or retry historical
  actions. Historic records remain audit evidence.
- Preserve email `sender_address` semantics: opaque values must not make a
  manually-authored sender-address rule match.
- Keep promotion conditions globally scoped, exact-match, and editable/testable
  through the existing rule editor.
- Do not introduce a Beads item, dependency, or unrelated migration.

## Task 1: Pin the identity and lifecycle regressions

**Files:**
- Modify: `tests/core/test_ingestion_policy.py`
- Modify: `roster/switchboard/tests/test_rule_promotion_trigger.py`
- Modify: `tests/integration/test_switchboard_rule_promotion_trigger_job.py`
- Modify: `tests/modules/test_module_pipeline.py`
- Modify: `tests/api/test_switchboard.py`
- Modify: `roster/relationship/tests/test_entity_dedup_curation_job.py`
- Modify: `frontend/src/components/ingestion/filters/RuleEditor.test.tsx`

1. Add failing tests showing that an opaque source endpoint cannot be covered by
   `sender_address`, but is covered by an exact `source_endpoint` rule.
2. Add an end-to-end promotion test for `spotify:tzeusii` that verifies the new
   proposal shape, application, and subsequent coverage suppression.
3. Pin routing-verdict identity selection: email uses the observed sender when
   available; non-email uses the stable source endpoint.
4. Add API/editor tests for creating and testing a `source_endpoint` rule.
5. Replace the old "resurface after decided" entity-dedup expectation with
   rejected and approved-but-unexecuted suppression tests. Retain pending and
   expired lifecycle coverage.
6. Run focused tests and record the expected failures before implementation.

## Task 2: Introduce a durable source-endpoint rule type

**Files:**
- Modify: `src/butlers/ingestion_policy.py`
- Modify: `roster/switchboard/api/models.py`
- Modify: `roster/switchboard/api/router.py`
- Modify: `roster/switchboard/tools/ingestion/ingest.py`
- Modify: `roster/switchboard/tools/routing/rule_promotion.py`
- Modify: `roster/switchboard/tools/routing/rule_promotion_apply.py`
- Modify: `roster/switchboard/tools/routing/verdict_log.py`
- Modify: `src/butlers/modules/pipeline.py`
- Add: `roster/switchboard/migrations/029_switchboard_source_endpoint_promotions.py`

1. Add `source_endpoint_identity` to the pre-classification envelope and an
   exact, case-normalized `source_endpoint` matcher.
2. Expose the condition through the global API schema and rule-test envelope;
   reject it from connector-scoped rules by leaving connector vocabulary
   unchanged.
3. Choose promotion shape by a full email match. Use `sender_address` for an
   actual email and `source_endpoint` for opaque connector identities.
4. Ensure runtime verdict logging chooses the observed sender for email and the
   connector endpoint for non-email traffic, so evidence and matching use the
   same stable identity.
5. Add a forward migration extending the promotion-suggestion rule-type check.
   Do not update historic suggestions or rules.
6. Add a provenance-gated compatibility matcher for legacy promotion-created
   opaque `sender_address` rows only, preserving manual rule safety and audit
   values.

## Task 3: Suppress repeat entity-merge proposals without hiding work

**Files:**
- Modify: `roster/relationship/jobs/relationship_jobs.py`

1. Extend the exact ordered-pair lookup from `pending` to `pending`,
   `approved`, and `rejected` action statuses for both canonical and legacy
   entity-merge tool names.
2. Report prior-decision suppression separately from already-pending work while
   retaining existing job result keys for dashboard compatibility.
3. Keep `expired` actions eligible for re-evaluation; an expiry is not an owner
   decision. Do not retry or mutate `approved` actions in this job.
4. Include the existing action ID/status in structured job logs for operator
   diagnosis.

## Task 4: Make the rule usable from the dashboard

**Files:**
- Modify: `frontend/src/components/ingestion/filters/RuleEditor.tsx`
- Modify: `frontend/src/api/types.ts`

1. Add a plainly labelled source-endpoint condition field using the existing
   native controls, validation patterns, and keyboard behavior.
2. Add the endpoint value to the rule test panel payload.
3. Keep current layout, colors, focus behavior, and screen-reader labels; this
   is one additional exact-match condition rather than a new interaction model.

## Task 5: Reconcile specifications and verify

**Files:**
- Modify: `openspec/specs/ingestion-policy/spec.md`
- Modify: `openspec/specs/switchboard-rule-promotion/spec.md`
- Modify: `openspec/specs/relationship-curation/spec.md`

1. Document source-endpoint exact matching and email/non-email identity
   selection.
2. Document that curation respects a rejected or awaiting-execution exact pair
   without treating that as automatic execution or historical repair.
3. Run focused Python, integration (where Docker is available), frontend, type,
   formatting/lint, and relevant broader regression suites.
4. Inspect the final diff, commit the scoped change, push the branch, and open a
   pull request without merging it.
