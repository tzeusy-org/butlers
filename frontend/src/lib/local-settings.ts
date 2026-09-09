function hasWindow() {
  return typeof window !== "undefined";
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

export function readNumberSetting(key: string, fallback: number): number {
  if (!hasWindow()) return fallback;
  try {
    const value = window.localStorage.getItem(key);
    if (value === null) return fallback;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  } catch {
    // Ignore localStorage read failures.
  }
  return fallback;
}

export function writeNumberSetting(key: string, value: number) {
  if (!hasWindow()) return;
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // Ignore localStorage write failures.
  }
}

