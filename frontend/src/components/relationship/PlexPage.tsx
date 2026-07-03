/**
 * PlexPage — /entities landing: the owner ego-graph ("plex").
 *
 * The canonical entity-graph landing surface: an exploration-first radial
 * canvas that supersedes the retired Hop, Columns, and Social-map views.
 *
 * Layout: full-bleed radial canvas; the horizontal flanks carry overlays.
 *   - Owner mode (default): contacts on concentric Dunbar tier rings around
 *     the owner; tier 1500 summarized as a periphery count, not drawn. Past
 *     the periphery, the dimension halo: non-person entities (organizations /
 *     places / things) as arc-grouped satellites ranked by recency, linked to
 *     the rings by the connection spotlight in both directions. Left flank:
 *     Worth attention. Right flank: capacity meters.
 *   - Neighbour mode (?center=<id>): the centered entity's neighbours fanned
 *     into predicate sectors; edge opacity carries confidence, node
 *     desaturation carries staleness (two separate manifesto axes). Right
 *     flank: the entity dossier (sparkline, dates, facts, latest touches).
 *
 * Interaction:
 *   - Click a mark to re-center; the hop trail persists in the URL.
 *   - Wheel zooms toward the cursor, dragging empty canvas pans; outer-tier
 *     labels fade in past ~1.35x zoom. "0" or the reset affordance restores.
 *   - Hovering a mark opens a micro-dossier card (tier, last seen, 90-day
 *     sparkline) and, in owner mode, lights up that person's edges to other
 *     visible people while the rest recede.
 *   - Dragging a person to another ring pins their Dunbar tier (the one
 *     manual override the manifesto allows); dashed border marks the pin.
 *
 * URL contract:
 *   ?center=<entityId>   re-centered entity (absent = owner)
 *   ?trail=<id,id,...>   hop trail (breadcrumb), oldest first
 *
 * Keyboard (scoped to the canvas container, never window-global):
 *   typing  find-as-you-type: matching marks stay lit with labels while the
 *           rest recede; Backspace edits, Esc clears, Enter jumps to the
 *           best match
 *   Esc     clear the find query, else pop one hop off the trail
 *   Enter   jump to the best find match, else open the centered record
 *   0       reset zoom and pan (only while no find query is active)
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";

import type { DunbarEntry } from "@/api/types";
import { EntityMark } from "@/components/ui/EntityMark";
import { Page } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { ActivitySparkline } from "@/components/relationship/ActivitySparkline";
import { LatestInteractionsBlock } from "@/components/relationship/LatestInteractionsBlock";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import { useDunbarRanking } from "@/hooks/use-memory";
import {
  useEntityCoreDates,
  useEntityFacts,
  useEntityNeighbours,
  usePlexHalo,
  useRelationshipEntitiesByIds,
  useUpdateEntityDunbarTier,
} from "@/hooks/use-entities";
import {
  daysSince,
  layoutHalo,
  layoutNeighbourPlex,
  layoutOwnerPlex,
  PLEX_HALO_FRACTION,
  PLEX_NODE_TIERS,
  PLEX_PERIPHERY_FRACTION,
  PLEX_RING_FRACTIONS,
  polar,
  prettyPredicate,
  TIER_RING_COLORS,
  TIERS,
  type HaloLayout,
  type HaloMarkLayout,
  type NeighbourPlexNode,
  type OwnerPlexNode,
  type PlexNodeTier,
  type Staleness,
  type Tier,
} from "@/components/relationship/plex-layout";
import { TIER_NAMES } from "@/components/memory/concentric-circles-constants";

// ---------------------------------------------------------------------------
// Sizing hooks (the overview archetype wrapper has auto height, so the canvas
// needs an explicit viewport-fill height).
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
  return { ref, size, el };
}

// ---------------------------------------------------------------------------
// URL helpers
// ---------------------------------------------------------------------------

function parseTrail(raw: string | null): string[] {
  if (!raw) return [];
  return raw.split(",").filter(Boolean);
}

// ---------------------------------------------------------------------------
// Camera (zoom + pan)
// ---------------------------------------------------------------------------

interface PlexView {
  zoom: number;
  x: number;
  y: number;
}

const VIEW_IDENTITY: PlexView = { zoom: 1, x: 0, y: 0 };
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 2.5;
/** Zoom level past which the outer tiers' name labels fade in. */
const ZOOM_LABEL_THRESHOLD = 1.35;

function isIdentity(view: PlexView): boolean {
  return view.zoom === 1 && view.x === 0 && view.y === 0;
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
// Hover info (micro-dossier card)
// ---------------------------------------------------------------------------

interface HoverInfo {
  entityId: string;
  name: string;
  entityType: string;
  tier: number | null;
  pinned: boolean;
  staleDays: number | null;
  /** Linked-people count for halo satellites; undefined for person marks. */
  personCount?: number;
  /** Node position in canvas coordinates (pre-camera). */
  x: number;
  y: number;
}

// ---------------------------------------------------------------------------
// Plex node (shared by both modes)
// ---------------------------------------------------------------------------

/** Pointer travel (px) past which a press becomes a drag, not a click. */
const DRAG_THRESHOLD = 6;

interface PlexNodeProps {
  entityId: string;
  name: string;
  x: number;
  y: number;
  size: number;
  entityType?: string;
  /** Test id override (halo marks report as their own kind). */
  testId?: string;
  showLabel: boolean;
  attention?: boolean;
  pinned?: boolean;
  /** Recede while another node's connections are spotlighted. */
  dimmed?: boolean;
  staleness: Staleness;
  /** Extra opacity multiplier (confidence axis in neighbour mode). */
  conf?: number;
  /** True while this node is being dragged (parent overrides x/y). */
  dragging?: boolean;
  /** Enable drag-to-retier (owner mode only). */
  draggable?: boolean;
  onCenter: (id: string) => void;
  onHoverStart?: () => void;
  onHoverEnd?: () => void;
  toCanvas?: (clientX: number, clientY: number) => { x: number; y: number };
  onDragMove?: (id: string, x: number, y: number) => void;
  onDragEnd?: (id: string, x: number, y: number) => void;
}

function PlexNode({
  entityId,
  name,
  x,
  y,
  size,
  entityType = "person",
  testId = "plex-node",
  showLabel,
  attention = false,
  pinned = false,
  dimmed = false,
  staleness,
  conf,
  dragging = false,
  draggable = false,
  onCenter,
  onHoverStart,
  onHoverEnd,
  toCanvas,
  onDragMove,
  onDragEnd,
}: PlexNodeProps) {
  const dragRef = useRef<{ startX: number; startY: number; active: boolean } | null>(
    null,
  );
  const suppressClick = useRef(false);

  function handlePointerDown(e: React.PointerEvent<HTMLButtonElement>) {
    if (!draggable || e.button !== 0) return;
    dragRef.current = { startX: e.clientX, startY: e.clientY, active: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function handlePointerMove(e: React.PointerEvent<HTMLButtonElement>) {
    const d = dragRef.current;
    if (!d || !toCanvas) return;
    if (!d.active) {
      if (Math.hypot(e.clientX - d.startX, e.clientY - d.startY) < DRAG_THRESHOLD)
        return;
      d.active = true;
      onHoverEnd?.();
    }
    const p = toCanvas(e.clientX, e.clientY);
    onDragMove?.(entityId, p.x, p.y);
  }

  function handlePointerUp(e: React.PointerEvent<HTMLButtonElement>) {
    const d = dragRef.current;
    dragRef.current = null;
    if (d?.active && toCanvas) {
      suppressClick.current = true;
      const p = toCanvas(e.clientX, e.clientY);
      onDragEnd?.(entityId, p.x, p.y);
    }
  }

  const markWrapClasses = [
    "rounded-full p-0.5",
    attention ? "outline outline-1 outline-[var(--amber)]" : "",
    pinned ? "border border-dashed border-[var(--role-owner)]" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      type="button"
      data-testid={testId}
      aria-label={`Center plex on ${name}`}
      title={name}
      onClick={() => {
        if (suppressClick.current) {
          suppressClick.current = false;
          return;
        }
        onCenter(entityId);
      }}
      onMouseEnter={onHoverStart}
      onMouseLeave={onHoverEnd}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      className={`group absolute left-0 top-0 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-0.5 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring ${
        dragging
          ? "z-20 cursor-grabbing"
          : "transition-transform duration-slow ease-out-quart"
      }`}
      style={{
        transform: `translate(${x}px, ${y}px) translate(-50%, -50%)`,
        opacity: dimmed ? 0.2 : conf !== undefined ? Math.max(0.45, conf) : undefined,
      }}
    >
      <span className={markWrapClasses} style={stalenessStyle(staleness)}>
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

/** Human words for the halo arc labels; entity types are system vocabulary. */
const HALO_TYPE_LABELS: Record<string, string> = {
  organization: "organizations",
  place: "places",
  other: "things",
};

/** SVG path for a circular arc (angle 0 = up, clockwise — matches polar()). */
function arcPath(cx: number, cy: number, r: number, a0: number, a1: number): string {
  const p0 = polar(cx, cy, r, r, a0);
  const p1 = polar(cx, cy, r, r, a1);
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M ${p0.x} ${p0.y} A ${r} ${r} 0 ${large} 1 ${p1.x} ${p1.y}`;
}

function OwnerPlexCanvas({
  nodes,
  tierCounts,
  ownerName,
  halo,
  satsByPerson,
  width,
  height,
  zoom,
  attentionIds,
  hoveredId,
  connectedIds,
  searchIds,
  toCanvas,
  onCenter,
  onHover,
  onHoverEnd,
  onRetier,
}: {
  nodes: OwnerPlexNode[];
  tierCounts: Record<Tier, number>;
  ownerName: string;
  /** Dimension halo layout; null while loading or when there are no satellites. */
  halo: HaloLayout | null;
  /** Person id → satellite ids, from the halo edges (instant spotlight). */
  satsByPerson: Map<string, Set<string>> | null;
  width: number;
  height: number;
  zoom: number;
  attentionIds: Set<string>;
  hoveredId: string | null;
  /** Neighbour ids of the hovered node; null until the fetch resolves. */
  connectedIds: Set<string> | null;
  /** Find-as-you-type matches; null while no query is active. Search dimming
      overrides the hover spotlight so the two never fight. */
  searchIds: Set<string> | null;
  toCanvas: (clientX: number, clientY: number) => { x: number; y: number };
  onCenter: (id: string) => void;
  onHover: (info: HoverInfo) => void;
  onHoverEnd: () => void;
  onRetier: (id: string, tier: PlexNodeTier, name: string) => void;
}) {
  const cx = width / 2;
  const cy = height / 2;
  const half = Math.min(width, height) / 2;
  // With a halo, the tier rings concede a sliver of radius so the band and
  // its labels stay inside the stage.
  const r = halo ? Math.min(half - 32, (half - 18) / PLEX_HALO_FRACTION) : half - 32;
  const haloR = r * PLEX_HALO_FRACTION;

  // Drag-to-retier: the dragged node follows the pointer; the nearest ring
  // lights up as the drop target.
  const [drag, setDrag] = useState<{ id: string; x: number; y: number } | null>(
    null,
  );

  const nearestTier = useCallback(
    (x: number, y: number): PlexNodeTier | null => {
      const dist = Math.hypot(x - cx, y - cy) / r;
      let best: PlexNodeTier | null = null;
      let bestDelta = 0.085;
      for (const tier of PLEX_NODE_TIERS) {
        const delta = Math.abs(dist - PLEX_RING_FRACTIONS[tier]);
        if (delta < bestDelta) {
          best = tier;
          bestDelta = delta;
        }
      }
      return best;
    },
    [cx, cy, r],
  );
  const dropTier = drag ? nearestTier(drag.x, drag.y) : null;

  function handleDragMove(id: string, x: number, y: number) {
    setDrag({ id, x, y });
  }

  function handleDragEnd(id: string, x: number, y: number) {
    setDrag(null);
    const tier = nearestTier(x, y);
    const node = nodes.find((n) => n.entityId === id);
    if (tier !== null && node && tier !== node.tier) {
      onRetier(id, tier, node.name);
    }
  }

  const nodePos = (node: OwnerPlexNode) =>
    drag?.id === node.entityId
      ? { x: drag.x, y: drag.y }
      : polar(cx, cy, r * node.radiusFrac, r * node.radiusFrac, node.angle);

  // Connection spotlight: only once the hovered node's neighbours resolved.
  const spotlight = hoveredId !== null && connectedIds !== null;
  const hoveredNode = spotlight
    ? nodes.find((n) => n.entityId === hoveredId)
    : undefined;

  // Halo spotlight, both directions. Satellite edges ship with the halo
  // payload, so this lights up instantly — no per-hover fetch.
  const haloMarks = useMemo(
    () => halo?.arcs.flatMap((a) => a.marks) ?? [],
    [halo],
  );
  const hoveredSat: HaloMarkLayout | null =
    hoveredId !== null
      ? (haloMarks.find((m) => m.entityId === hoveredId) ?? null)
      : null;
  const hoveredSatPersonIds = hoveredSat ? new Set(hoveredSat.personIds) : null;
  // Satellites lit while a person is hovered: halo edges plus anything the
  // neighbours fetch already resolved.
  const litSatIds: Set<string> | null =
    hoveredId !== null && !hoveredSat && (satsByPerson?.has(hoveredId) || spotlight)
      ? new Set([
          ...(satsByPerson?.get(hoveredId) ?? []),
          ...haloMarks
            .map((m) => m.entityId)
            .filter((id) => connectedIds?.has(id) ?? false),
        ])
      : null;
  const hoveredPersonNode =
    hoveredId !== null && !hoveredSat
      ? nodes.find((n) => n.entityId === hoveredId)
      : undefined;

  const satPos = (mark: HaloMarkLayout) => polar(cx, cy, haloR, haloR, mark.angle);

  return (
    <>
      <svg
        className="absolute inset-0"
        width={width}
        height={height}
        aria-hidden="true"
      >
        {/* Dimension halo arcs: one band segment per non-person entity type. */}
        {halo?.arcs.map((arc) => (
          <path
            key={`halo-arc-${arc.entityType}`}
            d={arcPath(cx, cy, haloR, arc.startAngle, arc.endAngle)}
            fill="none"
            stroke="var(--border-strong, currentColor)"
            strokeOpacity={0.45}
          />
        ))}
        {PLEX_NODE_TIERS.map((tier) => (
          <circle
            key={tier}
            cx={cx}
            cy={cy}
            r={r * PLEX_RING_FRACTIONS[tier]}
            fill="none"
            stroke={TIER_RING_COLORS[tier]}
            strokeOpacity={dropTier === tier ? 0.9 : 0.28}
            strokeWidth={dropTier === tier ? 1.5 : 1}
          />
        ))}
        {/* Periphery: tier 1500 summarized, never drawn as nodes. */}
        <circle
          cx={cx}
          cy={cy}
          r={r * PLEX_PERIPHERY_FRACTION}
          fill="none"
          stroke={TIER_RING_COLORS[1500]}
          strokeOpacity={0.35}
          strokeDasharray="3 7"
        />
        {/* Connection spotlight edges: hovered person to visible peers. */}
        {hoveredNode &&
          connectedIds &&
          nodes
            .filter((n) => connectedIds.has(n.entityId))
            .map((n) => {
              const a = nodePos(hoveredNode);
              const b = nodePos(n);
              return (
                <line
                  key={`spot-${n.entityId}`}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke="var(--fg, currentColor)"
                  strokeOpacity={0.35}
                />
              );
            })}
        {/* Halo spotlight edges: hovered person out to their satellites. */}
        {hoveredPersonNode &&
          litSatIds &&
          haloMarks
            .filter((m) => litSatIds.has(m.entityId))
            .map((m) => {
              const a = nodePos(hoveredPersonNode);
              const b = satPos(m);
              return (
                <line
                  key={`halo-spot-${m.entityId}`}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke="var(--fg, currentColor)"
                  strokeOpacity={0.3}
                  strokeDasharray="2 4"
                />
              );
            })}
        {/* Halo spotlight edges: hovered satellite in to its people. */}
        {hoveredSat &&
          hoveredSatPersonIds &&
          nodes
            .filter((n) => hoveredSatPersonIds.has(n.entityId))
            .map((n) => {
              const a = satPos(hoveredSat);
              const b = nodePos(n);
              return (
                <line
                  key={`halo-in-${n.entityId}`}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke="var(--fg, currentColor)"
                  strokeOpacity={0.3}
                  strokeDasharray="2 4"
                />
              );
            })}
      </svg>

      {/* Ring capacity labels, stacked up the 12 o'clock axis. */}
      {PLEX_NODE_TIERS.map((tier) => {
        const p = polar(
          cx,
          cy,
          r * PLEX_RING_FRACTIONS[tier],
          r * PLEX_RING_FRACTIONS[tier],
          0,
        );
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
        const p = polar(
          cx,
          cy,
          r * PLEX_PERIPHERY_FRACTION,
          r * PLEX_PERIPHERY_FRACTION,
          0,
        );
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

      {/* Halo arc labels: each names its dimension and opens the index
          filtered to that type. A truncated arc owns up to its cap. */}
      {halo?.arcs.map((arc) => {
        const p = polar(cx, cy, haloR, haloR, arc.midAngle);
        const shown = arc.marks.length;
        return (
          <Link
            key={`halo-label-${arc.entityType}`}
            to={`/entities/index?type=${encodeURIComponent(arc.entityType)}`}
            data-testid="plex-halo-arc-label"
            className="absolute left-0 top-0 bg-background px-1 font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--mfg)] hover:text-foreground"
            style={{
              transform: `translate(${p.x}px, ${p.y}px) translate(-50%, -50%)`,
            }}
            title={
              shown < arc.total
                ? `Showing the ${shown} most recently active of ${arc.total}`
                : undefined
            }
          >
            {HALO_TYPE_LABELS[arc.entityType] ?? arc.entityType}
            {" · "}
            <span className="tabular-nums">
              {shown < arc.total ? `${shown}/${arc.total}` : arc.total}
            </span>
          </Link>
        );
      })}

      {/* Halo satellite marks */}
      {haloMarks.map((mark) => {
        const p = satPos(mark);
        const isHovered = mark.entityId === hoveredId;
        const lit = litSatIds?.has(mark.entityId) ?? false;
        return (
          <PlexNode
            key={`halo-${mark.entityId}`}
            entityId={mark.entityId}
            name={mark.name}
            x={p.x}
            y={p.y}
            size={18}
            entityType={mark.entityType}
            testId="plex-halo-mark"
            showLabel={
              zoom >= ZOOM_LABEL_THRESHOLD ||
              lit ||
              isHovered ||
              (searchIds?.has(mark.entityId) ?? false)
            }
            dimmed={
              searchIds
                ? !searchIds.has(mark.entityId)
                : hoveredSat
                  ? !isHovered
                  : (spotlight || litSatIds !== null) && !lit
            }
            staleness={mark.staleness}
            onCenter={onCenter}
            onHoverStart={() =>
              onHover({
                entityId: mark.entityId,
                name: mark.name,
                entityType: mark.entityType,
                tier: null,
                pinned: false,
                staleDays: mark.staleDays,
                personCount: mark.personIds.length,
                x: p.x,
                y: p.y,
              })
            }
            onHoverEnd={onHoverEnd}
          />
        );
      })}

      {/* Contact nodes */}
      {nodes.map((node) => {
        const p = nodePos(node);
        const isHovered = node.entityId === hoveredId;
        return (
          <PlexNode
            key={node.entityId}
            entityId={node.entityId}
            name={node.name}
            x={p.x}
            y={p.y}
            size={node.size}
            showLabel={
              node.tier <= 50 ||
              zoom >= ZOOM_LABEL_THRESHOLD ||
              (searchIds?.has(node.entityId) ?? false)
            }
            attention={attentionIds.has(node.entityId)}
            pinned={node.pinned}
            dimmed={
              searchIds
                ? !searchIds.has(node.entityId)
                : hoveredSat
                  ? !(hoveredSatPersonIds?.has(node.entityId) ?? false)
                  : spotlight &&
                    !isHovered &&
                    !(connectedIds?.has(node.entityId) ?? false)
            }
            staleness={node.staleness}
            dragging={drag?.id === node.entityId}
            draggable
            toCanvas={toCanvas}
            onDragMove={handleDragMove}
            onDragEnd={handleDragEnd}
            onCenter={onCenter}
            onHoverStart={() =>
              onHover({
                entityId: node.entityId,
                name: node.name,
                entityType: "person",
                tier: node.tier,
                pinned: node.pinned,
                staleDays: node.staleDays,
                x: p.x,
                y: p.y,
              })
            }
            onHoverEnd={onHoverEnd}
          />
        );
      })}

      {/* Drop hint while dragging */}
      {drag && (
        <p
          className="pointer-events-none absolute left-1/2 top-2 -translate-x-1/2 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--mfg)]"
          data-testid="plex-drop-hint"
        >
          {dropTier !== null
            ? `Drop to pin: ${TIER_NAMES[dropTier]}`
            : "Drop on a ring to pin a tier"}
        </p>
      )}

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
  searchIds,
  onCenter,
  onHover,
  onHoverEnd,
}: {
  nodes: NeighbourPlexNode[];
  sectors: ReturnType<typeof layoutNeighbourPlex>["sectors"];
  centerId: string;
  centerName: string;
  centerType: string;
  width: number;
  height: number;
  /** Find-as-you-type matches; null while no query is active. */
  searchIds: Set<string> | null;
  onCenter: (id: string) => void;
  onHover: (info: HoverInfo) => void;
  onHoverEnd: () => void;
}) {
  const cx = width / 2;
  const cy = height / 2;
  // Sparse fans pull inward so a handful of neighbours does not scatter to
  // the corners of a large canvas.
  const spread = Math.min(1, 0.45 + nodes.length / 18);
  const r = (Math.min(width, height) / 2 - 48) * spread;

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
          const p = polar(
            cx,
            cy,
            r * node.radiusFrac,
            r * node.radiusFrac,
            node.angle,
          );
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
        const p = polar(cx, cy, r * 0.92, r * 0.92, sector.midAngle);
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
        const p = polar(
          cx,
          cy,
          r * node.radiusFrac,
          r * node.radiusFrac,
          node.angle,
        );
        return (
          <PlexNode
            key={`${node.predicate}-${node.entityId}`}
            entityId={node.entityId}
            name={node.name}
            x={p.x}
            y={p.y}
            size={node.size}
            entityType={node.entityType}
            showLabel
            dimmed={searchIds ? !searchIds.has(node.entityId) : false}
            staleness={node.staleness}
            conf={node.conf}
            onCenter={onCenter}
            onHoverStart={() =>
              onHover({
                entityId: node.entityId,
                name: node.name,
                entityType: node.entityType,
                tier: null,
                pinned: false,
                staleDays: node.staleDays,
                x: p.x,
                y: p.y,
              })
            }
            onHoverEnd={onHoverEnd}
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
// Hover card (micro-dossier)
// ---------------------------------------------------------------------------

function PlexHoverCard({
  info,
  left,
  top,
  onKeep,
  onRelease,
  onUnpin,
}: {
  info: HoverInfo;
  left: number;
  top: number;
  onKeep: () => void;
  onRelease: () => void;
  onUnpin: (id: string, name: string) => void;
}) {
  return (
    <div
      data-plex-overlay
      data-testid="plex-hover-card"
      className="absolute z-30 w-64 space-y-2 border border-border bg-background/95 p-3"
      style={{ left, top }}
      onMouseEnter={onKeep}
      onMouseLeave={onRelease}
    >
      <div className="flex items-center gap-2">
        <EntityMark name={info.name} entityType={info.entityType} size={22} />
        <span className="min-w-0 truncate text-sm font-medium">{info.name}</span>
      </div>
      <p className="font-mono text-[10px] uppercase tracking-[0.06em] text-[var(--dim)]">
        {info.entityType}
        {info.tier !== null && (
          <>
            {" · tier "}
            <span className="tabular-nums">{info.tier}</span>
            {info.pinned && " (pinned)"}
          </>
        )}
        {info.staleDays !== null ? (
          <>
            {" · "}
            <span className="tabular-nums">{info.staleDays}</span>d since contact
          </>
        ) : (
          " · no contact recorded"
        )}
        {info.personCount !== undefined &&
          (info.personCount === 0 ? (
            " · no people linked"
          ) : (
            <>
              {" · "}
              <span className="tabular-nums">{info.personCount}</span>
              {info.personCount === 1 ? " person" : " people"}
            </>
          ))}
      </p>
      <ActivitySparkline entityId={info.entityId} />
      <div className="flex items-center gap-3 pt-0.5">
        <Link
          to={`/entities/${info.entityId}`}
          className="text-xs text-primary hover:underline"
        >
          Open record
        </Link>
        {info.pinned && (
          <button
            type="button"
            onClick={() => onUnpin(info.entityId, info.name)}
            className="font-mono text-[10px] uppercase tracking-[0.04em] text-muted-foreground underline decoration-[var(--border-strong)] underline-offset-4 hover:text-foreground"
          >
            Unpin tier
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Entity dossier (neighbour mode, right flank)
// ---------------------------------------------------------------------------

function EntityDossier({
  entityId,
  name,
  entityType,
  tier,
  pinned,
  lastSeen,
  onUnpin,
}: {
  entityId: string;
  name: string;
  entityType: string;
  tier: number | null;
  pinned: boolean;
  lastSeen: string | null;
  onUnpin: (id: string, name: string) => void;
}) {
  const { data: factsData, isLoading: factsLoading } = useEntityFacts(entityId, {
    limit: 12,
  });
  const { data: datesData } = useEntityCoreDates(entityId);

  // Literal facts only: entity-valued facts are already on the canvas as
  // neighbours; repeating their UUIDs here is noise.
  const facts = (factsData?.items ?? []).filter(
    (f) => f.object_kind === "literal",
  );
  const dates = datesData?.items ?? [];

  return (
    <aside
      data-plex-overlay
      data-testid="plex-dossier"
      aria-label={`${name} dossier`}
      className="pointer-events-auto absolute bottom-0 right-0 top-0 z-10 w-72 space-y-5 overflow-y-auto border-l border-border bg-background/95 py-1 pl-4"
    >
      <section className="space-y-1.5">
        <div className="flex items-center gap-2">
          <EntityMark name={name} entityType={entityType} size={26} />
          <span className="min-w-0 truncate text-sm font-medium">{name}</span>
        </div>
        <p className="font-mono text-[10px] uppercase tracking-[0.06em] text-[var(--dim)]">
          {entityType}
          {tier !== null && (
            <>
              {" · tier "}
              <span className="tabular-nums">{tier}</span>
              {pinned && " (pinned)"}
            </>
          )}
          {lastSeen && (
            <>
              {" · seen "}
              <Time value={lastSeen} mode="relative" />
            </>
          )}
        </p>
        <div className="flex items-center gap-3">
          <Link
            to={`/entities/${entityId}`}
            className="text-xs text-primary hover:underline"
          >
            Open record
          </Link>
          {pinned && (
            <button
              type="button"
              onClick={() => onUnpin(entityId, name)}
              className="font-mono text-[10px] uppercase tracking-[0.04em] text-muted-foreground underline decoration-[var(--border-strong)] underline-offset-4 hover:text-foreground"
            >
              Unpin tier
            </button>
          )}
        </div>
      </section>

      <section>
        <p className="mb-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--mfg)]">
          90-day activity
        </p>
        <ActivitySparkline entityId={entityId} />
      </section>

      {dates.length > 0 && (
        <section>
          <p className="mb-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--mfg)]">
            Dates
          </p>
          <ul className="space-y-1">
            {dates.map((d) => (
              <li key={d.id} className="flex items-baseline justify-between gap-2">
                <span className="min-w-0 truncate text-xs">
                  {prettyPredicate(d.predicate)}
                </span>
                <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--dim)]">
                  in {d.days_until}d
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <p className="mb-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--mfg)]">
          Facts
        </p>
        {factsLoading && (
          <div className="space-y-1.5">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-4/5" />
          </div>
        )}
        {!factsLoading && facts.length === 0 && (
          <p className="font-serif text-sm italic text-muted-foreground">
            Nothing recorded yet.
          </p>
        )}
        {!factsLoading && facts.length > 0 && (
          <ul className="space-y-1.5">
            {facts.map((f) => (
              <li
                key={f.id}
                className={f.staleness_band === "stale" ? "opacity-50" : undefined}
              >
                <p className="font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--mfg)]">
                  {prettyPredicate(f.predicate)}
                </p>
                <p className="truncate text-xs" title={f.object}>
                  {f.object}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Latest touch per channel; the block hides itself when empty. */}
      <LatestInteractionsBlock entityId={entityId} />
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Flank overlays: worth attention (left) + capacity (right). Owner mode only.
// ---------------------------------------------------------------------------

function AttentionRail({
  tierCounts,
  onCenter,
  attention,
  attentionLoading,
}: {
  tierCounts: Record<Tier, number> | null;
  onCenter: (id: string) => void;
  attention: AttentionItem[];
  attentionLoading: boolean;
}) {
  return (
    <>
      <aside
        data-plex-overlay
        className="pointer-events-auto absolute left-0 top-1/2 z-10 w-56 -translate-y-1/2 space-y-6"
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
                <li
                  key={item.entityId}
                  className="flex items-start justify-between gap-2"
                >
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
                      <span className="tabular-nums">{item.sinceDays}</span>d since
                      contact
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
      </aside>

      <aside
        data-plex-overlay
        className="pointer-events-auto absolute right-0 top-1/2 z-10 w-56 -translate-y-1/2 space-y-6"
        aria-label="Capacity"
      >
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
      </aside>
    </>
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

const HOVER_SHOW_DELAY_MS = 220;
const HOVER_HIDE_DELAY_MS = 250;

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

  // Dimension halo: non-person entities banded around the rings (owner mode).
  const { data: haloData } = usePlexHalo(isOwnerMode);
  const haloLayout: HaloLayout | null = useMemo(() => {
    if (!haloData) return null;
    const layout = layoutHalo(haloData);
    return layout.arcs.length > 0 ? layout : null;
  }, [haloData]);
  // Person → satellites, inverted from the halo edges (instant spotlight).
  const satsByPerson = useMemo(() => {
    if (!haloData) return null;
    const map = new Map<string, Set<string>>();
    for (const satellites of Object.values(haloData.arcs)) {
      for (const sat of satellites) {
        for (const edge of sat.edges) {
          let set = map.get(edge.person_id);
          if (!set) {
            set = new Set();
            map.set(edge.person_id, set);
          }
          set.add(sat.entity_id);
        }
      }
    }
    return map;
  }, [haloData]);

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

  // Centered entity summary (name/type/tier/last seen) for the dossier.
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
      if (fromNeighbours) return fromNeighbours;
      const fromHalo = haloLayout?.arcs
        .flatMap((a) => a.marks)
        .find((m) => m.entityId === id)?.name;
      return fromHalo ?? "linked entity";
    },
    [entriesById, neighbourLayout, haloLayout],
  );

  // -------------------------------------------------------------------------
  // Camera: wheel zooms toward the cursor, dragging empty canvas pans.
  // -------------------------------------------------------------------------

  const { ref: fillRef, height: fillHeight } = useFillViewportHeight();
  const { ref: stageRef, size: stageSize, el: stageEl } = useElementSize();

  const [view, setView] = useState<PlexView>(VIEW_IDENTITY);
  const viewRef = useRef(view);
  useEffect(() => {
    viewRef.current = view;
  }, [view]);

  useEffect(() => {
    if (!stageEl) return;
    const onWheel = (e: WheelEvent) => {
      // Wheel over an overlay (dossier, hover card, flanks) scrolls that
      // overlay natively; only wheel over the graph itself zooms.
      if ((e.target as HTMLElement).closest("[data-plex-overlay]")) return;
      e.preventDefault();
      const rect = stageEl.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      setView((prev) => {
        const nz = Math.min(
          ZOOM_MAX,
          Math.max(ZOOM_MIN, prev.zoom * Math.exp(-e.deltaY * 0.0015)),
        );
        if (nz === prev.zoom) return prev;
        return {
          zoom: nz,
          x: px - (px - prev.x) * (nz / prev.zoom),
          y: py - (py - prev.y) * (nz / prev.zoom),
        };
      });
    };
    stageEl.addEventListener("wheel", onWheel, { passive: false });
    return () => stageEl.removeEventListener("wheel", onWheel);
  }, [stageEl]);

  const panRef = useRef<{
    px: number;
    py: number;
    startX: number;
    startY: number;
  } | null>(null);
  const [panning, setPanning] = useState(false);

  function handleStagePointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    const target = e.target as HTMLElement;
    if (target.closest("button, a, [data-plex-overlay]")) return;
    panRef.current = {
      px: viewRef.current.x,
      py: viewRef.current.y,
      startX: e.clientX,
      startY: e.clientY,
    };
    setPanning(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function handleStagePointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const p = panRef.current;
    if (!p) return;
    const dx = e.clientX - p.startX;
    const dy = e.clientY - p.startY;
    setView((prev) => ({ ...prev, x: p.px + dx, y: p.py + dy }));
  }

  function handleStagePointerUp() {
    panRef.current = null;
    setPanning(false);
  }

  const resetView = useCallback(() => setView(VIEW_IDENTITY), []);

  /** Client (screen) coordinates to canvas coordinates, inverting the camera. */
  const toCanvas = useCallback(
    (clientX: number, clientY: number) => {
      if (!stageEl) return { x: clientX, y: clientY };
      const rect = stageEl.getBoundingClientRect();
      const v = viewRef.current;
      return {
        x: (clientX - rect.left - v.x) / v.zoom,
        y: (clientY - rect.top - v.y) / v.zoom,
      };
    },
    [stageEl],
  );

  // -------------------------------------------------------------------------
  // Hover: micro-dossier card + connection spotlight (owner mode).
  // -------------------------------------------------------------------------

  const [hover, setHover] = useState<HoverInfo | null>(null);
  const showTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimers = useCallback(() => {
    if (showTimer.current) clearTimeout(showTimer.current);
    if (hideTimer.current) clearTimeout(hideTimer.current);
    showTimer.current = null;
    hideTimer.current = null;
  }, []);
  useEffect(() => clearTimers, [clearTimers]);

  const scheduleHover = useCallback(
    (info: HoverInfo) => {
      clearTimers();
      // Enrich from the ranking when the canvas could not supply tier data
      // (neighbour mode).
      const entry = entriesById.get(info.entityId);
      const enriched: HoverInfo =
        entry && info.tier === null
          ? {
              ...info,
              tier: entry.dunbar_tier,
              pinned: entry.dunbar_tier_override,
              staleDays: info.staleDays ?? daysSince(entry.last_interaction_at),
            }
          : info;
      showTimer.current = setTimeout(
        () => setHover(enriched),
        HOVER_SHOW_DELAY_MS,
      );
    },
    [clearTimers, entriesById],
  );

  const scheduleHide = useCallback(() => {
    if (showTimer.current) clearTimeout(showTimer.current);
    showTimer.current = null;
    hideTimer.current = setTimeout(() => setHover(null), HOVER_HIDE_DELAY_MS);
  }, []);

  const keepHover = useCallback(() => {
    if (hideTimer.current) clearTimeout(hideTimer.current);
    hideTimer.current = null;
  }, []);

  const clearHoverNow = useCallback(() => {
    clearTimers();
    setHover(null);
  }, [clearTimers]);

  // -------------------------------------------------------------------------
  // Find-as-you-type: printable keys on the focused canvas build a query;
  // matching marks stay lit (labels shown) while everything else recedes.
  // -------------------------------------------------------------------------

  const [query, setQuery] = useState("");

  // A hop is a context switch; a stale query would silently dim the new view.
  // Render-phase reset (the react.dev "adjust state when a prop changes"
  // pattern) so the old query never paints once.
  const [prevCenter, setPrevCenter] = useState(centerParam);
  if (prevCenter !== centerParam) {
    setPrevCenter(centerParam);
    if (query) setQuery("");
  }

  /** Every mark on the current canvas, by id — the find search space. */
  const searchSpace = useMemo(() => {
    const space: { id: string; name: string }[] = [];
    if (isOwnerMode) {
      for (const n of ownerLayout.nodes) space.push({ id: n.entityId, name: n.name });
      for (const arc of haloLayout?.arcs ?? [])
        for (const m of arc.marks) space.push({ id: m.entityId, name: m.name });
    } else {
      for (const n of neighbourLayout?.nodes ?? [])
        space.push({ id: n.entityId, name: n.name });
    }
    return space;
  }, [isOwnerMode, ownerLayout, haloLayout, neighbourLayout]);

  const normalizedQuery = query.trim().toLowerCase();
  const searchIds = useMemo(() => {
    if (!normalizedQuery) return null;
    return new Set(
      searchSpace
        .filter((e) => e.name.toLowerCase().includes(normalizedQuery))
        .map((e) => e.id),
    );
  }, [normalizedQuery, searchSpace]);

  /** Best match: earliest occurrence wins, then the shortest name. */
  const bestMatchId = useMemo(() => {
    if (!normalizedQuery) return null;
    const ranked = searchSpace
      .filter((e) => e.name.toLowerCase().includes(normalizedQuery))
      .sort((a, b) => {
        const ai = a.name.toLowerCase().indexOf(normalizedQuery);
        const bi = b.name.toLowerCase().indexOf(normalizedQuery);
        return ai - bi || a.name.length - b.name.length || a.name.localeCompare(b.name);
      });
    return ranked[0]?.id ?? null;
  }, [normalizedQuery, searchSpace]);

  // Connection spotlight: fetch the hovered person's neighbours (owner mode).
  const { data: hoverNeighbours } = useEntityNeighbours(
    isOwnerMode && hover ? hover.entityId : undefined,
    { rank: "weight", per_predicate: 24 },
  );
  const connectedIds = useMemo(() => {
    if (!hoverNeighbours) return null;
    const ids = new Set<string>();
    for (const list of Object.values(hoverNeighbours.neighbours)) {
      for (const entry of list) ids.add(entry.entity_id);
    }
    return ids;
  }, [hoverNeighbours]);

  // -------------------------------------------------------------------------
  // Drag-to-retier: pin the Dunbar tier by moving a person across rings.
  // -------------------------------------------------------------------------

  const retierMutation = useUpdateEntityDunbarTier();

  const handleRetier = useCallback(
    async (entityId: string, tier: PlexNodeTier, name: string) => {
      clearHoverNow();
      try {
        await retierMutation.mutateAsync({ entityId, tier });
        toast.success(`Pinned ${name} to ${TIER_NAMES[tier]}`);
      } catch (err) {
        toast.error(
          `Pin failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        );
      }
    },
    [retierMutation, clearHoverNow],
  );

  const handleUnpin = useCallback(
    async (entityId: string, name: string) => {
      clearHoverNow();
      try {
        await retierMutation.mutateAsync({ entityId, tier: null });
        toast.success(`Cleared pin for ${name}`);
      } catch (err) {
        toast.error(
          `Unpin failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        );
      }
    },
    [retierMutation, clearHoverNow],
  );

  // -------------------------------------------------------------------------
  // Hop navigation
  // -------------------------------------------------------------------------

  // Re-center on a node: push the current center onto the trail. Hopping
  // back onto the owner resets to the home plex (no trail).
  const handleCenter = useCallback(
    (id: string) => {
      clearHoverNow();
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
    [setSearchParams, ownerEntityId, clearHoverNow],
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
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "Escape") {
        e.preventDefault();
        // An active find owns Escape; the trail gets it back once cleared.
        if (query) setQuery("");
        else handlePop();
      } else if (e.key === "Backspace" && query) {
        e.preventDefault();
        setQuery((q) => q.slice(0, -1));
      } else if (e.key === "Enter") {
        if (query && bestMatchId) {
          e.preventDefault();
          handleCenter(bestMatchId);
        } else if (!isOwnerMode && centerParam) {
          e.preventDefault();
          void navigate(`/entities/${centerParam}`);
        }
      } else if (e.key === "0" && !query) {
        e.preventDefault();
        resetView();
      } else if (e.key.length === 1) {
        // Printable characters build the find query (space included — names
        // have spaces); preventDefault stops space from scrolling/clicking.
        e.preventDefault();
        setQuery((q) => q + e.key);
      }
    },
    [
      query,
      bestMatchId,
      handleCenter,
      handlePop,
      isOwnerMode,
      centerParam,
      navigate,
      resetView,
    ],
  );

  const isLoading = isOwnerMode ? rankingLoading : neighboursLoading;
  const isError = isOwnerMode ? rankingError : neighboursError;
  const neighbourEmpty =
    !isOwnerMode &&
    !neighboursLoading &&
    !neighboursError &&
    (neighbourLayout?.nodes.length ?? 0) === 0;

  const focusEntry = isOwnerMode ? null : entriesById.get(centerParam ?? "");
  const focusTier = centerSummary?.tier ?? focusEntry?.dunbar_tier ?? null;
  const focusPinned = focusEntry?.dunbar_tier_override ?? false;
  const focusLastSeen =
    centerSummary?.last_seen ?? focusEntry?.last_interaction_at ?? null;

  // Hover card screen position (canvas coords through the camera), flipped
  // when it would clip the right edge.
  const hoverScreen = hover
    ? {
        x: hover.x * view.zoom + view.x,
        y: hover.y * view.zoom + view.y,
      }
    : null;
  const hoverCardLeft =
    hoverScreen === null
      ? 0
      : hoverScreen.x > stageSize.width - 300
        ? hoverScreen.x - 280
        : hoverScreen.x + 24;
  const hoverCardTop =
    hoverScreen === null
      ? 0
      : Math.min(
          Math.max(hoverScreen.y - 40, 8),
          Math.max(8, stageSize.height - 190),
        );

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
        className="flex"
        style={{ height: fillHeight ?? undefined }}
      >
        {/* Canvas column */}
        <div
          ref={stageRef}
          tabIndex={0}
          role="application"
          aria-label="Life graph plex"
          onKeyDown={handleKeyDown}
          onPointerDown={handleStagePointerDown}
          onPointerMove={handleStagePointerMove}
          onPointerUp={handleStagePointerUp}
          data-testid="plex-canvas"
          className={`relative min-h-0 min-w-0 flex-1 select-none overflow-hidden rounded-sm outline-none focus-visible:ring-1 focus-visible:ring-ring ${
            panning ? "cursor-grabbing" : "cursor-grab"
          }`}
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

          {/* Camera layer: everything spatial pans and zooms together. */}
          {!isLoading &&
            !isError &&
            stageSize.width > 0 &&
            stageSize.height > 0 && (
              <div
                className="absolute inset-0"
                style={{
                  transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})`,
                  transformOrigin: "0 0",
                }}
              >
                {isOwnerMode ? (
                  <OwnerPlexCanvas
                    nodes={ownerLayout.nodes}
                    tierCounts={ownerLayout.tierCounts}
                    ownerName={ownerName}
                    halo={haloLayout}
                    satsByPerson={satsByPerson}
                    width={stageSize.width}
                    height={stageSize.height}
                    zoom={view.zoom}
                    attentionIds={attentionEntityIds}
                    hoveredId={hover?.entityId ?? null}
                    connectedIds={connectedIds}
                    searchIds={searchIds}
                    toCanvas={toCanvas}
                    onCenter={handleCenter}
                    onHover={scheduleHover}
                    onHoverEnd={scheduleHide}
                    onRetier={handleRetier}
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
                      searchIds={searchIds}
                      onCenter={handleCenter}
                      onHover={scheduleHover}
                      onHoverEnd={scheduleHide}
                    />
                  )
                )}
              </div>
            )}

          {/* Hover card: outside the camera so it never scales. */}
          {hover && hoverScreen && (
            <PlexHoverCard
              info={hover}
              left={hoverCardLeft}
              top={hoverCardTop}
              onKeep={keepHover}
              onRelease={scheduleHide}
              onUnpin={handleUnpin}
            />
          )}

          {/* Trail: top-left overlay. */}
          <div className="pointer-events-none absolute left-0 top-0 z-10 p-1">
            <TrailBreadcrumb
              trail={trail}
              centerName={isOwnerMode ? null : centerName}
              nameOf={nameOf}
              onJump={handleTrailJump}
              onReset={handleReset}
            />
          </div>

          {/* Find bar: appears only while a query is active. */}
          {query && (
            <p
              data-testid="plex-find"
              className="pointer-events-none absolute left-1/2 top-0 z-10 -translate-x-1/2 bg-background/95 p-1 font-mono text-[11px] tracking-[0.04em]"
              aria-live="polite"
            >
              <span className="uppercase text-[var(--dim)]">find </span>
              <span className="text-foreground">{query}</span>
              <span className="text-[var(--dim)]">
                {" · "}
                <span className="tabular-nums">{searchIds?.size ?? 0}</span>
                {(searchIds?.size ?? 0) === 1 ? " match" : " matches"}
                {(searchIds?.size ?? 0) > 0 && " · enter jumps"}
                {" · esc clears"}
              </span>
            </p>
          )}

          {/* Reset-view affordance: only while the camera is moved. */}
          {!isIdentity(view) && (
            <button
              type="button"
              data-plex-overlay
              onClick={resetView}
              className="absolute right-0 top-0 z-10 p-1 font-mono text-[10px] uppercase tracking-[0.06em] text-muted-foreground underline decoration-[var(--border-strong)] underline-offset-4 hover:text-foreground"
            >
              reset view
            </button>
          )}

          {/* Key legend: bottom-right, one quiet line. */}
          <p className="pointer-events-none absolute bottom-0 right-0 z-10 p-1 font-mono text-[9px] uppercase tracking-[0.08em] text-[var(--dim)]">
            type to find · esc back · enter open · 0 reset · drag a person to
            pin a tier
          </p>

          {/* Owner mode: attention + capacity flanks. */}
          {isOwnerMode && (
            <AttentionRail
              tierCounts={!rankingLoading ? ownerLayout.tierCounts : null}
              onCenter={handleCenter}
              attention={attention}
              attentionLoading={rankingLoading}
            />
          )}

          {/* Neighbour mode: the centered entity's dossier on the right flank. */}
          {!isOwnerMode && centerParam && (
            <EntityDossier
              entityId={centerParam}
              name={centerName}
              entityType={centerType}
              tier={focusTier}
              pinned={focusPinned}
              lastSeen={focusLastSeen}
              onUnpin={handleUnpin}
            />
          )}
        </div>
      </div>
    </Page>
  );
}
