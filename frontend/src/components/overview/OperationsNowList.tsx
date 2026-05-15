/**
 * OperationsNowList -- right-column Operations/Now signal list.
 *
 * Renders current non-issue operational signals as a compact row list:
 * pending approvals, QA patrol/investigation state, failed notification
 * pressure, and recent timeline activity.
 *
 * Row grid: auto kind badge / 1fr label / auto count badge.
 * Zero states: one compact serif italic line per signal when nothing to show.
 * Click targets route to canonical pages (/approvals, /qa, /notifications, /timeline).
 *
 * Topology: about/lay-and-land/frontend.md §Row anatomies
 * Doctrine: about/heart-and-soul/design-language.md §Editorial archetype
 */

import { Link } from "react-router";

import { Section } from "./Section";
import type { OverviewNowRow } from "./model";

interface OperationsNowListProps {
  rows: OverviewNowRow[];
}

const KIND_LABELS: Record<OverviewNowRow["kind"], string> = {
  approval: "approval",
  qa: "qa",
  notification: "notif",
  activity: "activity",
  error: "unavail",
};

export function OperationsNowList({ rows }: OperationsNowListProps) {
  return (
    <Section eyebrow="Now">
      {rows.length === 0 ? (
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
      ) : (
        <div role="list" aria-label="Operations now">
          {rows.map((row, i) => (
            <NowRow key={row.id} row={row} isFirst={i === 0} />
          ))}
        </div>
      )}
    </Section>
  );
}

interface NowRowProps {
  row: OverviewNowRow;
  isFirst: boolean;
}

function NowRow({ row, isFirst }: NowRowProps) {
  const inner = (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr auto",
        alignItems: "center",
        gap: "8px",
        paddingTop: "10px",
        paddingBottom: "10px",
        borderTop: isFirst ? "1px solid var(--border)" : undefined,
        borderBottom: "1px solid var(--border)",
      }}
    >
      {/* Kind badge */}
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "9px",
          color: "var(--muted-foreground)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-sm)",
          padding: "2px 5px",
          lineHeight: 1,
          whiteSpace: "nowrap",
        }}
      >
        {KIND_LABELS[row.kind]}
      </span>

      {/* Label */}
      <span
        style={{
          fontFamily: "var(--font-sans)",
          fontSize: "13px",
          color:
            row.kind === "error"
              ? "var(--muted-foreground)"
              : row.href
                ? "var(--foreground)"
                : "var(--muted-foreground)",
          fontStyle: row.kind === "error" ? "italic" : undefined,
          lineHeight: 1.4,
        }}
      >
        {row.label}
      </span>

      {/* Count badge (only when count is meaningful) */}
      {row.count != null && row.count > 0 ? (
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "11px",
            color: "var(--muted-foreground)",
            lineHeight: 1,
          }}
        >
          {row.count}
        </span>
      ) : (
        <span />
      )}
    </div>
  );

  if (row.href) {
    return (
      <div role="listitem">
        <Link
          to={row.href}
          style={{
            display: "block",
            textDecoration: "none",
            color: "inherit",
          }}
        >
          {inner}
        </Link>
      </div>
    );
  }

  return <div role="listitem">{inner}</div>;
}
