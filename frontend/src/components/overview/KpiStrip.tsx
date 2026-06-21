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

import { KPI_EYEBROW_STYLE } from "./kpi-eyebrow";

interface KpiCell {
  eyebrow: string;
  value: string | number;
  delta?: string;
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
      {cells.map((cell, i) => (
        <div
          key={cell.eyebrow}
          style={{
            paddingRight: i < 3 ? "16px" : undefined,
            paddingLeft: i > 0 ? "16px" : undefined,
            borderRight: i < 3 ? "1px solid var(--border)" : undefined,
          }}
        >
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
          </p>
          {/* Delta */}
          {cell.delta !== undefined && (
            <p
              className="tnum"
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "10px",
                lineHeight: 1,
                color: "var(--muted-foreground)",
                margin: 0,
              }}
            >
              {cell.delta}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
