/**
 * KpiStrip -- 4-cell hairline-divided KPI grid.
 *
 * Each cell stacks:
 *   mono eyebrow   10px, --font-mono, --muted-foreground, uppercase, 0.14em letter-spacing
 *   mega number    32px, --font-sans, weight 500, tracking -0.03em, .tnum
 *   mono delta     10px, --font-mono, --muted-foreground, .tnum
 *
 * No background fills, no card chrome. Hairline border-right on every cell
 * except the last.
 *
 * Topology: about/lay-and-land/frontend.md §KPI strip
 * Doctrine: about/heart-and-soul/design-language.md §KPI strip
 */

import type { CSSProperties } from "react";
import { Link } from "react-router";

import { KPI_EYEBROW_STYLE } from "./kpi-eyebrow";

interface KpiCell {
  eyebrow: string;
  value: string | number;
  delta?: string;
  /**
   * Tone for the delta line. `"amber"` tints it with `--amber-text` to flag a
   * stale/at-risk reading (e.g. a vital past its freshness SLA); defaults to
   * the quiet muted-foreground.
   */
  deltaTone?: "muted" | "amber";
  /** Optional `title` tooltip for the cell, e.g. the reading's data source. */
  title?: string;
  /** Adds an assistive-technology qualifier to a visually dashed value. */
  unavailable?: boolean;
  /**
   * When present, the whole cell becomes a navigable door to this route
   * (bu-27dxl.8.3). Omit whenever the cell's value is unavailable
   * (loading/error/degraded `—`) -- a dash must never masquerade as a working
   * door; a genuine zero keeps its door.
   */
  href?: string;
  /**
   * Accessible name for the door, overriding the default Link name (the
   * cell's rendered text). Use this whenever the visible eyebrow could be
   * misread as a narrower/filtered destination than the door actually opens
   * (e.g. "Healthy" opens the full unfiltered board, not a healthy-only view).
   */
  ariaLabel?: string;
}

interface KpiStripProps {
  cells: [KpiCell, KpiCell, KpiCell, KpiCell];
}

export function KpiStrip({ cells }: KpiStripProps) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(4, 1fr)",
      }}
      role="group"
      aria-label="Key performance indicators"
    >
      {cells.map((cell, i) => {
        const cellStyle: CSSProperties = {
          paddingRight: i < 3 ? "16px" : undefined,
          paddingLeft: i > 0 ? "16px" : undefined,
          borderRight: i < 3 ? "1px solid var(--border)" : undefined,
        };
        const cellContent = (
          <>
            {/* Eyebrow */}
            <p className="tnum uppercase" style={{ ...KPI_EYEBROW_STYLE, marginBottom: "6px" }}>
              {cell.eyebrow}
            </p>
            {/* Mega number */}
            <p
              className="tnum"
              style={{
                fontFamily: "var(--font-sans)",
                fontSize: "32px",
                fontWeight: 500,
                letterSpacing: "-0.03em",
                lineHeight: 1,
                color: "var(--foreground)",
                margin: 0,
                marginBottom: "4px",
              }}
            >
              {cell.value}
              {cell.unavailable && <span className="sr-only"> unavailable</span>}
            </p>
            {/* Delta */}
            {cell.delta !== undefined && (
              <p
                className="tnum"
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "10px",
                  lineHeight: 1,
                  color:
                    cell.deltaTone === "amber"
                      ? "var(--amber-text)"
                      : "var(--muted-foreground)",
                  margin: 0,
                }}
              >
                {cell.delta}
              </p>
            )}
          </>
        );

        // A door (bu-27dxl.8.3): unavailable cells never carry an href, so a
        // loading/error/degraded '—' can never masquerade as a clickable
        // value -- see each KpiStrip consumer for the availability gating.
        if (cell.href) {
          return (
            <Link
              key={cell.eyebrow}
              to={cell.href}
              title={cell.title}
              aria-label={cell.ariaLabel}
              className="block no-underline text-inherit cursor-pointer rounded-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-inset"
              style={cellStyle}
            >
              {cellContent}
            </Link>
          );
        }

        return (
          <div key={cell.eyebrow} title={cell.title} style={cellStyle}>
            {cellContent}
          </div>
        );
      })}
    </div>
  );
}
