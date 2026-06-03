/**
 * SubpageTabs — horizontal nav strip for the /entities sub-route family.
 *
 * Renders links to: Index / Hop / Columns / Concentration / Social-map.
 * The active tab is determined by the current pathname (exact match on the
 * canonical path for each tab).
 *
 * Design: plain horizontal link list, no card chrome, no gradient. Each tab
 * is a React Router <NavLink> so active styling is applied automatically.
 * Renders inside the Page shell (archetype="overview") above the main content.
 *
 * Spec: openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/tasks.md §8.6
 */

import { NavLink } from "react-router";

const TABS = [
  { label: "Index", to: "/entities" },
  { label: "Hop", to: "/entities/hop" },
  { label: "Columns", to: "/entities/columns" },
  { label: "Concentration", to: "/entities/concentration" },
  { label: "Social map", to: "/entities/social-map" },
] as const;

interface SubpageTabsProps {
  className?: string;
}

/**
 * Horizontal tab strip for /entities sub-routes.
 *
 * Each tab is a NavLink — React Router applies `aria-current="page"` to the
 * active link automatically, which the active class styling reads from.
 * The Index tab uses `end` matching so it does not stay active on sub-routes.
 */
export function SubpageTabs({ className }: SubpageTabsProps) {
  return (
    <nav
      aria-label="Entity views"
      className={`flex gap-1 border-b border-border pb-0 ${className ?? ""}`}
    >
      {TABS.map(({ label, to }) => (
        <NavLink
          key={to}
          to={to}
          end={to === "/entities"}
          className={({ isActive }) =>
            [
              "px-3 py-2 text-sm font-medium transition-colors",
              "hover:text-foreground",
              isActive
                ? "border-b-2 border-foreground text-foreground -mb-px"
                : "text-muted-foreground border-b-2 border-transparent -mb-px",
            ].join(" ")
          }
        >
          {label}
        </NavLink>
      ))}
    </nav>
  );
}
