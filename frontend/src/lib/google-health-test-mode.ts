/**
 * Helpers for the Google Health status card's test-mode warning banner.
 *
 * Kept in a plain .ts file (not colocated with the component) so it can
 * be unit-tested directly and so ``react-refresh/only-export-components``
 * stays satisfied on the component file.
 */

/**
 * The red banner elevates from the orange variant when
 * ``last_token_refresh_at`` is older than 5 days 6 hours. Google's
 * test-mode refresh tokens are documented to expire roughly 7 days after
 * issue; the 5d6h threshold builds in a safety margin that covers a
 * user's next poll cycle plus reaction time to re-grant scopes.
 */
export const TEST_MODE_RED_THRESHOLD_MS = (5 * 24 + 6) * 60 * 60 * 1000;

/** Deployment-guide anchor for the orange banner's "Learn more" link. */
export const TEST_MODE_LEARN_MORE_URL =
  "https://developers.google.com/identity/protocols/oauth2#expiration";

/**
 * Decide which test-mode banner variant to render given the account's
 * ``last_token_refresh_at`` timestamp. Returns ``"orange"`` when the
 * token is considered fresh enough (or when the timestamp is missing /
 * unparseable — conservative default), otherwise ``"red"``.
 *
 * The ``now`` parameter is exposed so tests can pin a deterministic
 * reference time; production callers rely on the default ``new Date()``.
 */
export function computeTestModeBannerVariant(
  lastTokenRefreshAt: string | null,
  now: Date = new Date(),
): "orange" | "red" {
  if (!lastTokenRefreshAt) return "orange";
  const refreshed = new Date(lastTokenRefreshAt);
  if (Number.isNaN(refreshed.getTime())) return "orange";
  const ageMs = now.getTime() - refreshed.getTime();
  return ageMs >= TEST_MODE_RED_THRESHOLD_MS ? "red" : "orange";
}
