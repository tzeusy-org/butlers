## ADDED Requirements

### Requirement: In-Game Status History

The Steam connector SHALL persist every in-game or online-status change it detects from `GetPlayerSummaries`, so that downstream readers can time play sessions. It SHALL do this in addition to submitting the `status_change` ingest event.

ID: REQ-connector-steam-001
Source: bu-r3fmja.7; deep dive 2026-11-03 [Observed] 74 `status_change` events in 14 days, none persisted
Scope: v1-mandatory

#### Scenario: A detected change is stored
- **WHEN** an `online_status` poll observes a change in `gameextrainfo`, `gameid` or `personastate`
- **THEN** the connector SHALL insert one `connectors.steam_status_history` row with `steam_id`, `steam_account_id`, `observed_at` (the poll time), `persona_state`, `game_id`, `game_name`, `prev_game_name` and `recorded_at`
- **AND** inserting the same `(steam_id, observed_at)` again SHALL be a no-op

#### Scenario: Storage failure does not stall polling
- **WHEN** the insert fails
- **THEN** the connector SHALL log the failure, keep advancing the `online_status` cursor, and report degraded health as it does for submission failures

#### Scenario: Chronicler can read it
- **WHEN** the table is created
- **THEN** `butler_chronicler_rw` SHALL have SELECT on `connectors.steam_status_history` and no write privilege
