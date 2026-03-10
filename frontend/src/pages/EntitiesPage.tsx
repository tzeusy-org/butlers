import { useState } from "react";
import { Link, useNavigate } from "react-router";
import {
  CheckCircleIcon,
  EditIcon,
  GitMergeIcon,
  Loader2Icon,
  TrashIcon,
  UserIcon,
} from "lucide-react";
import { toast } from "sonner";

import type { EntityParams, EntitySummary } from "@/api/types";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useDeleteEntity,
  useEntities,
  useMergeEntity,
  usePromoteEntity,
} from "@/hooks/use-memory";

const PAGE_SIZE = 50;

const ENTITY_TYPES = ["", "person", "organization", "place", "other"] as const;
const TYPE_LABELS: Record<string, string> = {
  "": "All Types",
  person: "Person",
  organization: "Organization",
  place: "Place",
  other: "Other",
};

function entityTypeBadgeVariant(
  entityType: string,
): "default" | "secondary" | "outline" {
  switch (entityType) {
    case "person":
      return "default";
    case "organization":
      return "secondary";
    default:
      return "outline";
  }
}

// ---------------------------------------------------------------------------
// Entity merge dialog
// ---------------------------------------------------------------------------

function EntityMergeDialog({
  sourceEntity,
  open,
  onOpenChange,
}: {
  sourceEntity: EntitySummary;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [search, setSearch] = useState("");
  const [selectedEntity, setSelectedEntity] = useState<EntitySummary | null>(null);
  const mergeMutation = useMergeEntity();

  const { data: searchResults, isLoading: isSearching } = useEntities(
    search.length >= 2 ? { q: search, limit: 10 } : { limit: 10 },
  );

  const candidates = (searchResults?.data ?? []).filter(
    (e) => e.id !== sourceEntity.id && !e.unidentified,
  );

  function handleConfirmMerge() {
    if (!selectedEntity) return;
    mergeMutation.mutate(
      {
        targetEntityId: selectedEntity.id,
        sourceEntityId: sourceEntity.id,
      },
      {
        onSuccess: (data) => {
          toast.success(
            `Merged into ${selectedEntity.canonical_name} (${data.facts_repointed} facts re-pointed)`,
          );
          onOpenChange(false);
          setSearch("");
          setSelectedEntity(null);
        },
        onError: (err) => {
          toast.error(`Merge failed: ${err instanceof Error ? err.message : "Unknown error"}`);
        },
      },
    );
  }

  function handleClose() {
    onOpenChange(false);
    setSearch("");
    setSelectedEntity(null);
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Merge Entity</DialogTitle>
          <DialogDescription>
            Merge <strong>{sourceEntity.canonical_name}</strong> into an existing entity. All facts
            will be re-pointed to the target and the source entity will be removed.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <label className="text-sm font-medium">Search for target entity</label>
            <Input
              className="mt-1"
              placeholder="Search by name or ID..."
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setSelectedEntity(null);
              }}
            />
          </div>

          {isSearching && <Skeleton className="h-20 w-full" />}

          {!isSearching && candidates.length > 0 && (
            <div className="border rounded-md divide-y max-h-48 overflow-y-auto">
              {candidates.map((e) => (
                <button
                  key={e.id}
                  type="button"
                  className={`w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors ${
                    selectedEntity?.id === e.id ? "bg-muted font-medium" : ""
                  }`}
                  onClick={() => setSelectedEntity(e)}
                >
                  <span className="font-medium">{e.canonical_name}</span>
                  <span className="text-muted-foreground ml-2 text-xs">
                    {e.entity_type} &middot; {e.fact_count} facts
                  </span>
                  <span className="text-muted-foreground ml-2 font-mono text-xs">
                    {e.id.slice(0, 8)}
                  </span>
                </button>
              ))}
            </div>
          )}

          {!isSearching && search.length >= 2 && candidates.length === 0 && (
            <p className="text-sm text-muted-foreground">No entities found.</p>
          )}

          {selectedEntity && (
            <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm dark:border-blue-800 dark:bg-blue-950">
              <span className="font-medium">Merge into: </span>
              {selectedEntity.canonical_name}
              <span className="text-muted-foreground ml-1">
                ({selectedEntity.entity_type}, {selectedEntity.fact_count} facts)
              </span>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            Cancel
          </Button>
          <Button
            onClick={handleConfirmMerge}
            disabled={!selectedEntity || mergeMutation.isPending}
          >
            {mergeMutation.isPending ? "Merging..." : "Merge"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Unidentified entities section
// ---------------------------------------------------------------------------

function UnidentifiedEntitiesSection({
  entities,
}: {
  entities: EntitySummary[];
}) {
  const navigate = useNavigate();
  const deleteMutation = useDeleteEntity();
  const [mergeTarget, setMergeTarget] = useState<EntitySummary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<EntitySummary | null>(null);

  if (entities.length === 0) return null;

  return (
    <>
      <Card className="border-orange-200 dark:border-orange-800">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Unidentified Entities
            <Badge
              style={{ backgroundColor: "#ea580c", color: "#fff" }}
              className="text-xs"
            >
              {entities.length}
            </Badge>
          </CardTitle>
          <CardDescription>
            Auto-created entities pending identification. Click into details to add names, or merge
            into an existing entity.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <TooltipProvider>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 pr-4 font-medium">Name</th>
                    <th className="pb-2 pr-4 font-medium text-right">Facts</th>
                    <th className="pb-2 pr-4 font-medium">Source Butler</th>
                    <th className="pb-2 pr-4 font-medium">Source Scope</th>
                    <th className="pb-2 pr-4 font-medium">Created</th>
                    <th className="pb-2 font-medium w-[100px]">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {entities.map((entity) => (
                    <tr key={entity.id} className="border-b last:border-0 hover:bg-muted/50">
                      <td className="py-2 pr-4">
                        <Link
                          to={`/entities/${entity.id}`}
                          className="font-medium text-primary hover:underline"
                        >
                          {entity.canonical_name}
                        </Link>
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums">{entity.fact_count}</td>
                      <td className="py-2 pr-4 text-muted-foreground font-mono text-xs">
                        {entity.source_butler ?? <span className="italic">—</span>}
                      </td>
                      <td className="py-2 pr-4 text-muted-foreground font-mono text-xs">
                        {entity.source_scope ?? <span className="italic">—</span>}
                      </td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {new Date(entity.created_at).toISOString().slice(0, 10)}
                      </td>
                      <td className="py-2">
                        <div className="flex items-center gap-1">
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon-xs"
                                onClick={() => navigate(`/entities/${entity.id}`)}
                              >
                                <EditIcon />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Edit</TooltipContent>
                          </Tooltip>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon-xs"
                                onClick={() => setMergeTarget(entity)}
                              >
                                <GitMergeIcon />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Merge</TooltipContent>
                          </Tooltip>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon-xs"
                                onClick={() => setDeleteTarget(entity)}
                              >
                                <TrashIcon />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Delete</TooltipContent>
                          </Tooltip>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </TooltipProvider>
        </CardContent>
      </Card>

      {mergeTarget && (
        <EntityMergeDialog
          sourceEntity={mergeTarget}
          open={mergeTarget !== null}
          onOpenChange={(open) => {
            if (!open) setMergeTarget(null);
          }}
        />
      )}

      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete entity?</AlertDialogTitle>
            <AlertDialogDescription>
              This will soft-delete <strong>{deleteTarget?.canonical_name}</strong> and
              unlink any associated contacts. The entity will be hidden from all
              views but can be recovered from the database. Entities with active
              facts cannot be deleted — retire or reassign those facts first.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={async () => {
                if (!deleteTarget) return;
                try {
                  await deleteMutation.mutateAsync(deleteTarget.id);
                  toast.success(`Deleted ${deleteTarget.canonical_name}`);
                } catch (err) {
                  toast.error(
                    `Delete failed: ${err instanceof Error ? err.message : "Unknown error"}`,
                  );
                }
                setDeleteTarget(null);
              }}
            >
              {deleteMutation.isPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function EntitiesPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [page, setPage] = useState(0);
  const [mergeTarget, setMergeTarget] = useState<EntitySummary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<EntitySummary | null>(null);
  const deleteMutation = useDeleteEntity();

  const params: EntityParams = {
    q: search || undefined,
    entity_type: typeFilter || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading } = useEntities(params);
  const entities = data?.data ?? [];
  const total = data?.meta?.total ?? 0;
  const hasMore =
    entities.length === PAGE_SIZE && (page + 1) * PAGE_SIZE < total;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  // Split out unidentified entities for the disambiguation section
  const unidentifiedEntities = entities.filter((e) => e.unidentified);

  function handleSearchChange(value: string) {
    setSearch(value);
    setPage(0);
  }

  function handleTypeChange(value: string) {
    setTypeFilter(value);
    setPage(0);
  }

  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Entities</h1>
        <p className="text-muted-foreground mt-1">
          Browse the knowledge graph — people, organizations, places, and more.
        </p>
      </div>

      {/* Unidentified entities needing disambiguation */}
      <UnidentifiedEntitiesSection entities={unidentifiedEntities} />

      {/* Entity table */}
      <Card>
        <CardHeader>
          <CardTitle>All Entities</CardTitle>
          <CardDescription>
            {total > 0
              ? `${total.toLocaleString()} entit${total !== 1 ? "ies" : "y"}`
              : ""}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {/* Filters */}
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <Input
              placeholder="Search by name or ID..."
              value={search}
              onChange={(e) => handleSearchChange(e.target.value)}
              className="max-w-sm"
            />
            <select
              value={typeFilter}
              onChange={(e) => handleTypeChange(e.target.value)}
              className="rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              {ENTITY_TYPES.map((t) => (
                <option key={t} value={t}>
                  {TYPE_LABELS[t]}
                </option>
              ))}
            </select>
          </div>

          {/* Table */}
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : entities.length === 0 ? (
            <p className="text-muted-foreground py-8 text-center text-sm">
              No entities found.
            </p>
          ) : (
            <TooltipProvider>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-2 pr-4 font-medium">Name</th>
                      <th className="pb-2 pr-4 font-medium">Type</th>
                      <th className="pb-2 pr-4 font-medium text-right">Facts</th>
                      <th className="pb-2 pr-4 font-medium">Created</th>
                      <th className="pb-2 font-medium w-[120px]">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entities.map((entity) => (
                      <tr
                        key={entity.id}
                        className="border-b last:border-0 hover:bg-muted/50"
                      >
                        <td className="py-2 pr-4">
                          <span className="inline-flex items-center gap-2">
                            <Link
                              to={`/entities/${entity.id}`}
                              className="font-medium text-primary hover:underline"
                            >
                              {entity.canonical_name}
                            </Link>
                            {entity.roles?.includes("owner") && (
                              <Badge
                                style={{ backgroundColor: "#7c3aed", color: "#fff" }}
                                className="text-xs"
                              >
                                Owner
                              </Badge>
                            )}
                            {entity.unidentified && (
                              <Badge
                                style={{ backgroundColor: "#ea580c", color: "#fff" }}
                                className="text-xs"
                              >
                                Unidentified
                              </Badge>
                            )}
                          </span>
                        </td>
                        <td className="py-2 pr-4">
                          <Badge variant={entityTypeBadgeVariant(entity.entity_type)}>
                            {entity.entity_type}
                          </Badge>
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums">
                          {entity.fact_count}
                        </td>
                        <td className="py-2 pr-4 text-muted-foreground">
                          {new Date(entity.created_at).toISOString().slice(0, 10)}
                        </td>
                        <td className="py-2">
                          <div className="flex items-center gap-1">
                            <Tooltip>
                              <TooltipTrigger asChild>
                                {entity.linked_contact_id ? (
                                  <Button
                                    variant="ghost"
                                    size="icon-xs"
                                    onClick={() => navigate(`/contacts/${entity.linked_contact_id}`)}
                                  >
                                    <UserIcon />
                                  </Button>
                                ) : (
                                  <Button
                                    variant="ghost"
                                    size="icon-xs"
                                    disabled
                                  >
                                    <UserIcon />
                                  </Button>
                                )}
                              </TooltipTrigger>
                              <TooltipContent>
                                {entity.linked_contact_id ? "View contact" : "No linked contact"}
                              </TooltipContent>
                            </Tooltip>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon-xs"
                                  onClick={() => navigate(`/entities/${entity.id}`)}
                                >
                                  <EditIcon />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>Edit</TooltipContent>
                            </Tooltip>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon-xs"
                                  onClick={() => setMergeTarget(entity)}
                                >
                                  <GitMergeIcon />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>Merge</TooltipContent>
                            </Tooltip>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon-xs"
                                  disabled={entity.roles?.includes("owner")}
                                  onClick={() => setDeleteTarget(entity)}
                                >
                                  <TrashIcon />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>
                                {entity.roles?.includes("owner") ? "Cannot delete owner" : "Delete"}
                              </TooltipContent>
                            </Tooltip>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </TooltipProvider>
          )}
        </CardContent>
      </Card>

      {/* Pagination */}
      {total > 0 && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">
            Showing {rangeStart}&ndash;{rangeEnd} of {total.toLocaleString()}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasMore}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {/* Merge dialog */}
      {mergeTarget && (
        <EntityMergeDialog
          sourceEntity={mergeTarget}
          open={mergeTarget !== null}
          onOpenChange={(open) => {
            if (!open) setMergeTarget(null);
          }}
        />
      )}

      {/* Delete confirmation */}
      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete entity?</AlertDialogTitle>
            <AlertDialogDescription>
              This will soft-delete <strong>{deleteTarget?.canonical_name}</strong> and
              unlink any associated contacts. The entity will be hidden from all
              views but can be recovered from the database. Entities with active
              facts cannot be deleted — retire or reassign those facts first.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={async () => {
                if (!deleteTarget) return;
                try {
                  await deleteMutation.mutateAsync(deleteTarget.id);
                  toast.success(`Deleted ${deleteTarget.canonical_name}`);
                } catch (err) {
                  toast.error(
                    `Delete failed: ${err instanceof Error ? err.message : "Unknown error"}`,
                  );
                }
                setDeleteTarget(null);
              }}
            >
              {deleteMutation.isPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
