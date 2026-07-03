/**
 * Unit tests for plex-layout — pure layout math for the /entities Plex.
 *
 * Covers:
 * - daysSince: null/undefined/invalid inputs, "now", past timestamps, clamping
 * - stalenessOf: band edges at 59/60/179/180 and the null → unknown bucket
 * - polar: angle 0 = up, clockwise winding, elliptical radii
 * - prettyPredicate: dash/underscore humanization
 * - layoutOwnerPlex: owner exclusion, tierCounts (incl. 1500), tier-1500
 *   entries not drawn, per-ring score ordering, angle notch invariant,
 *   radius/size constants, pinned flag threading
 * - layoutNeighbourPlex: empty response, count-proportional non-overlapping
 *   sectors, forward/reverse dedup keeping the heavier row, cross-predicate
 *   duplicates kept, remainder threading, conf/staleness threading, weight
 *   ranking within a sector
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  DunbarEntry,
  HaloResponse,
  HaloSatellite,
  NeighbourEntry,
  NeighboursResponse,
} from "@/api/types";
import {
  daysSince,
  HALO_ARC_GAP,
  HALO_ARC_ORDER,
  layoutHalo,
  layoutNeighbourPlex,
  layoutOwnerPlex,
  PLEX_MARK_SIZES,
  PLEX_NODE_TIERS,
  PLEX_RING_FRACTIONS,
  PLEX_TOP_NOTCH,
  polar,
  prettyPredicate,
  stalenessOf,
} from "./plex-layout";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function dunbarEntry(
  overrides: Partial<DunbarEntry> & { entity_id: string },
): DunbarEntry {
  return {
    contact_id: `contact-${overrides.entity_id}`,
    canonical_name: overrides.entity_id,
    dunbar_tier: 50,
    dunbar_score: 1,
    dunbar_tier_override: false,
    last_interaction_at: null,
    ...overrides,
  };
}

function neighbourEntry(
  overrides: Partial<NeighbourEntry> & { entity_id: string },
): NeighbourEntry {
  return {
    canonical_name: overrides.entity_id,
    entity_type: null,
    direction: "forward",
    src: "relationship",
    conf: 1,
    last_seen: null,
    weight: 1,
    verified: false,
    primary: null,
    ...overrides,
  };
}

function neighboursResponse(
  neighbours: Record<string, NeighbourEntry[]>,
  remainders: Record<string, number> = {},
): NeighboursResponse {
  return { neighbours, remainders };
}

// ---------------------------------------------------------------------------
// daysSince
// ---------------------------------------------------------------------------

describe("daysSince", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-01T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns null for null, undefined, and empty string", () => {
    expect(daysSince(null)).toBeNull();
    expect(daysSince(undefined)).toBeNull();
    expect(daysSince("")).toBeNull();
  });

  it("returns null for an unparseable timestamp", () => {
    expect(daysSince("not-a-date")).toBeNull();
  });

  it("returns 0 for the current instant", () => {
    expect(daysSince("2026-07-01T12:00:00Z")).toBe(0);
  });

  it("floors partial days: 23 hours ago is still 0 days", () => {
    expect(daysSince("2026-06-30T13:00:00Z")).toBe(0);
  });

  it("returns whole days for past timestamps", () => {
    expect(daysSince("2026-06-30T11:00:00Z")).toBe(1);
    expect(daysSince("2026-06-01T12:00:00Z")).toBe(30);
  });

  it("clamps future timestamps to 0 instead of going negative", () => {
    expect(daysSince("2026-07-10T12:00:00Z")).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// stalenessOf
// ---------------------------------------------------------------------------

describe("stalenessOf", () => {
  it("maps null to unknown", () => {
    expect(stalenessOf(null)).toBe("unknown");
  });

  it("is fresh below the soft threshold (0 and 59)", () => {
    expect(stalenessOf(0)).toBe("fresh");
    expect(stalenessOf(59)).toBe("fresh");
  });

  it("turns soft exactly at 60 days and stays soft at 179", () => {
    expect(stalenessOf(60)).toBe("soft");
    expect(stalenessOf(179)).toBe("soft");
  });

  it("turns hard exactly at 180 days and beyond", () => {
    expect(stalenessOf(180)).toBe("hard");
    expect(stalenessOf(1000)).toBe("hard");
  });
});

// ---------------------------------------------------------------------------
// polar
// ---------------------------------------------------------------------------

describe("polar", () => {
  it("points straight up at angle 0", () => {
    const p = polar(100, 100, 50, 30, 0);
    expect(p.x).toBeCloseTo(100);
    expect(p.y).toBeCloseTo(70); // up = smaller y
  });

  it("winds clockwise: quarter turn lands to the right, half turn below", () => {
    const right = polar(100, 100, 50, 30, Math.PI / 2);
    expect(right.x).toBeCloseTo(150);
    expect(right.y).toBeCloseTo(100);

    const down = polar(100, 100, 50, 30, Math.PI);
    expect(down.x).toBeCloseTo(100);
    expect(down.y).toBeCloseTo(130);

    const left = polar(100, 100, 50, 30, (3 * Math.PI) / 2);
    expect(left.x).toBeCloseTo(50);
    expect(left.y).toBeCloseTo(100);
  });

  it("uses rx horizontally and ry vertically (elliptical radii)", () => {
    const up = polar(0, 0, 80, 20, 0);
    expect(up.y).toBeCloseTo(-20); // vertical extent = ry
    const side = polar(0, 0, 80, 20, Math.PI / 2);
    expect(side.x).toBeCloseTo(80); // horizontal extent = rx
  });
});

// ---------------------------------------------------------------------------
// prettyPredicate
// ---------------------------------------------------------------------------

describe("prettyPredicate", () => {
  it("replaces dashes and underscores with spaces", () => {
    expect(prettyPredicate("family-of")).toBe("family of");
    expect(prettyPredicate("works_with")).toBe("works with");
  });

  it("handles mixed separators and multiple occurrences", () => {
    expect(prettyPredicate("close-friend_of-mine")).toBe("close friend of mine");
  });

  it("leaves plain predicates untouched", () => {
    expect(prettyPredicate("knows")).toBe("knows");
  });
});

// ---------------------------------------------------------------------------
// layoutOwnerPlex
// ---------------------------------------------------------------------------

describe("layoutOwnerPlex", () => {
  const OWNER = "ent-owner";

  it("excludes the owner from nodes and tier counts", () => {
    const layout = layoutOwnerPlex(
      [
        dunbarEntry({ entity_id: OWNER, dunbar_tier: 5 }),
        dunbarEntry({ entity_id: "ent-a", dunbar_tier: 5 }),
      ],
      OWNER,
    );
    expect(layout.nodes.map((n) => n.entityId)).toEqual(["ent-a"]);
    expect(layout.tierCounts[5]).toBe(1);
  });

  it("counts every tier including 1500, defaulting untouched tiers to 0", () => {
    const layout = layoutOwnerPlex(
      [
        dunbarEntry({ entity_id: "a", dunbar_tier: 5 }),
        dunbarEntry({ entity_id: "b", dunbar_tier: 5 }),
        dunbarEntry({ entity_id: "c", dunbar_tier: 15 }),
        dunbarEntry({ entity_id: "d", dunbar_tier: 1500 }),
        dunbarEntry({ entity_id: "e", dunbar_tier: 1500 }),
        dunbarEntry({ entity_id: "f", dunbar_tier: 1500 }),
      ],
      OWNER,
    );
    expect(layout.tierCounts).toEqual({
      5: 2,
      15: 1,
      50: 0,
      150: 0,
      500: 0,
      1500: 3,
    });
  });

  it("does not draw nodes for tier-1500 entries", () => {
    const layout = layoutOwnerPlex(
      [
        dunbarEntry({ entity_id: "inner", dunbar_tier: 500 }),
        dunbarEntry({ entity_id: "periphery", dunbar_tier: 1500 }),
      ],
      OWNER,
    );
    expect(layout.nodes.map((n) => n.entityId)).toEqual(["inner"]);
    expect(layout.tierCounts[1500]).toBe(1);
  });

  it("orders nodes within a ring by score descending", () => {
    const layout = layoutOwnerPlex(
      [
        dunbarEntry({ entity_id: "low", dunbar_tier: 15, dunbar_score: 1 }),
        dunbarEntry({ entity_id: "high", dunbar_tier: 15, dunbar_score: 9 }),
        dunbarEntry({ entity_id: "mid", dunbar_tier: 15, dunbar_score: 5 }),
      ],
      OWNER,
    );
    const ring = layout.nodes.filter((n) => n.tier === 15);
    expect(ring.map((n) => n.entityId)).toEqual(["high", "mid", "low"]);
  });

  it("keeps every node angle inside the notched span, for all ring sizes", () => {
    // Varied bucket sizes per tier exercise the modulo wrap on every ring
    // (a regression here spills nodes into the 12 o'clock label notch).
    const entries: DunbarEntry[] = [];
    PLEX_NODE_TIERS.forEach((tier, tierIndex) => {
      const count = tierIndex + 1; // 1, 2, 3, 4, 5 nodes
      for (let i = 0; i < count; i++) {
        entries.push(dunbarEntry({ entity_id: `t${tier}-${i}`, dunbar_tier: tier }));
      }
    });
    const layout = layoutOwnerPlex(entries, OWNER);
    expect(layout.nodes.length).toBe(15);
    for (const node of layout.nodes) {
      expect(node.angle).toBeGreaterThanOrEqual(PLEX_TOP_NOTCH);
      expect(node.angle).toBeLessThanOrEqual(2 * Math.PI - PLEX_TOP_NOTCH);
    }
  });

  it("assigns the tier's ring fraction and mark size to each node", () => {
    const layout = layoutOwnerPlex(
      PLEX_NODE_TIERS.map((tier) =>
        dunbarEntry({ entity_id: `ent-${tier}`, dunbar_tier: tier }),
      ),
      OWNER,
    );
    for (const tier of PLEX_NODE_TIERS) {
      const node = layout.nodes.find((n) => n.entityId === `ent-${tier}`)!;
      expect(node.radiusFrac).toBe(PLEX_RING_FRACTIONS[tier]);
      expect(node.size).toBe(PLEX_MARK_SIZES[tier]);
    }
  });

  it("threads the tier-override pin through to the node", () => {
    const layout = layoutOwnerPlex(
      [
        dunbarEntry({ entity_id: "pinned", dunbar_tier: 5, dunbar_tier_override: true }),
        dunbarEntry({ entity_id: "auto", dunbar_tier: 5, dunbar_tier_override: false }),
      ],
      OWNER,
    );
    expect(layout.nodes.find((n) => n.entityId === "pinned")?.pinned).toBe(true);
    expect(layout.nodes.find((n) => n.entityId === "auto")?.pinned).toBe(false);
  });

  it("marks never-observed contacts as unknown staleness", () => {
    const layout = layoutOwnerPlex(
      [dunbarEntry({ entity_id: "ghost", dunbar_tier: 50, last_interaction_at: null })],
      OWNER,
    );
    expect(layout.nodes[0].staleDays).toBeNull();
    expect(layout.nodes[0].staleness).toBe("unknown");
  });
});

// ---------------------------------------------------------------------------
// layoutNeighbourPlex
// ---------------------------------------------------------------------------

describe("layoutNeighbourPlex", () => {
  it("returns empty nodes and sectors for an empty response", () => {
    expect(layoutNeighbourPlex(neighboursResponse({}))).toEqual({
      nodes: [],
      sectors: [],
    });
  });

  it("treats predicates whose lists are all empty as an empty response", () => {
    expect(
      layoutNeighbourPlex(neighboursResponse({ knows: [], "family-of": [] })),
    ).toEqual({ nodes: [], sectors: [] });
  });

  it("sizes sectors by neighbour count, non-overlapping within [0, 2π]", () => {
    const layout = layoutNeighbourPlex(
      neighboursResponse({
        knows: [
          neighbourEntry({ entity_id: "a" }),
          neighbourEntry({ entity_id: "b" }),
          neighbourEntry({ entity_id: "c" }),
        ],
        "works-with": [neighbourEntry({ entity_id: "d" })],
      }),
    );
    expect(layout.sectors.map((s) => s.predicate)).toEqual(["knows", "works-with"]);

    const [big, small] = layout.sectors;
    // Count-proportional: the 3-neighbour sector is strictly wider.
    expect(big.endAngle - big.startAngle).toBeGreaterThan(
      small.endAngle - small.startAngle,
    );
    // Non-overlapping, in order, inside [0, 2π].
    expect(big.startAngle).toBeGreaterThanOrEqual(0);
    expect(big.endAngle).toBeLessThan(small.startAngle);
    expect(small.endAngle).toBeLessThanOrEqual(2 * Math.PI);
    // midAngle bisects each sector.
    for (const s of layout.sectors) {
      expect(s.midAngle).toBeCloseTo((s.startAngle + s.endAngle) / 2);
      expect(s.endAngle).toBeGreaterThan(s.startAngle);
    }
  });

  it("dedupes forward+reverse rows of one entity within a predicate, keeping the heavier row", () => {
    const layout = layoutNeighbourPlex(
      neighboursResponse({
        knows: [
          neighbourEntry({
            entity_id: "dup",
            direction: "forward",
            weight: 2,
            conf: 0.4,
            verified: false,
          }),
          neighbourEntry({
            entity_id: "dup",
            direction: "reverse",
            weight: 9,
            conf: 0.9,
            verified: true,
          }),
        ],
      }),
    );
    expect(layout.nodes).toHaveLength(1);
    // The kept row is the heavier one — its conf/verified survive.
    expect(layout.nodes[0].conf).toBe(0.9);
    expect(layout.nodes[0].verified).toBe(true);
  });

  it("keeps duplicates of the same entity across different predicates", () => {
    const layout = layoutNeighbourPlex(
      neighboursResponse({
        knows: [neighbourEntry({ entity_id: "both" })],
        "family-of": [neighbourEntry({ entity_id: "both" })],
      }),
    );
    const forEntity = layout.nodes.filter((n) => n.entityId === "both");
    expect(forEntity).toHaveLength(2);
    expect(new Set(forEntity.map((n) => n.predicate))).toEqual(
      new Set(["knows", "family-of"]),
    );
  });

  it("threads per-predicate remainders onto sectors, defaulting to 0", () => {
    const layout = layoutNeighbourPlex(
      neighboursResponse(
        {
          knows: [neighbourEntry({ entity_id: "a" })],
          "works-with": [neighbourEntry({ entity_id: "b" })],
        },
        { knows: 4 },
      ),
    );
    const byPredicate = new Map(layout.sectors.map((s) => [s.predicate, s]));
    expect(byPredicate.get("knows")?.remainder).toBe(4);
    expect(byPredicate.get("works-with")?.remainder).toBe(0);
  });

  it("threads conf and staleness from each entry onto its node", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-01T12:00:00Z"));
    try {
      const layout = layoutNeighbourPlex(
        neighboursResponse({
          knows: [
            neighbourEntry({
              entity_id: "old",
              conf: 0.3,
              last_seen: "2025-01-01T00:00:00Z",
              weight: 5,
            }),
            neighbourEntry({ entity_id: "never", conf: 0.8, last_seen: null, weight: 1 }),
          ],
        }),
      );
      const old = layout.nodes.find((n) => n.entityId === "old")!;
      expect(old.conf).toBe(0.3);
      expect(old.staleness).toBe("hard");
      expect(old.staleDays).toBeGreaterThanOrEqual(180);
      const never = layout.nodes.find((n) => n.entityId === "never")!;
      expect(never.conf).toBe(0.8);
      expect(never.staleness).toBe("unknown");
      expect(never.staleDays).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("threads entity_type onto each node, defaulting a null type to person", () => {
    const layout = layoutNeighbourPlex(
      neighboursResponse({
        "works-at": [
          neighbourEntry({ entity_id: "org", entity_type: "organization" }),
          // Registry miss: entity_type is null and must fall back to person so
          // EntityMark draws initials instead of an empty type glyph.
          neighbourEntry({ entity_id: "unknown", entity_type: null }),
        ],
      }),
    );
    const org = layout.nodes.find((n) => n.entityId === "org")!;
    const unknown = layout.nodes.find((n) => n.entityId === "unknown")!;
    expect(org.entityType).toBe("organization");
    expect(unknown.entityType).toBe("person");
  });

  it("ranks nodes by weight descending within a sector, null weight last", () => {
    const layout = layoutNeighbourPlex(
      neighboursResponse({
        knows: [
          neighbourEntry({ entity_id: "unweighted", weight: null }),
          neighbourEntry({ entity_id: "light", weight: 1 }),
          neighbourEntry({ entity_id: "heavy", weight: 10 }),
        ],
      }),
    );
    expect(layout.nodes.map((n) => n.entityId)).toEqual([
      "heavy",
      "light",
      "unweighted",
    ]);
    // Rank order is also angular order within the sector.
    const angles = layout.nodes.map((n) => n.angle);
    expect([...angles].sort((a, b) => a - b)).toEqual(angles);
    // And all nodes sit inside their sector.
    const sector = layout.sectors[0];
    for (const a of angles) {
      expect(a).toBeGreaterThan(sector.startAngle);
      expect(a).toBeLessThan(sector.endAngle);
    }
  });
});

// ---------------------------------------------------------------------------
// layoutHalo
// ---------------------------------------------------------------------------

function satellite(
  overrides: Partial<HaloSatellite> & { entity_id: string },
): HaloSatellite {
  return {
    canonical_name: overrides.entity_id,
    last_seen: null,
    edges: [],
    ...overrides,
  };
}

function haloResponse(counts: Record<string, number>): HaloResponse {
  const arcs: Record<string, HaloSatellite[]> = {};
  const totals: Record<string, number> = {};
  for (const [type, n] of Object.entries(counts)) {
    arcs[type] = Array.from({ length: n }, (_, i) =>
      satellite({ entity_id: `${type}-${i}` }),
    );
    totals[type] = n;
  }
  return { arcs, totals };
}

describe("layoutHalo", () => {
  it("returns no arcs for an empty response", () => {
    expect(layoutHalo({ arcs: {}, totals: {} }).arcs).toEqual([]);
  });

  it("omits zero-count types and keeps the fixed arc order", () => {
    const layout = layoutHalo(
      haloResponse({ other: 3, organization: 5, place: 0 }),
    );
    expect(layout.arcs.map((a) => a.entityType)).toEqual([
      "organization",
      "other",
    ]);
  });

  it("appends unknown types alphabetically after the known order", () => {
    const layout = layoutHalo(
      haloResponse({ zeta: 1, organization: 2, alpha: 1 }),
    );
    expect(layout.arcs.map((a) => a.entityType)).toEqual([
      "organization",
      "alpha",
      "zeta",
    ]);
  });

  it("keeps every arc and mark inside the notched span", () => {
    const layout = layoutHalo(
      haloResponse({ organization: 20, place: 6, other: 12 }),
    );
    for (const arc of layout.arcs) {
      expect(arc.startAngle).toBeGreaterThanOrEqual(PLEX_TOP_NOTCH);
      expect(arc.endAngle).toBeLessThanOrEqual(2 * Math.PI - PLEX_TOP_NOTCH);
      for (const mark of arc.marks) {
        expect(mark.angle).toBeGreaterThan(arc.startAngle);
        expect(mark.angle).toBeLessThan(arc.endAngle);
      }
    }
  });

  it("separates adjacent arcs by the configured gap", () => {
    const layout = layoutHalo(
      haloResponse({ organization: 8, place: 4, other: 8 }),
    );
    for (let i = 1; i < layout.arcs.length; i++) {
      const gap = layout.arcs[i].startAngle - layout.arcs[i - 1].endAngle;
      expect(gap).toBeCloseTo(HALO_ARC_GAP, 10);
    }
  });

  it("keeps marks out of the label window at the arc midpoint", () => {
    const layout = layoutHalo(haloResponse({ organization: 20 }));
    const arc = layout.arcs[0];
    const span = arc.endAngle - arc.startAngle;
    const window = Math.min(0.5, span * 0.4);
    for (const mark of arc.marks) {
      expect(Math.abs(mark.angle - arc.midAngle)).toBeGreaterThanOrEqual(
        window / 2,
      );
    }
    // The reserved window did not drop any satellite.
    expect(arc.marks.length).toBe(20);
  });

  it("threads totals through, falling back to the shown count", () => {
    const response = haloResponse({ organization: 2 });
    response.totals = { organization: 171 };
    const layout = layoutHalo(response);
    expect(layout.arcs[0].total).toBe(171);

    const noTotals = haloResponse({ place: 3 });
    noTotals.totals = {};
    expect(layoutHalo(noTotals).arcs[0].total).toBe(3);
  });

  it("dedupes person ids and derives staleness from last_seen", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-03T12:00:00Z"));
    try {
      const response: HaloResponse = {
        arcs: {
          organization: [
            satellite({
              entity_id: "org-1",
              last_seen: "2026-07-01T12:00:00Z",
              edges: [
                { person_id: "p1", predicate: "works-at" },
                { person_id: "p1", predicate: "member-of" },
                { person_id: "p2", predicate: "works-at" },
              ],
            }),
          ],
        },
        totals: { organization: 1 },
      };
      const mark = layoutHalo(response).arcs[0].marks[0];
      expect(mark.personIds).toEqual(["p1", "p2"]);
      expect(mark.staleDays).toBe(2);
      expect(mark.staleness).toBe("fresh");
    } finally {
      vi.useRealTimers();
    }
  });

  it("places a lone satellite as a single mark inside its arc span", () => {
    // Boundary: leftCount = ceil(1/2) = 1, so the right segment holds zero
    // marks and its (segEnd - segStart) / 0 division must never run. Regression
    // guard against the single-satellite arc dropping or NaN-positioning a mark.
    const layout = layoutHalo(haloResponse({ organization: 1 }));
    expect(layout.arcs).toHaveLength(1);
    const arc = layout.arcs[0];
    expect(arc.marks).toHaveLength(1);
    const mark = arc.marks[0];
    expect(Number.isNaN(mark.angle)).toBe(false);
    expect(mark.angle).toBeGreaterThan(arc.startAngle);
    expect(mark.angle).toBeLessThan(arc.endAngle);
  });

  it("covers HALO_ARC_ORDER exactly once each in a full response", () => {
    const layout = layoutHalo(
      haloResponse({ organization: 1, place: 1, other: 1 }),
    );
    expect(layout.arcs.map((a) => a.entityType)).toEqual([...HALO_ARC_ORDER]);
  });
});
