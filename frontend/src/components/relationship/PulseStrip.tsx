/**
 * PulseStrip
 *
 * Four stat tiles rendered at the top of the entity detail page:
 * Dunbar tier (interactive), last interaction, 30-day cadence, open loops.
 *
 * DunbarTile is clickable and opens a tier-picker dropdown. The lock glyph
 * indicates a manual pin (override). Auto = clear pin to revert to rank-based
 * assignment.
 */

import { useMemo, useState } from "react";
import { Check, ChevronDown, Loader2, Lock } from "lucide-react";
import { toast } from "sonner";
import { formatDistanceToNow } from "date-fns";

import type { EntityGift, EntityLoan, EntityTimelineItem } from "@/api/types";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  useEntityGifts,
  useEntityLoans,
  useEntityTimeline,
  useUpdateEntityDunbarTier,
} from "@/hooks/use-entities";

// ---------------------------------------------------------------------------
// Dunbar tier constants
// ---------------------------------------------------------------------------

// Canonical Dunbar layer sizes — tier values returned by the engine are the
// layer sizes themselves, not 1-indexed positions. Order matters for the menu.
const DUNBAR_TIERS: { tier: number; label: string; description: string }[] = [
  { tier: 5, label: "Support 5", description: "Closest support clique" },
  { tier: 15, label: "Sympathy 15", description: "Sympathy group" },
  { tier: 50, label: "Friends 50", description: "Good friends" },
  { tier: 150, label: "Network 150", description: "Meaningful contacts" },
  { tier: 500, label: "Acquaintances 500", description: "Acquaintances" },
  { tier: 1500, label: "Recognized 1500", description: "Recognizable" },
];

const _DUNBAR_LABEL: Record<number, string> = Object.fromEntries(
  DUNBAR_TIERS.map((t) => [t.tier, t.label]),
);

function dunbarLabel(tier: number | null | undefined): string | null {
  if (tier == null) return null;
  return _DUNBAR_LABEL[tier] ?? `Tier ${tier}`;
}

// ---------------------------------------------------------------------------
// DunbarTile
// ---------------------------------------------------------------------------

function DunbarTile({
  entityId,
  currentTier,
  isPinned,
}: {
  entityId: string;
  currentTier: number | null;
  isPinned: boolean;
}) {
  const updateTier = useUpdateEntityDunbarTier();
  const tierLabel = dunbarLabel(currentTier);

  function handleSet(tier: number | null) {
    updateTier.mutate(
      { entityId, tier },
      {
        onSuccess: (data) => {
          toast.success(
            tier == null
              ? "Dunbar tier pin cleared."
              : `Pinned to ${dunbarLabel(tier) ?? `tier ${tier}`}.`,
            { description: data.message },
          );
        },
        onError: (err) =>
          toast.error(
            `Failed to update tier: ${err instanceof Error ? err.message : "Unknown"}`,
          ),
      },
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={
            "group rounded-md border px-3 py-2.5 text-left transition-colors " +
            "hover:bg-accent focus:outline-none focus:ring-2 focus:ring-ring " +
            (isPinned ? "border-foreground/30" : "border-border")
          }
          disabled={updateTier.isPending}
        >
          <p className="text-muted-foreground text-[11px] uppercase tracking-wide flex items-center gap-1">
            <span>Dunbar tier</span>
            {isPinned && (
              <Lock
                className="h-2.5 w-2.5"
                aria-label="Pinned"
              />
            )}
          </p>
          <p
            className={
              "mt-1 text-sm font-medium leading-tight flex items-center gap-1.5 " +
              (tierLabel ? "text-foreground" : "text-muted-foreground")
            }
          >
            {updateTier.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : null}
            <span>{tierLabel ?? "Unranked"}</span>
            <ChevronDown className="h-3 w-3 opacity-50 group-hover:opacity-100 transition-opacity ml-auto" />
          </p>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        {DUNBAR_TIERS.map((opt) => {
          const selected = currentTier === opt.tier;
          return (
            <DropdownMenuItem
              key={opt.tier}
              onSelect={() => handleSet(opt.tier)}
              disabled={updateTier.isPending}
              className="flex flex-col items-start gap-0.5"
            >
              <div className="flex w-full items-center justify-between">
                <span className="text-sm font-medium">{opt.label}</span>
                {selected && <Check className="h-3.5 w-3.5" />}
              </div>
              <span className="text-muted-foreground text-xs">
                {opt.description}
              </span>
            </DropdownMenuItem>
          );
        })}
        {isPinned && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() => handleSet(null)}
              disabled={updateTier.isPending}
            >
              <span className="text-sm">Clear pin (auto)</span>
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ---------------------------------------------------------------------------
// PulseTile
// ---------------------------------------------------------------------------

function PulseTile({
  label,
  value,
  muted = false,
  emphasis = false,
}: {
  label: string;
  value: string;
  muted?: boolean;
  emphasis?: boolean;
}) {
  return (
    <div
      className={
        "rounded-md border px-3 py-2.5 " +
        (emphasis ? "bg-accent" : "bg-card")
      }
    >
      <p className="text-muted-foreground text-[11px] uppercase tracking-wide">
        {label}
      </p>
      <p
        className={
          "mt-1 text-sm font-medium leading-tight " +
          (muted ? "text-muted-foreground" : "text-foreground")
        }
      >
        {value}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PulseStrip
// ---------------------------------------------------------------------------

export interface PulseStripProps {
  entityId: string;
  dunbarTier: number | null;
  isPinned: boolean;
}

export function PulseStrip({ entityId, dunbarTier, isPinned }: PulseStripProps) {
  const { data: timelineItems, isLoading: timelineLoading } =
    useEntityTimeline(entityId);
  const { data: gifts } = useEntityGifts(entityId);
  const { data: loans } = useEntityLoans(entityId);

  const lastInteraction = useMemo(() => {
    if (!timelineItems) return null;
    return timelineItems.find(
      (it: EntityTimelineItem) => it.kind === "interaction" && it.valid_at,
    );
  }, [timelineItems]);

  // `now` is captured once at mount via lazy state init — Date.now() is impure
  // and would trip react-hooks/purity inside useMemo. The cadence window only
  // needs to be approximate, so a per-mount snapshot is fine.
  const [mountedAt] = useState(() => Date.now());
  const cadence30d = useMemo(() => {
    if (!timelineItems) return null;
    const cutoff = mountedAt - 30 * 24 * 60 * 60 * 1000;
    return timelineItems.filter(
      (it: EntityTimelineItem) =>
        it.kind === "interaction" &&
        it.valid_at &&
        new Date(it.valid_at).getTime() >= cutoff,
    ).length;
  }, [timelineItems, mountedAt]);

  const openLoops = useMemo(() => {
    const giftOpen = (gifts ?? []).filter(
      (g: EntityGift) =>
        g.status && g.status !== "given" && g.status !== "thanked",
    ).length;
    const loanOpen = (loans ?? []).filter(
      (l: EntityLoan) => l.settled !== "true",
    ).length;
    return giftOpen + loanOpen;
  }, [gifts, loans]);

  const isLoading = timelineLoading;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <DunbarTile
        entityId={entityId}
        currentTier={dunbarTier}
        isPinned={isPinned}
      />
      {/* formatDistanceToNow kept: PulseTile.value is typed string, cannot accept <Time> JSX */}
      <PulseTile
        label="Last interaction"
        value={
          isLoading
            ? "..."
            : lastInteraction?.valid_at
              ? formatDistanceToNow(new Date(lastInteraction.valid_at), {
                  addSuffix: true,
                })
              : "None recorded"
        }
        muted={!lastInteraction}
      />
      <PulseTile
        label="Last 30 days"
        value={
          isLoading
            ? "..."
            : cadence30d === null || cadence30d === 0
              ? "Quiet"
              : `${cadence30d} interaction${cadence30d === 1 ? "" : "s"}`
        }
        muted={cadence30d === 0}
      />
      <PulseTile
        label="Open loops"
        value={
          isLoading
            ? "..."
            : openLoops === 0
              ? "None"
              : `${openLoops} unresolved`
        }
        muted={openLoops === 0}
        emphasis={openLoops > 0}
      />
    </div>
  );
}
