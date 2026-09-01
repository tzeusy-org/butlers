## MODIFIED Requirements

### Requirement: Account Lifecycle Management

A Steam account SHALL support disconnection as a soft delete, permanent hard
deletion, and reconnection of a previously revoked account, retaining stored
credentials across a soft delete.

#### Scenario: Disconnect (soft delete)

- **WHEN** a user disconnects a Steam account via the dashboard
- **THEN** the account's `status` SHALL be set to `'revoked'`
- **AND** the connector SHALL stop polling this account on the next discovery cycle
- **AND** the companion entity and entity_info rows SHALL be retained (credentials are not deleted)
- **AND** if the disconnected account was primary, no automatic promotion occurs — the user must manually set a new primary

#### Scenario: Hard delete

- **WHEN** a user requests permanent deletion of a Steam account
- **THEN** the `public.steam_accounts` row SHALL be deleted (CASCADE deletes the companion entity and its entity_info)

#### Scenario: Reconnect a revoked account

- **WHEN** a user reconnects a previously revoked Steam account (same SteamID)
- **THEN** the existing row's `status` SHALL be updated to `'active'`
- **AND** the API key in entity_info SHALL be updated if a new key is provided
- **AND** the connector SHALL resume polling on the next discovery cycle
