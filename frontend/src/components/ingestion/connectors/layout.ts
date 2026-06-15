/**
 * Shared layout constants for the connectors roster.
 *
 * A single source of truth for the grid-template-columns value used by
 * both ConnectorsRoster (header row) and ConnectorRosterRow (data rows)
 * so that column alignment can never silently drift between the two.
 */

// Columns: health dot · channel · function · 24h activity · auth · events · disclosure
// sess and cost columns were removed — they had no backing data (always 0 / —).
export const CONNECTOR_ROSTER_GRID_COLUMNS =
  '14px 180px 1fr 120px 120px 80px 24px'
