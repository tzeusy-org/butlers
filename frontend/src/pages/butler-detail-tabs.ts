// ---------------------------------------------------------------------------
// Tab configuration for ButlerDetailPage
// ---------------------------------------------------------------------------
//
// One butler console, one tab set (bu-86c4c.18 -- JARVIS audit move 13).
//
// The old resident/operator mode split (Gate B bu-8bayc) hid half the console
// behind an undiscoverable toggle: Sessions was unreachable in resident mode,
// Activity/Approvals/Spend were unreachable in operator mode. That toggle,
// its localStorage persistence, and the deep-link auto-promotion machinery
// have all been deleted. There is now exactly one tab vocabulary for every
// butler.
//
// Low-frequency operational tabs (Config, Skills, Schedules, MCP, State,
// Models, Manage) are folded into a single "System" section rendered by
// <ButlerSystemSection>. Sessions and Logs are folded into "Activity",
// rendered by <ButlerActivitySection> alongside the existing analytics body.
//
// Extracted from ButlerDetailPage.tsx so that the page module only exports
// its component (react-refresh/only-export-components).

/** The single base tab vocabulary shown for every butler. */
export const BASE_TABS = [
  "overview",
  "activity",
  "approvals",
  "spend",
  "memory",
  "system",
] as const;

// Butler-specific conditional (domain) tabs. Appended after the base tabs;
// each butler shows at most its own bespoke tab(s), always reachable
// (never gated behind a mode).
const HEALTH_TABS = ["health"] as const;
const SWITCHBOARD_TABS = ["routing-log", "registry"] as const;
const EDUCATION_TABS = ["reviews"] as const;
const CHRONICLER_TABS = ["timelines"] as const;
const FINANCE_TABS = ["finances"] as const;
const GENERAL_TABS = ["collections", "entities"] as const;
const HOME_TABS = ["devices"] as const;
const LIFESTYLE_TABS = ["taste"] as const;
const QA_TABS = ["investigations"] as const;
const RELATIONSHIP_TABS = ["contacts"] as const;
const TRAVEL_TABS = ["trips"] as const;

export type TabValue =
  | (typeof BASE_TABS)[number]
  | (typeof HEALTH_TABS)[number]
  | (typeof SWITCHBOARD_TABS)[number]
  | (typeof EDUCATION_TABS)[number]
  | (typeof CHRONICLER_TABS)[number]
  | (typeof FINANCE_TABS)[number]
  | (typeof GENERAL_TABS)[number]
  | (typeof HOME_TABS)[number]
  | (typeof LIFESTYLE_TABS)[number]
  | (typeof QA_TABS)[number]
  | (typeof RELATIONSHIP_TABS)[number]
  | (typeof TRAVEL_TABS)[number];

/**
 * Returns the full set of valid tab values for the given butler: the shared
 * base vocabulary plus that butler's bespoke domain tab(s), if any.
 */
export function getAllTabs(butlerName: string): readonly string[] {
  const tabs: string[] = [...BASE_TABS];
  if (butlerName === "health") tabs.push(...HEALTH_TABS);
  if (butlerName === "switchboard") tabs.push(...SWITCHBOARD_TABS);
  if (butlerName === "education") tabs.push(...EDUCATION_TABS);
  if (butlerName === "chronicler") tabs.push(...CHRONICLER_TABS);
  if (butlerName === "finance") tabs.push(...FINANCE_TABS);
  if (butlerName === "general") tabs.push(...GENERAL_TABS);
  if (butlerName === "home") tabs.push(...HOME_TABS);
  if (butlerName === "lifestyle") tabs.push(...LIFESTYLE_TABS);
  if (butlerName === "qa") tabs.push(...QA_TABS);
  if (butlerName === "relationship") tabs.push(...RELATIONSHIP_TABS);
  if (butlerName === "travel") tabs.push(...TRAVEL_TABS);
  return tabs;
}

/**
 * Returns true if `value` is a valid top-level tab for the given butler.
 */
export function isValidTab(value: string | null, butlerName: string): value is TabValue {
  return getAllTabs(butlerName).includes(value as string);
}
