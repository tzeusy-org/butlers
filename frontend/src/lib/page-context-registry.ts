/**
 * Per-route page-context registry (bu-0ynlk.4).
 *
 * Every path in `SHELL_CAPABILITIES` (see `./shell-capability.ts`) needs a
 * descriptor here: what the ContextChip should attach by default, and how
 * much of it (`contextPolicy`). Keyed off the same path strings used in
 * `router-config.tsx` (including dynamic segments verbatim, e.g.
 * `"/sessions/:id"`), so `resolvePageContextDescriptor()` mirrors
 * `resolveShellCapability()`'s dynamic-segment matching.
 *
 * `contextPolicy` values:
 *   - "snapshot" — full typed capture (route, query_params, visible_resource,
 *     visible_summary), subject to the backend's redaction/size-budget
 *     validators. The default for ordinary dashboard pages.
 *   - "ref-only" — only `{ route }` is attached; no query params or resource
 *     detail. For destinations whose state is sensitive-adjacent but whose
 *     route alone is still useful grounding (e.g. "the owner was on the
 *     Models settings page").
 *   - "none" — no page_context is attached at all; the ContextChip renders
 *     a static "context not attached on this page" notice instead of a
 *     removable chip. For destinations that must never leak their state into
 *     a prompt (`/secrets`, permission grants).
 *
 * `about/heart-and-soul/security.md`: page context is the one path shipping
 * arbitrary dashboard state into a prompt, so this registry (not per-page ad
 * hoc judgment) is the single place that decision is made.
 */

export type ContextPolicy = "snapshot" | "ref-only" | "none";

export interface PageContextDescriptor {
  policy: ContextPolicy;
  /** Fallback ContextChip label when no `usePageSubject()` override is active. */
  summary: string;
}

const SNAPSHOT = (summary: string): PageContextDescriptor => ({ policy: "snapshot", summary });

/** One descriptor per `SHELL_CAPABILITIES` path (see shell-capability.ts). */
export const PAGE_CONTEXT_REGISTRY: Record<string, PageContextDescriptor> = {
  "/": SNAPSHOT("Overview"),
  "/butlers": SNAPSHOT("Butlers"),
  "/qa": SNAPSHOT("QA"),
  "/ingestion": SNAPSHOT("Ingestion timeline"),
  "/approvals": SNAPSHOT("Approvals"),
  "/decisions": SNAPSHOT("Decisions"),
  "/memory": SNAPSHOT("Memory"),
  "/entities": SNAPSHOT("Entities"),
  "/secrets": { policy: "none", summary: "Secrets" },
  "/settings": SNAPSHOT("Settings"),

  "/education": SNAPSHOT("Education"),
  "/health": SNAPSHOT("Health overview"),
  "/calendar": SNAPSHOT("Calendar"),
  "/chronicles": SNAPSHOT("Chronicles"),

  "/timeline": SNAPSHOT("Timeline"),
  "/notifications": SNAPSHOT("Notifications"),
  "/issues": SNAPSHOT("Issues"),
  "/sessions": SNAPSHOT("Sessions"),
  "/spend": SNAPSHOT("Spend"),
  "/audit-log": SNAPSHOT("Audit log"),
  "/system": SNAPSHOT("System"),

  "/health/measurements": SNAPSHOT("Health measurements"),
  "/health/medications": SNAPSHOT("Health medications"),
  "/health/conditions": SNAPSHOT("Health conditions"),
  "/health/symptoms": SNAPSHOT("Health symptoms"),
  "/health/meals": SNAPSHOT("Health meals"),
  "/health/research": SNAPSHOT("Health research"),

  // Grants/permissions and model-routing config are sensitive-adjacent: the
  // route alone is useful grounding, but query params/resource detail are not.
  "/settings/permissions": { policy: "none", summary: "Permissions" },
  "/settings/models": { policy: "ref-only", summary: "Models" },

  "/entities/index": SNAPSHOT("Entities index"),
  // SHELL_CAPABILITIES carries this literal query-string path as a distinct
  // command-palette entry ("Contacts"); it resolves to the same page as
  // "/entities/index" via resolvePageContextDescriptor()'s query stripping,
  // but a direct registry lookup by capability.path needs its own key.
  "/entities/index?has=contact": SNAPSHOT("Contacts"),
  "/entities/concentration": SNAPSHOT("Concentration"),
  "/entities/circles": SNAPSHOT("Circles"),
  "/ingestion/connectors": SNAPSHOT("Connectors"),
  "/ingestion/filters": SNAPSHOT("Ingestion filters"),

  "/butlers/:name": SNAPSHOT("Butler detail"),
  "/sessions/:id": SNAPSHOT("Session detail"),
  "/entities/:entityId": SNAPSHOT("Entity detail"),
  "/memory/facts/:factId": SNAPSHOT("Fact detail"),
  "/memory/rules/:ruleId": SNAPSHOT("Rule detail"),
  "/memory/episodes/:episodeId": SNAPSHOT("Episode detail"),
  "/approvals/:id": SNAPSHOT("Approval detail"),
  "/beads/:beadId": SNAPSHOT("Bead detail"),
  "/ingestion/connectors/:connectorType/:endpointIdentity": SNAPSHOT("Connector detail"),
  "/qa/patrols/:patrolId": SNAPSHOT("QA patrol detail"),
  "/qa/investigations/:attemptId": SNAPSHOT("QA investigation detail"),
};

/** Default descriptor for any route absent from the registry (should not happen). */
export const DEFAULT_PAGE_CONTEXT_DESCRIPTOR: PageContextDescriptor = SNAPSHOT("Dashboard");

function segmentPattern(pattern: string, pathname: string): boolean {
  const expected = pattern.split("/");
  const actual = pathname.split("/");
  if (expected.length !== actual.length) return false;
  return expected.every((part, i) => part.startsWith(":") || part === actual[i]);
}

/** Resolve a descriptor for a live pathname, matching dynamic segments. */
export function resolvePageContextDescriptor(pathname: string): PageContextDescriptor {
  const path = pathname.split("?", 1)[0].split("#", 1)[0];
  if (PAGE_CONTEXT_REGISTRY[path]) return PAGE_CONTEXT_REGISTRY[path];
  for (const [pattern, descriptor] of Object.entries(PAGE_CONTEXT_REGISTRY)) {
    if (pattern.includes(":") && segmentPattern(pattern, path)) return descriptor;
  }
  return DEFAULT_PAGE_CONTEXT_DESCRIPTOR;
}
