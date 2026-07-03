/**
 * The single command/route registry (bu-86c4c.7 — "One command spine").
 *
 * Before this, the sidebar (nav-config.ts), the two command palettes, the
 * g-chord switch statement in use-keyboard-shortcuts.ts, and the '?' help
 * sheet each hand-maintained their own copy of "what pages exist and how do
 * you reach them" — and they drifted (g-h pointed at the pre-redesign
 * /health/measurements route; /costs, /groups, /approvals/rules, and five of
 * six health sub-pages were never indexed anywhere but the router).
 *
 * This module is the one place that answers "what routes exist, and how are
 * they reached": every entrypoint (sidebar, command menu Pages group,
 * g-chords, the help sheet) reads from `ALL_ROUTES` / `G_CHORD_ROUTES` here.
 * A route that only lives in `EXTRA_ROUTES` is not orphaned — it's simply not
 * promoted to the sidebar, but the command menu and help sheet still index
 * it.
 */

import { navSections, type NavItem, type NavFlatItem } from "@/components/layout/nav-config";

export interface RouteEntry extends NavFlatItem {
  /** Section/group label shown in the command menu's Pages group and the help sheet. */
  section: string;
}

function flattenNavItems(items: NavItem[], section: string): RouteEntry[] {
  const result: RouteEntry[] = [];
  for (const item of items) {
    if (item.kind === "group") {
      for (const child of item.children) result.push({ ...child, section });
    } else {
      result.push({ ...item, section });
    }
  }
  return result;
}

/** Every route currently promoted to the sidebar. */
const SIDEBAR_ROUTES: RouteEntry[] = navSections.flatMap((s) =>
  flattenNavItems(s.items, s.title),
);

/**
 * Routes that exist in the app but are intentionally NOT promoted to the
 * sidebar — sub-pages reached by drilling in from a parent, or pages whose
 * primary entry point elsewhere is a redirect. Still indexed here so the
 * command menu, g-chords, and the '?' help sheet can reach them directly;
 * this is what "so /costs, /groups, and the six health sub-pages can never
 * be orphaned again" means in practice. (/approvals/rules was one of these
 * until bu-86c4c.12 merged it into /approvals as the always-visible Autonomy
 * panel and deleted the standalone route — nothing to index anymore.)
 */
const EXTRA_ROUTES: RouteEntry[] = [
  { path: "/costs", label: "Costs", section: "Main" },
  { path: "/groups", label: "Groups", section: "Main" },
  { path: "/health/measurements", label: "Measurements", section: "Health", butler: "health" },
  { path: "/health/medications", label: "Medications", section: "Health", butler: "health" },
  { path: "/health/conditions", label: "Conditions", section: "Health", butler: "health" },
  { path: "/health/symptoms", label: "Symptoms", section: "Health", butler: "health" },
  { path: "/health/meals", label: "Meals", section: "Health", butler: "health" },
  { path: "/health/research", label: "Research", section: "Health", butler: "health" },
  { path: "/settings/spend", label: "Spend Settings", section: "Settings" },
  { path: "/settings/permissions", label: "Permissions", section: "Settings" },
  { path: "/settings/models", label: "Models", section: "Settings" },
  { path: "/entities/index", label: "Entities Index", section: "Entities" },
  { path: "/entities/concentration", label: "Concentration", section: "Entities" },
  { path: "/qa/investigations", label: "QA Investigations", section: "QA" },
  // /contacts itself is a compatibility redirect (public.contacts was
  // dropped, core_134); index the real destination directly so the g-chord
  // and command menu don't bounce through it.
  { path: "/entities/index?has=contact", label: "Contacts", section: "Entities", chord: "c" },
];

/** Every known route: sidebar-promoted + extras. The palette's Pages source. */
export const ALL_ROUTES: RouteEntry[] = [...SIDEBAR_ROUTES, ...EXTRA_ROUTES];

/** `g`-chord letter → destination path, derived from the routes that declare one. */
export const G_CHORD_ROUTES: Record<string, string> = Object.fromEntries(
  ALL_ROUTES.filter((r): r is RouteEntry & { chord: string } => !!r.chord).map((r) => [
    r.chord,
    r.path,
  ]),
);
