/**
 * Public MCP kill switches: owner read and write of mcp_surface.controls.
 * Backed by /api/settings/public-mcp (REQ-dashboard-settings-console-001).
 */

import { apiFetch } from "./client.ts";
import type { ApiResponse } from "./types.ts";

export type ControlState = "enabled" | "disabled";

export interface PublicMcpControl {
  domain: string;
  /** Null when the control row is missing. */
  state: ControlState | null;
  changed_at: string | null;
  changed_by: string | null;
}

export interface PublicMcpSummary {
  disabled_domains: number;
  total_domains: number;
  all_disabled: boolean;
  /** Control rows absent from the table; neither enabled nor disabled. */
  missing_rows: number;
}

export interface PublicMcpControls {
  controls: PublicMcpControl[];
  summary: PublicMcpSummary;
}

export function getPublicMcpControls(): Promise<ApiResponse<PublicMcpControls>> {
  return apiFetch<ApiResponse<PublicMcpControls>>("/settings/public-mcp");
}

export function getPublicMcpSummary(): Promise<ApiResponse<PublicMcpSummary>> {
  return apiFetch<ApiResponse<PublicMcpSummary>>("/settings/public-mcp/summary");
}

export function setPublicMcpControl(
  domain: string,
  state: ControlState,
): Promise<ApiResponse<PublicMcpControl>> {
  return apiFetch<ApiResponse<PublicMcpControl>>(
    `/settings/public-mcp/${encodeURIComponent(domain)}`,
    { method: "PUT", body: JSON.stringify({ state }) },
  );
}
