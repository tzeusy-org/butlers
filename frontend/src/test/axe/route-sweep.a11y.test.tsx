// @vitest-environment jsdom
/**
 * Route-registry-driven axe completeness sweep (bu-qvnce.10, JARVIS pursuit
 * move 10).
 *
 * Before this, only 3 of the dashboard's ~38 routed pages had a real axe
 * pass (ButlersPage.a11y.test.tsx, ButlerDetailPage.a11y.test.tsx,
 * TimelineTab.a11y.test.tsx) — and nothing forced a newly added page to earn
 * one. This suite generalizes that pattern into a completeness CHECK driven
 * by `ALL_ROUTES` (src/lib/route-registry.ts — the app's single command/
 * route registry, already the source of truth for the sidebar, the command
 * menu's Pages group, g-chords, and the help sheet):
 *
 *   every route in ALL_ROUTES must be either
 *     (a) COVERED_ELSEWHERE — a real axe pass already exists (see the map
 *         below), or
 *     (b) in AXE_SKIP_MANIFEST (./skip-manifest.ts) with an honest,
 *         specific reason.
 *
 * A route with NEITHER fails this suite — that's the structural guarantee:
 * a newly added page is either given real axe coverage or an explicit,
 * reviewable "not yet, because X" entry. It can never be silently invisible
 * to the accessibility gate the way all but 3 pages were before this bead.
 *
 * Scope note: ALL_ROUTES indexes top-level/listing surfaces (sidebar +
 * EXTRA_ROUTES) — it deliberately does not enumerate every dynamic detail
 * route (e.g. /butlers/:name, /entities/:entityId, /sessions/:id), since
 * those aren't nav-registered in the first place. Some of those already
 * have their own dedicated axe test (ButlerDetailPage.a11y.test.tsx) as
 * coverage beyond what this registry-driven sweep enumerates; expanding
 * ALL_ROUTES itself (or a future sibling registry for detail routes) is the
 * structural lever for bringing them under this same completeness check.
 *
 * This is a burn-down list, NOT a gate-blocker: the vast majority of routes
 * start in the skip manifest. What's non-negotiable is that the manifest
 * stays honest (a real, specific reason) and complete (nothing falls
 * through the cracks silently).
 */

import { describe, expect, it } from "vitest";

import { ALL_ROUTES } from "@/lib/route-registry";
import { AXE_SKIP_MANIFEST } from "./skip-manifest";

// ---------------------------------------------------------------------------
// Routes with a real, passing axe test elsewhere in the tree today.
// ---------------------------------------------------------------------------

const COVERED_ELSEWHERE: Record<string, string> = {
  "/butlers": "src/pages/ButlersPage.a11y.test.tsx",
  "/timeline": "src/pages/TimelinePage.a11y.test.tsx",
  // "/ingestion" itself renders IngestionTabRedirect (a redirect, not a real
  // page), but the ingestion ledger UI it redirects to IS axe-tested
  // directly at the component level (mounted without going through this
  // route) — recorded here, not the skip manifest, since it's genuinely
  // covered, just not via this exact route path.
  "/ingestion": "src/components/ingestion/TimelineTab.a11y.test.tsx (component-level, not routed)",
  "/decisions": "src/pages/DecisionsPage.a11y.test.tsx",
};

// ---------------------------------------------------------------------------
// Completeness check
// ---------------------------------------------------------------------------

describe("route registry axe coverage completeness (bu-qvnce.10)", () => {
  it("every ALL_ROUTES path is covered elsewhere or explicitly skipped with a reason", () => {
    const skipPaths = new Set(AXE_SKIP_MANIFEST.map((entry) => entry.path));
    const uncovered = ALL_ROUTES.map((route) => route.path).filter(
      (path) => !(path in COVERED_ELSEWHERE) && !skipPaths.has(path),
    );
    expect(uncovered).toEqual([]);
  });

  it("every skip-manifest entry names a real, specific reason (not a placeholder)", () => {
    for (const entry of AXE_SKIP_MANIFEST) {
      expect(entry.reason.length).toBeGreaterThan(20);
      expect(entry.reason.trim()).not.toBe("");
    }
  });

  it("the skip manifest has no stale entries for routes ALL_ROUTES no longer has", () => {
    const routePaths = new Set(ALL_ROUTES.map((route) => route.path));
    const stale = AXE_SKIP_MANIFEST.map((entry) => entry.path).filter((path) => !routePaths.has(path));
    expect(stale).toEqual([]);
  });

  it("COVERED_ELSEWHERE and the skip manifest do not both claim the same route", () => {
    const skipPaths = new Set(AXE_SKIP_MANIFEST.map((entry) => entry.path));
    const overlap = Object.keys(COVERED_ELSEWHERE).filter((path) => skipPaths.has(path));
    expect(overlap).toEqual([]);
  });
});
