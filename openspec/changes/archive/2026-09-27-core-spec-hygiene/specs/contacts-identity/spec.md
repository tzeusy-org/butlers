## RENAMED Requirements

- FROM: `### Requirement: [TARGET-STATE] Contact search endpoint for typeahead`
- TO: `### Requirement: Contact search endpoint for typeahead`

## REMOVED Requirements

### Requirement: Secret key renames
**Reason**: The owner-identity secret key rename was a one-time migration that has completed; the current key names are the only ones any code reads.

**Migration**: None. The current key names are specified where the secrets are consumed (butler-secrets, core-credentials).
