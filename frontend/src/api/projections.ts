/**
 * Ambient projection grants: owner-minted, route-scoped read grants for glance
 * surfaces (wall display, e-ink, Home Assistant). Backed by /api/projections.
 *
 * A token is returned once, at mint or rotate; the list never carries it.
 */

import { apiFetch } from "./client.ts";
import type { ApiResponse } from "./types.ts";

export interface ProjectionGrant {
  id: string;
  label: string;
  surface_kind: string;
  audience_class: string;
  route_scope: string;
  expected_fetch_interval_seconds: number;
  created_at: string;
  revoked_at: string | null;
  last_fetch_at: string | null;
  last_fetch_status: string | null;
}

/** A grant plus the one-time token and its glance path. */
export interface MintedProjection extends ProjectionGrant {
  token: string;
  path: string;
}

export function listProjections(): Promise<ApiResponse<ProjectionGrant[]>> {
  return apiFetch<ApiResponse<ProjectionGrant[]>>("/projections");
}

export function revokeProjection(id: string): Promise<ApiResponse<ProjectionGrant>> {
  return apiFetch<ApiResponse<ProjectionGrant>>(`/projections/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function rotateProjection(id: string): Promise<ApiResponse<MintedProjection>> {
  return apiFetch<ApiResponse<MintedProjection>>(
    `/projections/${encodeURIComponent(id)}/rotate`,
    { method: "POST" },
  );
}
