/**
 * Soft-failure handling for calendar MCP mutation envelopes: the tools answer
 * HTTP 200 with a `status` / `persisted` payload, so success is decided here.
 */

import type { CalendarWorkspaceUserMutationAction } from "@/api/types.ts";

export function maybeText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Soft-failure status values returned by the calendar MCP mutation tools inside
 * an HTTP 200 response. The genuine-success statuses are open-ended
 * (`created` / `updated` / `deleted` / `ok` / queued approvals, etc.), so the
 * gate is a denylist of known failure states rather than an `ok`-only allowlist
 * — that keeps every real success path intact while catching soft failures.
 */
// NOTE: 'conflict' is intentionally excluded — it is handled as an interactive
// conflict-resolution flow (ConflictCard), not a terminal failure.
export const CALENDAR_MUTATION_FAILURE_STATUSES = new Set([
  "error",
  "not_found",
  "failed",
]);

/**
 * Inspect a calendar MCP mutation result envelope to distinguish genuine
 * success from a soft failure.
 *
 * The calendar MCP tools fail SOFT: they return `status: 'error' | 'conflict' |
 * 'not_found' | 'failed'` (and `set-primary` can return `status: 'ok',
 * persisted: false`) inside an HTTP 200 response. Treating any 200 as success
 * makes the UI claim a change happened when it did not. A mutation is only OK
 * when its `status` is not a known failure value AND `persisted` is not `false`.
 *
 * `result` is the `data.result` payload from a calendar workspace mutation
 * response (or any object exposing `status` / `persisted`).
 */
export function isCalendarMutationOk(result: unknown): boolean {
  if (typeof result !== "object" || result === null) {
    return false;
  }
  const record = result as Record<string, unknown>;
  if (record.persisted === false) {
    return false;
  }
  const status = maybeText(record.status);
  if (status && CALENDAR_MUTATION_FAILURE_STATUSES.has(status)) {
    return false;
  }
  return true;
}

/**
 * Build a human-readable failure message from a soft-failed mutation envelope,
 * preferring an explicit `error`/`message`/`detail` field and falling back to
 * the `status` string.
 */
export function calendarMutationErrorMessage(
  result: unknown,
  fallback: string,
): string {
  if (typeof result === "object" && result !== null) {
    const record = result as Record<string, unknown>;
    const explicit =
      maybeText(record.error) ||
      maybeText(record.message) ||
      maybeText(record.detail);
    if (explicit) {
      return explicit;
    }
    if (record.persisted === false) {
      return "no change was persisted";
    }
    const status = maybeText(record.status);
    if (status) {
      return status;
    }
  }
  return fallback;
}

export function buildRequestId(action: CalendarWorkspaceUserMutationAction): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return `calendar-${action}-${crypto.randomUUID()}`;
  }
  return `calendar-${action}-${Date.now()}`;
}
