// ---------------------------------------------------------------------------
// Tab configuration for ButlerDetailPage
// ---------------------------------------------------------------------------
//
// Gate B (bu-41p8z) resolved to B2: operator/resident mode toggle.
// Operator mode: full 10 spec-mandated base tabs (dashboard-butler-management spec.md:55, 178-179).
// Resident mode: narrow 7-tab Dispatch vocabulary; this is the default for first-time visitors.
// The toggle UI and localStorage persistence are wired up in ButlerDetailPage; mode survives reloads.
//
// Extracted from ButlerDetailPage.tsx so that the page module only exports its
// component (react-refresh/only-export-components).

/** Full 10 spec-mandated base tabs — shown in operator mode. */
export const BASE_TABS_OPERATOR = [
  "overview",
  "sessions",
  "config",
  "skills",
  "schedules",
  "trigger",
  "mcp",
  "state",
  "crm",
  "memory",
] as const;

/** Narrow 7-tab Dispatch vocabulary — shown in resident mode (future default). */
export const BASE_TABS_RESIDENT = [
  "overview",
  "activity",
  "logs",
  "approvals",
  "spend",
  "config",
  "memory",
] as const;

/**
 * Non-spec extension tab: Models.
 * Operator-only; not part of the 10 mandated base tabs.
 * Does not appear in resident mode.
 */
export const OPERATOR_EXTENSION_TABS = ["models"] as const;

// Butler-specific conditional tabs (health, switchboard routing, education reviews).
// Appended after the base tabs; visible regardless of mode.
const HEALTH_TABS = ["health"] as const;
const SWITCHBOARD_TABS = ["routing-log", "registry"] as const;
const EDUCATION_TABS = ["reviews"] as const;

// Bespoke tabs for domain butlers (stub UI — full implementation tracked separately).
const CHRONICLER_TABS = ["timelines"] as const;
const FINANCE_TABS = ["finances"] as const;
const GENERAL_TABS = ["collections"] as const;
const HOME_TABS = ["devices"] as const;
const LIFESTYLE_TABS = ["taste"] as const;
const MESSENGER_TABS = ["conversations"] as const;
const QA_TABS = ["investigations"] as const;
const RELATIONSHIP_TABS = ["contacts"] as const;
const TRAVEL_TABS = ["trips"] as const;

export type DetailMode = "operator" | "resident";

export type TabValue =
  | (typeof BASE_TABS_OPERATOR)[number]
  | (typeof BASE_TABS_RESIDENT)[number]
  | (typeof OPERATOR_EXTENSION_TABS)[number]
  | (typeof HEALTH_TABS)[number]
  | (typeof SWITCHBOARD_TABS)[number]
  | (typeof EDUCATION_TABS)[number]
  | (typeof CHRONICLER_TABS)[number]
  | (typeof FINANCE_TABS)[number]
  | (typeof GENERAL_TABS)[number]
  | (typeof HOME_TABS)[number]
  | (typeof LIFESTYLE_TABS)[number]
  | (typeof MESSENGER_TABS)[number]
  | (typeof QA_TABS)[number]
  | (typeof RELATIONSHIP_TABS)[number]
  | (typeof TRAVEL_TABS)[number];

/**
 * Returns the full set of valid tab values for the given butler and mode.
 * Operator mode: 10 spec-mandated base tabs + extension tabs (models).
 * Resident mode: 7-tab Dispatch vocabulary.
 * Butler-specific conditional tabs (health, switchboard) are appended
 * regardless of mode.
 */
export function getAllTabs(butlerName: string, mode: DetailMode): readonly string[] {
  const baseTabs: string[] =
    mode === "operator"
      ? [...BASE_TABS_OPERATOR, ...OPERATOR_EXTENSION_TABS]
      : [...BASE_TABS_RESIDENT];
  if (butlerName === "health") {
    baseTabs.push(...HEALTH_TABS);
  }
  if (butlerName === "switchboard") {
    baseTabs.push(...SWITCHBOARD_TABS);
  }
  if (butlerName === "education") {
    baseTabs.push(...EDUCATION_TABS);
  }
  if (butlerName === "chronicler") {
    baseTabs.push(...CHRONICLER_TABS);
  }
  if (butlerName === "finance") {
    baseTabs.push(...FINANCE_TABS);
  }
  if (butlerName === "general") {
    baseTabs.push(...GENERAL_TABS);
  }
  if (butlerName === "home") {
    baseTabs.push(...HOME_TABS);
  }
  if (butlerName === "lifestyle") {
    baseTabs.push(...LIFESTYLE_TABS);
  }
  if (butlerName === "messenger") {
    baseTabs.push(...MESSENGER_TABS);
  }
  if (butlerName === "qa") {
    baseTabs.push(...QA_TABS);
  }
  if (butlerName === "relationship") {
    baseTabs.push(...RELATIONSHIP_TABS);
  }
  if (butlerName === "travel") {
    baseTabs.push(...TRAVEL_TABS);
  }
  return baseTabs;
}

/**
 * Returns true if `value` is a valid tab for the given butler and mode.
 */
export function isValidTab(
  value: string | null,
  butlerName: string,
  mode: DetailMode,
): value is TabValue {
  return getAllTabs(butlerName, mode).includes(value as string);
}
