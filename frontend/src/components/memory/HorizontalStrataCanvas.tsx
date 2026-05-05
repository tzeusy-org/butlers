/**
 * HorizontalStrataCanvas -- mobile-first Dunbar social-map layout.
 *
 * Six horizontal bands stacked top-to-bottom, each representing a Dunbar tier.
 * The owner appears as a fixed header badge above the bands. Contacts are
 * placed as inline nodes within their tier band -- the same visual treatment
 * as the concentric rings (initials, color, tooltip).
 *
 * Why strata, not rings, at mobile: six concentric rings on a 390px viewport
 * (stage ≈ 234px after layout) produce a maxR of ≈93px. The innermost nodes
 * are unreadably small. A strata layout gives every tier a guaranteed height
 * band and scales gracefully.
 *
 * Prop contract is identical to ConcentricCirclesCanvas so SocialMapPage can
 * swap between them without extra wiring.
 */

import type { DunbarEntry } from "@/api/types";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  getInitials,
  matchesSearch,
  OWNER_COLOR,
  TIER_NAMES,
  TIER_RING_COLORS,
  TIERS,
  type Tier,
} from "./concentric-circles-constants";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// Same prop contract as ConcentricCirclesCanvas.
export interface HorizontalStrataCanvasProps {
  entries: DunbarEntry[];
  ownerEntityId: string | null;
  ownerName: string;
  /** width and height are accepted for API parity but the strata layout uses
   * CSS flex instead of fixed SVG dimensions. */
  width: number;
  height: number;
  searchQuery: string;
  focusTier: Tier | null;
  focusTrigger: number;
  expandedTiers: Set<Tier>;
  onNavigate: (entityId: string) => void;
  onTierExpand: (tier: Tier) => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const NODE_RADIUS = 20; // px, fixed radius for strata nodes
const NODE_DIAMETER = NODE_RADIUS * 2;

// ---------------------------------------------------------------------------
// StrataNode -- single contact node within a band
// ---------------------------------------------------------------------------

interface StrataNodeProps {
  entry: DunbarEntry;
  tier: Tier;
  dimmed: boolean;
  onNavigate: (entityId: string) => void;
}

function StrataNode({ entry, tier, dimmed, onNavigate }: StrataNodeProps) {
  const initials = getInitials(entry.canonical_name);
  const color = TIER_RING_COLORS[tier];

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={() => onNavigate(entry.entity_id)}
          style={{
            opacity: dimmed ? 0.3 : 1,
            transition: "opacity 150ms ease",
            cursor: "pointer",
            background: "none",
            border: "none",
            padding: 0,
            flexShrink: 0,
          }}
          aria-label={entry.canonical_name}
        >
          <svg
            width={NODE_DIAMETER}
            height={NODE_DIAMETER}
            viewBox={`0 0 ${NODE_DIAMETER} ${NODE_DIAMETER}`}
            overflow="visible"
            role="img"
            aria-hidden="true"
          >
            <circle
              cx={NODE_RADIUS}
              cy={NODE_RADIUS}
              r={NODE_RADIUS - 1}
              fill={color}
              fillOpacity={0.15}
              stroke={color}
              strokeWidth={1}
            />
            <text
              x={NODE_RADIUS}
              y={NODE_RADIUS}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={NODE_RADIUS * 0.85}
              fontWeight="600"
              fill={color}
            >
              {initials}
            </text>
            {entry.dunbar_tier_override && (
              // Pin override: dashed ring at radius*1.2, outside the node
              // boundary. Matches ConcentricCirclesCanvas. overflow="visible"
              // on the SVG lets the ring extend beyond the fixed viewBox.
              <circle
                cx={NODE_RADIUS}
                cy={NODE_RADIUS}
                r={NODE_RADIUS * 1.2}
                fill="none"
                stroke={color}
                strokeWidth={1.5}
                strokeDasharray="4,3"
              />
            )}
          </svg>
        </button>
      </TooltipTrigger>
      <TooltipContent side="top" sideOffset={4}>
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
// HorizontalStrataCanvas
// ---------------------------------------------------------------------------

export function HorizontalStrataCanvas({
  entries,
  ownerEntityId,
  ownerName,
  searchQuery,
  expandedTiers,
  onNavigate,
  onTierExpand,
}: HorizontalStrataCanvasProps) {
  // Group entries by tier, excluding the owner.
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

  const ownerInitials = getInitials(ownerName);

  return (
    <TooltipProvider>
      <div
        data-testid="strata-canvas"
        className="flex flex-col gap-0 w-full h-full overflow-y-auto"
        style={{ touchAction: "pan-y" }}
      >
        {/* Owner header badge */}
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border/40">
          <svg
            width={NODE_DIAMETER}
            height={NODE_DIAMETER}
            viewBox={`0 0 ${NODE_DIAMETER} ${NODE_DIAMETER}`}
            role="img"
            aria-label="You"
          >
            <circle
              cx={NODE_RADIUS}
              cy={NODE_RADIUS}
              r={NODE_RADIUS - 1}
              fill={OWNER_COLOR}
              fillOpacity={0.2}
              stroke={OWNER_COLOR}
              strokeWidth={1.5}
            />
            <text
              x={NODE_RADIUS}
              y={NODE_RADIUS}
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={NODE_RADIUS * 0.75}
              fontWeight="700"
              fill={OWNER_COLOR}
            >
              {ownerInitials}
            </text>
          </svg>
          <div>
            <div className="text-sm font-semibold" style={{ color: OWNER_COLOR }}>
              {ownerName}
            </div>
            <div className="text-xs text-muted-foreground">You · center</div>
          </div>
        </div>

        {/* Tier bands */}
        {TIERS.map((tier) => {
          const tierEntries = tierGroups[tier];
          const color = TIER_RING_COLORS[tier];
          const count = tierEntries.length;

          // Show all when: tier ≤ 15, tier is expanded, or a search is active.
          const isExpanded = tier <= 15 || expandedTiers.has(tier);
          const showAll = isExpanded || searchQuery.length > 0;
          const COLLAPSE_LIMIT = 5;
          const displayEntries = showAll ? tierEntries : tierEntries.slice(0, COLLAPSE_LIMIT);
          const hiddenCount = showAll ? 0 : tierEntries.length - COLLAPSE_LIMIT;

          return (
            <div
              key={tier}
              className="flex flex-col border-b border-border/40 last:border-b-0"
              style={{ backgroundColor: `${color}06` }}
            >
              {/* Band header */}
              <div className="flex items-center justify-between px-3 py-1.5">
                <div className="flex items-center gap-1.5">
                  <span
                    className="inline-block w-2 h-2 rounded-full"
                    style={{ backgroundColor: color, opacity: 0.7 }}
                  />
                  <span
                    className="text-xs font-semibold"
                    style={{ color }}
                  >
                    {TIER_NAMES[tier]}
                  </span>
                </div>
                <span className="text-xs text-muted-foreground">{count}</span>
              </div>

              {/* Node row */}
              {count > 0 ? (
                <div
                  className="flex flex-row flex-wrap items-center gap-1.5 px-3 pb-2"
                  role="list"
                  aria-label={`${TIER_NAMES[tier]} contacts`}
                >
                  {displayEntries.map((entry) => {
                    const dimmed = searchQuery.length > 0 && !matchesSearch(entry, searchQuery);
                    return (
                      <div key={entry.entity_id} role="listitem">
                        <StrataNode
                          entry={entry}
                          tier={tier}
                          dimmed={dimmed}
                          onNavigate={onNavigate}
                        />
                      </div>
                    );
                  })}
                  {hiddenCount > 0 && (
                    <button
                      type="button"
                      onClick={() => onTierExpand(tier)}
                      className="inline-flex items-center justify-center rounded-full text-xs font-bold transition-opacity hover:opacity-80"
                      style={{
                        width: NODE_DIAMETER,
                        height: NODE_DIAMETER,
                        backgroundColor: `${color}25`,
                        border: `1px solid ${color}`,
                        color,
                        flexShrink: 0,
                      }}
                      aria-label={`Show ${hiddenCount} more contacts in ${TIER_NAMES[tier]}`}
                    >
                      +{hiddenCount}
                    </button>
                  )}
                </div>
              ) : (
                <p className="px-3 pb-2 text-xs text-muted-foreground italic">
                  No contacts yet
                </p>
              )}
            </div>
          );
        })}
      </div>
    </TooltipProvider>
  );
}
