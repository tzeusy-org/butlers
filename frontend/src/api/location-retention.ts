/**
 * Raw OwnTracks location retention: the owner's horizon and expiry switch.
 * Backed by /api/chronicler/location-retention (REQ-chronicler-api-018, bu-s11n0s.75).
 *
 * The endpoint returns the policy itself, not an {data} envelope. A PUT is a compare-and-set on
 * `version`: a stale `expected_version` is a 409 whose detail names the current version.
 */

import { apiFetch } from "./client.ts";

export type LocationRetentionStatus = "not_enforced" | "enforcing";

export interface LocationRetentionPolicy {
  days: number;
  version: number;
  updated_at: string;
  min_days: number;
  max_days: number;
  raw_age_basis: string;
  precision_after_forgetting: string;
  covered_sources: string[];
  enforced: boolean;
  status: LocationRetentionStatus;
}

export interface LocationRetentionUpdate {
  days: number;
  expected_version: number;
  /** Omitted keeps the switch where it is. */
  enforced?: boolean;
}

export function getLocationRetention(): Promise<LocationRetentionPolicy> {
  return apiFetch<LocationRetentionPolicy>("/chronicler/location-retention");
}

export function setLocationRetention(
  update: LocationRetentionUpdate,
): Promise<LocationRetentionPolicy> {
  return apiFetch<LocationRetentionPolicy>("/chronicler/location-retention", {
    method: "PUT",
    body: JSON.stringify(update),
  });
}
