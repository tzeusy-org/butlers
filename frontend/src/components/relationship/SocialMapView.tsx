/**
 * SocialMapView — body of the Dunbar social map at /entities/social-map.
 *
 * This component contains all interactive state, data fetching, and canvas
 * rendering for the social map. It is designed to be rendered inside the
 * SubpageTabs chrome (SocialMapPage) without requiring the Page/SubpageTabs
 * wrapper to be duplicated.
 *
 * URL contract (round-trips via react-router useSearchParams):
 *   ?q={search}        — active search filter (case-insensitive substring)
 *   ?focus=tier-{N}    — jump-to-tier on load; N in 5|15|50|150|500|1500
 *
 * Example: /entities/social-map?focus=tier-50&q=alice
 *
 * Keyboard shortcuts:
 *   /        — focus the search input
 *   Escape   — clear search if input has text; navigate back otherwise
 *   1–6      — jump to tier 5/15/50/150/500/1500 by index
 *
 * Design: header above canvas, no border/card wrapper around the canvas,
 * legend inside the header area. Follows the dashboard's existing layout.
 *
 * Spec: openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/tasks.md §8.5
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { PinIcon, SearchIcon, XIcon } from "lucide-react";

import type { DunbarEntry } from "@/api/types";
import { Input } from "@/components/ui/input";
import { ConcentricCirclesCanvas } from "@/components/memory/ConcentricCirclesCanvas";
import { HorizontalStrataCanvas } from "@/components/memory/HorizontalStrataCanvas";
import { EmptyStatePanel } from "@/components/memory/EmptyStatePanel";
import {
  TIER_NAMES,
  TIER_RING_COLORS,
  TIERS,
  type Tier,
} from "@/components/memory/concentric-circles-constants";
import { useDebounce } from "@/hooks/use-debounce";
import { useDunbarRanking } from "@/hooks/use-memory";
import { useViewport } from "@/hooks/use-viewport";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parseFocusTier(raw: string | null): Tier | null {
  if (!raw) return null;
  const match = /^tier-(\d+)$/.exec(raw);
  if (!match) return null;
  const n = Number(match[1]) as Tier;
  return (TIERS as readonly number[]).includes(n) ? n : null;
}

function useElementSize() {
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [el, setEl] = useState<HTMLElement | null>(null);
  const ref = useCallback((node: HTMLElement | null) => setEl(node), []);
  useLayoutEffect(() => {
    if (!el) return;
    const measure = () => setSize({ width: el.clientWidth, height: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return { ref, size };
}

// Bottom gutter (px) preserved below the view so the canvas doesn't butt against
// the viewport edge -- matches the dashboard shell's p-6 (24px) content padding.
const FILL_BOTTOM_GUTTER = 24;

/**
 * useFillViewportHeight -- gives the view's root an explicit pixel height that
 * fills from its top to the bottom of the viewport.
 *
 * The social map lives inside the Page "overview" archetype, whose wrapper is a
 * plain `space-y-6` block (height: auto). A child `h-full` therefore collapses,
 * leaving the flex-1 canvas stage at its 300px min-height floor (only the top
 * ~1/3 of the screen fills). Measuring the root's viewport-relative top and
 * setting an explicit height restores the intended full-height layout; the
 * inner `flex-1` stage then handles controls-bar wrapping on its own.
 */
function useFillViewportHeight() {
  const [height, setHeight] = useState<number | null>(null);
  const [el, setEl] = useState<HTMLElement | null>(null);
  const ref = useCallback((node: HTMLElement | null) => setEl(node), []);
  useLayoutEffect(() => {
    if (!el) return;
    const measure = () => {
      const top = el.getBoundingClientRect().top;
      setHeight(Math.max(300, window.innerHeight - top - FILL_BOTTOM_GUTTER));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [el]);
  return { ref, height };
}

// ---------------------------------------------------------------------------
// Tier legend
// ---------------------------------------------------------------------------

function TierLegend({ tierGroups }: { tierGroups: Record<Tier, DunbarEntry[]> }) {
  return (
    <div className="flex flex-wrap gap-2">
      {TIERS.map((tier) => {
        const count = tierGroups[tier].length;
        const color = TIER_RING_COLORS[tier];
        return (
          <div key={tier} className="flex items-center gap-1 text-xs">
            <span
              className="inline-block w-2.5 h-2.5 rounded-full"
              style={{ backgroundColor: color, opacity: 0.7 }}
            />
            <span className="text-muted-foreground">{TIER_NAMES[tier]}</span>
            <span className="font-medium">{count}</span>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Expanded-tier pill bar
// ---------------------------------------------------------------------------

interface ExpandedTierPillBarProps {
  expandedTiers: Set<Tier>;
  onCollapse: (tier: Tier) => void;
  onResetAll: () => void;
}

function ExpandedTierPillBar({ expandedTiers, onCollapse, onResetAll }: ExpandedTierPillBarProps) {
  if (expandedTiers.size === 0) return null;

  return (
    <div className="flex items-center flex-wrap gap-2" aria-label="Expanded tiers">
      <span className="text-xs text-muted-foreground">Showing all:</span>
      {TIERS.filter((tier) => expandedTiers.has(tier)).map((tier) => {
        const color = TIER_RING_COLORS[tier];
        return (
          <button
            key={tier}
            type="button"
            onClick={() => onCollapse(tier)}
            className="inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors hover:opacity-80"
            style={{
              borderColor: color + "60",
              color,
              backgroundColor: color + "12",
            }}
            aria-label={`Collapse ${TIER_NAMES[tier]}`}
          >
            {TIER_NAMES[tier]}
            <XIcon className="h-3 w-3 opacity-70" />
          </button>
        );
      })}
      {expandedTiers.size > 1 && (
        <button
          type="button"
          onClick={onResetAll}
          className="text-xs text-muted-foreground underline-offset-2 hover:underline"
          aria-label="Collapse all expanded tiers"
        >
          Reset all
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Jump-to-tier chip set
// ---------------------------------------------------------------------------

interface JumpToTierChipsProps {
  onJump: (tier: Tier) => void;
  activeTier: Tier | null;
}

function JumpToTierChips({ onJump, activeTier }: JumpToTierChipsProps) {
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      <span className="text-xs text-muted-foreground mr-0.5">Jump to:</span>
      {TIERS.map((tier) => (
        <button
          key={tier}
          type="button"
          onClick={() => onJump(tier)}
          className={[
            "rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors",
            activeTier === tier
              ? "border-foreground bg-foreground text-background"
              : "border-border bg-background hover:bg-accent text-muted-foreground hover:text-foreground",
          ].join(" ")}
          style={activeTier === tier ? {} : { borderColor: TIER_RING_COLORS[tier] + "80" }}
          title={TIER_NAMES[tier]}
          aria-label={`Jump to ${TIER_NAMES[tier]} (tier ${tier})`}
        >
          {tier}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SocialMapView
// ---------------------------------------------------------------------------

export function SocialMapView() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // URL state
  const urlSearch = searchParams.get("q") ?? "";
  const urlFocus = parseFocusTier(searchParams.get("focus"));

  // Local search state (writes back to URL)
  const [searchInput, setSearchInput] = useState(urlSearch);
  const debouncedSearch = useDebounce(searchInput, 200);

  // Active focusTier drives the animation in the canvas.
  // We keep a local copy so clicking a chip re-triggers the animation even if
  // the URL param already matches.
  const [focusTier, setFocusTier] = useState<Tier | null>(urlFocus);
  // Track which focus was last applied so clicking the same tier re-fires
  const [focusTrigger, setFocusTrigger] = useState(0);

  // Expanded tiers
  const [expandedTiers, setExpandedTiers] = useState<Set<Tier>>(new Set());

  // Viewport -- drives layout switching. isMobile = viewport ≤640px → strata.
  const { isMobile } = useViewport();

  // Canvas size
  const { ref: stageRef, size: stageSize } = useElementSize();

  // Root fill height -- the "overview" archetype wrapper has auto height, so an
  // explicit pixel height is needed for the inner flex-1 stage to fill the page.
  const { ref: fillRef, height: fillHeight } = useFillViewportHeight();

  // Data
  const { data, isLoading, isError } = useDunbarRanking(true);
  const entries = data?.entries ?? [];
  const ownerEntityId = data?.owner_entity_id ?? null;
  const ownerEntry = entries.find((e) => e.entity_id === ownerEntityId);
  const ownerName = ownerEntry?.canonical_name ?? "You";

  // Tier groups for legend — memoized to avoid O(N) work on every render.
  // Depends on the raw `data` object (stable reference from TanStack Query) so
  // the memo only recomputes when the server response changes.
  const { tierGroups, hasPinnedOverride } = useMemo(() => {
    const rawEntries = data?.entries ?? [];
    const rawOwner = data?.owner_entity_id ?? null;
    const groups: Record<Tier, DunbarEntry[]> = {
      5: [],
      15: [],
      50: [],
      150: [],
      500: [],
      1500: [],
    };
    let pinned = false;
    for (const entry of rawEntries) {
      if (entry.entity_id === rawOwner) continue;
      const tier = entry.dunbar_tier as Tier;
      groups[tier].push(entry);
      if (entry.dunbar_tier_override) pinned = true;
    }
    return { tierGroups: groups, hasPinnedOverride: pinned };
  }, [data]);

  // Cold-start detection: fewer than 5 scored non-owner contacts means the
  // map has no meaningful signal yet. Computed from the same data object so
  // it stays in sync with the tier groups above.
  const scoredCount = useMemo(
    () =>
      (data?.entries ?? []).filter(
        (e) => e.dunbar_score > 0 && e.entity_id !== (data?.owner_entity_id ?? null),
      ).length,
    [data],
  );
  const isColdStart = !isLoading && !isError && scoredCount < 5;

  // Sync debounced search back to URL
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (debouncedSearch) {
          next.set("q", debouncedSearch);
        } else {
          next.delete("q");
        }
        return next;
      },
      { replace: true },
    );
  }, [debouncedSearch, setSearchParams]);

  // Search input ref for keyboard shortcut
  const searchRef = useRef<HTMLInputElement>(null);

  const handleJumpToTier = useCallback(
    (tier: Tier) => {
      setFocusTier(tier);
      setFocusTrigger((n) => n + 1);
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("focus", `tier-${tier}`);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  function handleNavigate(entityId: string) {
    navigate(`/entities/${entityId}`);
  }

  function handleTierExpand(tier: Tier) {
    setExpandedTiers((prev) => {
      const next = new Set(prev);
      if (next.has(tier)) {
        next.delete(tier);
      } else {
        next.add(tier);
      }
      return next;
    });
  }

  function handleResetAllExpanded() {
    setExpandedTiers(new Set());
  }

  // Keyboard shortcuts
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName;
      const isInput = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";

      if (e.key === "/" && !isInput) {
        e.preventDefault();
        searchRef.current?.focus();
        return;
      }

      if (e.key === "Escape") {
        if (searchInput) {
          setSearchInput("");
          searchRef.current?.blur();
        } else {
          navigate(-1);
        }
        return;
      }

      // 1–6: jump to tier by index
      const digitMatch = /^[1-6]$/.exec(e.key);
      if (digitMatch && !isInput) {
        const idx = Number(e.key) - 1;
        handleJumpToTier(TIERS[idx]);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [searchInput, navigate, handleJumpToTier]);

  // The canvas key re-mounts when stage dimensions change to reset internal state.
  // focusTrigger is passed as a separate prop so repeated jumps to the same tier
  // re-fire the animation without unmounting the canvas (preserving pan/zoom state).
  const canvasKey = `${stageSize.width}x${stageSize.height}`;

  return (
    <div
      ref={fillRef}
      className="flex flex-col space-y-4"
      style={{ height: fillHeight ?? undefined }}
    >
      {/* Controls bar: search, jump-to-tier, legend, and pin note */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          {/* Search input */}
          <div className="relative sm:w-64 shrink-0">
            <SearchIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
            <Input
              ref={searchRef}
              placeholder="Search contacts... (/)"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="pl-8 h-8 text-sm"
              aria-label="Search contacts"
            />
          </div>
          {/* Pinned-override note — only shown when at least one contact has a manual tier */}
          {hasPinnedOverride && (
            <p className="text-muted-foreground text-xs flex items-center gap-0.5">
              <PinIcon className="inline h-3 w-3" />
              Dashed border means manually pinned tier.
            </p>
          )}
        </div>

        {/* Jump-to-tier + legend row */}
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <JumpToTierChips onJump={handleJumpToTier} activeTier={focusTier} />
          {!isLoading && !isError && <TierLegend tierGroups={tierGroups} />}
        </div>

        {/* Expanded-tier pill bar -- only visible when at least one outer tier is expanded */}
        <ExpandedTierPillBar
          expandedTiers={expandedTiers}
          onCollapse={handleTierExpand}
          onResetAll={handleResetAllExpanded}
        />
      </div>

      {/* Canvas area -- no border/card wrapper per impeccable ban on nested cards */}
      <div
        ref={stageRef}
        className="relative flex-1 min-h-0 overflow-hidden"
        style={{ minHeight: 300 }}
      >
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center text-muted-foreground text-sm">
            Loading social map...
          </div>
        )}
        {isError && (
          <div className="absolute inset-0 flex items-center justify-center text-destructive text-sm">
            Couldn't load your social map. Try refreshing.
          </div>
        )}
        {!isLoading && !isError && stageSize.width > 0 && stageSize.height > 0 && (
          <div
            className="w-full h-full"
            style={isColdStart ? { opacity: 0.3, pointerEvents: "none" } : undefined}
            aria-hidden={isColdStart || undefined}
          >
            {isMobile ? (
              <HorizontalStrataCanvas
                key="strata"
                entries={entries}
                ownerEntityId={ownerEntityId}
                ownerName={ownerName}
                width={stageSize.width}
                height={stageSize.height}
                searchQuery={debouncedSearch}
                focusTier={focusTier}
                focusTrigger={focusTrigger}
                expandedTiers={expandedTiers}
                onNavigate={handleNavigate}
                onTierExpand={handleTierExpand}
              />
            ) : (
              <ConcentricCirclesCanvas
                key={canvasKey}
                entries={entries}
                ownerEntityId={ownerEntityId}
                ownerName={ownerName}
                width={stageSize.width}
                height={stageSize.height}
                searchQuery={debouncedSearch}
                focusTier={focusTier}
                focusTrigger={focusTrigger}
                expandedTiers={expandedTiers}
                onNavigate={handleNavigate}
                onTierExpand={handleTierExpand}
              />
            )}
          </div>
        )}
        {!isLoading && !isError && (stageSize.width === 0 || stageSize.height === 0) && (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">
            Sizing canvas...
          </div>
        )}

        {/* Cold-start overlay: actionable empty state centered over the dimmed canvas */}
        {isColdStart && (
          <div className="absolute inset-0 flex items-center justify-center">
            <EmptyStatePanel />
          </div>
        )}
      </div>
    </div>
  );
}
