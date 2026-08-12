/**
 * Route-registry-driven axe sweep — skip manifest (bu-qvnce.10, JARVIS
 * pursuit move 10).
 *
 * Burn-down list, NOT a gate-blocker: `route-sweep.a11y.test.tsx` asserts
 * that every path in `ALL_ROUTES` (src/lib/route-registry.ts) is either
 * axe-covered elsewhere (see `COVERED_ELSEWHERE` in that file) or has an
 * honest entry here explaining why it isn't yet. A route with NEITHER —
 * i.e. a newly added page nobody has made a decision about — fails the
 * sweep's completeness check. That's what "new pages get axe coverage
 * structurally" means in practice: silence is not an option, only an
 * explicit "covered" or "skipped, because X".
 *
 * To retire an entry: build it the way ButlersPage.a11y.test.tsx /
 * ButlerDetailPage.a11y.test.tsx / TimelineTab.a11y.test.tsx do — mock only
 * the page's data-hook layer (reuse the `vi.mock` list from its existing
 * `*.test.tsx`, referenced below where one exists), render the REAL page
 * component, run axe with `color-contrast` disabled (jsdom can't compute
 * it — see src/lib/contrast.test.ts for that coverage) — then delete the
 * entry here and add the route to `COVERED_ELSEWHERE`.
 */

export interface AxeSkipEntry {
  /** Exact path as it appears in ALL_ROUTES. */
  path: string;
  /** Honest, specific reason this route isn't swept yet. */
  reason: string;
}

export const AXE_SKIP_MANIFEST: AxeSkipEntry[] = [
  {
    path: "/ingestion/connectors",
    reason:
      "The connector roster has a dedicated route contract suite, but its provider and credential states still require isolated axe fixtures before joining this sweep.",
  },
  {
    path: "/ingestion/filters",
    reason:
      "The filters pipeline has a dedicated route contract suite, but its editor and server-state permutations still require isolated axe fixtures before joining this sweep.",
  },
];
