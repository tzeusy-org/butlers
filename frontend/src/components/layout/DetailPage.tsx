import { type ReactNode } from "react";

import { Page, type Breadcrumb } from "@/components/ui/page";

// ---------------------------------------------------------------------------
// DetailPage
//
// Canonical shell for all detail / drilldown pages. Wraps <Page archetype="detail">
// and enforces the four-tier information-density contract from the
// `about/lay-and-land/detail-page-audit.md`:
//
//   Hero      — record identity: title (H1 via Page), subtitle, type pill, actions
//   Pulse     — at-a-glance metric row (consumer-supplied ReactNode)
//   Primary   — dominant read surface (timeline, transcript, main table)
//   Supporting — 2-column grid of supporting panels (lg+)
//   Auxiliary  — conditional vertical stack; each child hides itself when empty
//   Practical  — collapsed-by-default drawer for settings / secrets (consumer-supplied ReactNode)
//
// <Page archetype="detail"> owns the single visible H1 (record.title). DetailPage
// does NOT render a second heading to avoid duplicate-H1 regressions.
//
// Consumers supply already-resolved ReactNode slots; DetailPage owns only
// chrome, loading/error states, and slot layout.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Record identity
// ---------------------------------------------------------------------------

export interface DetailRecord {
  /** Record's own canonical name — rendered as H1 by the underlying <Page>. */
  title: string;
  /**
   * Optional sub-line beneath the H1. Typically a predicate, type label, or
   * monospace ID. Rendered in muted color by <Page>.
   */
  subtitle?: string;
  /** The record's type string — rendered as a pill immediately below the heading. */
  type?: string;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface DetailPageProps {
  // ----- Record identity (hero) -----
  record: DetailRecord;

  // ----- Chrome (forwarded to <Page>) -----
  breadcrumbs?: Breadcrumb[];
  actions?: ReactNode;

  // ----- Body slots, in render order -----

  /**
   * At-a-glance metric row rendered between the hero and primary content.
   * Pass any ReactNode (e.g. <PulseStrip .../>). Omit or null to suppress.
   */
  pulse?: ReactNode | null;

  /**
   * Required. The dominant read surface — timeline, transcript, primary table.
   * Rendered full-width directly after the pulse strip.
   */
  primary: ReactNode;

  /**
   * Optional. Supporting panels rendered in a 2-column grid on lg+.
   * Each panel is responsible for its own empty handling.
   */
  supporting?: ReactNode | null;

  /**
   * Optional. Auxiliary sections stacked vertically. Each section should
   * return null when its data is empty (the shell does not paper over this).
   */
  auxiliary?: ReactNode | null;

  /**
   * Optional. Collapsed-by-default settings drawer.
   * Pass any ReactNode (e.g. <PracticalDrawer .../>). Omit or null to suppress.
   */
  practical?: ReactNode | null;

  // ----- Async state (forwarded to <Page>) -----
  loading?: boolean;
  error?: unknown | null;
  /** When set, the error region shows a retry button. Forwarded to <Page>. */
  onRetry?: () => void;
}

// ---------------------------------------------------------------------------
// DetailPage component
// ---------------------------------------------------------------------------

/**
 * Canonical detail-page shell. Composes the four-tier density contract:
 * hero (Page H1) → type pill → pulse → primary → supporting grid → auxiliary → practical drawer.
 */
export function DetailPage({
  record,
  breadcrumbs,
  actions,
  pulse,
  primary,
  supporting,
  auxiliary,
  practical,
  loading = false,
  error,
  onRetry,
}: DetailPageProps) {
  return (
    <Page
      title={record.title}
      description={record.subtitle}
      archetype="detail"
      breadcrumbs={breadcrumbs}
      actions={actions}
      loading={loading}
      error={error}
      onRetry={onRetry}
    >
      {/* Type pill — record classification badge beneath the H1 */}
      {record.type && (
        <div>
          <span className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
            {record.type}
          </span>
        </div>
      )}

      {/* Pulse strip — at-a-glance metrics */}
      {pulse != null && pulse}

      {/* Primary — dominant read surface, full width */}
      <div>{primary}</div>

      {/* Supporting grid — 2-column on lg+ */}
      {supporting != null && (
        <div className="grid gap-6 lg:grid-cols-2">{supporting}</div>
      )}

      {/* Auxiliary — conditional vertical stack */}
      {auxiliary != null && <div className="space-y-6">{auxiliary}</div>}

      {/* Practical drawer — collapsed by default */}
      {practical != null && practical}
    </Page>
  );
}
