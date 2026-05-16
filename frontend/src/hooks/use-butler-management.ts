/**
 * TanStack Query hooks for Phase 7 butler management endpoints (§9.2).
 *
 * Covers system prompt versioning, tool grants, memory access, and kill switch.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getButlerMemoryAccess,
  getButlerPrompt,
  getButlerPromptHistory,
  getButlerTools,
  killButler,
  updateButlerPrompt,
  updateButlerTool,
} from "@/api/index.ts";
import type { KillRequest, PromptUpdateRequest, ToolUpdateRequest } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// System prompt
// ---------------------------------------------------------------------------

/** Fetch the current system prompt for a butler (version DESC → head). */
export function useButlerPrompt(name: string) {
  return useQuery({
    queryKey: ["butlers", name, "prompt"],
    queryFn: () => getButlerPrompt(name),
    enabled: !!name,
  });
}

/** Mutation hook for updating a butler's system prompt. */
export function useUpdateButlerPrompt(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: PromptUpdateRequest) => updateButlerPrompt(name, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["butlers", name, "prompt"] });
      void queryClient.invalidateQueries({ queryKey: ["butlers", name, "prompt-history"] });
    },
  });
}

/** Fetch paginated prompt version history for a butler (newest first). */
export function useButlerPromptHistory(name: string, params?: { limit?: number; offset?: number }) {
  return useQuery({
    queryKey: ["butlers", name, "prompt-history", params],
    queryFn: () => getButlerPromptHistory(name, params),
    enabled: !!name,
  });
}

// ---------------------------------------------------------------------------
// Tool grants
// ---------------------------------------------------------------------------

/** Fetch tool grants for a butler. */
export function useButlerTools(name: string) {
  return useQuery({
    queryKey: ["butlers", name, "tools"],
    queryFn: () => getButlerTools(name),
    enabled: !!name,
  });
}

/** Mutation hook for updating a butler's tool grant. */
export function useUpdateButlerTool(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tool, body }: { tool: string; body: ToolUpdateRequest }) =>
      updateButlerTool(name, tool, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["butlers", name, "tools"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Memory access
// ---------------------------------------------------------------------------

/** Fetch memory tier access metadata for a butler. */
export function useButlerMemoryAccess(name: string) {
  return useQuery({
    queryKey: ["butlers", name, "memory-access"],
    queryFn: () => getButlerMemoryAccess(name),
    enabled: !!name,
  });
}

// ---------------------------------------------------------------------------
// Kill switch
// ---------------------------------------------------------------------------

/** Mutation hook for initiating a butler's graceful shutdown. */
export function useKillButler(name: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: KillRequest) => killButler(name, body),
    onSuccess: () => {
      // Invalidate butler status so the list/overview reflects shutdown state.
      void queryClient.invalidateQueries({ queryKey: ["butlers", name] });
      void queryClient.invalidateQueries({ queryKey: ["butlers"] });
    },
  });
}
