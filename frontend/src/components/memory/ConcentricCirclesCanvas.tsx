/**
 * ConcentricCirclesCanvas -- pure Dunbar social-map visualization.
 *
 * Stateless except for local pan/zoom state.
 * Receives entries, owner info, dimensions, search query, and focusTier
 * from the parent page. Non-matching nodes dim to 30% opacity.
 *
 * URL contract (owned by SocialMapPage, not this component):
 *   ?focus=tier-{N}   — integer N is one of 5|15|50|150|500|1500
 *   ?q={search}       — raw search string
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";

import type { DunbarEntry } from "@/api/types";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  circlePositions,
  easeOutExpo,
  getInitials,
  matchesSearch,
  OWNER_COLOR,
  TIER_BADGE_ANGLES,
  TIER_NAMES,
  TIER_RADIUS_FRACTIONS,
  TIER_RING_COLORS,
  TIERS,
  type Tier,
} from "./concentric-circles-constants";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MIN_SCALE = 0.5;
const MAX_SCALE = 8;

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
  dimmed: boolean;
  onNavigate: (entityId: string) => void;
}

function TierNode({ entry, x, y, tier, showName, radius, dimmed, onNavigate }: TierNodeProps) {
  const initials = getInitials(entry.canonical_name);
  const color = TIER_RING_COLORS[tier];
  const showAvatar = showName && !!entry.avatar_url;
  const clipId = `avatar-clip-${entry.entity_id}`;

  return (
    <Tooltip key={entry.entity_id}>
      <TooltipTrigger asChild>
        <g
          style={{ cursor: "pointer", opacity: dimmed ? 0.3 : 1, transition: "opacity 150ms ease" }}
          onClick={() => onNavigate(entry.entity_id)}
        >
            {showAvatar && (
              <defs>
                <clipPath id={clipId}>
                  <circle cx={x} cy={y} r={radius} />
                </clipPath>
              </defs>
            )}
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
            {entry.dunbar_tier_override && (
              <circle
                cx={x + radius * 0.7}
                cy={y - radius * 0.7}
                r={radius * 0.4}
                fill={color}
              />
            )}
            {showName && (
              <text
                x={x}
                y={y + radius + 9}
                textAnchor="middle"
                dominantBaseline="hanging"
                fontSize={8}
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
  );
}

// ---------------------------------------------------------------------------
// Canvas props
// ---------------------------------------------------------------------------

export interface ConcentricCirclesCanvasProps {
  entries: DunbarEntry[];
  ownerEntityId: string | null;
  ownerName: string;
  width: number;
  height: number;
  /** Debounced search query -- empty string means no filter. */
  searchQuery: string;
  /** Tier to animate the viewport toward. Null = no pending focus. */
  focusTier: Tier | null;
  /**
   * Monotonic counter incremented each time the parent wants to (re-)trigger
   * the jump animation. Incrementing this even with the same focusTier fires
   * the animation again without remounting the canvas.
   */
  focusTrigger: number;
  /** Tiers currently expanded to show all contacts (not just top 5). */
  expandedTiers: Set<Tier>;
  onNavigate: (entityId: string) => void;
  onTierExpand: (tier: Tier) => void;
}

// ---------------------------------------------------------------------------
// Canvas component
// ---------------------------------------------------------------------------

export function ConcentricCirclesCanvas({
  entries,
  ownerEntityId,
  ownerName,
  width,
  height,
  searchQuery,
  focusTier,
  focusTrigger,
  expandedTiers,
  onNavigate,
  onTierExpand,
}: ConcentricCirclesCanvasProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [viewBox, setViewBox] = useState({ x: 0, y: 0, w: width, h: height });
  // Keep a ref mirror of viewBox so the focusTier animation can read it
  // without becoming a stale-closure dependency.
  const viewBoxRef = useRef(viewBox);
  // Sync the ref after every render using a layout effect (not during render)
  useLayoutEffect(() => {
    viewBoxRef.current = viewBox;
  });

  const dragRef = useRef<{
    startClientX: number;
    startClientY: number;
    startVB: { x: number; y: number; w: number; h: number };
    moved: boolean;
  } | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const animFrameRef = useRef<number | null>(null);

  // Derived geometry
  const cx = width / 2;
  const cy = height / 2;
  const maxR = Math.min(cx, cy) - 24; // 24px padding

  // Wheel zoom -- non-passive native listener
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

  // Animate jump-to-tier: ease-out-expo pan + zoom to center the ring.
  // focusTrigger is a monotonic counter incremented by the parent each time it
  // wants to fire the animation, including repeated jumps to the same tier.
  // Reads the current viewBox via ref to avoid stale closure issues.
  useEffect(() => {
    if (focusTier === null) return;

    const tierR = maxR * TIER_RADIUS_FRACTIONS[focusTier];
    const targetScale = (Math.min(width, height) * 0.7) / (tierR * 2);
    const clampedScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, targetScale));
    const targetW = width / clampedScale;
    const targetH = height / clampedScale;
    const targetX = cx - targetW / 2;
    const targetY = cy - targetH / 2;

    // Read the current viewBox from the ref (not from a setState callback)
    const startVB = { ...viewBoxRef.current };
    const DURATION = 500;
    const start = performance.now();

    if (animFrameRef.current !== null) cancelAnimationFrame(animFrameRef.current);

    function step() {
      const elapsed = performance.now() - start;
      const t = Math.min(elapsed / DURATION, 1);
      const ease = easeOutExpo(t);
      setViewBox({
        x: startVB.x + (targetX - startVB.x) * ease,
        y: startVB.y + (targetY - startVB.y) * ease,
        w: startVB.w + (targetW - startVB.w) * ease,
        h: startVB.h + (targetH - startVB.h) * ease,
      });
      if (t < 1) animFrameRef.current = requestAnimationFrame(step);
    }
    animFrameRef.current = requestAnimationFrame(step);
    return () => {
      if (animFrameRef.current !== null) cancelAnimationFrame(animFrameRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusTrigger, cx, cy, maxR, width, height]);

  function handleMouseDown(e: React.MouseEvent<SVGSVGElement>) {
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
    if (tierGroups[tier]) tierGroups[tier].push(entry);
  }

  const scoredCount = entries.filter(
    (e) => e.dunbar_score > 0 && e.entity_id !== ownerEntityId,
  ).length;
  const isColdStart = scoredCount < 5;

  const navigateGuarded = (id: string) => {
    if (dragRef.current?.moved) return;
    onNavigate(id);
  };

  return (
    <TooltipProvider>
    <div className="relative w-full h-full">
      <div className="absolute top-2 right-2 z-10 flex items-center gap-1.5 rounded-md border bg-background/80 backdrop-blur px-2 py-1 text-xs text-muted-foreground shadow-sm">
        <span>Scroll to zoom &middot; drag to pan</span>
        <button
          type="button"
          onClick={resetView}
          className="ml-1 rounded px-1.5 py-0.5 text-xs font-medium text-foreground hover:bg-accent"
        >
          Reset
        </button>
        <span className="ml-1 tabular-nums">{currentScale.toFixed(1)}&times;</span>
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
        aria-label="Dunbar social map -- concentric rings of contacts"
      >
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
                fillOpacity={0.07}
                stroke={color}
                strokeWidth={0.75}
                strokeOpacity={0.6}
              />
              <text
                x={cx}
                y={cy - r + 12}
                textAnchor="middle"
                fontSize={9}
                fontWeight="700"
                fill={color}
                opacity={1.0}
              >
                {TIER_NAMES[tier]} ({count})
              </text>
            </g>
          );
        })}

        <g
          style={{ cursor: ownerEntityId ? "pointer" : "default" }}
          onClick={() => ownerEntityId && navigateGuarded(ownerEntityId)}
        >
          <circle
            cx={cx}
            cy={cy}
            r={maxR * 0.07}
            fill={OWNER_COLOR}
            fillOpacity={0.2}
            stroke={OWNER_COLOR}
            strokeWidth={1.5}
          />
          <text
            x={cx}
            y={cy}
            textAnchor="middle"
            dominantBaseline="central"
            fontSize={9}
            fontWeight="700"
            fill={OWNER_COLOR}
          >
            {getInitials(ownerName)}
          </text>
          <text
            x={cx}
            y={cy + maxR * 0.07 + 9}
            textAnchor="middle"
            dominantBaseline="hanging"
            fontSize={8}
            fill={OWNER_COLOR}
            opacity={0.85}
          >
            You
          </text>
        </g>

        {TIERS.map((tier) => {
          const tierEntries = tierGroups[tier];
          if (tierEntries.length === 0) return null;

          const tierR = maxR * TIER_RADIUS_FRACTIONS[tier];
          const prevTierIdx = TIERS.indexOf(tier) - 1;
          const prevR = prevTierIdx >= 0
            ? maxR * TIER_RADIUS_FRACTIONS[TIERS[prevTierIdx]]
            : maxR * 0.07;
          const nodeR = (tierR - prevR) / 2;
          const nodeRadius = Math.max(4, Math.min(nodeR * 0.65, tier <= 15 ? 18 : 12));
          const ringR = prevR + nodeR;

          const showName = tier <= 15;
          // Always show all entries when a search is active so matches in
          // collapsed outer rings are not hidden from the user.
          const isExpanded = tier <= 15 || expandedTiers.has(tier);
          const showAll = isExpanded || searchQuery.length > 0;
          const displayEntries = showAll ? tierEntries : tierEntries.slice(0, 5);
          const hiddenCount = showAll ? 0 : tierEntries.length - 5;

          const positions = circlePositions(displayEntries.length, ringR, cx, cy);
          const color = TIER_RING_COLORS[tier];

          return (
            <g key={tier}>
              {displayEntries.map((entry, i) => {
                const dimmed = searchQuery.length > 0 && !matchesSearch(entry, searchQuery);
                return (
                  <TierNode
                    key={entry.entity_id}
                    entry={entry}
                    x={positions[i].x}
                    y={positions[i].y}
                    tier={tier}
                    showName={showName}
                    radius={nodeRadius}
                    dimmed={dimmed}
                    onNavigate={navigateGuarded}
                  />
                );
              })}
              {/* Show "+N" expand button for collapsed tiers at a per-tier compass angle */}
              {hiddenCount > 0 && (() => {
                const badgeAngle = TIER_BADGE_ANGLES[tier] ?? 0;
                const bx = cx + ringR * Math.cos(badgeAngle);
                const by = cy + ringR * Math.sin(badgeAngle);
                return (
                  <g
                    style={{ cursor: "pointer" }}
                    role="button"
                    tabIndex={0}
                    onClick={() => onTierExpand(tier)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onTierExpand(tier); } }}
                    aria-label={`Show ${hiddenCount} more contacts in ${TIER_NAMES[tier]}`}
                  >
                    <circle
                      cx={bx}
                      cy={by}
                      r={nodeRadius}
                      fill={color}
                      fillOpacity={0.25}
                      stroke={color}
                      strokeWidth={1}
                    />
                    <text
                      x={bx}
                      y={by}
                      textAnchor="middle"
                      dominantBaseline="central"
                      fontSize={7}
                      fontWeight="700"
                      fill={color}
                    >
                      +{hiddenCount}
                    </text>
                  </g>
                );
              })()}
            </g>
          );
        })}

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
              fontSize={10}
              fill="currentColor"
              opacity={0.6}
            >
              Interact with your contacts to see
            </text>
            <text
              x={cx}
              y={cy + maxR * 0.12 + 32}
              textAnchor="middle"
              dominantBaseline="hanging"
              fontSize={10}
              fill="currentColor"
              opacity={0.6}
            >
              your social map take shape
            </text>
          </g>
        )}
      </svg>
    </div>
    </TooltipProvider>
  );
}
