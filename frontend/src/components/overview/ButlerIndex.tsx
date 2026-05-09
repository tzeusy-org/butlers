/**
 * ButlerIndex -- right-column butler list with letter-marks, sessions, and cost.
 *
 * Row grid: ButlerMark 16px / 1fr butler name / auto sessions / auto cost
 * Vertical padding: 10px per row, hairline border separators.
 *
 * Uses ButlerMark with tone="neutral" per the spec.
 *
 * Topology: about/lay-and-land/frontend.md §Butler letter-mark, §Row anatomies
 * Doctrine: about/heart-and-soul/design-language.md §Butler hue scope
 */

import { ButlerMark } from "@/components/ui/ButlerMark";
import { Section } from "./Section";

interface ButlerIndexEntry {
  name: string;
  sessions: number;
  costUsd: number;
}

interface ButlerIndexProps {
  butlers: ButlerIndexEntry[];
}

export function ButlerIndex({ butlers }: ButlerIndexProps) {
  return (
    <Section eyebrow="Butlers">
      <div role="list" aria-label="Butler index">
        {butlers.map((butler, i) => (
          <div
            key={butler.name}
            role="listitem"
            style={{
              display: "grid",
              gridTemplateColumns: "16px 1fr auto auto",
              alignItems: "center",
              gap: "8px",
              paddingTop: "10px",
              paddingBottom: "10px",
              borderTop: i === 0 ? "1px solid var(--border)" : undefined,
              borderBottom: "1px solid var(--border)",
            }}
          >
            {/* ButlerMark */}
            <ButlerMark name={butler.name} tone="neutral" />

            {/* Butler name */}
            <span
              style={{
                fontFamily: "var(--font-sans)",
                fontSize: "13px",
                fontWeight: 400,
                color: "var(--foreground)",
                lineHeight: 1.4,
              }}
            >
              {butler.name}
            </span>

            {/* Sessions count */}
            <span
              className="tnum"
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--muted-foreground)",
                lineHeight: 1.4,
              }}
            >
              {butler.sessions}
            </span>

            {/* Cost */}
            <span
              className="tnum"
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--muted-foreground)",
                lineHeight: 1.4,
                minWidth: "48px",
                textAlign: "right",
              }}
            >
              ${butler.costUsd.toFixed(3)}
            </span>
          </div>
        ))}
        {butlers.length === 0 && (
          <p
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: "14px",
              fontStyle: "italic",
              color: "var(--muted-foreground)",
              paddingTop: "10px",
              paddingBottom: "10px",
            }}
          >
            No butlers active.
          </p>
        )}
      </div>
    </Section>
  );
}
