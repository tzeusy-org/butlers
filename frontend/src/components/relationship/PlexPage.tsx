/**
 * PlexPage — /entities landing: the owner ego-graph ("plex").
 *
 * PROTOTYPE (proto/entities-plex). Reimagines the /entities landing per the
 * 2026-07-02 critique: exploration-first instead of curation-first.
 *
 * Layout: full-bleed radial canvas + quiet right rail.
 *   - Owner mode (default): contacts on concentric Dunbar tier rings around
 *     the owner; tier 1500 summarized as a periphery count, not drawn.
 *   - Neighbour mode (?center=<id>): the centered entity's neighbours fanned
 *     into predicate sectors; edge opacity carries confidence, node
 *     desaturation carries staleness (two separate manifesto axes).
 *   - Right rail: "Worth attention" (tier-weighted overdue contacts, with
 *     reasons) and per-tier capacity meters ("21 / 50").
 *
 * URL contract:
 *   ?center=<entityId>   re-centered entity (absent = owner)
 *   ?trail=<id,id,...>   hop trail (breadcrumb), oldest first
 *
 * Keyboard (scoped to the canvas container, never window):
 *   Esc     pop one hop off the trail
 *   Enter   open the centered entity's record
 */

import { useCallback, useLayoutEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import type { DunbarEntry } from "@/api/types";
import { EntityMark } from "@/components/ui/EntityMark";
import { Page } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import { useDunbarRanking } from "@/hooks/use-memory";
import {
  useEntityNeighbours,
  useRelationshipEntitiesByIds,
} from "@/hooks/use-entities";
import {
  daysSince,
  layoutNeighbourPlex,
  layoutOwnerPlex,
  PLEX_NODE_TIERS,
  PLEX_PERIPHERY_FRACTION,
  PLEX_RING_FRACTIONS,
  polar,
  prettyPredicate,
  TIER_RING_COLORS,
  TIERS,
  type NeighbourPlexNode,
  type OwnerPlexNode,
  type Staleness,
  type Tier,
} from "@/components/relationship/plex-layout";
import { TIER_NAMES } from "@/components/memory/concentric-circles-constants";

// ---------------------------------------------------------------------------
// Sizing hooks (same pattern as SocialMapView: the overview archetype wrapper
// has auto height, so the canvas needs an explicit viewport-fill height).
// ---------------------------------------------------------------------------

const FILL_BOTTOM_GUTTER = 24;

function useFillViewportHeight() {
  const [height, setHeight] = useState<number | null>(null);
  const [el, setEl] = useState<HTMLElement | null>(null);
  const ref = useCallback((node: HTMLElement | null) => setEl(node), []);
  useLayoutEffect(() => {
    if (!el) return;
    const measure = () => {
      const top = el.getBoundingClientRect().top;
      setHeight(Math.max(360, window.innerHeight - top - FILL_BOTTOM_GUTTER));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [el]);
  return { ref, height };
}

function useElementSize() {
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [el, setEl] = useState<HTMLElement | null>(null);
  const ref = useCallback((node: HTMLElement | null) => setEl(node), []);
  useLayoutEffect(() => {
    if (!el) return;
    const measure = () =>
      setSize({ width: el.clientWidth, height: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return { ref, size };
}

// ---------------------------------------------------------------------------
// URL helpers
// ---------------------------------------------------------------------------

function parseTrail(raw: string | null): string[] {
  if (!raw) return [];
  return raw.split(",").filter(Boolean);
}

// ---------------------------------------------------------------------------
// Staleness rendering: desaturation carries "long unseen", opacity stays for
// confidence (neighbour mode). One visual channel per axis.
// ---------------------------------------------------------------------------

function stalenessStyle(staleness: Staleness): React.CSSProperties {
  switch (staleness) {
    case "hard":
      return { filter: "grayscale(0.85)", opacity: 0.5 };
    case "soft":
      return { filter: "grayscale(0.45)", opacity: 0.78 };
    case "unknown":
      return { opacity: 0.65 };
    default:
      return {};
  }
}

// ---------------------------------------------------------------------------
// Plex node (shared by both modes)
// ---------------------------------------------------------------------------

interface PlexNodeProps {
  entityId: string;
  name: string;
  x: number;
  y: number;
  size: number;
  entityType?: string;
  showLabel: boolean;
  attention?: boolean;
  staleness: Staleness;
  /** Extra opacity multiplier (confidence axis in neighbour mode). */
  conf?: number;
  onCenter: (id: string) => void;
}

function PlexNode({
  entityId,
  name,
  x,
  y,
  size,
  entityType = "person",
  showLabel,
  attention = false,
  staleness,
  conf,
  onCenter,
}: PlexNodeProps) {
  return (
    <button
      type="button"
      data-testid="plex-node"
      aria-label={`Center plex on ${name}`}
      title={name}
      onClick={() => onCenter(entityId)}
      className="group absolute left-0 top-0 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-0.5 transition-transform duration-slow ease-out-quart focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      style={{
        transform: `translate(${x}px, ${y}px) translate(-50%, -50%)`,
        opacity: conf !== undefined ? Math.max(0.45, conf) : undefined,
      }}
    >
      <span
        className={
          attention
            ? "rounded-full p-0.5 outline outline-1 outline-[var(--amber)]"
            : "p-0.5"
        }
        style={stalenessStyle(staleness)}
      >
        <EntityMark name={name} entityType={entityType} size={size} />
      </span>
      {showLabel && (
        <span
          className="max-w-24 truncate text-[10px] leading-tight text-muted-foreground group-hover:text-foreground"
          style={stalenessStyle(staleness)}
        >
          {name}
        </span>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Owner-mode canvas
// ---------------------------------------------------------------------------

function OwnerPlexCanvas({
  nodes,
  tierCounts,
  ownerName,
  width,
  height,
  attentionIds,
  onCenter,
}: {
  nodes: OwnerPlexNode[];
  tierCounts: Record<Tier, number>;
  ownerName: string;
  width: number;
  height: number;
  attentionIds: Set<string>;
  onCenter: (id: string) => void;
}) {
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.min(width, height) / 2 - 28;

  return (
    <>
      <svg
        className="absolute inset-0"
        width={width}
        height={height}
        aria-hidden="true"
      >
        {PLEX_NODE_TIERS.map((tier) => (
          <circle
            key={tier}
            cx={cx}
            cy={cy}
            r={radius * PLEX_RING_FRACTIONS[tier]}
            fill="none"
            stroke={TIER_RING_COLORS[tier]}
            strokeOpacity={0.28}
          />
        ))}
        {/* Periphery: tier 1500 summarized, never drawn as nodes. */}
        <circle
          cx={cx}
          cy={cy}
          r={radius * PLEX_PERIPHERY_FRACTION}
          fill="none"
          stroke={TIER_RING_COLORS[1500]}
          strokeOpacity={0.35}
          strokeDasharray="3 7"
        />
      </svg>

      {/* Ring capacity labels, stacked up the 12 o'clock axis. */}
      {PLEX_NODE_TIERS.map((tier) => {
        const p = polar(cx, cy, radius * PLEX_RING_FRACTIONS[tier], 0);
        const over = tierCounts[tier] > tier;
        return (
          <span
            key={`label-${tier}`}
            data-testid={`plex-capacity-${tier}`}
            className="absolute left-0 top-0 -translate-x-1/2 -translate-y-1/2 bg-background px-1 font-mono text-[9px] uppercase tracking-[0.08em]"
            style={{
              transform: `translate(${p.x}px, ${p.y}px) translate(-50%, -50%)`,
              color: over ? "var(--amber)" : "var(--dim)",
            }}
          >
            <span className="tabular-nums">
              {tierCounts[tier]}/{tier}
            </span>
          </span>
        );
      })}
      {(() => {
        const p = polar(cx, cy, radius * PLEX_PERIPHERY_FRACTION, 0);
        return (
          <span
            className="absolute left-0 top-0 bg-background px-1 font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--dim)]"
            style={{
              transform: `translate(${p.x}px, ${p.y}px) translate(-50%, -50%)`,
            }}
          >
            <span className="tabular-nums">{tierCounts[1500]}</span> familiar
            faces
          </span>
        );
      })()}

      {/* Contact nodes */}
      {nodes.map((node) => {
        const p = polar(cx, cy, radius * node.radiusFrac, node.angle);
        return (
          <PlexNode
            key={node.entityId}
            entityId={node.entityId}
            name={node.name}
            x={p.x}
            y={p.y}
            size={node.size}
            showLabel={node.tier <= 50}
            attention={attentionIds.has(node.entityId)}
            staleness={node.staleness}
            onCenter={onCenter}
          />
        );
      })}

      {/* Owner at center: not a hop target, just you. */}
      <div
        className="absolute left-0 top-0 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1"
        style={{ transform: `translate(${cx}px, ${cy}px) translate(-50%, -50%)` }}
        data-testid="plex-owner"
      >
        <EntityMark name={ownerName} entityType="person" size={44} isOwner />
        <span className="text-xs font-medium">{ownerName}</span>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Neighbour-mode canvas
// ---------------------------------------------------------------------------

function NeighbourPlexCanvas({
  nodes,
  sectors,
  centerId,
  centerName,
  centerType,
  width,
  height,
  onCenter,
}: {
  nodes: NeighbourPlexNode[];
  sectors: ReturnType<typeof layoutNeighbourPlex>["sectors"];
  centerId: string;
  centerName: string;
  centerType: string;
  width: number;
  height: number;
  onCenter: (id: string) => void;
}) {
  const cx = width / 2;
  const cy = height / 2;
  // Sparse fans pull inward so a handful of neighbours does not scatter to
  // the corners of a large canvas.
  const spread = Math.min(1, 0.45 + nodes.length / 18);
  const radius = (Math.min(width, height) / 2 - 48) * spread;

  return (
    <>
      <svg
        className="absolute inset-0"
        width={width}
        height={height}
        aria-hidden="true"
      >
        {/* Edges: opacity carries assertion confidence. */}
        {nodes.map((node) => {
          const p = polar(cx, cy, radius * node.radiusFrac, node.angle);
          return (
            <line
              key={`edge-${node.predicate}-${node.entityId}`}
              x1={cx}
              y1={cy}
              x2={p.x}
              y2={p.y}
              stroke="var(--border-strong, currentColor)"
              strokeOpacity={0.15 + 0.6 * node.conf}
            />
          );
        })}
      </svg>

      {/* Sector labels: the predicate names this slice of the fan. */}
      {sectors.map((sector) => {
        const p = polar(cx, cy, radius * 0.92, sector.midAngle);
        return (
          <span
            key={`sector-${sector.predicate}`}
            data-testid="plex-sector-label"
            className="absolute left-0 top-0 bg-background px-1 font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--mfg)]"
            style={{
              transform: `translate(${p.x}px, ${p.y}px) translate(-50%, -50%)`,
            }}
          >
            {prettyPredicate(sector.predicate)}
            {sector.remainder > 0 && (
              <Link
                to={`/entities/${centerId}`}
                className="ml-1 text-[var(--dim)] hover:text-foreground"
                aria-label={`${sector.remainder} more ${prettyPredicate(sector.predicate)} neighbours on the record`}
              >
                +{sector.remainder}
              </Link>
            )}
          </span>
        );
      })}

      {/* Neighbour nodes */}
      {nodes.map((node) => {
        const p = polar(cx, cy, radius * node.radiusFrac, node.angle);
        return (
          <PlexNode
            key={`${node.predicate}-${node.entityId}`}
            entityId={node.entityId}
            name={node.name}
            x={p.x}
            y={p.y}
            size={node.size}
            showLabel
            staleness={node.staleness}
            conf={node.conf}
            onCenter={onCenter}
          />
        );
      })}

      {/* Centered entity */}
      <div
        className="absolute left-0 top-0 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1"
        style={{ transform: `translate(${cx}px, ${cy}px) translate(-50%, -50%)` }}
        data-testid="plex-center"
      >
        <EntityMark name={centerName} entityType={centerType} size={44} tone="fill" />
        <span className="text-xs font-medium">{centerName}</span>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Trail breadcrumb
// ---------------------------------------------------------------------------

function TrailBreadcrumb({
  trail,
  centerName,
  nameOf,
  onJump,
  onReset,
}: {
  trail: string[];
  centerName: string | null;
  nameOf: (id: string) => string;
  onJump: (index: number) => void;
  onReset: () => void;
}) {
  return (
    <nav
      aria-label="Hop trail"
      data-testid="plex-trail"
      className="pointer-events-auto flex flex-wrap items-center gap-1 font-mono text-[11px] uppercase tracking-[0.04em]"
    >
      <button
        type="button"
        onClick={onReset}
        className={
          trail.length === 0 && centerName === null
            ? "text-foreground"
            : "text-muted-foreground underline decoration-[var(--border-strong)] underline-offset-4 hover:text-foreground"
        }
      >
        You
      </button>
      {trail.map((id, i) => (
        <span key={`${id}-${i}`} className="flex items-center gap-1">
          <span className="text-[var(--dim)]" aria-hidden="true">
            /
          </span>
          <button
            type="button"
            onClick={() => onJump(i)}
            className="max-w-32 truncate text-muted-foreground underline decoration-[var(--border-strong)] underline-offset-4 hover:text-foreground"
          >
            {nameOf(id)}
          </button>
        </span>
      ))}
      {centerName !== null && (
        <span className="flex items-center gap-1">
          <span className="text-[var(--dim)]" aria-hidden="true">
            /
          </span>
          <span className="max-w-32 truncate text-foreground">{centerName}</span>
        </span>
      )}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Right rail: worth attention + capacity
// ---------------------------------------------------------------------------

function AttentionRail({
  entriesById,
  tierCounts,
  onCenter,
  attention,
  attentionLoading,
}: {
  entriesById: Map<string, DunbarEntry>;
  tierCounts: Record<Tier, number> | null;
  onCenter: (id: string) => void;
  attention: AttentionItem[];
  attentionLoading: boolean;
}) {
  return (
    <aside
      className="w-64 shrink-0 space-y-6 border-l border-border pl-6"
      aria-label="Worth attention"
      data-testid="plex-rail"
    >
      <section>
        <p className="mb-2 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--mfg)]">
          Worth attention
        </p>
        {attentionLoading && (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-4/5" />
          </div>
        )}
        {!attentionLoading && attention.length === 0 && (
          <p className="font-serif text-sm italic text-muted-foreground">
            No one is owed a call.
          </p>
        )}
        {!attentionLoading && attention.length > 0 && (
          <ul className="space-y-2.5">
            {attention.map((item) => (
              <li key={item.entityId} className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <button
                    type="button"
                    onClick={() => onCenter(item.entityId)}
                    className="block max-w-full truncate text-left text-sm text-foreground underline decoration-[var(--border-strong)] underline-offset-4 hover:decoration-foreground"
                  >
                    {item.name}
                  </button>
                  <p className="font-mono text-[10px] uppercase tracking-[0.06em] text-[var(--dim)]">
                    tier <span className="tabular-nums">{item.tier}</span>
                    {" · "}
                    <span className="tabular-nums">{item.sinceDays}</span>d since contact
                  </p>
                </div>
                <Link
                  to={`/entities/${item.entityId}`}
                  aria-label={`Open ${item.name}`}
                  className="shrink-0 text-[10px] text-muted-foreground hover:text-foreground"
                >
                  open
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      {tierCounts && (
        <section>
          <p className="mb-2 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--mfg)]">
            Capacity
          </p>
          <ul className="space-y-1.5">
            {TIERS.filter((t) => t !== 1500).map((tier) => {
              const count = tierCounts[tier];
              const over = count > tier;
              const frac = Math.min(1, count / tier);
              return (
                <li key={tier} className="flex items-center gap-2">
                  <span className="w-24 truncate text-xs text-muted-foreground">
                    {TIER_NAMES[tier]}
                  </span>
                  <span className="h-px flex-1 overflow-hidden rounded bg-border">
                    <span
                      className="block h-full"
                      style={{
                        width: `${frac * 100}%`,
                        backgroundColor: over
                          ? "var(--amber)"
                          : TIER_RING_COLORS[tier],
                      }}
                    />
                  </span>
                  <span
                    className="font-mono text-[10px] tabular-nums"
                    style={{ color: over ? "var(--amber)" : "var(--dim)" }}
                  >
                    {count}/{tier}
                  </span>
                </li>
              );
            })}
            <li className="flex items-center gap-2 pt-1">
              <span className="w-24 truncate text-xs text-muted-foreground">
                {TIER_NAMES[1500]}
              </span>
              <span className="flex-1" />
              <span className="font-mono text-[10px] tabular-nums text-[var(--dim)]">
                {tierCounts[1500]}
              </span>
            </li>
          </ul>
          <p className="mt-2 text-[10px] leading-snug text-muted-foreground">
            Layer sizes are cognitive limits, not settings. Over-capacity reads
            amber.
          </p>
        </section>
      )}

      <section className="border-t border-border pt-3">
        <Link
          to="/entities/index"
          className="font-mono text-[10px] uppercase tracking-[0.06em] text-muted-foreground underline decoration-[var(--border-strong)] underline-offset-4 hover:text-foreground"
        >
          Curation and full index
        </Link>
      </section>

      {/* This map is threaded in so future rail modules can resolve names. */}
      <span className="hidden" data-entries={entriesById.size} />
    </aside>
  );
}

interface AttentionItem {
  entityId: string;
  name: string;
  tier: number;
  sinceDays: number;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const ATTENTION_LIMIT = 5;

/** Reach-out cadence (days) per Dunbar tier. Tier 1500 is never nagged. */
const TIER_CADENCE_DAYS: Record<number, number> = {
  5: 7,
  15: 30,
  50: 90,
  150: 180,
  500: 365,
};

/** Tier weight for attention urgency: attention flows inward (manifesto). */
const TIER_URGENCY_WEIGHT: Record<number, number> = {
  5: 32,
  15: 16,
  50: 8,
  150: 4,
  500: 2,
  1500: 1,
};

export default function PlexPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const centerParam = searchParams.get("center");
  const trail = parseTrail(searchParams.get("trail"));

  // Owner ranking powers the owner plex, the capacity meters, and name lookups.
  const { data: ranking, isLoading: rankingLoading, isError: rankingError } =
    useDunbarRanking(true);
  const ownerEntityId = ranking?.owner_entity_id ?? null;
  const ownerEntry = ranking?.entries.find((e) => e.entity_id === ownerEntityId);
  const ownerName = ownerEntry?.canonical_name ?? "You";

  const entriesById = useMemo(() => {
    const map = new Map<string, DunbarEntry>();
    for (const entry of ranking?.entries ?? []) map.set(entry.entity_id, entry);
    return map;
  }, [ranking]);

  const isOwnerMode = centerParam === null || centerParam === ownerEntityId;

  // Owner-mode layout.
  const ownerLayout = useMemo(
    () => layoutOwnerPlex(ranking?.entries ?? [], ownerEntityId),
    [ranking, ownerEntityId],
  );

  // Neighbour-mode data + layout.
  const {
    data: neighbours,
    isLoading: neighboursLoading,
    isError: neighboursError,
  } = useEntityNeighbours(isOwnerMode ? undefined : (centerParam ?? undefined), {
    rank: "weight",
    per_predicate: 8,
  });
  const neighbourLayout = useMemo(
    () => (neighbours ? layoutNeighbourPlex(neighbours) : null),
    [neighbours],
  );

  // Centered entity summary (name/type/tier/last seen) for the focus card.
  const { data: centerSummaryData } = useRelationshipEntitiesByIds({
    ids: isOwnerMode || centerParam === null ? [] : [centerParam],
    limit: 1,
  });
  const centerSummary = centerSummaryData?.items?.[0];
  const centerName =
    centerSummary?.canonical_name ??
    entriesById.get(centerParam ?? "")?.canonical_name ??
    "…";
  const centerType = centerSummary?.entity_type ?? "person";

  // Attention: derived from the ranking itself (the contact-keyed overdue
  // endpoint was retired with public.contacts). Per-tier cadence vs days
  // since last interaction, tier-weighted so attention flows inward.
  const attention: AttentionItem[] = useMemo(() => {
    const candidates: (AttentionItem & { urgency: number })[] = [];
    for (const entry of ranking?.entries ?? []) {
      if (entry.entity_id === ownerEntityId) continue;
      const cadence = TIER_CADENCE_DAYS[entry.dunbar_tier];
      if (cadence === undefined) continue;
      const sinceDays = daysSince(entry.last_interaction_at);
      if (sinceDays === null || sinceDays <= cadence) continue;
      candidates.push({
        entityId: entry.entity_id,
        name: entry.canonical_name,
        tier: entry.dunbar_tier,
        sinceDays,
        urgency:
          (TIER_URGENCY_WEIGHT[entry.dunbar_tier] ?? 1) *
          ((sinceDays - cadence) / cadence),
      });
    }
    return candidates
      .sort((a, b) => b.urgency - a.urgency)
      .slice(0, ATTENTION_LIMIT)
      .map((c) => ({
        entityId: c.entityId,
        name: c.name,
        tier: c.tier,
        sinceDays: c.sinceDays,
      }));
  }, [ranking, ownerEntityId]);
  const attentionEntityIds = useMemo(
    () => new Set(attention.map((a) => a.entityId)),
    [attention],
  );

  // Name lookup for trail chips: ranking first, then the live canvases.
  const nameOf = useCallback(
    (id: string): string => {
      const fromRanking = entriesById.get(id)?.canonical_name;
      if (fromRanking) return fromRanking;
      const fromNeighbours = neighbourLayout?.nodes.find(
        (n) => n.entityId === id,
      )?.name;
      return fromNeighbours ?? "linked entity";
    },
    [entriesById, neighbourLayout],
  );

  // Re-center on a node: push the current center onto the trail. Hopping
  // back onto the owner resets to the home plex (no trail).
  const handleCenter = useCallback(
    (id: string) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (id === ownerEntityId) {
          next.delete("center");
          next.delete("trail");
          return next;
        }
        const prevCenter = next.get("center");
        const currentTrail = parseTrail(next.get("trail"));
        if (prevCenter && prevCenter !== id) currentTrail.push(prevCenter);
        if (currentTrail.length > 0) next.set("trail", currentTrail.join(","));
        else next.delete("trail");
        next.set("center", id);
        return next;
      });
    },
    [setSearchParams, ownerEntityId],
  );

  const handleTrailJump = useCallback(
    (index: number) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        const currentTrail = parseTrail(next.get("trail"));
        const target = currentTrail[index];
        const remaining = currentTrail.slice(0, index);
        if (target) next.set("center", target);
        if (remaining.length > 0) next.set("trail", remaining.join(","));
        else next.delete("trail");
        return next;
      });
    },
    [setSearchParams],
  );

  const handleReset = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("center");
      next.delete("trail");
      return next;
    });
  }, [setSearchParams]);

  const handlePop = useCallback(() => {
    if (trail.length > 0) {
      handleTrailJump(trail.length - 1);
    } else if (!isOwnerMode) {
      handleReset();
    }
  }, [trail, isOwnerMode, handleTrailJump, handleReset]);

  // Keyboard map: scoped to the canvas container, never window-global.
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "Escape") {
        e.preventDefault();
        handlePop();
      } else if (e.key === "Enter" && !isOwnerMode && centerParam) {
        e.preventDefault();
        void navigate(`/entities/${centerParam}`);
      }
    },
    [handlePop, isOwnerMode, centerParam, navigate],
  );

  const { ref: fillRef, height: fillHeight } = useFillViewportHeight();
  const { ref: stageRef, size: stageSize } = useElementSize();

  const isLoading = isOwnerMode ? rankingLoading : neighboursLoading;
  const isError = isOwnerMode ? rankingError : neighboursError;
  const neighbourEmpty =
    !isOwnerMode &&
    !neighboursLoading &&
    !neighboursError &&
    (neighbourLayout?.nodes.length ?? 0) === 0;

  const focusEntry = isOwnerMode ? null : entriesById.get(centerParam ?? "");
  const focusTier = centerSummary?.tier ?? focusEntry?.dunbar_tier ?? null;
  const focusLastSeen =
    centerSummary?.last_seen ?? focusEntry?.last_interaction_at ?? null;

  return (
    <Page
      archetype="overview"
      title="Entities"
      description="The life graph, centered on you. Click a mark to hop; the trail keeps the way back."
      breadcrumbs={[{ label: "Entities" }]}
    >
      <SubpageTabs />

      <div
        ref={fillRef}
        className="flex gap-6"
        style={{ height: fillHeight ?? undefined }}
      >
        {/* Canvas column */}
        <div
          ref={stageRef}
          tabIndex={0}
          role="application"
          aria-label="Life graph plex"
          onKeyDown={handleKeyDown}
          data-testid="plex-canvas"
          className="relative min-h-0 min-w-0 flex-1 overflow-hidden rounded-sm outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          {isLoading && (
            <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
              Loading the plex...
            </div>
          )}
          {isError && (
            <div
              className="absolute inset-0 flex items-center justify-center text-sm text-destructive"
              role="alert"
            >
              Failed to load the graph.
            </div>
          )}
          {neighbourEmpty && (
            <div className="absolute inset-0 flex items-center justify-center">
              <p className="font-serif text-sm italic text-muted-foreground">
                No relational facts yet. The record view may hold more.
              </p>
            </div>
          )}

          {!isLoading &&
            !isError &&
            stageSize.width > 0 &&
            stageSize.height > 0 &&
            (isOwnerMode ? (
              <OwnerPlexCanvas
                nodes={ownerLayout.nodes}
                tierCounts={ownerLayout.tierCounts}
                ownerName={ownerName}
                width={stageSize.width}
                height={stageSize.height}
                attentionIds={attentionEntityIds}
                onCenter={handleCenter}
              />
            ) : (
              neighbourLayout &&
              !neighbourEmpty && (
                <NeighbourPlexCanvas
                  nodes={neighbourLayout.nodes}
                  sectors={neighbourLayout.sectors}
                  centerId={centerParam!}
                  centerName={centerName}
                  centerType={centerType}
                  width={stageSize.width}
                  height={stageSize.height}
                  onCenter={handleCenter}
                />
              )
            ))}

          {/* Trail: top-left overlay. */}
          <div className="pointer-events-none absolute left-0 top-0 p-1">
            <TrailBreadcrumb
              trail={trail}
              centerName={isOwnerMode ? null : centerName}
              nameOf={nameOf}
              onJump={handleTrailJump}
              onReset={handleReset}
            />
          </div>

          {/* Focus card: bottom-left overlay, only when re-centered. */}
          {!isOwnerMode && centerParam && (
            <div
              className="absolute bottom-0 left-0 max-w-64 space-y-1 border-t border-border bg-background/90 py-2 pr-4"
              data-testid="plex-focus-card"
            >
              <div className="flex items-center gap-2">
                <EntityMark name={centerName} entityType={centerType} size={22} />
                <span className="truncate text-sm font-medium">{centerName}</span>
              </div>
              <p className="font-mono text-[10px] uppercase tracking-[0.06em] text-[var(--dim)]">
                {centerType}
                {focusTier !== null && (
                  <>
                    {" · tier "}
                    <span className="tabular-nums">{focusTier}</span>
                  </>
                )}
                {focusLastSeen && (
                  <>
                    {" · seen "}
                    <Time value={focusLastSeen} mode="relative" />
                  </>
                )}
              </p>
              <Link
                to={`/entities/${centerParam}`}
                className="inline-block text-xs text-primary hover:underline"
              >
                Open record
              </Link>
            </div>
          )}

          {/* Key legend: bottom-right, one quiet line. */}
          <p className="pointer-events-none absolute bottom-0 right-0 p-1 font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--dim)]">
            esc back · enter open record
          </p>
        </div>

        <AttentionRail
          entriesById={entriesById}
          tierCounts={isOwnerMode && !rankingLoading ? ownerLayout.tierCounts : null}
          onCenter={handleCenter}
          attention={attention}
          attentionLoading={rankingLoading}
        />
      </div>
    </Page>
  );
}
