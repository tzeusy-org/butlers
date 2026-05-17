/**
 * Feature flags for the Butlers dashboard.
 *
 * Each flag reads a VITE_* env var (set at build time or via docker-compose)
 * and falls back to a safe default:
 *   - dev  (import.meta.env.DEV === true):  flags default ON for local dev
 *   - prod (import.meta.env.DEV === false): flags default OFF for staged rollout
 *
 * Python side: app.py reads the INGESTION_DISPATCH_CONSOLE env var on startup
 * and logs the effective value. Set INGESTION_DISPATCH_CONSOLE=true in your
 * environment to enable the new ingestion sub-routes in production.
 */

/**
 * INGESTION_DISPATCH_CONSOLE — gates the /ingestion sub-route hierarchy
 * (§2.1) and all Wave-1 through Wave-3 ingestion redesign features.
 *
 * Default: true in dev, false in prod.
 * Override: VITE_INGESTION_DISPATCH_CONSOLE=true|false
 */
export const INGESTION_DISPATCH_CONSOLE: boolean = (() => {
  const raw = import.meta.env.VITE_INGESTION_DISPATCH_CONSOLE;
  if (raw === "true") return true;
  if (raw === "false") return false;
  // Fall back to dev=on, prod=off
  return import.meta.env.DEV === true;
})();
