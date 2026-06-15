/**
 * Utility functions for ContactChannelCard — extracted to a non-component file
 * so that react-refresh/only-export-components is satisfied (bu-dvquo).
 */

import type { ContactInfoEntry } from "@/api/types";

/**
 * Sort a contact_info array primary-first (stable within each group).
 * is_primary=true entries come before is_primary=false entries.
 * Relative order within each group is preserved (stable sort).
 *
 * Note: ContactInfoEntry carries no per-channel `verified` flag.
 * Amber unverified-dot treatment requires a backend schema addition.
 * See: bu-dvquo "Discovered follow-up: ContactInfoEntry lacks verified field".
 */
export function sortChannelsPrimaryFirst(entries: ContactInfoEntry[]): ContactInfoEntry[] {
  // Stable sort: is_primary=true → 0, is_primary=false → 1
  return [...entries].sort((a, b) => (a.is_primary === b.is_primary ? 0 : a.is_primary ? -1 : 1));
}
