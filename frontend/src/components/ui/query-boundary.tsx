import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// QueryBoundary -- shared three-way async state contract (bu-86c4c.2)
//
// The block-level counterpart to <Page>'s page-level loading/error/empty
// priority (components/ui/page.tsx). Use QueryBoundary for a list, table, or
// section nested inside an already-rendered page -- anywhere a component
// used to write `!isLoading && rows.length === 0 ? <Empty /> : ...` without
// checking `isError`.
//
// THE RULE THIS ENFORCES: a query that has ERRORED must never fall through to
// the empty branch. That silent conflation is the dominant "truth amnesty"
// defect from the 2026-07-03 JARVIS audit (move 1b) -- a killed backend must
// never render as a calm "nothing here" / "$0.00" / "No X yet".
//
// Priority: loading > error > empty > children.
// ---------------------------------------------------------------------------

export interface QueryBoundaryProps {
  isLoading: boolean;
  isError: boolean;
  /** The raw error value (e.g. react-query's `error`). Used to derive the default message. */
  error?: unknown;
  isEmpty: boolean;
  /** When provided, the error state renders a Retry button that calls this. */
  onRetry?: () => void;
  loadingFallback: ReactNode;
  emptyFallback: ReactNode;
  /** Override the derived error message. */
  errorMessage?: string;
  /** Optional label naming what failed to load, e.g. "measurements". Used in the default message. */
  sourceLabel?: string;
  className?: string;
  children: ReactNode;
}

function extractErrorMessage(error: unknown): string | null {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error) return error;
  // FastAPI-style error payloads sometimes surface as a plain object rather
  // than an Error instance. Check the common message-bearing shapes before
  // giving up on a detail message.
  if (error && typeof error === "object") {
    const record = error as Record<string, unknown>;
    for (const key of ["message", "detail", "error"]) {
      const value = record[key];
      if (typeof value === "string" && value) return value;
    }
  }
  return null;
}

/**
 * Renders one of loading / error-with-retry / empty / children, in that
 * priority order, from the three booleans every data hook already exposes
 * (`isLoading`, `isError`, and a caller-computed `isEmpty`).
 *
 * The error branch always carries `role="alert"` so degraded sources
 * announce themselves to assistive tech instead of blending into the page.
 */
export function QueryBoundary({
  isLoading,
  isError,
  error,
  isEmpty,
  onRetry,
  loadingFallback,
  emptyFallback,
  errorMessage,
  sourceLabel,
  className,
  children,
}: QueryBoundaryProps) {
  if (isLoading) return <>{loadingFallback}</>;

  if (isError) {
    const detail = errorMessage ?? extractErrorMessage(error);
    const subject = sourceLabel ? `Couldn't reach ${sourceLabel}` : "Couldn't load this data";
    return (
      <div
        role="alert"
        className={cn("flex flex-col items-start gap-2 py-8", className)}
      >
        <p className="font-serif text-sm italic text-destructive">
          {subject}. Retry{detail ? `. ${detail}` : "."}
        </p>
        {onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            Retry
          </Button>
        )}
      </div>
    );
  }

  if (isEmpty) return <>{emptyFallback}</>;

  return <>{children}</>;
}

// ---------------------------------------------------------------------------
// SourceDegradedNote -- per-source degraded indicator (bu-86c4c.2)
//
// For pages that compose several sources where only one has failed and the
// page as a whole still has useful content to show (so a full QueryBoundary
// swap-out would be too heavy-handed) -- e.g. a KPI total whose upstream
// source errored, a topology map missing its connector layer, a status-board
// footer stat. Mirrors the backend's existing `aggregates_available`
// degraded-envelope convention (see butlers/CLAUDE.md API Conventions):
// never suppress the missing source, name it inline instead.
// ---------------------------------------------------------------------------

export interface SourceDegradedNoteProps {
  /** What is degraded, e.g. "Connectors" or "Spend today". */
  label: string;
  /** Short explanation, e.g. "unavailable" or "data source unreachable". */
  detail?: string;
  onRetry?: () => void;
  /**
   * Text on the `onRetry` action button. Defaults to "Retry" (the common
   * case: a query failed, try it again). Override for a note whose action
   * isn't a retry of the same read — e.g. "Sync now" for a freshness plaque
   * whose data loaded fine but is stale, where the action triggers a sync
   * mutation rather than re-running the failed query.
   */
  retryLabel?: string;
  className?: string;
  /** Optional test id so a page-level test can assert the degraded (not empty) state renders. */
  testId?: string;
}

export function SourceDegradedNote({
  label,
  detail = "unavailable",
  onRetry,
  retryLabel = "Retry",
  className,
  testId,
}: SourceDegradedNoteProps) {
  return (
    <div
      role="alert"
      data-testid={testId}
      className={cn(
        "flex items-center gap-2 rounded-sm border border-[var(--amber)]/40 bg-[var(--amber)]/10 px-3 py-2 text-xs text-[var(--amber-text)]",
        className,
      )}
    >
      <span
        className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--amber)]"
        aria-hidden="true"
      />
      <span className="font-medium">
        {label}: {detail}
      </span>
      {onRetry && (
        <Button
          variant="link"
          size="sm"
          className="ml-auto h-auto p-0 text-xs text-[var(--amber-text)]"
          onClick={onRetry}
        >
          {retryLabel}
        </Button>
      )}
    </div>
  );
}
