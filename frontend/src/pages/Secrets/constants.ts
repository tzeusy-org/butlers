/**
 * Secrets page — shared constants for the passport-book redesign.
 *
 * Tweaks-panel persistence (Q9 resolution)
 * -----------------------------------------
 * UI tweaks (e.g. show-values toggle, identity filter, compact mode) are
 * persisted via localStorage, keyed under the `secrets.tweaks.*` namespace.
 *
 * Decision rationale:
 *   - The ingestion redesign and entity redesign both ship localStorage
 *     persistence for UI mode/toggle state (see ButlerDetailPage.tsx,
 *     EntityDetailPage.tsx, CalendarWorkspacePage.tsx). No server-side
 *     prefs or URL-fragment pattern was introduced by those pages.
 *   - Q9 in openspec/changes/redesign-secrets-passport/design.md specifies:
 *     "match whichever pattern the in-flight ingestion-redesign or
 *     entity-redesign ships first; fall back to localStorage keyed
 *     `secrets.tweaks.*`".
 *   - Since both prior redesigns shipped localStorage, this page matches
 *     that pattern. Key format: `secrets.tweaks.<toggle-name>`.
 *
 * Pattern: use readBooleanSetting / writeBooleanSetting from
 * `src/lib/local-settings.ts` for boolean tweaks. Fallback gracefully
 * when localStorage is unavailable (SSR, private browsing).
 */

/** Namespace prefix for all Secrets tweaks localStorage keys. */
export const SECRETS_TWEAKS_NS = "secrets.tweaks" as const;

/**
 * Returns the fully-qualified localStorage key for a named tweak.
 * e.g. secretsTweakKey("show-values") → "secrets.tweaks.show-values"
 */
export function secretsTweakKey(name: string): string {
  return `${SECRETS_TWEAKS_NS}.${name}`;
}
