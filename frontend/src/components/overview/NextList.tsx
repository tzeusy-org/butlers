/**
 * NextList -- right-column upcoming items list.
 *
 * Row grid: 50px mono time / 1fr label / auto kind tag.
 * Vertical padding: 10px per row, hairline border separators.
 *
 * Topology: about/lay-and-land/frontend.md §Row anatomies
 * Doctrine: about/heart-and-soul/design-language.md §Editorial archetype
 */

import { Section } from "./Section";

interface NextItem {
  time: string;
  label: string;
  kind: string;
}

interface NextListProps {
  items: NextItem[];
}

export function NextList({ items }: NextListProps) {
  return (
    <Section eyebrow="Next">
      <div role="list" aria-label="Upcoming items">
        {items.map((item, i) => (
          <div
            key={`${item.time}-${item.label}`}
            role="listitem"
            style={{
              display: "grid",
              gridTemplateColumns: "50px 1fr auto",
              alignItems: "center",
              gap: "8px",
              paddingTop: "10px",
              paddingBottom: "10px",
              borderTop: i === 0 ? "1px solid var(--border)" : undefined,
              borderBottom: "1px solid var(--border)",
            }}
          >
            {/* Time column */}
            <span
              className="tnum"
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                color: "var(--muted-foreground)",
                lineHeight: 1.4,
              }}
            >
              {item.time}
            </span>

            {/* Label column */}
            <span
              style={{
                fontFamily: "var(--font-sans)",
                fontSize: "13px",
                color: "var(--foreground)",
                lineHeight: 1.4,
              }}
            >
              {item.label}
            </span>

            {/* Kind tag */}
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "9px",
                color: "var(--muted-foreground)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-sm)",
                padding: "2px 5px",
                lineHeight: 1,
              }}
            >
              {item.kind}
            </span>
          </div>
        ))}
        {items.length === 0 && (
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
            Nothing scheduled.
          </p>
        )}
      </div>
    </Section>
  );
}
