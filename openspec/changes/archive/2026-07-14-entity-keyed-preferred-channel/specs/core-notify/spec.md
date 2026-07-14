# Core Notify — Preferred Channel Resolution

## ADDED Requirements

### Requirement: Preferred-channel resolution for contact-targeted notifications
The notify path MUST consult a targeted contact entity's active `prefers-channel`
fact when no deliverable channel is forced by the caller, and MUST use that
channel only when it is currently deliverable. When no usable preference exists,
channel selection MUST fall back to the existing contact-fact precedence
(`has-handle` telegram → `has-email`), unchanged. This requirement adds no new
deliverable channels; the deliverable set remains as defined by the Channel
Validation requirement.

#### Scenario: Preference honored when deliverable
- **WHEN** a notification targets a `contact_id` whose entity has an active
  `prefers-channel="telegram"` fact, the entity has a telegram handle, and the
  caller did not force a different channel
- **THEN** the notification is sent on telegram

#### Scenario: Preference skipped when not deliverable
- **WHEN** a notification targets a `contact_id` whose entity prefers a channel
  not in the deliverable set (e.g. `prefers-channel="discord"`)
- **THEN** the preference is ignored without error
- **AND** channel selection falls back to the existing contact-fact precedence

#### Scenario: No preference falls back unchanged
- **WHEN** a notification targets a `contact_id` whose entity has no active
  `prefers-channel` fact
- **THEN** channel selection uses the existing contact-fact precedence
  (`has-handle` telegram → `has-email`) exactly as before this change

#### Scenario: Explicit channel still wins
- **WHEN** the caller forces a specific deliverable channel and the contact has a
  different `prefers-channel` preference
- **THEN** the forced channel is used and the preference is not consulted
