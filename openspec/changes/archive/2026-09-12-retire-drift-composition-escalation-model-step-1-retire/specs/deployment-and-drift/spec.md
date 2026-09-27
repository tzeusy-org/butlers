## REMOVED Requirements

### Requirement: QA Escalation After Sustained Drift

**Reason**: Retired for one archive step only, so that
`retire-drift-composition-escalation-model-step-2-restore` can re-add the
requirement stating a single escalation model. The baseline states the
drift-composition model (a first-detected marker keyed by a fingerprint of the
whole drifted set, escalating once per composition); the episode model that
`define-infrastructure-reliability-lifecycle` introduces contradicts it on two
scenarios. OpenSpec has no operation that replaces a baseline scenario set
under one requirement name -- a `## MODIFIED` block must reproduce every
baseline scenario name verbatim, and `REMOVED` plus `ADDED` of the same
requirement in one change is refused -- so remove-then-add across two changes
is the only route. QA escalation after sustained drift is NOT being retired as
a behaviour; step 2 restates it as an episode lifecycle.
