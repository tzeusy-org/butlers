/**
 * Shared layout constants for the connectors roster.
 *
 * A single source of truth for the grid-template-columns value used by
 * both ConnectorsRoster (header row) and ConnectorRosterRow (data rows)
 * so that column alignment can never silently drift between the two.
 */

/**
 * Grid columns for the connector roster.
 *
 * Column 1 (24px): liveness + state indicator column — two stacked dots.
 *   (Widened from 14px; sess and cost columns were removed — no backing data.)
 * Column 2 (180px): channel name + kind
 * Column 3 (1fr): function gloss + meta
 * Column 4 (120px): 24h sparkline
 * Column 5 (120px): auth pill
 * Column 6 (80px): events (last 24h)
 * Column 7 (24px): disclosure arrow
 */
export const CONNECTOR_ROSTER_GRID_COLUMNS =
  '24px 180px 1fr 120px 120px 80px 24px'
