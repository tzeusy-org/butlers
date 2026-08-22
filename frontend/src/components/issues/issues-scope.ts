/**
 * Scope vocabulary for the Issues feed (bu-6jv4m.3).
 *
 * Every Issues view is bounded: a time window (7d by default), optional
 * severity/butler pills, an optional text filter, and — for the exact Audit
 * evidence door — a single pinned group. An empty result inside those bounds
 * is a statement about the bounds, not about the fleet. Rendering it as "No
 * issues recorded." asserts a fleet-wide, all-time calm that the request never
 * established, which is exactly how a missed lookup used to read as an
 * all-clear.
 *
 * So the empty/all-clear copy is built from the scope that actually produced
 * it. One function, used by both the verdict opener and the panel, so the two
 * can never disagree about what was searched.
 */

export interface IssuesScope {
  /** Active time window token: "24h" | "7d" | "30d" | "all". */
  window: string;
  /** Exact `issue_key` pinned by the Audit door's `?group=` deep link. */
  group?: string;
  /** Free-text `?q=` substring filter. */
  q?: string;
  /** Selected severity pills. */
  severities?: string[];
  /** Selected butler pills. */
  butlers?: string[];
}

/**
 * Render *scope* as a phrase that slots after "in": e.g.
 * `No active issues in the last 7d, critical, matching “oauth”`.
 *
 * The window always leads, because it is always present — there is no
 * unscoped view of this feed to describe.
 */
export function describeIssuesScope(scope: IssuesScope): string {
  const parts: string[] = [scope.window === "all" ? "all time" : `the last ${scope.window}`];

  // The pinned group comes from the server-resolved Audit door, so the view is
  // one group wide however calm the rest of the fleet is.
  if (scope.group) parts.push("one selected group");
  if (scope.severities?.length) parts.push(scope.severities.join(" + "));
  if (scope.butlers?.length) parts.push(scope.butlers.join(" + "));
  if (scope.q) parts.push(`matching “${scope.q}”`);

  return parts.join(", ");
}
