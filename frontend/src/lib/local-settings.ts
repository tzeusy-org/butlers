const AUTO_REFRESH_INTERVAL_OPTIONS = [5_000, 10_000, 30_000, 60_000] as const;

export const AUTO_REFRESH_ENABLED_KEY = "butlers:auto-refresh:enabled";
export const AUTO_REFRESH_INTERVAL_KEY = "butlers:auto-refresh:interval";
export const RECENT_SEARCHES_KEY = "butlers:recent-searches";

export type AutoRefreshInterval = (typeof AUTO_REFRESH_INTERVAL_OPTIONS)[number];

function hasWindow() {
  return typeof window !== "undefined";
}

export function getAutoRefreshIntervals(): readonly AutoRefreshInterval[] {
  return AUTO_REFRESH_INTERVAL_OPTIONS;
}

export function isAutoRefreshInterval(value: number): value is AutoRefreshInterval {
  return AUTO_REFRESH_INTERVAL_OPTIONS.includes(value as AutoRefreshInterval);
}

export function readBooleanSetting(key: string, fallback: boolean): boolean {
  if (!hasWindow()) return fallback;
  try {
    const value = window.localStorage.getItem(key);
    if (value === "true") return true;
    if (value === "false") return false;
  } catch {
    // Ignore localStorage read failures.
  }
  return fallback;
}

export function writeBooleanSetting(key: string, value: boolean) {
  if (!hasWindow()) return;
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // Ignore localStorage write failures.
  }
}

export function readIntervalSetting(
  key: string,
  fallback: number,
): AutoRefreshInterval {
  if (!hasWindow()) return fallback as AutoRefreshInterval;
  try {
    const value = Number(window.localStorage.getItem(key));
    if (isAutoRefreshInterval(value)) {
      return value as AutoRefreshInterval;
    }
  } catch {
    // Ignore localStorage read failures.
  }

  if (isAutoRefreshInterval(fallback)) {
    return fallback as AutoRefreshInterval;
  }
  return 10_000;
}

export function writeIntervalSetting(key: string, value: number) {
  if (!hasWindow()) return;
  if (!isAutoRefreshInterval(value)) return;
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // Ignore localStorage write failures.
  }
}
