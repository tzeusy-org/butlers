/**
 * AttentionList -- rule-separated rows for items that need attention.
 *
 * Row grid: 24px severity glyph / 1fr title+detail / auto action arrow.
 * Vertical padding: 18px per row.
 *
 * Inline empty state: serif italic "Nothing waiting." in muted color,
 * no illustration, no action button.
 *
 * Topology: about/lay-and-land/frontend.md §Row anatomies
 * Doctrine: about/heart-and-soul/design-language.md §Attention list
 */

import type { Issue } from "@/api/types";

interface AttentionListProps {
  items: Issue[];
}

/**
 * Map severity string to a one-character glyph and a color.
 */
function severityGlyph(severity: string): { char: string; color: string } {
  switch (severity.toLowerCase()) {
    case "high":
    case "critical":
    case "error":
      return { char: "!", color: "var(--destructive)" };
    case "medium":
    case "warning":
    case "warn":
      return { char: "~", color: "var(--severity-medium)" }; // amber
    default:
      return { char: "·", color: "var(--muted-foreground)" };
  }
}

export function AttentionList({ items }: AttentionListProps) {
  if (items.length === 0) {
    return (
      <p
        style={{
          fontFamily: "var(--font-serif)",
          fontSize: "16px",
          fontStyle: "italic",
          color: "var(--muted-foreground)",
          lineHeight: 1.6,
        }}
      >
        Nothing waiting.
      </p>
    );
  }

  return (
    <div role="list" aria-label="Attention items">
      {items.map((item, i) => {
        const { char, color } = severityGlyph(item.severity);
        return (
          <div
            key={`${item.butler}-${item.type}-${i}`}
            role="listitem"
            style={{
              display: "grid",
              gridTemplateColumns: "24px 1fr auto",
              alignItems: "start",
              gap: "8px",
              paddingTop: "18px",
              paddingBottom: "18px",
              borderTop: i === 0 ? "1px solid var(--border)" : undefined,
              borderBottom: "1px solid var(--border)",
            }}
          >
            {/* Mark column: severity glyph */}
            <span
              aria-label={`Severity: ${item.severity}`}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "14px",
                fontWeight: 500,
                color,
                lineHeight: 1,
                paddingTop: "2px",
              }}
            >
              {char}
            </span>

            {/* Title + detail column */}
            <div>
              <p
                style={{
                  fontFamily: "var(--font-sans)",
                  fontSize: "14px",
                  fontWeight: 500,
                  color: "var(--foreground)",
                  lineHeight: 1.4,
                  margin: 0,
                }}
              >
                {item.description}
              </p>
              <p
                style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: "13px",
                  color: "var(--muted-foreground)",
                  lineHeight: 1.5,
                  margin: 0,
                  marginTop: "2px",
                }}
              >
                {item.butler}
                {item.error_message ? `: ${item.error_message}` : ""}
              </p>
            </div>

            {/* Action column: arrow link if available */}
            {item.link ? (
              <a
                href={item.link}
                aria-label={`View: ${item.description}`}
                style={{
                  color: "var(--muted-foreground)",
                  fontSize: "16px",
                  lineHeight: 1,
                  textDecoration: "none",
                  paddingTop: "2px",
                }}
              >
                →
              </a>
            ) : (
              <span aria-hidden="true" />
            )}
          </div>
        );
      })}
    </div>
  );
}
