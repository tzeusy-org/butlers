/**
 * Pure utilities for GoogleHealthStatusCard.
 *
 * Extracted here so callers can unit-test the logic without importing
 * React or rendering the component (resolves react-refresh/only-export-components).
 */

/** Seven-day expiry window used for test-mode OAuth tokens (ms). */
export const TEST_MODE_TOKEN_TTL_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * Returns a human-readable token expiry estimate string.
 *
 * - ``test_mode === true``: counts down from ``last_token_refresh_at + 7 days``.
 * - ``test_mode === false``: long-lived production token.
 * - ``last_token_refresh_at`` absent in test mode: "Unknown".
 */
export function computeTokenExpiry(
  testMode: boolean,
  lastTokenRefreshAt: string | null,
  now: Date = new Date(),
): string {
  if (!testMode) {
    return "Long-lived (production mode)";
  }
  if (!lastTokenRefreshAt) {
    return "Unknown";
  }
  try {
    const refreshDate = new Date(lastTokenRefreshAt);
    if (Number.isNaN(refreshDate.getTime())) return "Unknown";
    const expiryMs = refreshDate.getTime() + TEST_MODE_TOKEN_TTL_MS;
    const remainingMs = expiryMs - now.getTime();
    if (remainingMs <= 0) {
      return "Expired";
    }
    const totalSeconds = Math.floor(remainingMs / 1000);
    const days = Math.floor(totalSeconds / 86400);
    const hours = Math.floor((totalSeconds % 86400) / 3600);
    return `in ~${days}d ${hours}h`;
  } catch {
    return "Unknown";
  }
}
