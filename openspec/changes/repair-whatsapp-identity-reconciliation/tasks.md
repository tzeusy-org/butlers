## 1. Contract and Regression Baseline

- [x] 1.1 Add requirement-ID citations to focused tests for every mandatory delta requirement.
- [x] 1.2 Record red test evidence for the transport alias, LID history leak, excerpt identity loss,
  JID-shaped fact entity creation, and unsafe reconciliation cases.
  Evidence: [`verification.md`](verification.md). The LID, excerpt, and fact-guard records are
  collection-level command summaries; they do not claim assertion traces that were not retained.

## 2. Canonical WhatsApp Identity

- [x] 2.1 Implement one shared `whatsapp_user_client` to `whatsapp_jid` identity translation used by
  lookup, phone fallback, storage normalization, and sender channel-fact assertion.
- [x] 2.2 Prove known, unknown, ambiguous, device-qualified, and unchanged transport-channel behavior
  with focused identity and routing tests.

## 3. Structured Per-Speaker Decomposition

- [x] 3.1 Normalize mapped LIDs and device-qualified JIDs before WhatsApp normalized text and
  conversation-history construction, keeping raw provider data internal.
- [x] 3.2 Preserve structured conversation messages until Switchboard resolves each distinct speaker
  once and reuses the primary speaker result.
- [x] 3.3 Join model-selected excerpts back to authoritative message IDs and carry `sender_identity`
  and `sender_entity_id` through fan-out.
- [x] 3.4 Cover mixed known/unknown multi-speaker batches, duplicate concepts, fail-open resolution, and
  content-blind speaker labels with unit and integration tests.

## 4. Fact-Storage Protection

- [x] 4.1 Reject WhatsApp JID/LID-shaped fact-storage entity creation with a structured,
  identifier-blind error while preserving ordinary entity creation.
- [x] 4.2 Update shared memory and Relationship fact-extraction instructions to use excerpt
  `sender_entity_id` and never treat transport identifiers as entity names.
- [x] 4.3 Verify a routed WhatsApp fact uses the intended existing or transitory entity and creates no
  JID/LID-named entity.

## 5. Guarded Historical Reconciliation

- [x] 5.1 Extract the audited relationship pair transaction behind a FastAPI-free service while
  preserving the existing endpoint contract and merge-review behavior.
- [x] 5.2 Implement reference-complete empty-shell classification, distinct-candidate matching,
  existing-decision exclusion, deterministic plan digesting, and in-transaction drift checks.
- [x] 5.3 Add a PEP 723 repository command that defaults to content-blind dry-run and requires
  `--apply` plus the exact plan digest.
- [x] 5.4 Verify authorization, ambiguity, protected references, race/drift, audit, abort-on-failure,
  post-apply recount, and zero automatic invocation.

## 6. Quality, Operability, and Delivery

- [x] 6.1 Validate the OpenSpec change, overwrite guard, requirement test citations, Ruff, formatting,
  and the affected connector/pipeline/memory/relationship suites.
- [x] 6.2 Run broader merge-readiness gates and independently review behavior, engineering quality,
  privacy, and operator safety; resolve every blocking finding.
- [ ] 6.3 Push the reviewed branch, open a content-blind pull request, and verify required CI checks on
  the exact head.
- [ ] 6.4 After deployment authorization, verify live connector health and forward identity behavior;
  do not execute reconciliation without a separate reviewed dry-run digest.
