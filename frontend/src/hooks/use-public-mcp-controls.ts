/** TanStack Query hooks for the Public MCP kill switches (/api/settings/public-mcp). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getPublicMcpControls,
  getPublicMcpSummary,
  setPublicMcpControl,
  type ControlState,
} from "@/api/public-mcp-controls.ts";

export const PUBLIC_MCP_KEY = ["public-mcp-controls"] as const;
export const PUBLIC_MCP_SUMMARY_KEY = ["console-panel-public-mcp"] as const;

export function usePublicMcpControls() {
  return useQuery({
    queryKey: PUBLIC_MCP_KEY,
    queryFn: getPublicMcpControls,
    staleTime: 10_000,
  });
}

/** The console panel's own fetch; a failure here never touches another panel. */
export function usePublicMcpSummary() {
  return useQuery({
    queryKey: PUBLIC_MCP_SUMMARY_KEY,
    queryFn: getPublicMcpSummary,
    retry: 1,
  });
}

// HONEST-PENDING, never optimistic: a kill switch must show what the server holds. The row
// only changes once the server answers; a failed write leaves the list as it was.
export function useSetPublicMcpControl() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { domain: string; state: ControlState }) =>
      setPublicMcpControl(vars.domain, vars.state),
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: PUBLIC_MCP_KEY }),
        queryClient.invalidateQueries({ queryKey: PUBLIC_MCP_SUMMARY_KEY }),
      ]),
  });
}
