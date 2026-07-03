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

import type { CSSProperties } from "react";
import { Link } from "react-router";
import { RowLink } from "@/components/ui/RowLink";

export interface AttentionListItem {
  id: string;
  severity: string;
  title: string;
  detail?: string | null;
  href?: string | null;
  /**
   * True when this row represents a failed upstream data source rather than
   * a real operational signal. The title+detail column gets role="alert" so
   * a degraded source announces itself to assistive tech -- the outer row
   * keeps role="listitem" so the ARIA list contract (every direct child of
   * role="list" is a listitem) stays intact.
   */
  isSourceError?: boolean;
  /**
   * Retry callback for a source-error row. Rendered as a "Retry" button in
   * the action column when `href` is absent -- a row whose copy says "retry"
   * must offer an actual retry control, not just prose (bu-86c4c.2).
   */
  onRetry?: () => void;
  /**
   * Inline verb-labeled decision buttons for an individually-actionable
   * approval row (bu-86c4c.14 -- Act loop / hot queue). All three are
   * optional independently so a caller can wire only what it supports; any
   * one present renders the inline action group instead of the arrow/retry
   * column. `*Pending` disables its own button while the mutation is in
   * flight, without borrowing another row's pending state.
   */
  onApprove?: () => void;
  onDeny?: () => void;
  onDefer?: () => void;
  approvePending?: boolean;
  denyPending?: boolean;
  deferPending?: boolean;
}

interface AttentionListProps {
  items: AttentionListItem[];
}

/**
 * Map severity string to a one-character glyph and a color.
 */
/** Shared look for the inline Approve/Deny/Defer verb buttons (bu-86c4c.14). */
const inlineVerbButtonStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "11px",
  color: "var(--muted-foreground)",
  background: "none",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  padding: "2px 8px",
  lineHeight: 1.4,
  cursor: "pointer",
  whiteSpace: "nowrap",
};

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
        // A row with its own inline Approve/Deny/Defer buttons cannot also be
        // a real <a> wrapper -- interactive content nested inside interactive
        // content is invalid HTML/ARIA (bu-86c4c.16's RowLink docs cover the
        // same constraint). Those rows keep the small trailing arrow link;
        // every other href row becomes a full-row RowLink (bu-86c4c.4 --
        // drill-down sweep: the entire row is the target, not a 16px glyph).
        const hasInlineActions = Boolean(item.onApprove || item.onDeny || item.onDefer);
        const rowGridStyle: CSSProperties = {
          display: "grid",
          gridTemplateColumns: "24px 1fr auto",
          alignItems: "start",
          gap: "8px",
          paddingTop: "18px",
          paddingBottom: "18px",
          borderTop: i === 0 ? "1px solid var(--border)" : undefined,
          borderBottom: "1px solid var(--border)",
        };

        const rowContent = (
          <>
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
            <div role={item.isSourceError ? "alert" : undefined}>
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
                {item.title}
              </p>
              {item.detail ? (
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
                  {item.detail}
                </p>
              ) : null}
            </div>

            {/* Action column: inline decision verbs, arrow (visual cue only
                when the whole row is the link target), retry button, or
                spacer */}
            {hasInlineActions ? (
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                {item.onApprove && (
                  <button
                    type="button"
                    onClick={item.onApprove}
                    disabled={item.approvePending}
                    style={inlineVerbButtonStyle}
                  >
                    {item.approvePending ? "Approving…" : "Approve"}
                  </button>
                )}
                {item.onDeny && (
                  <button
                    type="button"
                    onClick={item.onDeny}
                    disabled={item.denyPending}
                    style={inlineVerbButtonStyle}
                  >
                    {item.denyPending ? "Denying…" : "Deny"}
                  </button>
                )}
                {item.onDefer && (
                  <button
                    type="button"
                    onClick={item.onDefer}
                    disabled={item.deferPending}
                    style={inlineVerbButtonStyle}
                  >
                    {item.deferPending ? "Deferring…" : "Defer"}
                  </button>
                )}
                {item.href && (
                  <Link
                    to={item.href}
                    aria-label={`View: ${item.title}`}
                    style={{
                      color: "var(--muted-foreground)",
                      fontSize: "16px",
                      lineHeight: 1,
                      textDecoration: "none",
                    }}
                  >
                    →
                  </Link>
                )}
              </div>
            ) : item.href ? (
              <span
                aria-hidden="true"
                style={{
                  color: "var(--muted-foreground)",
                  fontSize: "16px",
                  lineHeight: 1,
                  paddingTop: "2px",
                }}
              >
                →
              </span>
            ) : item.onRetry ? (
              <button
                type="button"
                onClick={item.onRetry}
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "11px",
                  color: "var(--muted-foreground)",
                  background: "none",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  padding: "2px 8px",
                  lineHeight: 1.4,
                  cursor: "pointer",
                }}
              >
                Retry
              </button>
            ) : (
              <span aria-hidden="true" />
            )}
          </>
        );

        if (item.href && !hasInlineActions) {
          return (
            <RowLink
              key={item.id}
              to={item.href}
              role="listitem"
              aria-label={`View: ${item.title}`}
              style={{ ...rowGridStyle, color: "inherit", textDecoration: "none" }}
            >
              {rowContent}
            </RowLink>
          );
        }

        return (
          <div key={item.id} role="listitem" style={rowGridStyle}>
            {rowContent}
          </div>
        );
      })}
    </div>
  );
}
