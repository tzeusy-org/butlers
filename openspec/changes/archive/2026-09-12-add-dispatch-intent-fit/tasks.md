## 1. Capability descriptors

- [x] 1.1 Add `ModelFeature`, three-valued `Support`, and a validated
  `CapabilityDescriptor` that layers a per-entry envelope over an adapter baseline.
- [x] 1.2 Declare `declared_capabilities` on `RuntimeAdapter` and every in-tree
  adapter; derive `session_resume` from `supports_resume`.
- [x] 1.3 Reject unusable stored envelopes without echoing the stored value.

## 2. Catalog envelope

- [x] 2.1 Add migration `core_204` with `capabilities`, `max_context_tokens`, and
  `max_output_tokens` plus shape/positivity constraints.

## 3. Dispatch intent

- [x] 3.1 Derive `DispatchIntent` deterministically from trigger source and tier.
- [x] 3.2 Define `evaluate_fit` with the capability, context, deadline, and budget
  rules and their consequence-dependent handling of unknowns.

## 4. Fit before ranking

- [x] 4.1 Add `_RESOLVE_CANDIDATES_SQL` sharing the existing eligibility CTE.
- [x] 4.2 Add `resolve_dispatch`: fit every candidate, then pick the winning tier,
  narrow by priority, and rank with the unchanged evidence/round-robin tie-break.
- [x] 4.3 Add `intent=` to `resolve_model_with_effective_tier` and wire the spawner.

## 5. Resolution receipt

- [x] 5.1 Record requested vs effective intent, per-candidate outcomes and fit
  findings, evidence age, and the winner reason; attach it to `TierQuotaExhausted`.

## 6. Verification

- [x] 6.1 Unit tests for descriptors, adapter baselines, intent derivation, and
  every fit rule.
- [x] 6.2 Database-backed tests for hard-fit exclusion, tier fallthrough, priority
  and round-robin ordering, quota interaction, receipt shape, and a control proving
  a no-requirements intent selects exactly what the legacy resolver selects.
