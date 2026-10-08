## ADDED Requirements

### Requirement: Recorded Decision Intent State Is Visible

When a selected decision carries a recorded intent, its inline detail SHALL render the intent's
chosen option and an honest status line: `pending` and `applying` read as recorded and awaiting
application to the tracker, `applied` reads as applied, and `failed` reads as failed with its
categorical reason. A pending intent carrying a `last_error` SHALL name that the last attempt did
not complete. This rendering SHALL remain read-only: it SHALL NOT add approve, deny, close, choose,
retry, or default-application controls, and a decision without an intent SHALL render exactly as
before.

#### Scenario: A pending intent is shown as awaiting application

- **WHEN** the owner selects a decision whose intent is `pending`
- **THEN** the detail names the chosen option and that it awaits application
- **AND** no mutation control is rendered

#### Scenario: A failed intent names its reason

- **WHEN** the owner selects a decision whose intent is `failed`
- **THEN** the detail names the chosen option, that applying it failed, and the categorical reason
