## REMOVED Requirements

### Requirement: Fleet-Halt Visibility

**Reason**: Retired for one archive step only, so that
`rename-fleet-halt-scenario-heading-step-2-restore` can re-add the identical
requirement under a scenario heading that names the guarantee ("The owner is
notified exactly once per breach window") rather than the mechanism ("An
attention-ledger push notifies the owner once per breach window"). OpenSpec has
no rename operation for a scenario heading, so remove-then-add is the only
route. The requirement is NOT being retired as a behaviour.
