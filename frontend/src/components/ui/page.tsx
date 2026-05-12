import { useEffect } from "react";

import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { useBreadcrumbsControl } from "@/components/ui/breadcrumbs-control";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { CardSkeleton } from "@/components/skeletons/card-skeleton";
import { StatsSkeleton } from "@/components/skeletons/stats-skeleton";
import { TableSkeleton } from "@/components/skeletons/table-skeleton";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Breadcrumb {
  label: string;
  /** Omit for the current (final) crumb */
  href?: string;
}

export interface PageEmptyStateProps {
  title: string;
  description: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}

export interface PageProps {
  // --- identity ---
  title: string;
  description?: string;
  breadcrumbs?: Breadcrumb[];

  // --- chrome ---
  /** Status pills rendered inline with the H1 on the title row (e.g. maturity badge). */
  status?: React.ReactNode;
  actions?: React.ReactNode;

  // --- async state ---
  loading?: boolean;
  /** Non-null triggers the error region. Message extracted via instanceof Error. */
  error?: unknown | null;
  /** Non-null and !loading renders EmptyState */
  empty?: PageEmptyStateProps | null;
  /** When set, error region shows a retry button */
  onRetry?: () => void;

  // --- layout ---
  archetype: "overview" | "list" | "detail" | "workspace" | "editor" | "editorial" | "status-board";
  /** Editor archetype only: number of CardSkeleton placeholders (default 2) */
  skeletonSectionCount?: number;

  /**
   * Status-board archetype only: header slot rendered above the body grid.
   * Consumers supply their own BoardHeader component here. The slot is rendered
   * without a border by the Page shell; the consumer's component carries its
   * own border-bottom if desired.
   */
  header?: React.ReactNode;
  /**
   * Status-board archetype only: footer slot rendered below the body grid.
   * Consumers supply their own BoardFooter component here.
   */
  footer?: React.ReactNode;

  children: React.ReactNode;
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/** Heading block skeleton shared across all archetypes */
function HeadingBlockSkeleton() {
  return (
    <div className="space-y-2">
      <div className="h-8 w-48 animate-pulse rounded bg-muted" />
      <div className="h-4 w-64 animate-pulse rounded bg-muted" />
    </div>
  );
}

/** Shared heading block rendered when not loading */
function HeadingBlock({
  title,
  description,
  breadcrumbs,
  status,
  actions,
}: {
  title: string;
  description?: string;
  breadcrumbs?: Breadcrumb[];
  status?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      {breadcrumbs && breadcrumbs.length > 0 && (
        <Breadcrumbs items={breadcrumbs} />
      )}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
            {status && <div className="flex items-center gap-2">{status}</div>}
          </div>
          {description && (
            <p className="text-muted-foreground mt-1">{description}</p>
          )}
        </div>
        {actions && <div className="shrink-0">{actions}</div>}
      </div>
    </div>
  );
}

/** Extract a human-readable error message from an unknown error */
function extractErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

// ---------------------------------------------------------------------------
// Per-archetype skeleton bodies
// ---------------------------------------------------------------------------

function OverviewSkeleton() {
  return (
    <>
      <StatsSkeleton count={4} />
      <CardSkeleton />
      <CardSkeleton />
    </>
  );
}

/** Default columns for the list skeleton table */
const LIST_SKELETON_COLUMNS = [
  { width: "w-32" },
  { width: "w-48" },
  { width: "w-24" },
  { width: "w-20" },
  { width: "w-16", alignRight: true as const },
];

function ListSkeleton() {
  return (
    <Card>
      <CardContent className="pt-6">
        <TableSkeleton rows={5} columns={LIST_SKELETON_COLUMNS} />
      </CardContent>
    </Card>
  );
}

function DetailSkeleton() {
  return (
    <>
      <CardSkeleton />
      <div className="h-10 w-full animate-pulse rounded bg-muted" />
      <div className="h-48 w-full animate-pulse rounded bg-muted" />
    </>
  );
}

function WorkspaceSkeleton() {
  return <div className="h-96 w-full animate-pulse rounded-lg bg-muted" />;
}

function EditorSkeleton({ count }: { count: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <CardSkeleton key={i} />
      ))}
    </>
  );
}

/**
 * Skeleton for the status-board archetype: a header line, a 4×2 grid of cell
 * placeholders (h-56 each, matching the board cell minimum height), and a footer band.
 */
function StatusBoardSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      {/* Header line */}
      <div className="h-14 w-full animate-pulse rounded bg-muted" />
      {/* 2-column × 4-row cell grid */}
      <div className="grid grid-cols-2 gap-4">
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="h-56 w-full animate-pulse rounded bg-muted" />
        ))}
      </div>
      {/* Footer band */}
      <div className="h-16 w-full animate-pulse rounded bg-muted" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Max-width wrapper per archetype
// ---------------------------------------------------------------------------

function ArchetypeWrapper({
  archetype,
  children,
  header,
  footer,
}: {
  archetype: PageProps["archetype"];
  children: React.ReactNode;
  header?: React.ReactNode;
  footer?: React.ReactNode;
}) {
  if (archetype === "detail") {
    return <div className="max-w-5xl">{children}</div>;
  }
  if (archetype === "editor") {
    return <div className="max-w-2xl">{children}</div>;
  }
  if (archetype === "editorial") {
    // The editorial archetype owns its own layout (two-column grid, max-width
    // 1280px, responsive page padding). The <Page> wrapper does not add
    // space-y-6 here; editorial pages compose the two-column region directly.
    return (
      <div
        className="px-4 py-8 sm:px-8 lg:px-14 lg:py-12"
        style={{
          maxWidth: "1280px",
        }}
      >
        {children}
      </div>
    );
  }
  if (archetype === "status-board") {
    // The status-board archetype owns its chrome: header slot at top (consumer's
    // BoardHeader carries its own border-bottom), body in the middle (flex-1;
    // consumer composes the grid layout themselves), footer slot at bottom.
    return (
      <div className="flex min-h-full flex-col">
        {header && <div>{header}</div>}
        <div className="flex-1">{children}</div>
        {footer && <div>{footer}</div>}
      </div>
    );
  }
  // overview, list, workspace: unrestricted
  return <>{children}</>;
}

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

/**
 * Page primitive. Wraps the <main> outlet content for all page archetypes:
 * overview, list, detail, workspace, editor, editorial, and status-board.
 *
 * Priority: loading > error > empty > children.
 */
export function Page({
  title,
  description,
  breadcrumbs,
  status,
  actions,
  loading = false,
  error,
  empty,
  onRetry,
  archetype,
  skeletonSectionCount = 2,
  header,
  footer,
  children,
}: PageProps) {
  const { setSupplyingBreadcrumbs } = useBreadcrumbsControl();

  // Manage document.title automatically; restore previous title on unmount
  useEffect(() => {
    const previousTitle = document.title;
    document.title = `${title} | Butlers`;
    return () => {
      document.title = previousTitle;
    };
  }, [title]);

  // Tell PageHeader to suppress its URL-segment auto-builder when this page
  // supplies explicit breadcrumbs. Reset on unmount so transitioning to a page
  // that does not supply breadcrumbs restores the auto-builder.
  // Derive a stable boolean to avoid re-running the effect on every render when
  // an inline array is passed.
  const supplyingBreadcrumbs = breadcrumbs != null && breadcrumbs.length > 0;
  useEffect(() => {
    setSupplyingBreadcrumbs(supplyingBreadcrumbs);
    return () => {
      setSupplyingBreadcrumbs(false);
    };
  }, [supplyingBreadcrumbs, setSupplyingBreadcrumbs]);

  // Warn in development when list pages pass empty (should handle inline)
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" && archetype === "list" && empty != null) {
      console.warn(
        "[Page] archetype=\"list\" with a non-null `empty` prop is unsupported. " +
          "List pages must handle empty state inside their <Card> body and pass empty={null} to <Page>.",
      );
    }
  }, [archetype, empty]);

  // -- Loading state ---------------------------------------------------------
  if (loading) {
    // The status-board archetype manages its own heading region (BoardHeader);
    // render StatusBoardSkeleton directly without the standard HeadingBlock skeleton.
    if (archetype === "status-board") {
      // The status-board consumer owns its entire heading region (BoardHeader);
      // breadcrumbs are not rendered here in any state so there is no
      // loading-then-gone flicker. Navigation context lives in the header slot.
      return (
        <ArchetypeWrapper archetype={archetype} header={header} footer={footer}>
          <div role="status" aria-label="Loading">
            <StatusBoardSkeleton />
          </div>
        </ArchetypeWrapper>
      );
    }
    return (
      <ArchetypeWrapper archetype={archetype}>
        <div className="space-y-6" role="status" aria-label="Loading">
          {/* Render breadcrumbs even while loading so the shell auto-builder
              stays suppressed and navigation context is visible immediately. */}
          {breadcrumbs && breadcrumbs.length > 0 && (
            <Breadcrumbs items={breadcrumbs} />
          )}
          {/* The editorial archetype manages its own heading region inside children;
              it does not render the standard HeadingBlock skeleton. */}
          {archetype !== "editorial" && <HeadingBlockSkeleton />}
          {archetype === "overview" && <OverviewSkeleton />}
          {archetype === "list" && <ListSkeleton />}
          {archetype === "detail" && <DetailSkeleton />}
          {archetype === "workspace" && <WorkspaceSkeleton />}
          {archetype === "editor" && <EditorSkeleton count={skeletonSectionCount} />}
          {archetype === "editorial" && <WorkspaceSkeleton />}
        </div>
      </ArchetypeWrapper>
    );
  }

  // -- Error state -----------------------------------------------------------
  if (error != null) {
    const message = extractErrorMessage(error);
    // The editorial and status-board archetypes do not use the standard
    // HeadingBlock for their error region; render a simple error card.
    if (archetype === "editorial" || archetype === "status-board") {
      return (
        <ArchetypeWrapper archetype={archetype} header={header} footer={footer}>
          <Card className="border-destructive" role="alert">
            <CardHeader>
              <p className="font-semibold text-destructive">Something went wrong</p>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-destructive">{message}</p>
              {onRetry && (
                <Button variant="outline" size="sm" onClick={onRetry}>
                  Retry
                </Button>
              )}
            </CardContent>
          </Card>
        </ArchetypeWrapper>
      );
    }
    return (
      <ArchetypeWrapper archetype={archetype}>
        <div className="space-y-6">
          <HeadingBlock
            title={title}
            description={description}
            breadcrumbs={breadcrumbs}
            status={status}
            actions={actions}
          />
          <Card className="border-destructive" role="alert">
            <CardHeader>
              <p className="font-semibold text-destructive">Something went wrong</p>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-destructive">{message}</p>
              {onRetry && (
                <Button variant="outline" size="sm" onClick={onRetry}>
                  Retry
                </Button>
              )}
            </CardContent>
          </Card>
        </div>
      </ArchetypeWrapper>
    );
  }

  // -- Empty state -----------------------------------------------------------
  if (empty != null) {
    return (
      <ArchetypeWrapper archetype={archetype} header={header} footer={footer}>
        <div className="space-y-6">
          {archetype !== "editorial" && archetype !== "status-board" && (
            <HeadingBlock
              title={title}
              description={description}
              breadcrumbs={breadcrumbs}
              status={status}
              actions={actions}
            />
          )}
          <EmptyState
            title={empty.title}
            description={empty.description}
            icon={empty.icon}
            action={empty.action}
          />
        </div>
      </ArchetypeWrapper>
    );
  }

  // -- Children --------------------------------------------------------------
  // For editorial archetype, children own the full layout (no HeadingBlock,
  // no space-y-6 wrapper). The two-column grid is composed directly in children.
  if (archetype === "editorial") {
    return (
      <ArchetypeWrapper archetype={archetype}>
        {children}
      </ArchetypeWrapper>
    );
  }

  // For status-board archetype, children are the body grid. The header and
  // footer slots are rendered by ArchetypeWrapper. No HeadingBlock or h1 is
  // rendered here; the consumer's BoardHeader carries its own title display.
  // An optional breadcrumbs+actions chrome strip renders between the header slot
  // and the body when either prop is provided (e.g. butler detail page).
  if (archetype === "status-board") {
    const hasChromeStrip = (breadcrumbs != null && breadcrumbs.length > 0) || actions != null;
    return (
      <ArchetypeWrapper archetype={archetype} header={header} footer={footer}>
        {hasChromeStrip && (
          <div className="flex min-w-0 items-center justify-between gap-4 px-7 pt-4 pb-2">
            {breadcrumbs && breadcrumbs.length > 0 && (
              <Breadcrumbs items={breadcrumbs} />
            )}
            {actions && <div className="shrink-0">{actions}</div>}
          </div>
        )}
        {children}
      </ArchetypeWrapper>
    );
  }

  return (
    <ArchetypeWrapper archetype={archetype}>
      <div className="space-y-6">
        <HeadingBlock
          title={title}
          description={description}
          breadcrumbs={breadcrumbs}
          status={status}
          actions={actions}
        />
        {children}
      </div>
    </ArchetypeWrapper>
  );
}
