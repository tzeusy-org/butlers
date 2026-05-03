/**
 * @deprecated No consumers remain. The visualization has been extracted to
 * ConcentricCirclesCanvas.tsx and the page chassis lives in SocialMapPage.tsx.
 * This file is kept only for git history; it can be deleted once the PR is merged.
 *
 * ConcentricCirclesDialog — Dunbar social map visualization (deprecated modal form).
 *
 * Renders a concentric-rings SVG diagram where:
 * - The owner sits at the center
 * - Each ring represents a Dunbar tier (5/15/50/150/500/1500)
 * - Contacts are placed within their tier ring
 * - Progressive detail by tier (avatar+name for 5/15, initials+hover for 50,
 *   count badge + top-5 for 150+)
 * - Manual tier overrides get a pin icon accent
 * - Click-through to /entities/:entityId
 * - Cold-start empty state when < 5 contacts scored
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { CrosshairIcon, PinIcon } from "lucide-react";

import type { DunbarEntry } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useDunbarRanking } from "@/hooks/use-memory";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TIERS = [5, 15, 50, 150, 500, 1500] as const;
type Tier = (typeof TIERS)[number];

const TIER_NAMES: Record<Tier, string> = {
  5: "Support Clique",
  15: "Sympathy Group",
  50: "Good Friends",
  150: "Dunbar's Number",
  500: "Acquaintances",
  1500: "Recognizable",
};

// Ring radii as fractions of the total radius (0 = center, 1 = edge)
// Tiers closer to center get proportionally larger rings to show more detail
const TIER_RADIUS_FRACTIONS: Record<Tier, number> = {
  5: 0.16,
  15: 0.30,
  50: 0.46,
  150: 0.62,
  500: 0.78,
  1500: 0.94,
};

// Single warm hue (terracotta family, h≈35) with chroma + lightness falloff:
// intimacy reads as saturation, not as a rainbow gradient. Owner stays violet
// so the center reads as distinct from the innermost tier.
const TIER_RING_COLORS: Record<Tier, string> = {
  5: "oklch(0.50 0.18 35)",
  15: "oklch(0.58 0.16 35)",
  50: "oklch(0.65 0.13 35)",
  150: "oklch(0.71 0.10 35)",
  500: "oklch(0.76 0.06 35)",
  1500: "oklch(0.80 0.02 35)",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

/** Place N nodes evenly around a circle of radius r, centered at cx,cy. */
function circlePositions(
  n: number,
  r: number,
  cx: number,
  cy: number,
  angleOffset = 0,
): Array<{ x: number; y: number }> {
  return Array.from({ length: n }, (_, i) => {
    const angle = angleOffset + (i * 2 * Math.PI) / n - Math.PI / 2;
    return {
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
    };
  });
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface TierNodeProps {
  entry: DunbarEntry;
  x: number;
  y: number;
  tier: Tier;
  showName: boolean;
  radius: number;
  onNavigate: (entityId: string) => void;
}

function TierNode({ entry, x, y, tier, showName, radius, onNavigate }: TierNodeProps) {
  const initials = getInitials(entry.canonical_name);
  const color = TIER_RING_COLORS[tier];
  // Show avatar image for tier 5/15 when available; fall back to initials.
  const showAvatar = showName && !!entry.avatar_url;
  const clipId = `avatar-clip-${entry.entity_id}`;

  return (
    <TooltipProvider key={entry.entity_id}>
      <Tooltip>
        <TooltipTrigger asChild>
          <g
            style={{ cursor: "pointer" }}
            onClick={() => onNavigate(entry.entity_id)}
          >
            {/* Clip path so avatar image is masked to the circle */}
            {showAvatar && (
              <defs>
                <clipPath id={clipId}>
                  <circle cx={x} cy={y} r={radius} />
                </clipPath>
              </defs>
            )}
            {/* Background circle */}
            <circle
              cx={x}
              cy={y}
              r={radius}
              fill={color}
              fillOpacity={0.15}
              stroke={color}
              strokeWidth={entry.dunbar_tier_override ? 2 : 1}
              strokeDasharray={entry.dunbar_tier_override ? "3,2" : undefined}
            />
            {/* Initials text — always rendered; avatar overlays it when loaded */}
            <text
              x={x}
              y={y}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={radius * 0.85}
              fontWeight="600"
              fill={color}
            >
              {initials}
            </text>
            {/* Avatar image — overlaid on top of initials so initials show on load failure */}
            {showAvatar && (
              <image
                href={entry.avatar_url!}
                x={x - radius}
                y={y - radius}
                width={radius * 2}
                height={radius * 2}
                clipPath={`url(#${clipId})`}
                preserveAspectRatio="xMidYMid slice"
              />
            )}
            {/* Pin icon indicator for overrides */}
            {entry.dunbar_tier_override && (
              <circle
                cx={x + radius * 0.7}
                cy={y - radius * 0.7}
                r={radius * 0.4}
                fill={color}
              />
            )}
            {/* Name label below node (only for detail tiers) */}
            {showName && (
              <text
                x={x}
                y={y + radius + 11}
                textAnchor="middle"
                dominantBaseline="hanging"
                fontSize={10}
                fill="currentColor"
                opacity={0.85}
              >
                {entry.canonical_name.length > 12
                  ? entry.canonical_name.slice(0, 11) + "…"
                  : entry.canonical_name}
              </text>
            )}
          </g>
        </TooltipTrigger>
        <TooltipContent side="top" sideOffset={8}>
          <p className="font-medium">{entry.canonical_name}</p>
          <p className="text-xs opacity-75">
            Tier {tier} · Score {entry.dunbar_score.toFixed(2)}
            {entry.dunbar_tier_override && " · pinned"}
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ---------------------------------------------------------------------------
// Main visualization
// ---------------------------------------------------------------------------

interface ConcentricCirclesVisualizationProps {
  entries: DunbarEntry[];
  ownerEntityId: string | null;
  ownerName: string;
  width: number;
  height: number;
  onNavigate: (entityId: string) => void;
}

const MIN_SCALE = 0.5;
const MAX_SCALE = 8;

function ConcentricCirclesVisualization({
  entries,
  ownerEntityId,
  ownerName,
  width,
  height,
  onNavigate,
}: ConcentricCirclesVisualizationProps) {
  const [expandedTier, setExpandedTier] = useState<Tier | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [viewBox, setViewBox] = useState({ x: 0, y: 0, w: width, h: height });
  const dragRef = useRef<{
    startClientX: number;
    startClientY: number;
    startVB: { x: number; y: number; w: number; h: number };
    moved: boolean;
  } | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  // Wheel zoom around cursor. Use a non-passive native listener since React
  // attaches onWheel as passive (preventDefault becomes a no-op).
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    function onWheel(e: WheelEvent) {
      e.preventDefault();
      const rect = svg!.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      setViewBox((vb) => {
        const svgX = vb.x + (mx / rect.width) * vb.w;
        const svgY = vb.y + (my / rect.height) * vb.h;
        const currentScale = width / vb.w;
        const factor = Math.exp(-e.deltaY * 0.0015);
        const target = Math.max(MIN_SCALE, Math.min(MAX_SCALE, currentScale * factor));
        const newW = width / target;
        const newH = height / target;
        const newX = svgX - (mx / rect.width) * newW;
        const newY = svgY - (my / rect.height) * newH;
        return { x: newX, y: newY, w: newW, h: newH };
      });
    }
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [width, height]);

  function handleMouseDown(e: React.MouseEvent<SVGSVGElement>) {
    // Only initiate pan with primary button; let node clicks bubble through.
    if (e.button !== 0) return;
    dragRef.current = {
      startClientX: e.clientX,
      startClientY: e.clientY,
      startVB: viewBox,
      moved: false,
    };
  }

  function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const dxClient = e.clientX - drag.startClientX;
    const dyClient = e.clientY - drag.startClientY;
    if (!drag.moved && Math.hypot(dxClient, dyClient) > 3) {
      drag.moved = true;
      setIsDragging(true);
    }
    if (!drag.moved) return;
    const dx = (dxClient * drag.startVB.w) / rect.width;
    const dy = (dyClient * drag.startVB.h) / rect.height;
    setViewBox({
      ...drag.startVB,
      x: drag.startVB.x - dx,
      y: drag.startVB.y - dy,
    });
  }

  function endDrag() {
    dragRef.current = null;
    setIsDragging(false);
  }

  function resetView() {
    setViewBox({ x: 0, y: 0, w: width, h: height });
  }

  const cx = width / 2;
  const cy = height / 2;
  const maxR = Math.min(cx, cy) - 24; // 24px padding
  const currentScale = width / viewBox.w;

  // Group entries by tier (exclude owner)
  const tierGroups: Record<Tier, DunbarEntry[]> = {
    5: [],
    15: [],
    50: [],
    150: [],
    500: [],
    1500: [],
  };
  for (const entry of entries) {
    if (entry.entity_id === ownerEntityId) continue;
    const tier = entry.dunbar_tier as Tier;
    if (tierGroups[tier]) {
      tierGroups[tier].push(entry);
    }
  }

  // Check cold start: fewer than 5 scored contacts (score > 0)
  const scoredCount = entries.filter(
    (e) => e.dunbar_score > 0 && e.entity_id !== ownerEntityId,
  ).length;
  const isColdStart = scoredCount < 5;

  // Click handlers that respect drag-guard (so dragging doesn't navigate).
  const navigateGuarded = (id: string) => {
    if (dragRef.current?.moved) return;
    onNavigate(id);
  };

  return (
    <div className="relative w-full h-full">
      <div className="absolute top-2 right-2 z-10 flex items-center gap-1.5 rounded-md border bg-background/80 backdrop-blur px-2 py-1 text-xs text-muted-foreground shadow-sm">
        <span>Scroll to zoom · drag to pan</span>
        <button
          type="button"
          onClick={resetView}
          className="ml-1 rounded px-1.5 py-0.5 text-xs font-medium text-foreground hover:bg-accent"
        >
          Reset
        </button>
        <span className="ml-1 tabular-nums">{currentScale.toFixed(1)}×</span>
      </div>
      <svg
        ref={svgRef}
        width="100%"
        height="100%"
        viewBox={`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`}
        preserveAspectRatio="xMidYMid meet"
        className="select-none touch-none"
        style={{ cursor: isDragging ? "grabbing" : "grab", display: "block" }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
        role="img"
        aria-label="Dunbar social map: concentric rings of contacts"
      >
        {/* Tier rings (outermost first so inner rings draw on top) */}
        {[...TIERS].reverse().map((tier) => {
          const r = maxR * TIER_RADIUS_FRACTIONS[tier];
          const color = TIER_RING_COLORS[tier];
          const count = tierGroups[tier].length;
          return (
            <g key={tier}>
              <circle
                cx={cx}
                cy={cy}
                r={r}
                fill={color}
                fillOpacity={0.04}
                stroke={color}
                strokeWidth={0.75}
                strokeOpacity={0.4}
              />
              {/* Tier label at top of ring */}
              <text
                x={cx}
                y={cy - r + 14}
                textAnchor="middle"
                fontSize={13}
                fontWeight={600}
                fill={color}
                opacity={0.85}
              >
                {TIER_NAMES[tier]} ({count})
              </text>
            </g>
          );
        })}

        {/* Owner center node */}
        <g
          style={{ cursor: ownerEntityId ? "pointer" : "default" }}
          onClick={() => ownerEntityId && navigateGuarded(ownerEntityId)}
        >
          <circle
            cx={cx}
            cy={cy}
            r={maxR * 0.07}
            fill="oklch(0.55 0.18 290)"
            fillOpacity={0.2}
            stroke="oklch(0.55 0.18 290)"
            strokeWidth={1.5}
          />
          <text
            x={cx}
            y={cy}
            textAnchor="middle"
            dominantBaseline="central"
            fontSize={maxR * 0.07 * 0.7}
            fontWeight="700"
            fill="oklch(0.55 0.18 290)"
          >
            {getInitials(ownerName)}
          </text>
          <text
            x={cx}
            y={cy + maxR * 0.07 + 11}
            textAnchor="middle"
            dominantBaseline="hanging"
            fontSize={10}
            fill="currentColor"
            opacity={0.7}
          >
            You
          </text>
        </g>

        {/* Contact nodes per tier */}
        {TIERS.map((tier) => {
          const tierEntries = tierGroups[tier];
          if (tierEntries.length === 0) return null;

          const tierR = maxR * TIER_RADIUS_FRACTIONS[tier];
          // Previous tier radius (for placing nodes between rings)
          const prevTierIdx = TIERS.indexOf(tier) - 1;
          const prevR = prevTierIdx >= 0 ? maxR * TIER_RADIUS_FRACTIONS[TIERS[prevTierIdx]] : maxR * 0.07;
          const nodeR = (tierR - prevR) / 2;
          const nodeRadius = Math.max(4, Math.min(nodeR * 0.65, tier <= 15 ? 18 : 12));
          const ringR = prevR + nodeR;

          // For tiers 5 and 15: show all with name
          // For tier 50+: show top 5 or all if expanded, with count badge
          // (tier 50 ring only fits ~22 nodes; cap to prevent overlap)
          const showName = tier <= 15;
          const showAll = tier <= 15 || expandedTier === tier;
          const displayEntries = showAll ? tierEntries : tierEntries.slice(0, 5);
          const hiddenCount = showAll ? 0 : tierEntries.length - 5;

          const positions = circlePositions(displayEntries.length, ringR, cx, cy);

          return (
            <g key={tier}>
              {displayEntries.map((entry, i) => (
                <TierNode
                  key={entry.entity_id}
                  entry={entry}
                  x={positions[i].x}
                  y={positions[i].y}
                  tier={tier}
                  showName={showName}
                  radius={nodeRadius}
                  onNavigate={navigateGuarded}
                />
              ))}
              {/* "+N more" badge for large tiers when not expanded */}
              {hiddenCount > 0 && (
                <g
                  style={{ cursor: "pointer" }}
                  onClick={() => setExpandedTier(expandedTier === tier ? null : tier)}
                >
                  <circle
                    cx={cx + ringR}
                    cy={cy}
                    r={nodeRadius}
                    fill={TIER_RING_COLORS[tier]}
                    fillOpacity={0.25}
                    stroke={TIER_RING_COLORS[tier]}
                    strokeWidth={1}
                  />
                  <text
                    x={cx + ringR}
                    y={cy}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fontSize={9}
                    fontWeight="700"
                    fill={TIER_RING_COLORS[tier]}
                  >
                    +{hiddenCount}
                  </text>
                </g>
              )}
            </g>
          );
        })}

        {/* Cold start overlay */}
        {isColdStart && (
          <g>
            <rect
              x={cx - 140}
              y={cy + maxR * 0.12}
              width={280}
              height={52}
              rx={8}
              fill="var(--background)"
              fillOpacity={0.9}
              stroke="var(--border)"
              strokeWidth={1}
            />
            <text
              x={cx}
              y={cy + maxR * 0.12 + 18}
              textAnchor="middle"
              dominantBaseline="hanging"
              fontSize={13}
              fontWeight={500}
              fill="currentColor"
              opacity={0.7}
            >
              Interact with your contacts to see
            </text>
            <text
              x={cx}
              y={cy + maxR * 0.12 + 36}
              textAnchor="middle"
              dominantBaseline="hanging"
              fontSize={13}
              fontWeight={500}
              fill="currentColor"
              opacity={0.7}
            >
              your social map take shape
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tier legend
// ---------------------------------------------------------------------------

function TierLegend({ tierGroups }: { tierGroups: Record<Tier, DunbarEntry[]> }) {
  return (
    <div className="flex flex-wrap gap-2 mt-2 justify-center">
      {TIERS.map((tier) => {
        const count = tierGroups[tier].length;
        const color = TIER_RING_COLORS[tier];
        return (
          <div key={tier} className="flex items-center gap-1 text-xs">
            <span
              className="inline-block w-2.5 h-2.5 rounded-full"
              style={{ backgroundColor: color, opacity: 0.7 }}
            />
            <span className="text-muted-foreground">
              {TIER_NAMES[tier]}
            </span>
            <span className="font-medium">{count}</span>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Public dialog component
// ---------------------------------------------------------------------------

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

export function ConcentricCirclesDialog() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const { data, isLoading, isError } = useDunbarRanking(open);
  const { ref: stageRef, size: stageSize } = useElementSize();

  const entries = data?.entries ?? [];
  const ownerEntityId = data?.owner_entity_id ?? null;
  const ownerEntry = entries.find((e) => e.entity_id === ownerEntityId);
  const ownerName = ownerEntry?.canonical_name ?? "You";

  // Group by tier for legend
  const tierGroups: Record<Tier, DunbarEntry[]> = {
    5: [],
    15: [],
    50: [],
    150: [],
    500: [],
    1500: [],
  };
  for (const entry of entries) {
    if (entry.entity_id === ownerEntityId) continue;
    const tier = entry.dunbar_tier as Tier;
    if (tierGroups[tier]) {
      tierGroups[tier].push(entry);
    }
  }

  function handleNavigate(entityId: string) {
    setOpen(false);
    navigate(`/entities/${entityId}`);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <CrosshairIcon className="h-4 w-4 mr-1.5" />
          Concentric Circles
        </Button>
      </DialogTrigger>
      <DialogContent
        className="sm:max-w-none max-w-none"
        style={{ width: "60vw", height: "80vh", display: "flex", flexDirection: "column" }}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            Concentric Circles
          </DialogTitle>
          <DialogDescription>
            Your contacts arranged by Dunbar tier: rings represent intimacy
            layers from inner circle (5) out to acquaintances (1500).
            {entries.some((e) => e.dunbar_tier_override) && (
              <span className="ml-1">
                <PinIcon className="inline h-3 w-3 mr-0.5" />
                Dashed border = manually pinned tier.
              </span>
            )}
          </DialogDescription>
        </DialogHeader>

        <div
          style={{ flex: "1 1 0", minHeight: 0, display: "flex", flexDirection: "column", gap: "0.5rem" }}
        >
          {isLoading && (
            <div className="flex items-center justify-center flex-1 text-muted-foreground text-sm">
              Loading social map…
            </div>
          )}
          {isError && (
            <div className="flex items-center justify-center flex-1 text-destructive text-sm">
              Failed to load social map. Is the relationship butler running?
            </div>
          )}
          {!isLoading && !isError && (
            <>
              <div
                ref={stageRef}
                className="relative w-full overflow-hidden"
                style={{ flex: "1 1 0", minHeight: 0 }}
              >
                {stageSize.width > 0 && stageSize.height > 0 ? (
                  <ConcentricCirclesVisualization
                    key={`${stageSize.width}x${stageSize.height}`}
                    entries={entries}
                    ownerEntityId={ownerEntityId}
                    ownerName={ownerName}
                    width={stageSize.width}
                    height={stageSize.height}
                    onNavigate={handleNavigate}
                  />
                ) : (
                  <div className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">
                    Sizing canvas…
                  </div>
                )}
              </div>
              <TierLegend tierGroups={tierGroups} />
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
