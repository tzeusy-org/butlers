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
  archetype: "overview" | "list" | "detail" | "workspace" | "editor";
  /** Editor archetype only: number of CardSkeleton placeholders (default 2) */
  skeletonSectionCount?: number;

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
  actions,
}: {
  title: string;
  description?: string;
  breadcrumbs?: Breadcrumb[];
  actions?: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      {breadcrumbs && breadcrumbs.length > 0 && (
        <Breadcrumbs items={breadcrumbs} />
      )}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
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

// ---------------------------------------------------------------------------
// Max-width wrapper per archetype
// ---------------------------------------------------------------------------

function ArchetypeWrapper({
  archetype,
  children,
}: {
  archetype: PageProps["archetype"];
  children: React.ReactNode;
}) {
  if (archetype === "detail") {
    return <div className="max-w-5xl">{children}</div>;
  }
  if (archetype === "editor") {
    return <div className="max-w-2xl">{children}</div>;
  }
  // overview, list, workspace: unrestricted
  return <>{children}</>;
}

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

/**
 * Page primitive. Wraps the <main> outlet content for all five page archetypes:
 * overview, list, detail, workspace, and editor.
 *
 * Priority: loading > error > empty > children.
 */
export function Page({
  title,
  description,
  breadcrumbs,
  actions,
  loading = false,
  error,
  empty,
  onRetry,
  archetype,
  skeletonSectionCount = 2,
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
    return (
      <ArchetypeWrapper archetype={archetype}>
        <div className="space-y-6" role="status" aria-label="Loading">
          {/* Render breadcrumbs even while loading so the shell auto-builder
              stays suppressed and navigation context is visible immediately. */}
          {breadcrumbs && breadcrumbs.length > 0 && (
            <Breadcrumbs items={breadcrumbs} />
          )}
          <HeadingBlockSkeleton />
          {archetype === "overview" && <OverviewSkeleton />}
          {archetype === "list" && <ListSkeleton />}
          {archetype === "detail" && <DetailSkeleton />}
          {archetype === "workspace" && <WorkspaceSkeleton />}
          {archetype === "editor" && <EditorSkeleton count={skeletonSectionCount} />}
        </div>
      </ArchetypeWrapper>
    );
  }

  // -- Error state -----------------------------------------------------------
  if (error != null) {
    const message = extractErrorMessage(error);
    return (
      <ArchetypeWrapper archetype={archetype}>
        <div className="space-y-6">
          <HeadingBlock
            title={title}
            description={description}
            breadcrumbs={breadcrumbs}
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
      <ArchetypeWrapper archetype={archetype}>
        <div className="space-y-6">
          <HeadingBlock
            title={title}
            description={description}
            breadcrumbs={breadcrumbs}
            actions={actions}
          />
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
  return (
    <ArchetypeWrapper archetype={archetype}>
      <div className="space-y-6">
        <HeadingBlock
          title={title}
          description={description}
          breadcrumbs={breadcrumbs}
          actions={actions}
        />
        {children}
      </div>
    </ArchetypeWrapper>
  );
}
