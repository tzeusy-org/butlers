/**
 * Feature flags for the Butlers dashboard.
 *
 * Each flag reads a VITE_* env var (set at build time or via docker-compose)
 * and falls back to a safe default. As of 2026-07-03 (owner decision),
 * INGESTION_DISPATCH_CONSOLE defaults ON in both dev and prod; legacy
 * ingestion capabilities orphaned by the flip are accepted.
 *
 * Python side: app.py reads the INGESTION_DISPATCH_CONSOLE env var on startup
 * and logs the effective value. Set INGESTION_DISPATCH_CONSOLE=false in your
 * environment to force the legacy ingestion surface.
 */

/**
 * INGESTION_DISPATCH_CONSOLE — gates the /ingestion sub-route hierarchy
 * (§2.1) and all Wave-1 through Wave-3 ingestion redesign features.
 *
 * Default: true in both dev and prod (owner decision 2026-07-03).
 * Override: VITE_INGESTION_DISPATCH_CONSOLE=true|false
 *   (set to false as a kill switch to force the legacy surface).
 */
export const INGESTION_DISPATCH_CONSOLE: boolean = (() => {
  const raw = import.meta.env.VITE_INGESTION_DISPATCH_CONSOLE;
  if (raw === "true") return true;
  if (raw === "false") return false;
  // Default ON in both dev and prod.
  return true;
})();
