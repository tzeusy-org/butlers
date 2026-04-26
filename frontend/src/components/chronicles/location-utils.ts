// ---------------------------------------------------------------------------
// location-utils — calendar episode location parsing (bu-ig72b.24)
//
// Utility for parsing free-text location strings from Google Calendar events.
// No geocoding is performed: only "lat,lng" coordinate pairs are recognised.
// ---------------------------------------------------------------------------

/**
 * Parse a "lat,lng" string as returned by the Google Calendar location field.
 *
 * Accepts: optional whitespace around comma, decimal or integer degrees,
 * optional leading sign.  Both lat and lng must be present.
 *
 * Returns { lat, lng } on success, or null if the string does not match.
 * NO geocoding is attempted for non-coordinate strings.
 */
export function parseLatLng(location: string): { lat: number; lng: number } | null {
  // Match: optional sign, digits, optional decimal, comma, same for lng.
  const match = /^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/.exec(location)
  if (!match) return null
  const lat = parseFloat(match[1])
  const lng = parseFloat(match[2])
  // Sanity-check coordinate ranges.
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null
  return { lat, lng }
}
