## ADDED Requirements

### Requirement: Free/Busy Scope Coverage

The `calendar` OAuth scope the connector already requires for event access (`https://www.googleapis.com/auth/calendar`, validated as `calendar` in `granted_scopes`) also authorizes Google Calendar `/freeBusy` queries. The availability finder built on free/busy therefore SHALL require no additional OAuth scope or re-authorization.

#### Scenario: Existing calendar scope authorizes free/busy

- **WHEN** a Google account is connected with `calendar` in its `granted_scopes`
- **THEN** that grant is sufficient to query Google Calendar `/freeBusy` for the account's calendars
- **AND** no additional scope SHALL be requested solely to support free/busy availability queries

#### Scenario: No re-authorization prompt for availability

- **WHEN** the availability finder queries free/busy for an already-connected account
- **THEN** the user SHALL NOT be prompted to re-authorize, because the required scope was granted at connection time
