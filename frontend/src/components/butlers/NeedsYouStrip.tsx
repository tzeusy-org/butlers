// ---------------------------------------------------------------------------
// NeedsYouStrip — triage strip for the /butlers status board (bu-86c4c.17)
//
// Leads the board with every butler that needs the owner: offline,
// quarantined (with its reason), or overdue against its own cron cadence.
// Collapses to a single calm line ("All N healthy") when the fleet needs
// nothing -- per the audit's "one calm green line on a good day" concept.
//
// Doctrine:
//   - Real <Link> elements (cmd-click, SPA nav) -- no div-onClick rows.
//   - No em-dash in any visible string.
//   - Tailwind tokens only (no inline style, no raw oklch).
// ---------------------------------------------------------------------------

import { Link } from "react-router"

import type { StatusBoardRow } from "@/hooks/use-butler-status-board"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Compact duration label ("3d", "5h", "12m") for the overdue reason line. */
function formatSilenceCompact(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  if (days >= 1) return `${days}d`
  const hours = Math.floor(seconds / 3600)
  if (hours >= 1) return `${hours}h`
  return `${Math.max(1, Math.floor(seconds / 60))}m`
}

/** Root-evidence reason line for one needs-you row -- never a bare status word. */
function reasonFor(row: StatusBoardRow): string {
  if (row.activity === "offline") return "offline"
  if (row.activity === "quarantined") return row.quarantineReason ?? "quarantined"
  if (row.activity === "overdue") {
    const silence = row.silenceSeconds != null ? formatSilenceCompact(row.silenceSeconds) : "unknown"
    return `silent ${silence}, expected ${row.cadenceLabel ?? "regularly"}`
  }
  return ""
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface NeedsYouStripProps {
  /** Rows needing attention, in stable roster order. */
  rows: StatusBoardRow[]
  /** Total fleet size, used for the all-clear line. */
  total: number
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function NeedsYouStrip({ rows, total }: NeedsYouStripProps) {
  if (rows.length === 0) {
    return (
      <div
        role="status"
        className="border-b border-border px-7 py-3 font-mono text-xs text-muted-foreground"
      >
        All {total} {total === 1 ? "butler" : "butlers"} healthy
      </div>
    )
  }

  return (
    <div role="group" aria-label="Needs your attention" className="border-b border-border px-7 py-4">
      <span className="mb-2 block font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        {rows.length} {rows.length === 1 ? "thing needs" : "things need"} you
      </span>
      <ul className="flex flex-col gap-1.5">
        {rows.map((row) => (
          <li key={row.name}>
            <Link
              to={`/butlers/${row.name}`}
              className="flex flex-wrap items-baseline gap-x-2 text-sm no-underline text-inherit hover:underline"
            >
              <span className="font-medium capitalize">{row.name}</span>
              <span className="text-xs text-muted-foreground">{reasonFor(row)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
