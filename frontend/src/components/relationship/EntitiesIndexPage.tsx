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

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import {
  ArchiveIcon,
  CheckCircleIcon,
  GitMergeIcon,
  Loader2Icon,
  PlusIcon,
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
import { Label } from "@/components/ui/label";
import { Page } from "@/components/ui/page";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import {
  useArchiveRelationshipEntity,
  useCreateRelationshipEntity,
  useDismissRelationshipEntityQueueItem,
  useEntityFinderSearch,
  useForgetRelationshipEntity,
  usePromoteRelationshipEntity,
  useRelationshipEntities,
  useRelationshipEntitiesByIds,
  useRelationshipEntityQueue,
} from "@/hooks/use-entities";
import { MergeCompareDialog } from "@/components/relationship/MergeCompareDialog";
import { ENTITY_BADGE_TEXT } from "@/lib/entity-model";
import { getBulkConfirmGloss, type BulkConfirmAction } from "@/lib/entity-glosses";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

// Toolbar search ranks across the WHOLE entity set (not the current page). The
// search endpoint caps at 50 hits; we hydrate full summaries for exactly that
// id set, so the hydrate limit matches the search cap.
const SEARCH_RESULT_LIMIT = 50;

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

/** The shared-evidence row that triggered a compare (predicate + object). */
type CompareTrigger = { predicate: string; object: string };

/**
 * A pair of entity ids handed to the compare view (the merge-review surface),
 * optionally carrying the shared evidence that triggered it so the compare view
 * can pre-highlight that row.
 */
type MergePair = { entityA: string; entityB: string; highlight?: CompareTrigger | null };

function isOwner(entity: ActionEntity) {
  return entity.roles?.includes("owner") ?? false;
}

/**
 * Read ALL duplicate-candidate peer entity ids from a queue entry's evidence.
 *
 * The shared-fact duplicate evidence carries ``peer_entity_ids`` (every entity
 * holding the same identifier). An entity can share an identifier with more than
 * one peer; each peer is comparable independently, so the queue card renders one
 * link per peer rather than collapsing to ``peer_entity_ids[0]``.
 */
function peerEntityIdsFromEvidence(evidence: Record<string, unknown>): string[] {
  const peers = evidence?.["peer_entity_ids"];
  if (!Array.isArray(peers)) return [];
  return peers.filter((p): p is string => typeof p === "string");
}

/**
 * Read the triggering shared evidence (predicate + value) from a duplicate
 * entry, when present. Handed to the compare view so the matching shared row is
 * pre-highlighted. Returns ``null`` when the entry was flagged by metadata only.
 */
function compareTriggerFromEvidence(
  evidence: Record<string, unknown>,
): CompareTrigger | null {
  const predicate = evidence?.["predicate"];
  const value = evidence?.["shared_value"];
  if (typeof predicate === "string" && typeof value === "string") {
    return { predicate, object: value };
  }
  return null;
}

/** Human-readable predicate label (deterministic, no prose). */
function prettyPredicate(predicate: string): string {
  return predicate.replaceAll("-", " ").replaceAll("_", " ");
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

/**
 * Confirm dialog for a bulk gutter action (archive / forget). The body is the
 * canned serif confirm gloss from entity-glosses.ts — no generated prose.
 */
function BulkConfirmDialog({
  action,
  count,
  isPending,
  onConfirm,
  onOpenChange,
}: {
  action: BulkConfirmAction | null;
  count: number;
  isPending: boolean;
  onConfirm: () => void;
  onOpenChange: (open: boolean) => void;
}) {
  const title = action === "forget" ? "Delete entities" : "Archive entities";
  const commitLabel = action === "forget" ? "Delete" : "Archive";
  return (
    <Dialog open={action !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription
            className="italic"
            style={{ fontFamily: "'Source Serif 4', Georgia, serif" }}
            data-testid="bulk-confirm-gloss"
          >
            {action ? getBulkConfirmGloss(action, count) : ""}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            variant={action === "forget" ? "destructive" : "default"}
            disabled={isPending}
            onClick={onConfirm}
            data-testid="bulk-confirm-commit"
          >
            {isPending ? "Working…" : commitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
  const { data, isFetching, isError } = useEntityFinderSearch(search, { limit: 8 });

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
          {search.trim() !== "" && isError && !isFetching && (
            <p className="text-sm text-destructive" role="alert">
              Search failed. Try again in a moment.
            </p>
          )}
          {search.trim() !== "" && !isError && candidates.length === 0 && !isFetching && (
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
  /** Keyboard-cursor row index (``-1`` when no row is focused). */
  cursor?: number;
  /**
   * True when the active toolbar search query failed (e.g. a 500). Renders an
   * error state instead of the "no entities found" empty copy so a failed
   * search is never mistaken for a genuinely empty result set.
   */
  searchFailed?: boolean;
}

function EntityTable({
  entities,
  isLoading,
  onMergeEntity,
  onForgetEntity,
  selectedIds,
  onToggleSelect,
  cursor = -1,
  searchFailed = false,
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

  if (searchFailed) {
    return (
      <EmptyState
        title="Search failed."
        description="The entity search could not be completed. Try again in a moment."
      />
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
          {entities.map((entity, index) => (
            <tr
              key={entity.id}
              aria-selected={index === cursor || undefined}
              data-cursor={index === cursor || undefined}
              // Keyboard cursor focus = 2px left border, no glow (spec
              // "Keyboard maps per view": "Focus states MUST be visible per the
              // design language (2px left border, no glow)"). The transparent
              // border on non-cursored rows keeps the table from reflowing.
              className={`border-b border-l-2 last:border-b-0 hover:bg-muted/50 ${
                index === cursor
                  ? "border-l-foreground bg-muted/40"
                  : "border-l-transparent"
              }`}
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
                    {entity.tier}: {DUNBAR_TIER_LABELS[entity.tier] ?? `Tier ${entity.tier}`}
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

  // Resolve peer entity ids to display names off the queue's own items — the
  // duplicate evidence carries peer ids, not names, and a peer that needs
  // attention also surfaces as its own queue entry. Deterministic; no fetch.
  const nameById = new Map(items.map((e) => [e.entity_id, e.canonical_name]));

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
          nameById={nameById}
          onMergeEntity={onMergeEntity}
          onComparePair={onComparePair}
        />
      )}
      {duplicates.length > 0 && (
        <QueueSection
          title="Duplicate candidate"
          items={duplicates}
          accentColor="var(--amber)"
          nameById={nameById}
          onMergeEntity={onMergeEntity}
          onComparePair={onComparePair}
        />
      )}
      {stale.length > 0 && (
        <QueueSection
          title="Stale"
          items={stale}
          accentColor="var(--muted-foreground)"
          nameById={nameById}
          onMergeEntity={onMergeEntity}
          onComparePair={onComparePair}
        />
      )}
    </div>
  );
}

/**
 * Render the duplicate-candidate evidence drill: the shared value, and each peer
 * the entity collides with as a link that opens the compare view for that pair
 * (carrying the triggering shared evidence so it pre-highlights). Falls back to
 * the target picker when the entry was flagged by metadata only (no peer).
 */
function DuplicateEvidence({
  entry,
  nameById,
  onMergeEntity,
  onComparePair,
}: {
  entry: RelationshipQueueEntry;
  nameById: Map<string, string>;
  onMergeEntity: (entity: ActionEntity) => void;
  onComparePair: (pair: MergePair) => void;
}) {
  const trigger = compareTriggerFromEvidence(entry.evidence);
  const peerIds = peerEntityIdsFromEvidence(entry.evidence);

  if (peerIds.length === 0) {
    // No known peer — route through the target picker.
    const actionEntity: ActionEntity = {
      id: entry.entity_id,
      canonical_name: entry.canonical_name,
      entity_type: entry.entity_type,
    };
    return <MergeEntityButton entity={actionEntity} onSelect={onMergeEntity} />;
  }

  return (
    <div className="mt-0.5 space-y-0.5" data-testid="queue-duplicate-evidence">
      {trigger && (
        <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--mfg)]">
          {prettyPredicate(trigger.predicate)}{" "}
          <span className="tabular-nums normal-case text-[var(--dim)]">{trigger.object}</span>
        </p>
      )}
      <ul className="space-y-0.5">
        {peerIds.map((peerId) => (
          <li key={peerId} className="flex items-center justify-between gap-2">
            <button
              type="button"
              data-testid="queue-duplicate-peer"
              aria-label={`Compare ${entry.canonical_name} with ${nameById.get(peerId) ?? "peer"}`}
              onClick={() =>
                onComparePair({
                  entityA: entry.entity_id,
                  entityB: peerId,
                  highlight: trigger,
                })
              }
              className="min-w-0 truncate text-left text-xs text-[var(--amber)] underline decoration-[var(--border-strong)] underline-offset-4 hover:decoration-[var(--amber)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              {nameById.get(peerId) ?? "Linked entity"}
            </button>
            <Link
              to={`/entities/${peerId}`}
              aria-label={`Open ${nameById.get(peerId) ?? "peer"}`}
              className="shrink-0 text-[10px] text-muted-foreground hover:text-foreground"
            >
              →
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function QueueSection({
  title,
  items,
  accentColor,
  nameById,
  onMergeEntity,
  onComparePair,
}: {
  title: string;
  items: RelationshipQueueEntry[];
  accentColor: string;
  nameById: Map<string, string>;
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
      <ul className="space-y-2">
        {items.map((entry) => {
          const actionEntity: ActionEntity = {
            id: entry.entity_id,
            canonical_name: entry.canonical_name,
            entity_type: entry.entity_type,
          };

          return (
            <li key={entry.entity_id} className="text-sm">
              <div className="flex items-center justify-between gap-2">
                <Link
                  to={`/entities/${entry.entity_id}`}
                  className="min-w-0 truncate text-primary hover:underline"
                >
                  {entry.canonical_name}
                </Link>
                <div className="flex shrink-0 items-center gap-1">
                  {entry.bucket === "unidentified" && (
                    <>
                      <PromoteEntityButton entity={actionEntity} />
                      {/* No known peer — route through the target picker so the
                          owner selects the entity to compare against. */}
                      <MergeEntityButton entity={actionEntity} onSelect={onMergeEntity} />
                    </>
                  )}
                  {entry.bucket === "stale" && <ArchiveEntityButton entity={actionEntity} />}
                  <DismissQueueItemButton entity={actionEntity} />
                </div>
              </div>
              {/* Stale: surface the staleness age inline beside a detail link. */}
              {entry.bucket === "stale" && entry.last_seen && (
                <p
                  className="font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--dim)]"
                  data-testid="queue-stale-age"
                >
                  last seen <Time value={entry.last_seen} mode="relative" />
                </p>
              )}
              {/* Duplicate-candidate: render the evidence drill (shared value +
                  one comparable peer per collision). */}
              {entry.bucket === "duplicate-candidate" && (
                <DuplicateEvidence
                  entry={entry}
                  nameById={nameById}
                  onMergeEntity={onMergeEntity}
                  onComparePair={onComparePair}
                />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create-entity dialog
// ---------------------------------------------------------------------------

function CreateEntityDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [entityType, setEntityType] = useState<EntityType>("person");
  const createMutation = useCreateRelationshipEntity();

  function handleClose() {
    setName("");
    setEntityType("person");
    onClose();
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      await createMutation.mutateAsync({
        canonicalName: name.trim(),
        entityType,
      });
      toast.success(`Created entity "${name.trim()}"`);
      handleClose();
    } catch (err) {
      toast.error(`Create failed: ${err instanceof Error ? err.message : "Unknown error"}`);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) handleClose(); }}>
      <DialogContent data-testid="create-entity-dialog">
        <DialogHeader>
          <DialogTitle>New entity</DialogTitle>
          <DialogDescription>
            Create a new entity in the relationship graph.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="create-entity-name">Name</Label>
            <Input
              id="create-entity-name"
              data-testid="create-entity-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Full name or organisation name"
              autoFocus
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="create-entity-type">Type</Label>
            <Select
              value={entityType}
              onValueChange={(v) => setEntityType(v as EntityType)}
            >
              <SelectTrigger id="create-entity-type" data-testid="create-entity-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ENTITY_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {TYPE_LABELS[t]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={handleClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={createMutation.isPending || !name.trim()}
              data-testid="create-entity-submit"
            >
              {createMutation.isPending ? (
                <Loader2Icon className="mr-2 h-4 w-4 animate-spin" />
              ) : null}
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
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
  // The bulk gutter's archive/forget actions confirm through a serif-gloss
  // dialog before committing. ``null`` means no confirm is open.
  const [bulkConfirm, setBulkConfirm] = useState<BulkConfirmAction | null>(null);
  // Toolbar search query. Wired to the relationship search endpoint (same
  // deterministic ranking as the Cmd-K Finder) instead of a client-side
  // substring pass. Spec: "Index toolbar search uses the search endpoint".
  const [searchQuery, setSearchQuery] = useState("");

  const [createEntityOpen, setCreateEntityOpen] = useState(false);

  const archiveMutation = useArchiveRelationshipEntity();
  const forgetMutation = useForgetRelationshipEntity();

  // The focusable list container for the Index keyboard map. Bindings attach
  // HERE (onKeyDown), never to window — the map is active only while the list
  // has keyboard focus, so it never shadows app-wide shortcuts or other views.
  const listRef = useRef<HTMLDivElement>(null);
  // Cursor row for keyboard navigation (index into the visible rows). ``-1``
  // means no row is focused yet.
  const [cursor, setCursor] = useState(-1);
  // Anchor for Shift+arrow range extension.
  const [anchor, setAnchor] = useState<number | null>(null);

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

  async function handleBulkConfirm() {
    const action = bulkConfirm;
    if (!action) return;
    const ids = Array.from(selectedIds);
    const mutate = action === "forget" ? forgetMutation : archiveMutation;
    const verb = action === "forget" ? "Deleted" : "Archived";
    // allSettled, not all: a partial failure must deselect only the entities
    // that actually succeeded and keep the failed ones selected, so the owner
    // can see and retry them. Promise.all would reject on the first failure and
    // leave the whole (now-stale) selection in place.
    const results = await Promise.allSettled(ids.map((id) => mutate.mutateAsync(id)));
    const failedIds = ids.filter((_, i) => results[i].status === "rejected");
    const succeeded = ids.length - failedIds.length;

    if (succeeded > 0) {
      toast.success(`${verb} ${succeeded} ${succeeded === 1 ? "entity" : "entities"}`);
    }
    if (failedIds.length > 0) {
      const firstReason = results.find((r) => r.status === "rejected") as
        | PromiseRejectedResult
        | undefined;
      const reason =
        firstReason?.reason instanceof Error ? firstReason.reason.message : "Unknown error";
      const noun = failedIds.length === 1 ? "entity" : "entities";
      toast.error(
        `${action === "forget" ? "Delete" : "Archive"} failed for ${failedIds.length} ${noun}: ${reason}`,
      );
    }

    // Keep only the failed entities selected.
    setSelectedIds(new Set(failedIds));
    setBulkConfirm(null);
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
  const allEntities = data?.items ?? [];

  // Toolbar search: the search endpoint is authoritative for WHICH entities
  // match (same ranking as the Finder). It ranks across the WHOLE entity set,
  // so its hits are NOT confined to the current paginated page — we must hydrate
  // full summaries for the matched id set rather than intersecting with the
  // loaded page (which would silently drop every match not on that page).
  const { data: searchData, isError: isSearchError } = useEntityFinderSearch(searchQuery, {
    limit: SEARCH_RESULT_LIMIT,
  });
  const isSearching = searchQuery.trim().length > 0;
  const searchIds = useMemo(
    () => (searchData?.results ?? []).map((r) => r.entity_id),
    [searchData],
  );
  // Hydrate full relationship summaries (tier, last_seen, contact counts, …) for
  // exactly the search-matched ids, so the table keeps its rich columns for EVERY
  // match. The active filter chips are passed through so search stays constrained
  // to the same population as the unsearched list (the backend ANDs them with the
  // id set); pagination is omitted. The hook self-disables when there are no ids.
  const {
    data: searchHydrated,
    isError: isHydrateError,
    isLoading: isHydrateLoading,
  } = useRelationshipEntitiesByIds({
    entity_type: typeFilters,
    state: stateFilter ?? undefined,
    has: hasContact ? "contact" : undefined,
    ids: searchIds,
    limit: SEARCH_RESULT_LIMIT,
  });
  // A failed search must not masquerade as "no entities" — surface the error
  // distinctly instead of collapsing to the empty state.
  const searchFailed = isSearching && (isSearchError || isHydrateError);
  const entities = isSearching
    ? (() => {
        const byId = new Map((searchHydrated?.items ?? []).map((e) => [e.id, e]));
        // Preserve the search endpoint's score ordering; drop any hit that the
        // hydrate filtered out (e.g. archived/tombstoned entities).
        return (searchData?.results ?? [])
          .map((r) => byId.get(r.entity_id))
          .filter((e): e is RelationshipEntitySummary => e !== undefined);
      })()
    : allEntities;
  const total = isSearching ? entities.length : (data?.total ?? 0);

  // A search has no rows to show *yet* until the finder has answered and, when it
  // returned hits, the hydration has resolved. Treat that window as loading so the
  // table shows skeletons instead of flashing a false "No entities found.".
  const searchResolving =
    isSearching &&
    !searchFailed &&
    (searchData === undefined || (searchIds.length > 0 && isHydrateLoading));
  const tableLoading = isSearching ? searchResolving : isLoading;

  // Offset pagination applies to the unfiltered list; an active toolbar search
  // filters in place across the loaded page, so paging is suppressed.
  const rangeStart = total === 0 ? 0 : isSearching ? 1 : offset + 1;
  const rangeEnd = isSearching ? total : Math.min(offset + PAGE_SIZE, total);
  const hasMore = !isSearching && offset + PAGE_SIZE < total;
  const hasPrev = !isSearching && offset > 0;

  // Index keyboard map (spec: "Index bulk-select gutter" keyboard map). Bound to
  // the FOCUSED list container via onKeyDown — never window-global — so it is
  // active only while the list holds keyboard focus and never shadows the
  // app-wide Cmd-K / "/" shortcuts or other views.
  //   ↑/↓            move the cursor
  //   Space          toggle selection at the cursor
  //   Shift+↑/↓      extend the selection range from the anchor
  //   Esc            clear the selection
  //   Enter          open the cursor row's detail page
  const navigate = useNavigate();
  const handleListKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLDivElement>) => {
      // Defer to inputs (the toolbar search lives outside this container, but
      // guard anyway for embedded controls).
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;

      // n — open the create-entity dialog (guard for modifier keys to avoid
      // shadowing browser shortcuts like Ctrl+N / Cmd+N).
      if (e.key === "n" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        setCreateEntityOpen(true);
        return;
      }

      if (entities.length === 0) return;

      const clamp = (n: number) => Math.max(0, Math.min(entities.length - 1, n));
      const select = (from: number, to: number) => {
        const lo = Math.min(from, to);
        const hi = Math.max(from, to);
        setSelectedIds(() => {
          const next = new Set<string>();
          for (let i = lo; i <= hi; i += 1) next.add(entities[i].id);
          return next;
        });
      };

      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const delta = e.key === "ArrowDown" ? 1 : -1;
        const nextCursor = clamp(cursor < 0 ? 0 : cursor + delta);
        if (e.shiftKey) {
          const base = anchor ?? (cursor < 0 ? 0 : cursor);
          if (anchor === null) setAnchor(base);
          select(base, nextCursor);
        } else {
          setAnchor(null);
        }
        setCursor(nextCursor);
      } else if (e.key === " " || e.key === "Spacebar") {
        // Guard a stale cursor (e.g. the list shrank under us).
        if (cursor < 0 || cursor >= entities.length) return;
        e.preventDefault();
        toggleSelect(entities[cursor].id);
        setAnchor(cursor);
      } else if (e.key === "Escape") {
        e.preventDefault();
        setSelectedIds(new Set());
        setAnchor(null);
      } else if (e.key === "Enter") {
        if (cursor < 0 || cursor >= entities.length) return;
        e.preventDefault();
        void navigate(`/entities/${entities[cursor].id}`);
      }
    },
    [entities, cursor, anchor, navigate],
  );

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

      {/* Toolbar: search + New entity button */}
      <div className="mb-3 flex items-center gap-2">
        <Input
          type="search"
          aria-label="Search entities"
          placeholder="Search entities…"
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          data-testid="entities-toolbar-search"
          className="flex-1"
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setCreateEntityOpen(true)}
          data-testid="new-entity-button"
        >
          <PlusIcon className="mr-1.5 h-3.5 w-3.5" />
          New entity
        </Button>
      </div>

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
          {/* Bulk gutter — slim, hairline-ruled (no card). The merge action is
              enabled only when exactly two rows are selected; archive and forget
              confirm through a serif-gloss dialog. */}
          {selectedList.length > 0 && (
            <div
              className="flex items-center justify-between gap-2 border-y border-border py-1.5"
              data-testid="bulk-gutter"
            >
              <span
                className="font-mono text-[11px] uppercase tracking-[0.08em] text-muted-foreground"
                data-testid="bulk-gutter-count"
              >
                <span className="tabular-nums">{selectedList.length}</span> selected
              </span>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  data-testid="gutter-merge"
                  disabled={selectedList.length !== 2}
                  onClick={handleGutterMerge}
                  title={
                    selectedList.length === 2
                      ? "Compare and merge the two selected"
                      : "Select exactly two to merge"
                  }
                  className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.04em] text-muted-foreground underline decoration-[var(--border-strong)] underline-offset-4 hover:text-foreground disabled:opacity-40 disabled:no-underline disabled:hover:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  <GitMergeIcon className="h-3.5 w-3.5" aria-hidden />
                  Merge
                </button>
                <button
                  type="button"
                  data-testid="gutter-archive"
                  onClick={() => setBulkConfirm("archive")}
                  className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.04em] text-muted-foreground underline decoration-[var(--border-strong)] underline-offset-4 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  <ArchiveIcon className="h-3.5 w-3.5" aria-hidden />
                  Archive
                </button>
                <button
                  type="button"
                  data-testid="gutter-forget"
                  onClick={() => setBulkConfirm("forget")}
                  className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.04em] text-[var(--red)] underline decoration-[var(--border-strong)] underline-offset-4 hover:decoration-[var(--red)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  <TrashIcon className="h-3.5 w-3.5" aria-hidden />
                  Forget
                </button>
                <button
                  type="button"
                  data-testid="gutter-clear"
                  onClick={() => setSelectedIds(new Set())}
                  className="font-mono text-[11px] uppercase tracking-[0.04em] text-muted-foreground underline decoration-[var(--border-strong)] underline-offset-4 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  Clear
                </button>
              </div>
            </div>
          )}
          {/* Focusable list container — the Index keyboard map binds HERE
              (onKeyDown), never to window. */}
          <div
            ref={listRef}
            tabIndex={0}
            role="grid"
            aria-label="Entity list"
            onKeyDown={handleListKeyDown}
            data-testid="entity-list-container"
            className="rounded-sm outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <EntityTable
              entities={entities}
              isLoading={tableLoading}
              onMergeEntity={setMergeSourceEntity}
              onForgetEntity={setForgetSourceEntity}
              selectedIds={selectedIds}
              onToggleSelect={toggleSelect}
              cursor={cursor}
              searchFailed={searchFailed}
            />
          </div>

          {/* Pagination */}
          {total > 0 && (
            <div className="flex items-center justify-between">
              <p className="text-sm tabular-nums text-muted-foreground">
                Showing {rangeStart}&ndash;{rangeEnd} of {total.toLocaleString()}
                {isSearching && entities.length >= SEARCH_RESULT_LIMIT && (
                  <span className="ml-1 text-xs text-muted-foreground/70">
                    (top {SEARCH_RESULT_LIMIT} results shown)
                  </span>
                )}
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
        highlightFact={comparePair?.highlight ?? null}
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
      <BulkConfirmDialog
        action={bulkConfirm}
        count={selectedList.length}
        isPending={archiveMutation.isPending || forgetMutation.isPending}
        onConfirm={handleBulkConfirm}
        onOpenChange={(open) => {
          if (!open) setBulkConfirm(null);
        }}
      />
      <CreateEntityDialog
        open={createEntityOpen}
        onClose={() => setCreateEntityOpen(false)}
      />
    </Page>
  );
}
