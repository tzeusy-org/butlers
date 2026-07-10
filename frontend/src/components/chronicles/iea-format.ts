// ---------------------------------------------------------------------------
// iea-format — shared duration / delta formatters for the IEA day surfaces
// (Day Ribbon, Balance rings, Who-you-were-with, Trends). Pure functions, no
// React, so they are trivially unit-testable and reused across panels.
// ---------------------------------------------------------------------------

/** Format a duration in seconds as "Hh MMm", "MMm", or "0m". */
export function formatSeconds(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return total === 0 ? "0m" : `${total}s`;
  const mins = Math.round(total / 60);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h <= 0) return `${m}m`;
  return m === 0 ? `${h}h` : `${h}h ${m.toString().padStart(2, "0")}m`;
}

/**
 * Format a signed delta (seconds) vs a baseline as "+1h 20m", "−45m", or
 * "on par" when it rounds to under a minute. Uses a real minus sign (U+2212),
 * not a hyphen, per the design-language typographic rules.
 */
export function formatSignedDelta(deltaSeconds: number): string {
  const rounded = Math.round(deltaSeconds / 60) * 60;
  if (rounded === 0) return "on par";
  const sign = rounded > 0 ? "+" : "−";
  return `${sign}${formatSeconds(Math.abs(rounded))}`;
}

/**
 * Direction of a delta for colour / arrow selection. "flat" when it rounds to
 * under a minute either way, so a rounding artifact never reads as a change.
 */
export function deltaDirection(deltaSeconds: number): "up" | "down" | "flat" {
  const rounded = Math.round(deltaSeconds / 60) * 60;
  if (rounded > 0) return "up";
  if (rounded < 0) return "down";
  return "flat";
}
