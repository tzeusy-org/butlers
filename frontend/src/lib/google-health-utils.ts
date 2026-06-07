/**
 * Utility functions for Google Health connector UI logic.
 *
 * Extracted from pages.tsx so they can be shared / tested without triggering
 * the react-refresh/only-export-components rule (which fires when non-component
 * symbols are exported from a file that also exports React components).
 *
 * All helpers are pure functions. Callers inside React components must NOT call
 * them with the default `nowMs` parameter during render (that would call Date.now()
 * inside the render phase, violating react-hooks/purity). Instead, capture nowMs
 * once in a module-level helper outside the render body and pass it explicitly.
 *
 * [bu-hh875]
 */

/** Test-mode token expiry threshold in milliseconds (6 of 7 days elapsed). */
export const TEST_MODE_WARN_AGE_MS = 6 * 24 * 60 * 60 * 1000;

/**
 * Returns true when a test-mode token is near (or past) the 7-day expiry.
 * Near = the token's last_token_refresh_at is >= 6 days ago (leaving < 24 h).
 *
 * Returns false when last_token_refresh_at is null (no signal — don't warn).
 *
 * @param lastTokenRefreshAt ISO-8601 timestamp string or null.
 * @param nowMs              Current time in milliseconds (defaults to Date.now()).
 *                           Accepted as a parameter to keep the function pure
 *                           and testable without mocking timers.
 */
export function isTestModeTokenNearExpiry(
  lastTokenRefreshAt: string | null,
  nowMs: number = Date.now(),
): boolean {
  if (!lastTokenRefreshAt) return false;
  const refreshed = new Date(lastTokenRefreshAt).getTime();
  if (isNaN(refreshed)) return false;
  const ageMs = nowMs - refreshed;
  return ageMs >= TEST_MODE_WARN_AGE_MS;
}

/**
 * Returns true when a test-mode token has passed the 7-day expiry.
 *
 * @param lastTokenRefreshAt ISO-8601 timestamp string or null.
 * @param nowMs              Current time in milliseconds (defaults to Date.now()).
 */
export function isTestModeTokenExpired(
  lastTokenRefreshAt: string | null,
  nowMs: number = Date.now(),
): boolean {
  if (!lastTokenRefreshAt) return false;
  const refreshed = new Date(lastTokenRefreshAt).getTime();
  if (isNaN(refreshed)) return false;
  const ageMs = nowMs - refreshed;
  return ageMs >= 7 * 24 * 60 * 60 * 1000;
}
