/**
 * EntitiesIndexPage — /entities canonical index for the relationship butler.
 *
 * Replaces the memory butler's EntitiesPage at the /entities route per
 * tasks.md §8.1 (entity-redesign Phase 2).
 *
 * Layout (spec §Requirement: Entity index page):
 *   - SubpageTabs strip (Index active)
 *   - Tabular list in the main column (from 9.1 list+filter API)
 *   - Filter chips: type, state (unidentified / duplicate-candidate / stale),
 *     has=contact
 *   - Curation queue right rail (from 9.5 queue endpoint)
 *
 * Renders inside <Page archetype="overview"> with breadcrumb "Entities".
 *
 * Spec: openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md
 *       §Requirement: Entity index page
 */

import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import {
  ArchiveIcon,
  CheckCircleIcon,
  GitMergeIcon,
  Loader2Icon,
  TrashIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import type {
  RelationshipEntitySummary,
  RelationshipEntityListParams,
  RelationshipQueueEntry,
} from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { EntityMark } from "@/components/ui/EntityMark";
import { Input } from "@/components/ui/input";
import { Page } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import {
  useArchiveRelationshipEntity,
  useDismissRelationshipEntityQueueItem,
  useEntityFinderSearch,
  useForgetRelationshipEntity,
  usePromoteRelationshipEntity,
  useRelationshipEntities,
  useRelationshipEntityQueue,
} from "@/hooks/use-entities";
import { MergeCompareDialog } from "@/components/relationship/MergeCompareDialog";
import { ENTITY_BADGE_TEXT } from "@/lib/entity-model";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

const ENTITY_TYPES = [
  "person",
  "organization",
  "location",
  "product",
  "group",
  "other",
] as const;
type EntityType = (typeof ENTITY_TYPES)[number];
const DEFAULT_ENTITY_TYPES: EntityType[] = ["person", "organization"];
const EMPTY_TYPE_SENTINEL = "__none__";

const TYPE_LABELS: Record<EntityType, string> = {
  person: "Person",
  organization: "Org",
  location: "Location",
  product: "Product",
  group: "Group",
  other: "Other",
};

const STATE_CHIPS = [
  { value: "unidentified", label: "Unidentified" },
  { value: "duplicate-candidate", label: "Duplicate" },
  { value: "stale", label: "Stale" },
] as const;
type EntityState = (typeof STATE_CHIPS)[number]["value"];

type ActionEntity = {
  id: string;
  canonical_name: string;
  entity_type: string;
  roles?: string[];
};

/** A pair of entity ids handed to the compare view (the merge-review surface). */
type MergePair = { entityA: string; entityB: string };

function isOwner(entity: ActionEntity) {
  return entity.roles?.includes("owner") ?? false;
}

/**
 * Read the duplicate-candidate peer entity id from a queue entry's evidence.
 *
 * The shared-fact duplicate evidence carries ``peer_entity_ids`` (the entities
 * holding the same identifier). When present, the merge action can open the
 * compare view for that pair directly without a target search.
 */
function peerEntityIdFromEvidence(evidence: Record<string, unknown>): string | null {
  const peers = evidence?.["peer_entity_ids"];
  if (Array.isArray(peers) && peers.length > 0 && typeof peers[0] === "string") {
    return peers[0];
  }
  return null;
}

// ---------------------------------------------------------------------------
// Relationship entity actions
// ---------------------------------------------------------------------------

function PromoteEntityButton({ entity }: { entity: ActionEntity }) {
  const promoteMutation = usePromoteRelationshipEntity();

  async function handlePromote() {
    try {
      await promoteMutation.mutateAsync({
        entityId: entity.id,
        canonicalName: entity.canonical_name,
        entityType: entity.entity_type,
      });
      toast.success(`Promoted ${entity.canonical_name}`);
    } catch (err) {
      toast.error(`Promote failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      aria-label={`Promote ${entity.canonical_name}`}
      title="Promote"
      disabled={promoteMutation.isPending}
      onClick={handlePromote}
    >
      {promoteMutation.isPending ? (
        <Loader2Icon className="animate-spin" />
      ) : (
        <CheckCircleIcon />
      )}
    </Button>
  );
}

function ArchiveEntityButton({ entity }: { entity: ActionEntity }) {
  const archiveMutation = useArchiveRelationshipEntity();

  async function handleArchive() {
    try {
      await archiveMutation.mutateAsync(entity.id);
      toast.success(`Archived ${entity.canonical_name}`);
    } catch (err) {
      toast.error(`Archive failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      aria-label={`Archive ${entity.canonical_name}`}
      title={isOwner(entity) ? "Cannot archive owner" : "Archive"}
      disabled={isOwner(entity) || archiveMutation.isPending}
      onClick={handleArchive}
    >
      {archiveMutation.isPending ? (
        <Loader2Icon className="animate-spin" />
      ) : (
        <ArchiveIcon />
      )}
    </Button>
  );
}

function ForgetEntityDialog({
  entity,
  onOpenChange,
}: {
  entity: ActionEntity | null;
  onOpenChange: (open: boolean) => void;
}) {
  const forgetMutation = useForgetRelationshipEntity();

  function handleClose(open: boolean) {
    onOpenChange(open);
  }

  async function handleForget() {
    if (!entity) return;
    try {
      await forgetMutation.mutateAsync(entity.id);
      toast.success(`Deleted ${entity.canonical_name}`);
      handleClose(false);
    } catch (err) {
      toast.error(`Forget failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  }

  return (
    <Dialog open={entity !== null} onOpenChange={handleClose}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Delete entity</DialogTitle>
          <DialogDescription>
            Delete {entity?.canonical_name}? This tombstones the entity and retracts active
            relationship facts. This action cannot be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => handleClose(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={forgetMutation.isPending}
            onClick={handleForget}
          >
            {forgetMutation.isPending ? "Deleting..." : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ForgetEntityButton({
  entity,
  onSelect,
}: {
  entity: ActionEntity;
  onSelect: (entity: ActionEntity) => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      aria-label={`Delete ${entity.canonical_name}`}
      title={isOwner(entity) ? "Cannot delete owner" : "Delete"}
      disabled={isOwner(entity)}
      onClick={() => onSelect(entity)}
    >
      <TrashIcon />
    </Button>
  );
}

function DismissQueueItemButton({ entity }: { entity: ActionEntity }) {
  const dismissMutation = useDismissRelationshipEntityQueueItem();

  async function handleDismiss() {
    try {
      await dismissMutation.mutateAsync(entity.id);
      toast.success(`Dismissed ${entity.canonical_name}`);
    } catch (err) {
      toast.error(`Dismiss failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      aria-label={`Dismiss ${entity.canonical_name}`}
      title="Dismiss"
      disabled={dismissMutation.isPending}
      onClick={handleDismiss}
    >
      {dismissMutation.isPending ? (
        <Loader2Icon className="animate-spin" />
      ) : (
        <XIcon />
      )}
    </Button>
  );
}

function MergeEntityButton({
  entity,
  onSelect,
}: {
  entity: ActionEntity;
  onSelect: (entity: ActionEntity) => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      aria-label={`Merge ${entity.canonical_name}`}
      title="Merge"
      onClick={() => onSelect(entity)}
    >
      <GitMergeIcon />
    </Button>
  );
}

/**
 * Pick a merge target for a source entity, then open the compare view.
 *
 * Used when no duplicate peer is known up-front — the unidentified-card merge
 * action and the table-row merge action (spec: the unidentified queue card
 * "opens the compare view for the unidentified entity and an owner-selected
 * target entity"). The owner searches for a target; confirming hands the pair to
 * the compare view via {@link onPickPair}. This dialog never merges directly —
 * every merge routes through the compare view first.
 */
function MergeTargetPickerDialog({
  sourceEntity,
  onOpenChange,
  onPickPair,
}: {
  sourceEntity: ActionEntity | null;
  onOpenChange: (open: boolean) => void;
  onPickPair: (pair: MergePair) => void;
}) {
  const [search, setSearch] = useState("");
  const [targetId, setTargetId] = useState<string | null>(null);
  const { data, isFetching } = useEntityFinderSearch(search, { limit: 8 });

  const candidates = (data?.results ?? []).filter(
    (candidate) => candidate.entity_id !== sourceEntity?.id,
  );
  const selectedTarget = candidates.find((candidate) => candidate.entity_id === targetId);

  function handleClose(open: boolean) {
    onOpenChange(open);
    if (!open) {
      setSearch("");
      setTargetId(null);
    }
  }

  function handleContinue() {
    if (!sourceEntity || !selectedTarget) return;
    onPickPair({ entityA: sourceEntity.id, entityB: selectedTarget.entity_id });
    handleClose(false);
  }

  return (
    <Dialog open={sourceEntity !== null} onOpenChange={handleClose}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Merge entity</DialogTitle>
          <DialogDescription>
            Find the entity to compare with {sourceEntity?.canonical_name}. You will review the
            differences before any merge is committed.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Input
            aria-label="Search merge target"
            placeholder="Search target entity"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setTargetId(null);
            }}
          />
          {isFetching && <Skeleton className="h-10 w-full" />}
          {search.trim() !== "" && candidates.length === 0 && !isFetching && (
            <p className="text-sm text-muted-foreground">No matching entity found.</p>
          )}
          {candidates.length > 0 && (
            <div className="max-h-56 overflow-y-auto rounded-md border">
              {candidates.map((candidate) => (
                <button
                  key={candidate.entity_id}
                  type="button"
                  className={`block w-full px-3 py-2 text-left text-sm hover:bg-muted ${
                    targetId === candidate.entity_id ? "bg-muted font-medium" : ""
                  }`}
                  onClick={() => setTargetId(candidate.entity_id)}
                >
                  {candidate.canonical_name}
                  <span className="ml-2 text-xs text-muted-foreground">
                    {candidate.entity_type}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => handleClose(false)}>
            Cancel
          </Button>
          <Button type="button" disabled={!selectedTarget} onClick={handleContinue}>
            Compare
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Filter chips bar
// ---------------------------------------------------------------------------

interface FilterChipsProps {
  typeFilters: EntityType[];
  stateFilter: EntityState | null;
  hasContact: boolean;
  onTypeChange: (type: EntityType) => void;
  onStateChange: (state: EntityState | null) => void;
  onHasContactChange: (v: boolean) => void;
}

function FilterChips({
  typeFilters,
  stateFilter,
  hasContact,
  onTypeChange,
  onStateChange,
  onHasContactChange,
}: FilterChipsProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* Type filter chips */}
      {ENTITY_TYPES.map((t) => (
        <Button
          key={t}
          variant={typeFilters.includes(t) ? "default" : "outline"}
          size="sm"
          onClick={() => onTypeChange(t)}
        >
          {TYPE_LABELS[t]}
        </Button>
      ))}

      {/* Separator */}
      <span className="h-5 w-px bg-border mx-1" aria-hidden="true" />

      {/* has=contact chip */}
      <Button
        variant={hasContact ? "default" : "outline"}
        size="sm"
        onClick={() => onHasContactChange(!hasContact)}
      >
        Has contact
      </Button>

      {/* State chips */}
      {STATE_CHIPS.map(({ value, label }) => (
        <Button
          key={value}
          variant={stateFilter === value ? "default" : "outline"}
          size="sm"
          onClick={() => onStateChange(stateFilter === value ? null : value)}
        >
          {label}
        </Button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Entity table
// ---------------------------------------------------------------------------

const DUNBAR_TIER_LABELS: Record<number, string> = {
  5: "Support Clique",
  15: "Sympathy Group",
  50: "Good Friends",
  150: "Meaningful",
  500: "Acquaintances",
  1500: "Familiar Faces",
};

interface EntityTableProps {
  entities: RelationshipEntitySummary[];
  isLoading: boolean;
  onMergeEntity: (entity: ActionEntity) => void;
  onForgetEntity: (entity: ActionEntity) => void;
  selectedIds: Set<string>;
  onToggleSelect: (id: string) => void;
}

function EntityTable({
  entities,
  isLoading,
  onMergeEntity,
  onForgetEntity,
  selectedIds,
  onToggleSelect,
}: EntityTableProps) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (entities.length === 0) {
    return (
      <EmptyState
        title="No entities found."
        description="Entities appear as the butler builds the knowledge graph."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="entity-table">
        <thead>
          <tr className="border-b text-left text-muted-foreground">
            <th className="pb-2 pr-2 font-medium w-8" aria-label="Select" />
            <th className="pb-2 pr-2 font-medium w-8" aria-label="Type" />
            <th className="pb-2 pr-4 font-medium">Name</th>
            <th className="pb-2 pr-4 font-medium">Tier</th>
            <th className="pb-2 pr-4 font-medium">Last seen</th>
            <th className="pb-2 pr-4 font-medium text-right tabular-nums">Contacts</th>
            <th className="pb-2 font-medium">Aliases</th>
            <th className="pb-2 pl-4 font-medium text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {entities.map((entity) => (
            <tr
              key={entity.id}
              className="border-b last:border-0 hover:bg-muted/50"
            >
              <td className="py-2.5 pr-2">
                <input
                  type="checkbox"
                  aria-label={`Select ${entity.canonical_name}`}
                  checked={selectedIds.has(entity.id)}
                  onChange={() => onToggleSelect(entity.id)}
                />
              </td>
              <td className="py-2.5 pr-2">
                <EntityMark
                  name={entity.canonical_name}
                  entityType={entity.entity_type}
                  isOwner={entity.roles?.includes("owner")}
                  isUnidentified={entity.metadata?.["unidentified"] === "true"}
                />
              </td>
              <td className="py-2.5 pr-4">
                <span className="inline-flex items-center gap-2">
                  <Link
                    to={`/entities/${entity.id}`}
                    className="font-medium text-primary hover:underline"
                  >
                    {entity.canonical_name}
                  </Link>
                  {entity.roles?.includes("owner") && (
                    <Badge
                      style={{ backgroundColor: "var(--role-owner)", color: ENTITY_BADGE_TEXT }}
                      className="text-xs"
                    >
                      Owner
                    </Badge>
                  )}
                  {entity.metadata?.["unidentified"] === "true" && (
                    <Badge
                      style={{
                        backgroundColor: "var(--state-unidentified)",
                        color: ENTITY_BADGE_TEXT,
                      }}
                      className="text-xs"
                    >
                      Unidentified
                    </Badge>
                  )}
                </span>
              </td>
              <td className="py-2.5 pr-4">
                {entity.tier != null ? (
                  <Badge variant="outline" className="text-xs tabular-nums">
                    {entity.tier} — {DUNBAR_TIER_LABELS[entity.tier] ?? `Tier ${entity.tier}`}
                  </Badge>
                ) : (
                  <span className="text-muted-foreground text-xs">—</span>
                )}
              </td>
              <td className="py-2.5 pr-4 text-muted-foreground">
                {entity.last_seen ? (
                  <Time value={entity.last_seen} mode="relative" />
                ) : (
                  <span className="text-xs text-muted-foreground">—</span>
                )}
              </td>
              <td className="py-2.5 pr-4 text-right tabular-nums text-muted-foreground">
                {entity.contact_fact_count}
              </td>
              <td className="py-2.5 text-muted-foreground text-xs">
                {entity.aliases.length > 0 ? entity.aliases.join(", ") : "—"}
              </td>
              <td className="py-2.5 pl-4">
                <div className="flex justify-end gap-1">
                  {entity.metadata?.["unidentified"] === "true" && (
                    <PromoteEntityButton entity={entity} />
                  )}
                  <MergeEntityButton entity={entity} onSelect={onMergeEntity} />
                  <ArchiveEntityButton entity={entity} />
                  <ForgetEntityButton entity={entity} onSelect={onForgetEntity} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Curation queue right rail (9.5)
// ---------------------------------------------------------------------------

function QueueRail({
  onMergeEntity,
  onComparePair,
}: {
  onMergeEntity: (entity: ActionEntity) => void;
  onComparePair: (pair: MergePair) => void;
}) {
  const { data, isLoading, isError, error } = useRelationshipEntityQueue({ limit: 20 });

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-5 w-full" />
        <Skeleton className="h-5 w-3/4" />
        <Skeleton className="h-5 w-5/6" />
      </div>
    );
  }

  if (isError) {
    const message = error instanceof Error ? error.message : "Failed to load queue.";
    return (
      <p className="text-xs text-destructive" role="alert">
        {message}
      </p>
    );
  }

  const items = data?.items ?? [];

  if (items.length === 0) {
    return (
      <p
        className="text-sm italic text-muted-foreground"
        style={{ fontFamily: "'Source Serif 4', Georgia, serif" }}
        data-testid="queue-rail-empty"
      >
        Nothing waiting.
      </p>
    );
  }

  // Group by bucket
  const unidentified = items.filter((e) => e.bucket === "unidentified");
  const duplicates = items.filter((e) => e.bucket === "duplicate-candidate");
  const stale = items.filter((e) => e.bucket === "stale");

  return (
    <div className="space-y-4" data-testid="queue-rail">
      {unidentified.length > 0 && (
        <QueueSection
          title="Unidentified"
          items={unidentified}
          accentColor="var(--amber)"
          onMergeEntity={onMergeEntity}
          onComparePair={onComparePair}
        />
      )}
      {duplicates.length > 0 && (
        <QueueSection
          title="Duplicate candidate"
          items={duplicates}
          accentColor="var(--amber)"
          onMergeEntity={onMergeEntity}
          onComparePair={onComparePair}
        />
      )}
      {stale.length > 0 && (
        <QueueSection
          title="Stale"
          items={stale}
          accentColor="var(--muted-foreground)"
          onMergeEntity={onMergeEntity}
          onComparePair={onComparePair}
        />
      )}
    </div>
  );
}

function QueueSection({
  title,
  items,
  accentColor,
  onMergeEntity,
  onComparePair,
}: {
  title: string;
  items: RelationshipQueueEntry[];
  accentColor: string;
  onMergeEntity: (entity: ActionEntity) => void;
  onComparePair: (pair: MergePair) => void;
}) {
  return (
    <div>
      <p
        className="text-xs font-semibold uppercase tracking-wide mb-2"
        style={{ color: accentColor }}
      >
        {title}
      </p>
      <ul className="space-y-1">
        {items.map((entry) => {
          const actionEntity: ActionEntity = {
            id: entry.entity_id,
            canonical_name: entry.canonical_name,
            entity_type: entry.entity_type,
          };

          // Duplicate-candidate entries carry the peer entity in their evidence;
          // their merge action opens the compare view for that pair directly.
          // Unidentified entries have no known peer — route through the target
          // picker so the owner selects the entity to compare against.
          const peerId =
            entry.bucket === "duplicate-candidate"
              ? peerEntityIdFromEvidence(entry.evidence)
              : null;

          function handleMerge(selected: ActionEntity) {
            if (peerId) {
              onComparePair({ entityA: selected.id, entityB: peerId });
            } else {
              onMergeEntity(selected);
            }
          }

          return (
            <li
              key={entry.entity_id}
              className="flex items-center justify-between gap-2 text-sm"
            >
              <Link
                to={`/entities/${entry.entity_id}`}
                className="min-w-0 truncate text-primary hover:underline"
              >
                {entry.canonical_name}
              </Link>
              <div className="flex shrink-0 gap-1">
                {entry.bucket === "unidentified" && (
                  <PromoteEntityButton entity={actionEntity} />
                )}
                {entry.bucket !== "stale" && (
                  <MergeEntityButton entity={actionEntity} onSelect={handleMerge} />
                )}
                {entry.bucket === "stale" && <ArchiveEntityButton entity={actionEntity} />}
                <DismissQueueItemButton entity={actionEntity} />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export function EntitiesIndexPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [offset, setOffset] = useState(0);
  const [mergeSourceEntity, setMergeSourceEntity] = useState<ActionEntity | null>(null);
  const [forgetSourceEntity, setForgetSourceEntity] = useState<ActionEntity | null>(null);
  const [comparePair, setComparePair] = useState<MergePair | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  function toggleSelect(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const selectedList = Array.from(selectedIds);

  // The bulk gutter's merge action is enabled only when EXACTLY two rows are
  // selected (spec: "enabled only when exactly two rows are selected"). It opens
  // the compare view for that pair before any merge can be committed.
  function handleGutterMerge() {
    if (selectedList.length !== 2) return;
    setComparePair({ entityA: selectedList[0], entityB: selectedList[1] });
  }

  // URL is the source of truth for all filter chips.
  // Repeated ?type=person&type=organization activates multiple type chips.
  // Absence defaults to people + organizations for the base index.
  // ?state=unidentified activates the Unidentified chip; etc.
  // ?has=contact activates the Has contact chip; any other value or absence deactivates it.
  const rawTypes = searchParams.getAll("type");
  const typeFilters: EntityType[] =
    rawTypes.length === 0
      ? DEFAULT_ENTITY_TYPES
      : ENTITY_TYPES.filter((type) => rawTypes.includes(type));

  const rawState = searchParams.get("state");
  const stateFilter: EntityState | null =
    rawState !== null && STATE_CHIPS.some((c) => c.value === rawState)
      ? (rawState as EntityState)
      : null;

  const hasContact = searchParams.get("has") === "contact";

  const params: RelationshipEntityListParams = {
    entity_type: typeFilters,
    state: stateFilter ?? undefined,
    has: hasContact ? "contact" : undefined,
    limit: PAGE_SIZE,
    offset,
  };

  const { data, isLoading, error } = useRelationshipEntities(params);
  const entities = data?.items ?? [];
  const total = data?.total ?? 0;

  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + PAGE_SIZE, total);
  const hasMore = offset + PAGE_SIZE < total;
  const hasPrev = offset > 0;

  function handleTypeChange(type: EntityType) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        const currentRawTypes = next.getAll("type");
        const currentTypes =
          currentRawTypes.length === 0
            ? DEFAULT_ENTITY_TYPES
            : ENTITY_TYPES.filter((candidate) => currentRawTypes.includes(candidate));
        const selected = currentTypes.includes(type)
          ? currentTypes.filter((candidate) => candidate !== type)
          : ENTITY_TYPES.filter((candidate) => currentTypes.includes(candidate) || candidate === type);
        next.delete("type");
        if (selected.length === 0) {
          next.append("type", EMPTY_TYPE_SENTINEL);
        } else {
          selected.forEach((candidate) => next.append("type", candidate));
        }
        return next;
      },
      { replace: false },
    );
    setOffset(0);
  }

  function handleStateChange(state: EntityState | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (state !== null) {
          next.set("state", state);
        } else {
          next.delete("state");
        }
        return next;
      },
      { replace: false },
    );
    setOffset(0);
  }

  function handleHasContactChange(v: boolean) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (v) {
          next.set("has", "contact");
        } else {
          next.delete("has");
        }
        return next;
      },
      { replace: false },
    );
    setOffset(0);
  }

  return (
    <Page
      archetype="overview"
      title="Entities"
      description="Browse the relationship graph: people, organizations, and more."
      breadcrumbs={[{ label: "Entities" }]}
      error={error}
    >
      {/* SubpageTabs strip — Index is active */}
      <SubpageTabs />

      {/* Filter chips */}
      <FilterChips
        typeFilters={typeFilters}
        stateFilter={stateFilter}
        hasContact={hasContact}
        onTypeChange={handleTypeChange}
        onStateChange={handleStateChange}
        onHasContactChange={handleHasContactChange}
      />

      {/* Main content + right rail */}
      <div className="flex gap-6">
        {/* Main column — entity table */}
        <div className="min-w-0 flex-1 space-y-4">
          {/* Bulk gutter — merge action enabled only when exactly two rows selected */}
          {selectedList.length > 0 && (
            <div
              className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/40 px-3 py-2"
              data-testid="bulk-gutter"
            >
              <span className="text-sm text-muted-foreground">
                {selectedList.length} selected
                {selectedList.length !== 2 ? " — select exactly two to merge" : ""}
              </span>
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setSelectedIds(new Set())}
                >
                  Clear
                </Button>
                <Button
                  type="button"
                  size="sm"
                  data-testid="gutter-merge"
                  disabled={selectedList.length !== 2}
                  onClick={handleGutterMerge}
                >
                  <GitMergeIcon />
                  Merge
                </Button>
              </div>
            </div>
          )}
          <EntityTable
            entities={entities}
            isLoading={isLoading}
            onMergeEntity={setMergeSourceEntity}
            onForgetEntity={setForgetSourceEntity}
            selectedIds={selectedIds}
            onToggleSelect={toggleSelect}
          />

          {/* Pagination */}
          {total > 0 && (
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                Showing {rangeStart}&ndash;{rangeEnd} of {total.toLocaleString()}
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!hasPrev}
                  onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!hasMore}
                  onClick={() => setOffset((o) => o + PAGE_SIZE)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </div>

        {/* Right rail — curation queue */}
        <aside
          className="w-64 shrink-0 space-y-4 border-l border-border pl-6"
          aria-label="Curation queue"
        >
          <p className="text-sm font-semibold text-foreground">Queue</p>
          <QueueRail onMergeEntity={setMergeSourceEntity} onComparePair={setComparePair} />
        </aside>
      </div>
      {mergeSourceEntity !== null && (
        <MergeTargetPickerDialog
          sourceEntity={mergeSourceEntity}
          onOpenChange={(open) => {
            if (!open) setMergeSourceEntity(null);
          }}
          onPickPair={setComparePair}
        />
      )}
      <MergeCompareDialog
        pair={comparePair}
        onOpenChange={(open) => {
          if (!open) setComparePair(null);
        }}
        onResolved={() => setSelectedIds(new Set())}
      />
      <ForgetEntityDialog
        entity={forgetSourceEntity}
        onOpenChange={(open) => {
          if (!open) setForgetSourceEntity(null);
        }}
      />
    </Page>
  );
}
