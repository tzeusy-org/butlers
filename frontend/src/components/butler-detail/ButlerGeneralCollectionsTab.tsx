// ---------------------------------------------------------------------------
// ButlerGeneralCollectionsTab — bu-iuol4.30
//
// Collections bespoke tab for the General butler detail page.
//
// Layout (4-col panel grid, 3 rows):
//   Row 1: KPI strip (span 4)
//     — total collections, total entities, recently modified collection name,
//       largest collection size
//   Row 2: Collections directory (span 3, paginated table)
//         + Recent items sidebar (span 1, last 5 entities)
//   Row 3: Collection size histogram (span 2, bar chart by size bracket)
//         + Quick actions card (span 2, "Create collection" + search)
//
// Data:
//   - useGeneralStats()    → /api/general/stats  (bu-iuol4.31)
//   - useGeneralCollections() → /api/general/collections
//   - useGeneralEntities()    → /api/general/entities
//
// Quick actions: "Create collection" opens a modal sheet (no navigate — keeps
// the user in context; collect modal for future backend integration).
// ---------------------------------------------------------------------------

import { useCallback, useState } from "react";
import { AlertTriangle, Plus, Search } from "lucide-react";

import type { GeneralCollection, GeneralEntity } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { KpiCell } from "./atoms";
import {
  useGeneralCollections,
  useGeneralEntities,
  useGeneralStats,
} from "@/hooks/use-general";
import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function ErrorLine({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="flex items-center gap-1.5 text-sm text-destructive min-w-0"
      data-testid="error-state-line"
    >
      <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span className="truncate">{children}</span>
    </p>
  );
}

function LoadingRows({ count = 4 }: { count?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="flex items-center gap-2" data-testid="loading-line">
          <Skeleton className="h-3 w-28 rounded" />
          <Skeleton className="h-3 flex-1 rounded" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel 1: KPI strip
// ---------------------------------------------------------------------------

interface KpiStripProps {
  totalCollections: number;
  totalEntities: number;
  lastModifiedCollection: string | null;
  largestCollectionSize: number;
  isLoading: boolean;
  isError: boolean;
}

function CollectionsKpiStrip({
  totalCollections,
  totalEntities,
  lastModifiedCollection,
  largestCollectionSize,
  isLoading,
  isError,
}: KpiStripProps) {
  if (isLoading) {
    return (
      <Card data-testid="kpi-strip">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Collections overview</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
            {Array.from({ length: 4 }, (_, i) => (
              <div key={i} className="space-y-1" data-testid="loading-line">
                <Skeleton className="h-2.5 w-20 rounded" />
                <Skeleton className="h-7 w-12 rounded" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (isError) {
    return (
      <Card data-testid="kpi-strip">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Collections overview</CardTitle>
        </CardHeader>
        <CardContent>
          <ErrorLine>Could not load collections overview.</ErrorLine>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card data-testid="kpi-strip">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Collections overview</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-6" data-testid="kpi-row">
          <div data-testid="kpi-item">
            <KpiCell label="Total collections" value={String(totalCollections)} />
          </div>
          <div data-testid="kpi-item">
            <KpiCell label="Total items" value={String(totalEntities)} />
          </div>
          <div data-testid="kpi-item">
            <KpiCell
              label="Recently modified"
              value={lastModifiedCollection ?? "—"}
              sub={lastModifiedCollection == null ? "no collections yet" : undefined}
            />
          </div>
          <div data-testid="kpi-item">
            <KpiCell label="Largest collection" value={String(largestCollectionSize)} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Panel 2: Collections directory (paginated table)
// ---------------------------------------------------------------------------

const COLLECTIONS_PAGE_SIZE = 10;

interface CollectionsDirectoryProps {
  collections: GeneralCollection[];
  total: number;
  page: number;
  onPageChange: (page: number) => void;
  isLoading: boolean;
  isError: boolean;
}

function CollectionsDirectory({
  collections,
  total,
  page,
  onPageChange,
  isLoading,
  isError,
}: CollectionsDirectoryProps) {
  const totalPages = Math.max(1, Math.ceil(total / COLLECTIONS_PAGE_SIZE));

  return (
    <Card className="lg:col-span-3" data-testid="collections-directory-card">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Collections</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && collections.length === 0 ? (
          <LoadingRows count={5} />
        ) : isError ? (
          <ErrorLine>Could not load collections.</ErrorLine>
        ) : collections.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
            No collections yet.
          </p>
        ) : (
          <>
            <div className="overflow-x-auto" data-testid="collections-table">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-xs text-muted-foreground">
                    <th className="py-1.5 pr-3 text-left font-medium">Name</th>
                    <th className="py-1.5 pr-3 text-left font-medium">Items</th>
                    <th className="py-1.5 text-left font-medium">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {collections.map((col) => (
                    <tr
                      key={col.id}
                      className="hover:bg-muted/50 transition-colors"
                      data-testid="collection-row"
                    >
                      <td className="py-2 pr-3 font-medium truncate max-w-[200px]">
                        {col.name}
                        {col.description ? (
                          <span className="ml-2 text-xs text-muted-foreground font-normal">
                            {col.description}
                          </span>
                        ) : null}
                      </td>
                      <td className="py-2 pr-3 tabular-nums tnum text-sm">
                        {col.entity_count}
                      </td>
                      <td className="py-2 text-xs text-muted-foreground tabular-nums whitespace-nowrap">
                        <Time value={col.created_at} mode="relative" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {total > COLLECTIONS_PAGE_SIZE && (
              <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
                <span>
                  Page {page + 1} of {totalPages}
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page === 0}
                    onClick={() => onPageChange(page - 1)}
                  >
                    Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page + 1 >= totalPages}
                    onClick={() => onPageChange(page + 1)}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Panel 3: Recent items sidebar (last 5 entities)
// ---------------------------------------------------------------------------

interface RecentItemsSidebarProps {
  entities: GeneralEntity[];
  isLoading: boolean;
  isError: boolean;
}

function RecentItemsSidebar({ entities, isLoading, isError }: RecentItemsSidebarProps) {
  return (
    <Card className="lg:col-span-1" data-testid="recent-items-card">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Recent items</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && entities.length === 0 ? (
          <LoadingRows count={3} />
        ) : isError ? (
          <ErrorLine>Could not load recent items.</ErrorLine>
        ) : entities.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
            No items yet.
          </p>
        ) : (
          <ul className="space-y-3" data-testid="recent-items-list">
            {entities.slice(0, 5).map((entity) => (
              <li key={entity.id} className="text-sm" data-testid="recent-item">
                <div className="flex items-start gap-2">
                  {entity.collection_name ? (
                    <Badge variant="outline" className="shrink-0 text-xs">
                      {entity.collection_name}
                    </Badge>
                  ) : null}
                  <span className="text-xs text-muted-foreground tabular-nums tnum whitespace-nowrap">
                    <Time value={entity.created_at} mode="relative" />
                  </span>
                </div>
                {entity.tags.length > 0 ? (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {entity.tags.slice(0, 3).map((tag) => (
                      <Badge key={tag} variant="secondary" className="text-xs">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Panel 4: Collection size histogram (recharts bar chart)
//
// Each bucket from /api/general/stats.size_histogram is rendered as a bar.
// Palette uses graduated hsl(var(--primary)) opacity rather than raw hex.
// ---------------------------------------------------------------------------

// Graduated fill palette for the 4 histogram brackets.
// Uses CSS variables so theme switching works correctly.
const HISTOGRAM_FILLS = [
  "hsl(var(--primary) / 0.35)",
  "hsl(var(--primary) / 0.55)",
  "hsl(var(--primary) / 0.75)",
  "hsl(var(--primary) / 1)",
];

interface HistogramBucket {
  bracket: string;
  count: number;
}

interface SizeHistogramProps {
  buckets: HistogramBucket[];
  isLoading: boolean;
  isError: boolean;
}

function SizeHistogramPanel({ buckets, isLoading, isError }: SizeHistogramProps) {
  return (
    <Card className="lg:col-span-2" data-testid="size-histogram-card">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Collection sizes</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-[120px] w-full rounded" data-testid="loading-line" />
          </div>
        ) : isError ? (
          <ErrorLine>Could not load size histogram.</ErrorLine>
        ) : buckets.every((b) => b.count === 0) ? (
          <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
            No collections to display.
          </p>
        ) : (
          <div data-testid="histogram-chart">
            <ResponsiveContainer width="100%" height={120}>
              <BarChart data={buckets} margin={{ top: 4, right: 4, bottom: 4, left: 0 }}>
                <XAxis
                  dataKey="bracket"
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  allowDecimals={false}
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  width={28}
                />
                <Tooltip
                  content={({ active, payload, label }) => {
                    if (!active || !payload?.length || !label) return null;
                    const count = (payload[0]?.value as number) ?? 0;
                    return (
                      <div className="rounded-md border bg-popover px-3 py-2 text-sm shadow-md">
                        <p className="font-medium">{label} items</p>
                        <p className="text-muted-foreground tabular-nums">
                          {count} collection{count !== 1 ? "s" : ""}
                        </p>
                      </div>
                    );
                  }}
                />
                <Bar dataKey="count" isAnimationActive={false} radius={[2, 2, 0, 0]}>
                  {buckets.map((entry, idx) => (
                    <Cell
                      key={entry.bracket}
                      fill={HISTOGRAM_FILLS[idx] ?? HISTOGRAM_FILLS[HISTOGRAM_FILLS.length - 1]}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            <p className="sr-only">
              Collection size distribution: {buckets.map((b) => `${b.bracket}: ${b.count}`).join(", ")}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Panel 5: Quick actions card
//
// "Create collection" opens a simple Dialog (modal) rather than navigating
// away — keeps the user in context. The modal is a stub; the form data
// is wired for future backend integration (POST /api/general/collections).
// ---------------------------------------------------------------------------

interface QuickActionsProps {
  onSearchChange: (q: string) => void;
  searchValue: string;
}

function QuickActionsCard({ onSearchChange, searchValue }: QuickActionsProps) {
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");

  function resetForm() {
    setNewName("");
    setNewDescription("");
  }

  function handleOpenChange(open: boolean) {
    if (!open) resetForm();
    setCreateOpen(open);
  }

  function handleCreate() {
    // Stub: future backend integration — POST /api/general/collections
    // For now the dialog closes and resets state.
    resetForm();
    setCreateOpen(false);
  }

  return (
    <>
      <Card className="lg:col-span-2" data-testid="quick-actions-card">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Quick actions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button
            size="sm"
            className="w-full"
            onClick={() => setCreateOpen(true)}
            data-testid="create-collection-button"
          >
            <Plus className="mr-2 h-3.5 w-3.5" aria-hidden />
            Create collection
          </Button>
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input
              placeholder="Search collections..."
              className="pl-8 text-sm"
              value={searchValue}
              onChange={(e) => onSearchChange(e.target.value)}
              data-testid="collection-search-input"
            />
          </div>
        </CardContent>
      </Card>

      <Dialog open={createOpen} onOpenChange={handleOpenChange}>
        <DialogContent data-testid="create-collection-dialog">
          <DialogHeader>
            <DialogTitle>Create collection</DialogTitle>
            <DialogDescription>
              Add a new collection to store related items.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 pt-2">
            <div>
              <label htmlFor="collection-name" className="text-sm font-medium">
                Name
              </label>
              <Input
                id="collection-name"
                placeholder="Collection name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                className="mt-1"
              />
            </div>
            <div>
              <label htmlFor="collection-description" className="text-sm font-medium">
                Description
                <span className="ml-1 text-xs text-muted-foreground">(optional)</span>
              </label>
              <Input
                id="collection-description"
                placeholder="What is this collection for?"
                value={newDescription}
                onChange={(e) => setNewDescription(e.target.value)}
                className="mt-1"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleOpenChange(false)}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                disabled={!newName.trim()}
                onClick={handleCreate}
                data-testid="confirm-create-button"
              >
                Create
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// ButlerGeneralCollectionsTab — entry point
// ---------------------------------------------------------------------------

export default function ButlerGeneralCollectionsTab() {
  const [collectionsPage, setCollectionsPage] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");

  const handleSearchChange = useCallback((q: string) => {
    setSearchQuery(q);
    setCollectionsPage(0);
  }, []);

  const {
    data: stats,
    isLoading: statsLoading,
    isError: statsError,
  } = useGeneralStats();

  const {
    data: collectionsResp,
    isLoading: collectionsLoading,
    isError: collectionsError,
  } = useGeneralCollections({
    q: searchQuery || undefined,
    offset: collectionsPage * COLLECTIONS_PAGE_SIZE,
    limit: COLLECTIONS_PAGE_SIZE,
  });

  const {
    data: entitiesResp,
    isLoading: entitiesLoading,
    isError: entitiesError,
  } = useGeneralEntities({ limit: 5 });

  const collections = collectionsResp?.data ?? [];
  const collectionsTotal = collectionsResp?.meta?.total ?? 0;
  const entities = entitiesResp?.data ?? [];
  const sizeHistogram = stats?.size_histogram ?? [];

  const hasError = statsError || collectionsError || entitiesError;

  return (
    <div className="space-y-4 pt-4" data-testid="general-collections-tab">
      {/* Error banner */}
      {hasError && (
        <p className="text-sm text-destructive" data-testid="collections-load-error">
          Some data failed to load. Displayed values may be incomplete.
        </p>
      )}

      {/* Row 1: KPI strip */}
      <CollectionsKpiStrip
        totalCollections={stats?.total_collections ?? 0}
        totalEntities={stats?.total_entities ?? 0}
        lastModifiedCollection={stats?.last_modified_collection ?? null}
        largestCollectionSize={stats?.largest_collection_size ?? 0}
        isLoading={statsLoading}
        isError={statsError}
      />

      {/* Row 2: Collections directory (span 3) + Recent items (span 1) */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <CollectionsDirectory
          collections={collections}
          total={collectionsTotal}
          page={collectionsPage}
          onPageChange={setCollectionsPage}
          isLoading={collectionsLoading}
          isError={collectionsError}
        />
        <RecentItemsSidebar
          entities={entities}
          isLoading={entitiesLoading}
          isError={entitiesError}
        />
      </div>

      {/* Row 3: Size histogram (span 2) + Quick actions (span 2) */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <SizeHistogramPanel
          buckets={sizeHistogram}
          isLoading={statsLoading}
          isError={statsError}
        />
        <QuickActionsCard
          searchValue={searchQuery}
          onSearchChange={handleSearchChange}
        />
      </div>
    </div>
  );
}
