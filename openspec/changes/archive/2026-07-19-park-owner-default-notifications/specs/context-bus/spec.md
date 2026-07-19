# Situational Context Bus — Delta

## ADDED Requirements

### Requirement: Suppressing Context Wake Anchor

The system SHALL require consumers that defer routine owner-default
notifications for `dnd` or `sleeping` to use only active context entries and
compute a wake anchor as the latest `expires_at` among all active suppressing
entries. The selected ledger reason SHALL remain deterministic: `dnd` takes
precedence over `sleeping`, but reason precedence SHALL NOT shorten the wake
anchor. This is a read-only use of existing TTL-bearing context data and SHALL
not add a producer, schema field, or cross-butler write.

#### Scenario: DND reason preserves a later sleeping expiry

- **WHEN** active DND expires at 09:00 UTC and active sleeping expires at 10:00
  UTC
- **THEN** the suppressing-context result selects `dnd` as its reason
- **AND** it returns 10:00 UTC as the wake anchor

#### Scenario: Only active suppressors contribute to the wake anchor

- **WHEN** a DND entry is superseded or expired and a sleeping entry is active
- **THEN** the result ignores the inactive DND entry and uses the sleeping
  entry's expiry

#### Scenario: No suppressor produces no wake anchor

- **WHEN** no active DND or sleeping entry exists
- **THEN** the suppressing-context result is absent and the notify caller can
  continue its normal policy evaluation
